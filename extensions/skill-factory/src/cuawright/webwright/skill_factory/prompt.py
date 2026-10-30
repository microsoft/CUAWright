"""Prompt helper: prepend a resolved SKILL-LIBRARY hint to a task prompt so the agent reuses it.

The library lookup is done HERE, out of the agent's step loop, not by the agent. Earlier this
hint injected a bash COMMAND for the agent to run; the agent then spent its first few steps
querying the library, reading the source, and recording a decision before it ever touched the
browser — and paid all of that even when the verdict was "skip". Every input to that lookup (the
task text, the library path) is known before the agent starts, so it is resolved up front and the
RESULT is injected instead:

  skip / error  -> nothing is prepended: no tokens, no wasted steps.
  use / adapt   -> the chosen skill, its source path, and grade-honest reuse guidance are prepended,
                   so the agent's first real step is work, not a lookup.

Kept at the prompt level (not system_template) because webwright merges system_template by
replacement; a task-prompt hint is non-invasive and leaves default behavior unchanged when unused.
"""
from __future__ import annotations

from pathlib import Path


def _resolved_hint(rec: dict) -> str:
    """Render the already-made recommendation for the agent: which skill, where, how to reuse."""
    return (
        "## Skill library (already checked for you — reuse before solving from scratch)\n"
        f"A relevant skill was found: {rec.get('skill_id')}\n"
        f"source: {rec.get('source_path', '')}\n"
        f"how to reuse: {rec.get('how_to_reuse', '')}\n"
        "Read the source and reuse it as described above before planning from scratch.\n\n"
        "---\n"
    )


def with_skill_hint(task_prompt: str, *, task: str, library: str) -> str:
    """Resolve the library lookup out of the agent loop and prepend the result to `task_prompt`.

    Fail-open: any error (a bad library path, an LLM/gateway failure) or a "skip" verdict returns
    the task prompt unchanged — the lookup must never block or corrupt a solve, only help it.
    """
    library = str(Path(library).resolve())
    try:
        from cuawright.webwright.tools.skill_use import recommend
        rec = recommend(task, library)
    except Exception:
        return task_prompt          # lookup failed — solve without a hint, never block
    if rec.get("verdict") == "skip" or not rec.get("skill_id"):
        return task_prompt          # nothing useful — no hint, no tokens, no steps
    return _resolved_hint(rec) + task_prompt
