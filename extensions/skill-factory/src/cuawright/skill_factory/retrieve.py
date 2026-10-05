"""Retrieve: task -> most relevant candidate skills (relevance only).

Stable interface (swappable implementation):
    retrieve(task, library, *, k=3, method="llm") -> [Candidate]
MVP: a single LLM call that lists the whole library as a flat catalog in the prompt and lets it
pick. Swap to embeddings when the library grows large — the interface stays the same.
"""
from __future__ import annotations
from dataclasses import dataclass

from .library import Library, Skill
from .llm import llm_json


@dataclass
class Candidate:
    skill: Skill
    score: float          # relevance 0..1

    reason: str


def _catalog(library: Library) -> str:
    lines = []
    for s in library.list():
        lines.append(
            f"- skill_id: {s.skill_id}\n"
            f"  template: {s.meta.get('template','')}\n"
            f"  site: {s.meta.get('site','')}\n"
            f"  summary: {s.summary}\n"
            f"  params: {s.signature.get('params', [])}"
        )
    return "\n".join(lines)


def _retrieve_llm(task: str, library: Library, k: int) -> list[Candidate]:
    cat = _catalog(library)
    if not cat:
        return []
    sys = (
        "You match a web task to the most RELEVANT skills in a catalog (relevance only — not yet "
        "whether to use them). Return STRICT JSON: "
        '{"candidates":[{"skill_id":"...","score":<0..1>,"reason":"..."}]}, most relevant first, '
        f"at most {k}. score = how relevant. If nothing is relevant, return an empty list."
    )
    user = f"## Task\n{task}\n\n## Skill catalog\n{cat}\n\nReturn at most {k} candidates."
    out = llm_json(sys, user)
    cands = []
    for c in (out.get("candidates") or [])[:k]:
        sk = library.get(c.get("skill_id", ""))
        if sk:
            try:
                score = float(c.get("score", 0))
            except Exception:
                score = 0.0
            cands.append(Candidate(skill=sk, score=score, reason=c.get("reason", "")))
    return cands


def _retrieve_simple(task: str, library: Library, k: int) -> list[Candidate]:
    """No-LLM fallback: rank by keyword overlap between task and template/summary."""
    toks = set(task.lower().split())
    scored = []
    for s in library.list():
        bag = (s.meta.get("template", "") + " " + s.summary).lower().split()
        overlap = len(toks & set(bag))
        if overlap:
            scored.append(Candidate(skill=s, score=overlap / (len(toks) or 1), reason="keyword overlap"))
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:k]


_RETRIEVERS = {"llm": _retrieve_llm, "simple": _retrieve_simple}


def retrieve(task: str, library: Library, *, k: int = 3, method: str = "llm") -> list[Candidate]:
    return _RETRIEVERS[method](task, library, k)
