"""learn — the friendly entry: turn a folder of finished runs into library skills.

    python -m cuawright.webwright.skill_factory learn <runs_dir> [--library ./library] [--golds golds.json]
                                     [--chunk 25] [--dry-run]

No manifest to write. For every run dir under <runs_dir> it reads task.json (task text,
start_url) and agent_response.json (the answer), gates it (gold if --golds has this
task_id, else self_verify), asks the LLM ONCE per chunk to group tasks into templates and
extract per-task params, then feeds the groups to evolve. Idempotent: processed run dirs
are remembered in <library>/.learned.json and skipped next time. The generated manifest
of each chunk is saved next to the ledger for auditing.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from .gate import gate
from .library import Library
from .llm import llm_json
from .update import Trace, evolve


def infer_schema(answer):
    """Mechanically derive output_schema from the answer's shape."""
    if isinstance(answer, list):
        item = answer[0] if answer else ""
        t = ("number" if isinstance(item, (int, float)) and not isinstance(item, bool)
             else "object" if isinstance(item, dict) else "string")
        return {"type": "array", "items": {"type": t}}
    if isinstance(answer, (int, float)) and not isinstance(answer, bool):
        return {"type": "number"}
    if isinstance(answer, dict):
        return {"type": "object"}
    return {"type": "string"}


def _is_structured(a) -> bool:
    return isinstance(a, (list, dict)) or (isinstance(a, (int, float)) and not isinstance(a, bool))


_CANON_SYS = (
    "You are given a task TEMPLATE and the ANSWER each solve produced (some may be prose, some "
    "already structured). Do TWO things:\n"
    "1) Define ONE canonical output_schema for the skill — a JSON-schema object. Prefer a "
    "fixed-length array or an object with NAMED fields capturing exactly the datum the task asks "
    "for (e.g. an array [airline, price], or an object {\"airline\":..., \"price\":...}).\n"
    "2) Rewrite EACH answer to that schema, in the SAME order as given.\n"
    "RESHAPE ONLY: a rewritten answer may use ONLY information already present in that same "
    "answer — never invent, look up, or fill a value the answer does not contain; use null for a "
    "field the answer lacks.\n"
    "Return STRICT JSON: {\"output_schema\": {...}, \"answers\": [<one reshaped answer per input>]}."
)


def canonicalize_answers(template, answers):
    """One canonical output_schema for a template group + each member's answer reshaped to it.
    Mechanical (no model) when the answers already share one structured shape; otherwise one LLM
    call defines the schema and reshapes each answer (reshape-only — it never adds data). This is
    where a template gets its ONE output shape, instead of each solve inferring its own."""
    answers = list(answers)
    if not answers:
        return {"type": "string"}, answers
    # fast path: already structured and one consistent shape -> no model, behaviour unchanged
    if all(_is_structured(a) for a in answers):
        shapes = {json.dumps(infer_schema(a), sort_keys=True) for a in answers}
        if len(shapes) == 1:
            return infer_schema(answers[0]), answers
    # model path: define schema + reshape each answer
    listing = "\n".join(f"{i}: {json.dumps(a, ensure_ascii=False)}" for i, a in enumerate(answers))
    out = llm_json(_CANON_SYS, f"## Template\n{template}\n\n## Answers\n{listing}")
    schema = out.get("output_schema") if isinstance(out.get("output_schema"), dict) else None
    coerced = out.get("answers")
    if schema is None or not isinstance(coerced, list) or len(coerced) != len(answers):
        # LLM returned nothing usable -> infer from a structured member, else keep raw as strings
        struct = next((a for a in answers if _is_structured(a)), None)
        return (infer_schema(struct) if struct is not None else {"type": "string"}), answers
    return schema, coerced


def _recover_answer(d: Path):
    """Recover (answer, status) for a run dir. Returns (ok, answer, status_or_reason):
      (True, answer, status)   learnable — answer from agent_response.json, else the exit message
      (False, None, reason)    skip, with a human reason

    Plain webwright solves write NO agent_response.json; the answer is in the trajectory's exit
    message (extra.submission / extra.final_response — always a STRING, structured only when the
    task asked for a format). Only a Submitted, non-empty exit yields an answer; a failed/limited
    run has none and is skipped."""
    tj = d / "task.json"
    if not tj.exists():
        return (False, None, "no task.json")
    try:
        task = json.loads(tj.read_text(encoding="utf-8"))
    except Exception as e:
        return (False, None, f"unreadable task.json ({e})")
    if not task.get("task"):
        return (False, None, "task.json has no 'task' (not a solve run)")

    # 1) pipeline artifact wins when present
    ar = d / "agent_response.json"
    if ar.exists():
        try:
            resp = json.loads(ar.read_text(encoding="utf-8"))
        except Exception as e:
            return (False, None, f"unreadable agent_response.json ({e})")
        ans = resp.get("retrieved_data")
        if ans is not None:
            return (True, ans, resp.get("status") or "SUCCESS")
        # present but no retrieved_data -> fall through to the exit message

    # 2) fallback: the trajectory's exit message
    trj = d / "trajectory.json"
    if not trj.exists():
        return (False, None, "no agent_response.json and no trajectory.json")
    try:
        msgs = json.loads(trj.read_text(encoding="utf-8")).get("messages", [])
    except Exception as e:
        return (False, None, f"unreadable trajectory.json ({e})")
    exit_msg = next((m for m in reversed(msgs) if m.get("role") == "exit"), None)
    if not exit_msg:
        return (False, None, "no exit message (run did not finish)")
    ex = exit_msg.get("extra", {})
    st = ex.get("exit_status", "")
    if st != "Submitted":
        return (False, None, f"exit_status={st or '?'} (no answer)")
    raw = ex.get("submission") or ex.get("final_response") or ""
    if not isinstance(raw, str) or not raw.strip():
        return (False, None, "Submitted but empty final_response")
    # the exit answer is a STRING; parse it when it is JSON-ish, else keep it as prose.
    # Coercion to the template's canonical output_schema happens later, at aggregation.
    try:
        ans = json.loads(raw)
    except Exception:
        ans = raw
    return (True, ans, "SUCCESS")


def answer_from_run(run_dir):
    """Public/testable wrapper: (answer, status) for a learnable run, or None to skip."""
    ok, ans, info = _recover_answer(Path(run_dir))
    return (ans, info) if ok else None


def collect_runs(runs_dir: Path, ledger: dict):
    """[{dir, task_id, task, start_url, answer, status}] for finished runs not yet learned.
    The answer is taken from agent_response.json when present, else recovered from the
    trajectory's exit message (plain webwright solves write no agent_response.json). Runs with
    no recoverable answer are skipped LOUDLY with a reason."""
    out = []
    skipped = 0
    for d in sorted(Path(runs_dir).iterdir()):
        if not d.is_dir() or str(d.resolve()) in ledger["runs"]:
            continue
        ok, answer, info = _recover_answer(d)
        if not ok:
            if info != "no task.json":   # bare dirs aren't runs — stay quiet about those
                skipped += 1
                print(f"  skip {d.name}: {info}")
            continue
        status = info
        t = json.loads((d / "task.json").read_text(encoding="utf-8"))
        # strip pipeline text the wrapper may have carried into the prompt: the
        # skill-library hint and the answer-output instruction must not leak into templates
        task = t.get("task", "")
        if "## Skill library" in task:
            task = task.split("---", 1)[-1].strip()
        if "Additionally, write the final answer into" in task:
            task = task.split("Additionally, write the final answer into", 1)[0].strip()
        out.append({"dir": str(d.resolve()), "task_id": t.get("task_id", d.name),
                    "task": task, "start_url": t.get("start_url", ""),
                    "answer": answer, "status": status})
    if skipped:
        print(f"  ! {skipped} run(s) skipped (no recoverable answer) — reasons above. A run has an "
              f"answer only if it finished (exit_status=Submitted) with a non-empty final_response.")
    return out


_GROUP_SYS = (
    "You organize solved web tasks into task TEMPLATES. A template is ONE parameterized program: "
    "two tasks share a template only if a single such program, on the SAME site, could serve both "
    "and return the SAME output schema.\n"
    "SAME template (merge) — they differ only in:\n"
    "  - parameter values (names, dates, places, counts); or\n"
    "  - phrasing: treat paraphrases of the SAME goal as identical ('cheapest' = 'lowest-priced' = "
    "'least expensive'); word order and choice of verb do not matter; or\n"
    "  - filters that narrow the candidate set (a color, a price ceiling, a location, an amenity) — "
    "these are parameters, not new templates.\n"
    "DIFFERENT template (split) — any of these changes the skill, even when the wording looks alike:\n"
    "  - a different SITE (amazon vs ebay): a different website needs different code, so it is a "
    "different skill, NEVER a parameter;\n"
    "  - a different core ACTION (search vs book vs cancel vs compare vs recommend);\n"
    "  - a different OPTIMIZATION OBJECTIVE / ranking criterion (cheapest vs fastest vs "
    "fewest-stops vs highest-rated vs best): it changes what the skill sorts by and extracts, so it "
    "is its own template — merge only criteria that are the SAME objective worded differently;\n"
    "  - a different OUTPUT schema.\n"
    "You are given existing template strings and a numbered task list. Return STRICT JSON:\n"
    '{"groups": [{"template": "canonical sentence with {{param}} placeholders", '
    '"members": [{"i": <task index>, "params": {"<name>": "<value>", ...}}]}]}\n'
    "Write each template in ONE canonical phrasing; do NOT preserve any member's original wording. "
    "If a task matches an EXISTING template, use that exact template string verbatim. "
    "Every task index appears in exactly one group. A group may have a single member. "
    "Params must be the concrete values from the task text."
)


def group_chunk(runs, existing_templates):
    listing = "\n".join(f"{i}: {r['task'][:220]}" for i, r in enumerate(runs))
    existing = "\n".join(f"- {t}" for t in existing_templates) or "(none yet)"
    try:
        out = llm_json(_GROUP_SYS, f"## Existing templates\n{existing}\n\n## Tasks\n{listing}")
    except Exception as exc:
        raise SystemExit(
            f"learn: the grouping LLM call failed: {exc}\n"
            f"Check OPENAI_API_KEY — and on a custom gateway also set "
            f"OPENAI_ENDPOINT (and OPENAI_MODEL), or SKILL_MODEL_ENDPOINT/SKILL_MODEL_NAME. "
            f"The endpoint is the FULL request URL (e.g. https://gateway.example/api/responses), "
            f"not a base path.")
    return out.get("groups", [])


def learn(runs_dir, library_root, golds=None, chunk=25, dry_run=False, verify="strict",
          rounds=2, on_fail="reference", draws=2):
    lib = Library(library_root)
    ledger_path = Path(library_root) / ".learned.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {"runs": {}}
    golds = golds or {}

    runs = collect_runs(Path(runs_dir), ledger)
    if not runs:
        print("nothing new to learn"); return

    # gate first — wrong solves never reach the grouping step
    admitted = []
    for r in runs:
        g = (gate(r["answer"], gold=golds[r["task_id"]], method="gold")
             if r["task_id"] in golds
             else gate(r["answer"], method="self_verify", status=r.get("status", "")))
        r["admit"] = g.admit
        if g.admit:
            admitted.append(r)
        else:
            print(f"  gate ✗ {r['task_id']}: {g.reason}")
    print(f"{len(admitted)}/{len(runs)} runs admitted by gate "
          f"({'gold' if golds else 'self_verify'})")
    if not golds:
        print("  ! gate=self_verify: shape check + the agent's own SUCCESS report — an answer "
              "the agent wrongly believed still PASSES. Pass --golds for real verification.")
    if not admitted:
        return

    for lo in range(0, len(admitted), chunk):
        batch = admitted[lo:lo + chunk]
        existing = [s.meta.get("template", "") for s in lib.list()]
        groups = group_chunk(batch, existing)
        print(f"\nchunk {lo // chunk + 1}: {len(batch)} runs -> {len(groups)} template(s)")
        for g in groups:
            members = [m for m in g.get("members", []) if 0 <= m.get("i", -1) < len(batch)]
            print(f"  {g.get('template', '?')[:90]}  ({len(members)} solve(s))")
        if dry_run:
            continue
        for g in groups:
            tmpl = g.get("template", "")
            members = [m for m in g.get("members", []) if 0 <= m.get("i", -1) < len(batch)]
            if not members:
                continue
            # aggregation defines ONE canonical output_schema for the template and reshapes every
            # member's answer to it (raw answers may be prose or differently shaped across solves)
            schema, coerced = canonicalize_answers(tmpl, [batch[m["i"]]["answer"] for m in members])
            traces = []
            for m, answer in zip(members, coerced):
                r = batch[m["i"]]
                code_p = Path(r["dir"]) / "final_script.py"
                traces.append(Trace(
                    template=tmpl,
                    code=code_p.read_text(encoding="utf-8") if code_p.exists() else "",
                    answer=answer, correct=True,
                    # existing template -> mark adapt so evolve REFINES instead of ignoring
                    verdict="adapt" if tmpl in existing else "skip",
                    meta={"params": m.get("params", {}),
                          "site": urlparse(r["start_url"]).netloc,
                          "start_url": r["start_url"],
                          "output_schema": schema}))
            if not traces:
                continue
            log = evolve(traces, lib, verify=verify, rounds=rounds, on_fail=on_fail, draws=draws)
            print(f"  evolve: {json.dumps(log)}")
            if log.get("rejected"):
                # skill did not land -> leave these runs OUT of the ledger so a later
                # learn (fixed model/site/verify mode) can try them again
                print(f"  ! runs kept un-learned (skill rejected) — re-run learn to retry")
                continue
            for m in g.get("members", []):
                if 0 <= m.get("i", -1) < len(batch):
                    ledger["runs"][batch[m["i"]]["dir"]] = {"template": tmpl}
        # audit trail + idempotence, saved per chunk
        ledger_path.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    if not dry_run:
        skills = lib.list()
        print(f"\nlibrary now has {len(skills)} skill(s); ledger -> {ledger_path}")
        for sk in skills:
            print(f"  [{sk.meta.get('grade', '?')}] {lib.path(sk.skill_id)}")


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="python -m cuawright.webwright.skill_factory learn",
                                description="Distill a folder of finished runs into library skills.")
    p.add_argument("runs_dir", help="Folder containing webwright run directories.")
    p.add_argument("--library", default="library")
    p.add_argument("--golds", default="", help="JSON file {task_id: gold_answer} -> gold gate.")
    p.add_argument("--chunk", type=int, default=25, help="Runs per LLM grouping call.")
    p.add_argument("--dry-run", action="store_true", help="Show the grouping plan, change nothing.")
    p.add_argument("--draws", type=int, default=2, metavar="N",
                   help="Independent distillation attempts before giving up (default 2). "
                        "Stops at the first that verifies.")
    p.add_argument("--verify-rounds", type=int, default=2,
                   help="Total build attempts (first + repairs) before giving up. Default 2.")
    p.add_argument("--on-fail", default="reference", choices=["reject", "reference"],
                   help="Failed verification: reject (default; runs stay retryable) or land as "
                        "grade=reference — readable prior for the agent, standalone NOT trusted.")
    p.add_argument("--verify", default="strict", choices=["off", "shape", "strict"],
                   help="A skill must REPLAY its own training taskspecs standalone before it may "
                        "enter the library. strict (default): it must reproduce the recorded "
                        "answers; shape: any non-empty, schema-shaped answer passes — use this for "
                        "task families whose answers are live data (prices, listings); off: skip.")
    a = p.parse_args(argv)
    golds = json.loads(Path(a.golds).read_text(encoding="utf-8")) if a.golds else {}
    learn(a.runs_dir, a.library, golds=golds, chunk=a.chunk, dry_run=a.dry_run,
          verify=a.verify, rounds=a.verify_rounds, on_fail=a.on_fail, draws=a.draws)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
