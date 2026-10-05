"""skill_use — the skill-library query for THIS task: the `recommend` decision.

Resolved OUT of the agent's step loop — `route` and the prompt-hint injection call it before/around
a solve, so the agent never spends its own steps querying the library. Also runnable as a CLI:

    python -m cuawright.tools.skill_use --task "Get the latest release version of facebook/react" \
        --library "$WORKSPACE_DIR/../library"

It retrieves the most relevant skill (relevance) and judges utility, then returns a JSON
recommendation: verdict (run / adapt / skip), the chosen skill, how to reuse it, the filled params,
and the path to its source. run = an executable skill that covers the task and whose slots all fill,
so it runs directly; adapt = reuse the core and change what differs; skip = solve from scratch.
Retrieval/judgement never block a solve — on any error it returns skip.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from cuawright.skill_factory.library import Library
from cuawright.skill_factory.retrieve import retrieve
from cuawright.skill_factory.decide import decide, promote


def _load_replays(lib: Library, skill_id: str) -> list:
    """The skill's past working value-sets (replays.json), used as form hints when filling."""
    f = lib.path(skill_id).parent / "replays.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8")) or []
        except Exception:
            return []
    return []


def _how_to_reuse(verdict: str, grade: str | None) -> str:
    """What the agent should DO with the skill — honest about whether it can just be run.

    Grade-blind advice ('copy the source into your script') is what made an agent loop re-emitting
    a 700-line skill; the instruction must match what the skill actually is.
    """
    if verdict == "run":
        return ("RUN it directly: python <source_path>/skill.py taskspec.json (or pass the params "
                "as --flags). It is executable and fits this task; only fall back to adapting if "
                "the answer does not actually address the task.")
    if grade == "executable":
        return ("ADAPT: this skill runs standalone, but not as-is for this task. FIRST read the "
                "ENTIRE source file (cat the whole file — do NOT read only the top), then reuse its "
                "login/navigation/extraction core and change only the part that differs.")
    return ("READ it as a PRIOR: it is not proven to run standalone, so do not just execute it. "
            "FIRST read the ENTIRE source file (cat the whole file — do NOT read only the top), then "
            "reuse its approach (login/navigation/extraction) and write the final step yourself.")


def recommend(task: str, library_root: str) -> dict:
    root = Path(library_root).resolve()
    # A missing/empty library is almost always a wrong path (relative paths resolve inside the
    # agent's workspace). Say so LOUDLY instead of a silent skip — checked before Library(),
    # whose constructor would mkdir the bogus path and hide the mistake.
    if not root.is_dir():
        return {"verdict": "skip", "skill_id": None,
                "reason": f"no skill library at {root} — check the --library path (relative paths "
                          f"resolve inside the agent's workspace)",
                "warning": f"library missing or empty at {root}"}
    if not any((p / "meta.json").exists() for p in root.iterdir() if p.is_dir()):
        # the directory is there and simply has nothing in it — sending the user to check the
        # path would be a wild goose chase; the usual cause is that learn rejected the candidate
        return {"verdict": "skip", "skill_id": None,
                "reason": f"skill library at {root} is empty — nothing has landed yet. If learn "
                          f"just rejected a skill, re-run it: distillation is stochastic and the "
                          f"runs stay retryable.",
                "warning": f"library empty at {root}"}
    lib = Library(root)
    cands = retrieve(task, lib)
    if not cands:
        return {"verdict": "skip", "skill_id": None, "reason": "library has no relevant skill"}
    d = decide(task, cands)
    # the decision must point at a RETRIEVED candidate — an LLM-hallucinated id (even one that
    # happens to exist in the library) must not be recommended
    if d.verdict != "skip" and d.skill_id not in {c.skill.skill_id for c in cands}:
        return {"verdict": "skip", "skill_id": None,
                "reason": f"decided skill '{d.skill_id}' is not among the retrieved candidates"}
    if d.verdict == "skip" or not d.skill_id:
        return {"verdict": "skip", "skill_id": None, "reason": d.reason}
    sk = lib.get(d.skill_id)
    if not sk:
        return {"verdict": "skip", "skill_id": None,
                "reason": f"decided skill '{d.skill_id}' is not in the library"}

    verdict, reason, params = d.verdict, d.reason, None
    if d.verdict == "use":
        # decide only judged shape-fit; promote decides run vs adapt (grade + 3a + fillable slots)
        pr = promote(task, sk, examples=_load_replays(lib, sk.skill_id))
        verdict = pr["verdict"]
        if verdict == "run":
            params = pr["params"]
        else:
            reason = pr.get("reason", reason)

    grade = sk.meta.get("grade")
    out = {"verdict": verdict, "skill_id": sk.skill_id, "reason": reason,
           "grade": grade, "summary": sk.summary,
           "call": sk.signature.get("call", ""), "source_path": str(lib.path(sk.skill_id)),
           "output_schema": sk.meta.get("output_schema"),
           "how_to_reuse": _how_to_reuse(verdict, grade)}
    if params is not None:
        out["params"] = params            # ready-to-run taskspec params for verdict == "run"
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m cuawright.tools.skill_use",
        description="Query the skill library for a reusable skill for the current task.",
    )
    p.add_argument("--task", required=True, help="The current task description / intent.")
    p.add_argument("--library", default=os.environ.get("SKILL_LIBRARY_ROOT", "library"),
                   help="Path to the skill library dir (default: $SKILL_LIBRARY_ROOT or ./library).")
    p.add_argument("--output", default="", help="Also write the JSON to this path (stdout "
                                                "always gets it too).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = recommend(args.task, args.library)
    except Exception as exc:
        # Degrade to skip so solving is never blocked — but say LOUDLY that the library
        # was NOT consulted: a config/auth error here silently disables all reuse otherwise.
        result = {"verdict": "skip", "skill_id": None, "error": str(exc),
                  "reason": "LOOKUP FAILED (library was NOT consulted) — this is an error, "
                            "not a no-match. Check OPENAI_API_KEY and, on a custom gateway, "
                            "OPENAI_ENDPOINT / SKILL_MODEL_ENDPOINT.",
                  }
        print(f"skill_use ERROR: {exc}", file=sys.stderr)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(payload)
    print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
