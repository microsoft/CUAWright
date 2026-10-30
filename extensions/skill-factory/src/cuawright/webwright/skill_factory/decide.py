"""Decide whether to use: candidates + task -> use / adapt / skip (utility).

Stable interface (swappable implementation):
    decide(task, candidates, *, method="llm") -> Decision
Relevant != useful: retrieve gives "how similar", decide gives "whether and how to use it".
"""
from __future__ import annotations
from dataclasses import dataclass

from .fill import fill_params
from .llm import llm_json


@dataclass
class Decision:
    verdict: str            # "use" | "adapt" | "skip"
    skill_id: str | None
    reason: str


def _decide_llm(task: str, candidates) -> Decision:
    if not candidates:
        return Decision("skip", None, "no candidate skills")
    cat = "\n".join(
        f"- skill_id: {c.skill.skill_id} | template: {c.skill.meta.get('template','')} | "
        f"summary: {c.skill.summary} | params: {c.skill.signature.get('params', [])}"
        for c in candidates
    )
    sys = (
        "Decide whether a library skill is worth using for THIS task. Output STRICT JSON: "
        '{"verdict":"use|adapt|skip","skill_id":"...","reason":"..."}.\n'
        "- use   = the skill fits the task as-is (just different parameter values).\n"
        "- adapt = the skill's expensive core (login / navigation / extraction) is reusable, but the "
        "FINAL step differs; the agent should reuse the front and add/adapt only the last step.\n"
        "- skip  = no candidate is worth it; solve from scratch (skill_id = null).\n"
        "Relevance is not enough — only 'use'/'adapt' if it genuinely saves work."
    )
    user = f"## Task\n{task}\n\n## Candidate skills (most relevant first)\n{cat}"
    out = llm_json(sys, user)
    verdict = out.get("verdict", "skip")
    if verdict not in ("use", "adapt", "skip"):
        verdict = "skip"
    skill_id = out.get("skill_id") if verdict != "skip" else None
    return Decision(verdict=verdict, skill_id=skill_id, reason=out.get("reason", ""))


_DECIDERS = {"llm": _decide_llm}


def decide(task: str, candidates, *, method: str = "llm") -> Decision:
    return _DECIDERS[method](task, candidates)


# --- promoting a 'use' to a concrete route: run it, or hand it to the agent -------------------
# `decide` only judges shape-fit (template + slot names). Running a skill also needs it to be
# independently runnable (grade), the task to ask for nothing the skill can't express (3a), and
# every slot to be fillable from the task (fill). These add those checks on top of a 'use'.

_GAP_SYS = (
    "You are given a web task and a reusable skill's TEMPLATE (a fixed sentence with {slots}). "
    "The skill can vary ONLY the {slots}; every other word is FIXED behaviour it always performs.\n"
    "Decide whether running this skill would FAITHFULLY answer THIS task, or whether it would "
    "silently drop, narrow, or change what the task asks for. Check BOTH directions:\n"
    "1. The task asks for MORE than the template can express — an extra filter, condition, or "
    "output requirement that is not a slot and not implied by the fixed words (e.g. the task wants "
    "'nonstop' or 'under $300' but the template has no such slot).\n"
    "2. The template's FIXED words impose a constraint the task did NOT ask for, that changes the "
    "result set (e.g. the template always finds the 'earliest nonstop' flight, but the task asks "
    "for 'a flight' or 'the cheapest flight' — running it would silently narrow or re-select).\n"
    "A skill MAY do more that is HARMLESS (ordering, formatting the task does not care about); that "
    "is fine. Report a mismatch only when the skill would drop a requirement or change the result "
    "the task actually wants.\n"
    "Return STRICT JSON: {\"unmet\": \"<short phrase naming the mismatch, or empty string if the "
    "skill faithfully answers the task>\"}. If the task only changes the slot VALUES and asks for "
    "the same thing the fixed words describe, unmet is \"\"."
)


def template_gap(task: str, template: str) -> str:
    """3a: return a short phrase for what the task needs but the template can't express, or ""."""
    if not template:
        return ""
    out = llm_json(_GAP_SYS, f"## Task\n{task}\n\n## Skill template\n{template}")
    gap = (out.get("unmet") if isinstance(out, dict) else "") or ""
    return str(gap).strip()


def promote(task: str, skill, *, examples=None) -> dict:
    """Given a task and the skill decide chose (verdict 'use'), decide run vs adapt.

    Returns {"verdict": "run", "params": {...}} when the skill is executable, its template covers
    the task, and every slot fills; otherwise {"verdict": "adapt", "reason": "..."} naming why.
    `skill` is a library Skill; `examples` are its replays (form hints for filling).
    """
    grade = skill.meta.get("grade")
    if grade != "executable":
        # not proven to run standalone — read it, don't run it
        return {"verdict": "adapt",
                "reason": f"grade={grade or 'unverified'}: not proven to run standalone"}

    gap = template_gap(task, skill.meta.get("template", ""))
    if gap:
        # 3a: the task needs something the skill can't express — running it would silently drop it
        return {"verdict": "adapt", "reason": f"task needs something the skill can't express: {gap}"}

    names = list(skill.signature.get("params", []))
    params = fill_params(task, names, examples=examples)
    missing = [p for p in names if params.get(p) is None]
    if missing:
        # a slot the task doesn't state — declining to guess is the whole point (see fill.py)
        return {"verdict": "adapt",
                "reason": f"task does not state: {', '.join(missing)} — won't guess"}

    return {"verdict": "run", "params": params}
