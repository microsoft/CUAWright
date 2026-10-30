"""Slot filling: given a task and the parameter names a skill expects, extract each value.

This is the piece the router needs before it can run a skill directly — the skill takes a
taskspec of {param: value}, and those values have to come from the task's natural-language
intent. It is deliberately separate from `decide` (which only judges *whether* a skill fits):
filling must be conditioned on the chosen skill's signature, because the slots are the skill's,
not the task's (a task saying "from SFO to BOS" fills `origin_code`/`destination_code` for one
skill but `origin`/`destination` for another).

Filling is also where the router's one unbacked risk lives: a value extracted wrong but
plausibly (Aug 15 read as Aug 5) runs fine and returns a real, wrong answer. So the prompt's
job is narrow and strict — take what the task states, do not invent — and any param the task
does not state comes back null so the caller can decline to run rather than guess.
"""
from __future__ import annotations

from .llm import llm_json

_SYS = (
    "You are given a web task and the EXACT parameter names a reusable skill expects. Your job "
    "is to read the values out of the task — nothing more.\n"
    "Return STRICT JSON: {\"params\": {\"<name>\": \"<value>\", ...}}, one entry per requested "
    "name.\n"
    "Rules:\n"
    "- Take the value from what the task actually says. Prefer the task's own wording "
    "(a code, a name, a date, a count) verbatim.\n"
    "- Extract the MINIMAL span that identifies the value: drop leading articles/prepositions "
    "('the', 'a', 'in', 'on', 'for') and any trailing words that belong to a DIFFERENT slot. "
    "Each slot gets only its own words — e.g. 'the price of the second item' with slots "
    "{field, position} is field='price', position='second', not field='price of the second item'.\n"
    "- Fill EVERY requested name. If the task does not state a value for one, use null — do NOT "
    "invent, guess, or carry one over from an example. A wrong-but-plausible value is worse than "
    "null, because it makes the skill return a confident wrong answer.\n"
    "- Only the listed names. Ignore anything in the task that isn't one of them.\n"
    "- If example values from the skill's past runs are shown, use them ONLY to see the expected "
    "FORM of a value (e.g. an airport CODE vs a city name), never as an answer to copy."
)


def fill_params(task: str, param_names, *, examples=None) -> dict:
    """Extract {param: value} for the given skill parameter names from the task text.

    `param_names`: the skill's signature params. `examples`: optional list of {param: value}
    dicts from the skill's replays.json — shown to convey the expected form of each value, not
    to be copied. Any parameter the task does not state comes back as None.
    """
    names = [str(p) for p in (param_names or [])]
    if not names:
        return {}
    user = f"## Task\n{task}\n\n## Parameters to fill (return exactly these names)\n{names}"
    if examples:
        # a couple of past value-sets, purely to show the shape each slot takes
        shown = [{k: e.get(k) for k in names if k in e} for e in list(examples)[:3]]
        user += f"\n\n## Example value-sets from this skill's past runs (FORM only, never copy)\n{shown}"
    out = llm_json(_SYS, user)
    got = out.get("params") if isinstance(out, dict) else None
    if not isinstance(got, dict):
        return {p: None for p in names}
    # normalize to exactly the requested names; missing -> None, blanks -> None
    result = {}
    for p in names:
        v = got.get(p)
        result[p] = v if v not in ("", "null", "None") else None
    return result
