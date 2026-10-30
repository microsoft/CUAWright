"""python -m cuawright.webwright.skill_factory build <spec.yaml> — solve a template's instances, then learn.

build = solve x N + learn. Give it a skill spec (a task template + a table of instances) and it
solves each instance with the webwright agent, then distills the solves into a verified library
skill. If you ALREADY have finished run directories, use `learn` instead — it skips solving.

The spec is a single human-editable file (draft one with `init`):

    task: earliest nonstop flight from {origin} to {destination} on {date} (one-way)? ...
    start_url: https://www.google.com/flights
    instances:
      - {origin: "Seattle (SEA)",       destination: "New York (JFK)",  date: "2026-08-15"}
      - {origin: "San Francisco (SFO)",  destination: "Boston (BOS)",    date: "2026-08-15"}
      - {origin: "Los Angeles (LAX)",    destination: "Chicago (ORD)",   date: "2026-08-15"}
    build:                 # optional — verification / aggregation policy, all with defaults
      verify: strict       # strict | shape | off
      draws: 2             # independent distillation attempts
      verify_rounds: 3     # repair rounds within one attempt
      on_fail: reference   # reference = keep a readable prior if replay fails | reject = executable-or-nothing
      chunk: 25

Machine-specific settings stay OUT of the spec (so it stays committable): the agent's model
config is a CLI flag (-c model.yaml, repeatable), as are --library and --jobs (how many solves
to run in parallel — a property of your box and gateway, not of the skill). CLI flags override
the spec's `build:` block; the block overrides the built-in defaults.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

from .learn import learn

_ANSWER_INSTR = ('Additionally, write the final answer into $WORKSPACE_DIR/agent_response.json '
                 'as {"retrieved_data": <the answer, as a JSON list>}.')


def _fill(template: str, params: dict) -> str:
    """Substitute {name} holes in the template with this instance's values."""
    holes = set(re.findall(r"{(\w+)}", template))
    missing = holes - set(map(str, params))
    if missing:
        raise SystemExit(f"build: instance {json.dumps(params, ensure_ascii=False)} is missing "
                         f"value(s) for {sorted(missing)} (holes in the task template)")
    return re.sub(r"{(\w+)}", lambda m: str(params[m.group(1)]), template)


def _already_solved(outputs: Path, core_task: str) -> Path | None:
    """Resume: a prior run whose task text contains this instance AND wrote an answer."""
    for tj in outputs.glob("*/task.json"):
        try:
            task = json.loads(tj.read_text(encoding="utf-8")).get("task", "")
        except Exception:
            continue
        if core_task in task and (tj.parent / "agent_response.json").exists():
            return tj.parent
    return None


def _solve(core_task: str, start_url: str, library: Path, outputs: Path,
           task_id: str, cfg: list[str], log_path: Path | None = None) -> int:
    """Run one agent solve. Always launches the agent (build needs a real trajectory to distill),
    but with the library hint resolved out of the loop. log_path captures output in parallel mode."""
    from .prompt import with_skill_hint
    from .route import run_webwright
    prompt = with_skill_hint(core_task + " " + _ANSWER_INSTR, task=core_task, library=str(library))
    return run_webwright(prompt, start_url=start_url, cfg=cfg, outputs=str(outputs),
                         task_id=task_id, log_path=log_path)


def _pick(cli, spec_val, default):
    if cli is not None:
        return cli
    if spec_val is not None:
        return spec_val
    return default


def build(spec_path: str, library: str, cfg: list[str], *, verify=None, verify_rounds=None,
          on_fail=None, chunk=None, golds=None, outputs_dir=None, dry_run=False,
          assume_yes=False, jobs=1, draws=None) -> int:
    spec = yaml.safe_load(Path(spec_path).read_text(encoding="utf-8")) or {}
    task = spec.get("task", "").strip()
    start_url = spec.get("start_url", "").strip()
    instances = spec.get("instances") or []
    policy = spec.get("build") or {}
    if not task or not start_url or not instances:
        raise SystemExit("build: spec needs non-empty 'task', 'start_url', and 'instances'.")

    verify = _pick(verify, policy.get("verify"), "strict")
    verify_rounds = _pick(verify_rounds, policy.get("verify_rounds"), 2)
    on_fail = _pick(on_fail, policy.get("on_fail"), "reference")
    chunk = _pick(chunk, policy.get("chunk"), 25)
    draws = _pick(draws, policy.get("draws"), 2)

    lib = Path(library).resolve()
    outputs = Path(outputs_dir).resolve() if outputs_dir else (Path(spec_path).resolve().parent /
                                                               "build_outputs")
    outputs.mkdir(parents=True, exist_ok=True)

    concrete = [(_fill(task, p), p) for p in instances]

    print(f"build plan: {len(concrete)} instance(s) of\n  {task}\n"
          f"start_url: {start_url}\noutputs:   {outputs}\n"
          f"policy:    verify={verify} draws={draws} rounds={verify_rounds} on_fail={on_fail}\n"
          f"jobs:      {jobs} solve(s) in parallel\n"
          f"library:   {lib}\n")
    for i, (ct, _) in enumerate(concrete):
        state = "already solved (will reuse)" if _already_solved(outputs, ct) else "will solve"
        print(f"  [{i}] {state}: {ct[:110]}")

    to_solve = [c for c in concrete if not _already_solved(outputs, c[0])]

    if to_solve:
        from .route import agent_cfg
        cfg = agent_cfg(cfg)
        print(f"agent cfg: {' '.join(cfg) if cfg else 'webwright defaults (api.openai.com)'}\n")

    if dry_run:
        print("\n--dry-run: nothing solved, nothing learned.")
        return 0

    if to_solve and not assume_yes:
        if not sys.stdin.isatty():
            raise SystemExit("\nbuild: solving costs real agent time. Re-run with --yes to proceed "
                             "(or --dry-run to just see the plan).")
        ans = input(f"\nSolve {len(to_solve)} instance(s) with the agent? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("aborted."); return 1

    solved = len(concrete) - len(to_solve)   # the resumed ones
    failed = []

    def _one(i_ct):
        i, ct = i_ct
        log = outputs / f"solve_{i:02d}.log" if jobs > 1 else None
        rc = _solve(ct, start_url, lib, outputs, f"build_{i:02d}", cfg, log)
        return i, ct, rc, log

    pending = [(i, ct) for i, (ct, _p) in enumerate(concrete) if not _already_solved(outputs, ct)]

    def _ticker(stop: threading.Event, idxs: list[int]) -> None:
        """A solve is 10-60 min of silence otherwise, which reads as a hang. Report the step
        each instance is on, so progress is visible without opening the logs."""
        while not stop.wait(30):
            parts = []
            for i in idxs:
                runs = sorted(outputs.glob(f"build_{i:02d}_*"))
                steps = len(list((runs[-1] / "steps").glob("*"))) if runs and (runs[-1] / "steps").is_dir() else 0
                parts.append(f"[{i}] {steps} steps")
            print(f"  … {'  '.join(parts)}", flush=True)

    if jobs > 1 and len(pending) > 1:
        print(f"\n-- solving {len(pending)} instance(s), {jobs} at a time "
              f"(output -> {outputs}/solve_NN.log; progress every 30s) --")
        # as_completed, not map: map yields in submission order, so a finished instance
        # stays invisible behind a slow one and the run looks hung when it isn't
        stop = threading.Event()
        tick = threading.Thread(target=_ticker, args=(stop, [i for i, _ in pending]), daemon=True)
        tick.start()
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [pool.submit(_one, p) for p in pending]
            for done in as_completed(futures):
                i, ct, rc, log = done.result()
                # the answer file is the contract, not the exit code: a solve that wrote its
                # answer and then exited non-zero (killed, late crash) is still usable — learn
                # reads the artifact, so build must not disagree with it
                if _already_solved(outputs, ct):
                    solved += 1
                    note = "" if rc == 0 else f" (exited {rc}, but the answer was written)"
                    print(f"  [{i}] done ({solved}/{len(pending)}){note}: {ct[:70]}", flush=True)
                else:
                    failed.append((i, ct))
                    print(f"  ! [{i}] no answer (exit {rc}) — see {log}", flush=True)
        stop.set()
    else:
        for i, ct in pending:
            print(f"\n-- solving [{i}] {ct[:90]}")
            _, _, rc, _ = _one((i, ct))
            if _already_solved(outputs, ct):
                solved += 1
            else:
                failed.append((i, ct))
                print(f"  ! instance [{i}] did not produce an answer (exit {rc}); continuing")

    print(f"\nsolved {solved}/{len(concrete)} instance(s)" +
          (f"; {len(failed)} failed" if failed else ""))
    if solved == 0:
        raise SystemExit("build: no instance solved — nothing to learn.")

    print("\n-- learning from the solves --")
    learn(str(outputs), library, golds=golds, chunk=chunk, verify=verify,
          rounds=verify_rounds, on_fail=on_fail, draws=draws)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m cuawright.webwright.skill_factory build",
                                description="Solve a template's instances, then learn a skill.")
    p.add_argument("spec", help="skill.yaml: task template + start_url + instances (draft with init).")
    p.add_argument("--library", default="library")
    p.add_argument("-c", "--config", action="append", default=[], dest="cfg",
                   help="webwright model config for the agent (repeatable). Machine-specific — "
                        "keep it out of the spec.")
    p.add_argument("--verify", choices=["off", "shape", "strict"],
                   help="Override the spec's build.verify (default strict).")
    p.add_argument("--verify-rounds", type=int, help="Override build.verify_rounds (default 2).")
    p.add_argument("--on-fail", choices=["reject", "reference"], help="Override build.on_fail.")
    p.add_argument("--chunk", type=int, help="Override build.chunk (runs per grouping call).")
    p.add_argument("--draws", type=int, metavar="N",
                   help="Override build.draws: independent distillation attempts (default 2).")
    p.add_argument("--golds", default="", help="JSON file {task_id: gold_answer} for a gold gate.")
    p.add_argument("--outputs", help="Where to write solves (default: <spec dir>/build_outputs).")
    p.add_argument("--jobs", type=int, default=1, metavar="N",
                   help="Solve up to N instances in parallel (default 1). Machine-specific — "
                        "how much concurrency your box and gateway tolerate — so it is a flag, "
                        "not spec policy.")
    p.add_argument("--dry-run", action="store_true", help="Print the plan; solve/learn nothing.")
    p.add_argument("--yes", action="store_true", help="Skip the confirmation before solving.")
    a = p.parse_args(argv)
    golds = json.loads(Path(a.golds).read_text(encoding="utf-8")) if a.golds else None
    return build(a.spec, a.library, a.cfg, verify=a.verify, verify_rounds=a.verify_rounds,
                 on_fail=a.on_fail, chunk=a.chunk, golds=golds, outputs_dir=a.outputs,
                 dry_run=a.dry_run, assume_yes=a.yes, jobs=a.jobs, draws=a.draws)


if __name__ == "__main__":
    sys.exit(main())
