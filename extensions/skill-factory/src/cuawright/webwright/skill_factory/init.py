"""python -m cuawright.webwright.skill_factory init "<one-line need>" — draft a skill spec to review.

One LLM call turns a natural-language need into a skill.yaml DRAFT: a task template with
{parameter} holes, a *guessed* start_url, and a few PROPOSED instances — real, varied values to
fill the template. The proposals are a starting point, not ground truth: they are marked as
guesses, and a wrong one would quietly train the skill on the wrong answer, so REVIEW them
(replace, add, or delete rows) before running `build skill.yaml`. `--rows` sets how many
instances to propose. If the task is account-scoped (values private to your login), the model is
told to leave the rows blank rather than invent plausible-looking wrong ones.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .llm import llm_json

_SYS = (
    "You turn a user's one-line description of a web task into a REUSABLE TEMPLATE. "
    "Return STRICT JSON: {\"task\": \"<one sentence with {param} holes>\", "
    "\"params\": [\"<name>\", ...], \"start_url\": \"<best-guess full URL of the site to start on>\"}.\n"
    "The user almost always describes ONE CONCRETE INSTANCE of a task they will repeat with "
    "different values ('the cheapest makeup remover on Amazon' means 'the cheapest {product} on "
    "Amazon'). GENERALIZE: every concrete value they mention — a product, a place, a date, a "
    "name, a count — becomes a {hole}. Do not echo their example values back; the task must "
    "contain holes, not their specifics.\n"
    "Keep genuinely site-fixed things literal (the site itself, the phrasing). Every {hole} in "
    "task MUST appear in params and vice-versa. Ask for the answer in a stable, unambiguous form. "
    "Only if the need truly has nothing that could vary, return params: [].\n"
    "Also judge whether this task's ANSWER DRIFTS. The test is narrow: **run the same task again "
    "tomorrow, changing nothing — is the correct answer still the same string?** Being fetched "
    "live from a busy website does NOT make an answer drift; only the answer moving does.\n"
    "Same site, both cases: 'the earliest nonstop flight from SEA to JFK on 2026-08-15' does NOT "
    "drift — a schedule is published weeks ahead and reads the same tomorrow. 'the cheapest "
    "flight on that route' DOES drift — the fare moves hourly. So: prices, fares, stock, "
    "'today's top seller', anything ranked by a live number → drifts. Schedules, specs, IDs, "
    "counts of past things, published text → does not.\n"
    "Return \"drifts\": true|false, and \"drift_reason\": \"<one clause: what would move, or why "
    "nothing would>\".\n"
    "Finally, PROPOSE the requested number of concrete instances to fill the template — return "
    "\"instances\": [{\"<param>\": \"<value>\", ...}, ...], one object per instance, every param "
    "present in each. These are a STARTING POINT the user will review, so make them worth "
    "reviewing: (a) REAL and likely to actually exist on that site right now — a real product, a "
    "real airport code, a real repo — not a placeholder like 'item1'; (b) VARIED — every "
    "instance should differ on every param, spanning easy and less-easy cases, so the distilled "
    "skill is exercised across the range, not on one lucky value. If the task is account-scoped "
    "(the values are private to a user's login — their order numbers, their repos) and you cannot "
    "name real ones, return \"instances\": [] rather than inventing plausible-looking wrong values."
)


def _row(params: list[str], values: dict | None) -> str:
    values = values or {}
    cols = ", ".join(f"{p}: \"{values.get(p, '____')}\"" for p in params)
    return f"  - {{{cols}}}"


def _yaml_skeleton(task: str, params: list[str], start_url: str, rows: int,
                   drifts: bool = False, reason: str = "",
                   instances: list[dict] | None = None) -> str:
    # Proposed values are a reviewed starting point, not ground truth: render what the model
    # proposed, falling back to ____ for any missing param or any row it didn't propose. When
    # nothing was proposed (e.g. account-scoped), every cell is ____ — the old blank-form behaviour.
    instances = instances or []
    proposed = bool(instances)
    row_vals = (instances + [None] * rows)[:rows] if instances else [None] * rows
    instance_lines = "\n".join(_row(params, v) for v in row_vals)
    # strict compares the replay against the recorded answer, so it is only fair when the
    # answer holds still. On a drifting answer (a price, stock, a ranking) strict rejects a
    # working skill for doing its job — pick shape up front rather than let the user find out
    # after paying for the solves.
    verify = "shape " if drifts else "strict"
    why = (f"# this answer drifts ({reason}), so replay only checks the shape —\n"
           f"  # strict would reject a working skill for reporting today's truth\n  "
           if drifts else
           f"# this answer should hold still ({reason}), so replay demands the same answer back\n  ")
    header = (
        "# Draft skill spec — the instance values below are PROPOSED guesses. REVIEW them:\n"
        "# replace any that are wrong or don't exist, then: build skill.yaml\n"
        if proposed else
        "# Draft skill spec — fill the ____ values (your ground truth), then: build skill.yaml\n"
    )
    inst_comment = (
        "instances:            # PROPOSED — real+varied guesses to review, edit, add or delete\n"
        if proposed else
        "instances:            # give a few real instances (3+ makes a verifiable skill)\n"
    )
    return (
        f"{header}"
        f"# The {{holes}} in `task` are the parameters; each is a column below.\n\n"
        f"task: {task}\n"
        f"start_url: {start_url}    # guessed — check it opens the right page\n\n"
        f"{inst_comment}"
        f"{instance_lines}\n\n"
        f"build:                # optional policy — CLI flags override these\n"
        f"  {why}"
        f"verify: {verify}     # strict (reproduce answers) | shape (drifting data) | off\n"
        f"  draws: 2            # independent distillation attempts — a draw can come out brittle\n"
        f"  verify_rounds: 2    # repair rounds within one attempt\n"
        f"  on_fail: reference  # if replay fails: reference = keep a readable prior (never empty-handed,\n"
        f"                      # the agent can still adapt it) | reject = executable or nothing (stricter)\n"
        f"  chunk: 25\n"
    )


def init(need: str, out_path: str, rows: int = 3) -> int:
    out = Path(out_path)
    if out.exists():
        raise SystemExit(f"init: {out} already exists — remove it or pass -o another path.")
    data = llm_json(_SYS, f"Task need: {need}\nPropose {rows} instances.")
    task = (data.get("task") or "").strip()
    params = [str(p) for p in (data.get("params") or [])]
    start_url = (data.get("start_url") or "").strip()
    holes = set(re.findall(r"{(\w+)}", task))
    if not task or not holes:
        # No holes means the need names ONE task, not a task TYPE — and a skill is only worth
        # building for something you will repeat with different values.
        raise SystemExit(
            f"init: nothing in this need varies, so there is no reusable skill to build:\n"
            f"  {task or '(no task returned)'}\n\n"
            f"Say what CHANGES between runs — name the varying part:\n"
            f"  instead of 'the cheapest makeup remover on Amazon'\n"
            f"  try     'the cheapest <product> on Amazon, for any product'\n\n"
            f"If you really only want this one answer once, you don't need a skill — solve it "
            f"directly (webwright), or use /webwright:craft to get a re-runnable CLI for it.")
    # trust the holes actually in the template over a possibly-mismatched params list
    params = [p for p in params if p in holes] or sorted(holes)
    drifts = bool(data.get("drifts"))
    reason = (data.get("drift_reason") or "").strip().rstrip(".") or (
        "it changes on its own" if drifts else "nothing in it moves on its own")
    # keep only well-formed instances: a dict naming known params, at least one value present.
    instances = []
    for inst in (data.get("instances") or []):
        if isinstance(inst, dict):
            row = {p: str(inst[p]) for p in params if inst.get(p) not in (None, "")}
            if row:
                instances.append(row)
    out.write_text(_yaml_skeleton(task, params, start_url, rows, drifts, reason, instances),
                   encoding="utf-8")
    proposed = bool(instances)
    next_line = (f"Next: REVIEW the proposed values in {out} (they are guesses — replace any that "
                 f"are wrong), then\n" if proposed else
                 f"Next: fill the ____ values in {out}, then\n")
    print(f"wrote {out}\n\n  task:      {task}\n  params:    {params}\n  start_url: {start_url}\n"
          f"  instances: {len(instances)} proposed (review before build)\n"
          f"  verify:    {'shape (this answer drifts)' if drifts else 'strict (this answer should hold still)'}\n\n"
          f"{next_line}"
          f"  python -m cuawright.webwright.skill_factory build {out} --library ./library -c your_model.yaml")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m cuawright.webwright.skill_factory init",
                                description="Draft a skill.yaml skeleton from a one-line need.")
    p.add_argument("need", help="One-line description of the repeatable task you want a skill for.")
    p.add_argument("-o", "--out", default="skill.yaml", help="Where to write the spec.")
    p.add_argument("--rows", type=int, default=3,
                   help="How many instances to propose (default 3). Edit them after.")
    a = p.parse_args(argv)
    return init(a.need, a.out, rows=a.rows)


if __name__ == "__main__":
    sys.exit(main())
