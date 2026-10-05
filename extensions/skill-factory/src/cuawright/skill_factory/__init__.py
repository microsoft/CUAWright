"""cuawright.skill_factory — a memory/skill library module for cuawright.

Store solved tasks as reusable, executable code skills; retrieve + judge (use/adapt/skip) at
solve time; admit via a gate; and grow the library incrementally (evolve). Plugs into webwright
as an optional extension:
  - solve-time reuse  : the `skill_use` tool (agent invokes it like self_reflection / image_qa)
  - offline growth    : `update.evolve` (run after solves to distill gate-passed solves into skills)

Backend-agnostic: configure_llm(model) wires it to any webwright Model.
"""
from .library import Library, Skill
from .retrieve import retrieve, Candidate
from .decide import decide, Decision
from .gate import gate, GateResult
from .llm import configure_llm
from .prompt import with_skill_hint

# NOTE: `update` (evolve / Trace) is deliberately NOT imported here. It is the module run as a
# CLI (`python -m cuawright.skill_factory.update`); importing it eagerly makes runpy print a
# "found in sys.modules" RuntimeWarning on every CLI invocation. Import it directly:
#     from cuawright.skill_factory.update import evolve, Trace

__all__ = [
    "Library", "Skill", "retrieve", "Candidate", "decide", "Decision",
    "gate", "GateResult", "configure_llm", "with_skill_hint",
]
