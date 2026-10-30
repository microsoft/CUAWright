"""Route a task: judge, then act — run a matching skill directly, or hand it to the agent.

One entry, symmetric. `route` decides (search the library, pick a skill, judge run-vs-adapt, fill
params) and then carries the decision out:

  run    the skill is executable, covers the task, and every slot fills -> run it. If it produces
         a well-shaped answer, that is the answer and no agent starts. If it fails (crash, timeout,
         empty, wrong shape) it falls back to the agent, exactly as an adapt would.
  adapt  reuse the skill but not as-is -> hand the task to the agent with the skill as a prior.
  skip   nothing useful -> hand the task to the agent from scratch.

Judgement, execution, and fallback all live here; the two executors are injected so this file
holds only the policy, not their mechanics: `run_skill_fn` runs a skill, `agent_fn(task, hint)`
launches the agent. When `agent_fn` is None (inspection / the CLI) the agent branch returns the
resolved instruction instead of launching — so you can see the decision without acting on it.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from .execute import run_skill
from .gate import gate

_ANSWER_INSTR = ('Additionally, write the final answer into $WORKSPACE_DIR/agent_response.json '
                 'as {"retrieved_data": <the answer, as a JSON list>}.')


def run_webwright(prompt: str, *, start_url: str, cfg=None, outputs: str = ".",
                  task_id: str = "task", log_path=None) -> int:
    """Launch one webwright agent solve on `prompt`. Shared by build (always-agent, for training
    trajectories) and by route's agent branch (fallback / adapt / skip). Returns the exit code."""
    cmd = [sys.executable, "-m", "cuawright.webwright.run.cli", "main", "-t", prompt,
           "--start-url", start_url, "-o", str(outputs), "--task-id", task_id]
    for c in (cfg or []):
        cmd += ["-c", c]
    if log_path is None:
        return subprocess.run(cmd).returncode
    with open(log_path, "w", encoding="utf-8") as f:
        return subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT).returncode


def agent_cfg(cfg: list[str]) -> list[str]:
    """Resolve the webwright config for a launched agent, shared by build and route.

    If you pass -c, base.yaml is re-added unless you already named a base — because -c REPLACES
    webwright's defaults, so passing only your model yaml would drop base.yaml (which holds the
    agent scaffolding: system/instance templates) and the agent would fail to construct. This lets
    you pass just `-c model.yaml`. Otherwise, if OPENAI_ENDPOINT / OPENAI_MODEL name a gateway,
    build the config from them so nothing has to be written to a file: webwright's DEFAULT_CONFIGS
    (base + model) plus inline model.key=value overrides. The agent's model yaml never reads
    OPENAI_*, so without this a gateway you exported would send the module to your gateway and every
    solve to api.openai.com (401 after you'd already said yes). Also sets max_output_tokens (16000,
    override SKILL_AGENT_MAX_TOKENS) — base.yaml's 4000 truncates the agent mid-script when it
    reuses a large skill and the run loops.
    """
    if cfg:
        # -c REPLACES the defaults; base.yaml carries the agent's system/instance templates, so
        # re-add it unless a base was named — otherwise `-c model.yaml` alone yields an agent with
        # no template (a cryptic pydantic ValidationError, not a helpful message).
        if not any("base" in c for c in cfg):
            cfg = ["base.yaml", *cfg]
        return cfg
    over = [f"model.{key}={val}" for key, val in
            (("openai_endpoint", os.environ.get("OPENAI_ENDPOINT")),
             ("model_name", os.environ.get("OPENAI_MODEL"))) if val]
    if not over:
        return cfg
    over.append(f"model.max_output_tokens={os.environ.get('SKILL_AGENT_MAX_TOKENS', '16000')}")
    from cuawright.webwright.run.cli import DEFAULT_CONFIGS
    return list(DEFAULT_CONFIGS) + over


def agent_launcher(*, start_url: str, cfg=None, outputs: str = ".", task_id: str = "task",
                   log_path=None):
    """Build an agent_fn(task, hint) for route: prepend the hint, add the answer instruction, and
    launch cuawright.webwright. Its return value becomes route's outcome["result"] (the agent's exit code)."""
    def agent_fn(task: str, hint: str) -> int:
        prompt = (hint + "\n" if hint else "") + task + " " + _ANSWER_INSTR
        return run_webwright(prompt, start_url=start_url, cfg=cfg, outputs=outputs,
                             task_id=task_id, log_path=log_path)
    return agent_fn


def _hint(rec: dict, *, tried_error: str = "") -> str:
    """The block prepended to the agent's task when a skill is worth reusing: source path + guidance.
    On a run-fallback it also says the direct run was tried and failed, so the agent adapts rather
    than blindly re-running the same skill."""
    sid = rec.get("skill_id")
    if not sid:
        return ""
    lines = ["## A library skill may help this task (reuse before solving from scratch)",
             f"skill: {sid}",
             f"source: {rec.get('source_path', '')}",
             f"how to reuse: {rec.get('how_to_reuse', '')}"]
    if tried_error:
        lines.insert(1, f"NOTE: running it directly was tried and failed ({tried_error}); "
                        f"adapt it rather than re-running as-is.")
    return "\n".join(lines) + "\n"


def _well_shaped(answer, output_schema) -> bool:
    """A last cheap guard on a direct run's answer: non-empty and matching the declared shape.
    Not a correctness check — it cannot be, with no ground truth — just the shape gate."""
    try:
        return gate(answer, output_schema=output_schema, method="self_verify").admit
    except Exception:
        return answer not in (None, "", [])


def _to_agent(task, rec, agent_fn, *, reason, tried_error="", fell_back=False, with_skill=True):
    """Hand off to the agent. If agent_fn is given, launch it and return its result; otherwise
    return the resolved instruction (hint) so a caller can inspect the decision without acting."""
    skill_id = rec.get("skill_id") if with_skill else None
    hint = _hint(rec, tried_error=tried_error) if with_skill else ""
    if agent_fn is not None:
        result = agent_fn(task, hint)
        return {"action": "agent", "launched": True, "fell_back": fell_back,
                "skill_id": skill_id, "reason": reason, "result": result}
    return {"action": "agent", "launched": False, "fell_back": fell_back,
            "skill_id": skill_id, "reason": reason, "hint": hint}


def route(task: str, library: str, *, recommend_fn=None, run_skill_fn=run_skill,
          agent_fn=None, on_decision=None) -> dict:
    """Judge the task, then act. Returns an outcome dict (see module docstring).

    `agent_fn(task, hint) -> result` launches the agent; leave it None to only decide (the agent
    branch returns the instruction, unlaunched). `on_decision(rec)` fires with the recommendation
    BEFORE anything is executed, so a caller can show the decision before the skill/agent runs.
    `recommend_fn`/`run_skill_fn` are injectable too.
    """
    if recommend_fn is None:
        from cuawright.webwright.tools.skill_use import recommend as recommend_fn
    rec = recommend_fn(task, library)
    if on_decision is not None:
        on_decision(rec)            # show the decision before the skill or agent runs
    verdict = rec.get("verdict")

    if verdict == "run" and rec.get("source_path"):
        res = run_skill_fn(rec["source_path"], rec.get("params") or {},
                           output_schema=rec.get("output_schema"))
        if res["ok"] and _well_shaped(res["answer"], rec.get("output_schema")):
            return {"action": "answered", "via": "skill", "skill_id": rec.get("skill_id"),
                    "params": rec.get("params"), "answer": res["answer"]}
        # observable failure (or wrong shape) -> fall back to the agent, as an adapt
        err = res.get("error") or "answer failed the shape check"
        return _to_agent(task, rec, agent_fn, reason=err, tried_error=err, fell_back=True)

    if verdict in ("run", "adapt"):
        # adapt, or a run we couldn't execute (no source) — hand to the agent WITH the skill
        return _to_agent(task, rec, agent_fn, reason=rec.get("reason"))

    # skip (or anything unexpected): agent from scratch, no skill hint
    return _to_agent(task, rec, agent_fn, reason=rec.get("reason"), with_skill=False)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m cuawright.webwright.skill_factory route",
        description="Route a task: run a matching skill directly, or hand it to the agent.")
    p.add_argument("--task", required=True, help="The task, in natural language.")
    p.add_argument("--library", default=os.environ.get("SKILL_LIBRARY_ROOT", "library"),
                   help="Path to the skill library (default: $SKILL_LIBRARY_ROOT or ./library).")
    p.add_argument("--start-url", help="If given, the agent branch LAUNCHES a webwright solve on "
                                       "this URL. Without it, the agent branch only reports the "
                                       "decision (inspect mode).")
    p.add_argument("-c", "--config", action="append", default=[],
                   help="webwright model config for a launched agent (repeatable). base.yaml is "
                        "added automatically, so usually just your model yaml: -c model_gateway.yaml.")
    p.add_argument("-o", "--out", default=".", help="Output dir for a launched agent solve.")
    p.add_argument("--task-id", default="route_task", help="Task id for a launched agent solve.")
    p.add_argument("--json", action="store_true", help="Print the raw outcome as JSON.")
    a = p.parse_args(argv)
    # Same config resolution as build: no -c + OPENAI_ENDPOINT/OPENAI_MODEL in env -> auto-build
    # the agent config (zero -c); pass -c and you own it fully.
    cfg = agent_cfg(a.config)
    # With --start-url the agent branch actually launches webwright; without it, route only decides
    # (and still runs a directly-runnable skill) so you can inspect the decision cheaply.
    agent_fn = (agent_launcher(start_url=a.start_url, cfg=cfg, outputs=a.out, task_id=a.task_id)
                if a.start_url else None)

    def show_decision(rec):        # print the decision BEFORE the skill/agent runs
        if not a.json:
            print(f"  route decided: {rec.get('verdict')}   skill: {rec.get('skill_id')}")
            if rec.get("reason"):
                print(f"  reason: {rec['reason']}")
            if rec.get("verdict") == "run" or a.start_url:
                # something will execute: a directly-runnable skill, or (with --start-url) the
                # agent — including 'skip', where the agent solves from scratch
                where = "skill" if rec.get("verdict") == "run" else "agent from scratch" \
                        if rec.get("verdict") == "skip" else "agent"
                print(f"  --- running ({where}) below ---")

    out = route(a.task, a.library, agent_fn=agent_fn, on_decision=show_decision)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if out["action"] == "answered":
        print(f"  ran directly, NO agent — answer: {json.dumps(out['answer'], ensure_ascii=False)}")
    elif out.get("launched"):
        print(f"  -> agent solve launched{' (after the direct run failed)' if out.get('fell_back') else ''}"
              f"; exit code {out.get('result')}")
    else:
        print("  -> not run directly; the task is handed to the agent"
              f"{' to ADAPT the skill' if out.get('skill_id') else ' from scratch'}.")
        if out.get("hint"):
            print("  --- what the agent receives ---")
            print("  " + out["hint"].replace("\n", "\n  ").rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
