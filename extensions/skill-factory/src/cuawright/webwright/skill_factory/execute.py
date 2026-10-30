"""Direct execution: run an executable skill on filled params, no agent, no model.

This is what the router does on a `run` verdict. It reuses exactly the mechanism replay already
uses — write a taskspec.json, run `python skill.py taskspec.json` in a scratch dir with a timeout,
read agent_response.json — the difference being that here there is NO known answer to compare
against (that is the task being solved), so success is only "it produced a well-shaped answer",
never "it produced the right answer". The caller must treat a returned answer as provisional and
fall back to the agent on failure; a wrong-but-well-shaped answer is the known limitation this
cannot catch.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_TIMEOUT = int(os.environ.get("SKILL_RUN_TIMEOUT", "240"))   # matches replay's budget


def run_skill(source_path: str, params: dict, *, start_url: str = "",
              output_schema=None, timeout: int = _TIMEOUT) -> dict:
    """Execute a skill standalone on `params`. No model.

    Returns {"ok": bool, "answer": <retrieved_data or None>, "error": "<why, if not ok>"}.
    ok is False on crash, timeout, or no/blank output — all observable failures the caller can
    fall back on. ok being True means only that a non-null answer was produced, NOT that it is
    correct: there is no ground truth here to check against.
    """
    skill = Path(source_path)
    if skill.is_dir():
        skill = skill / "skill.py"
    if not skill.is_file():
        return {"ok": False, "answer": None, "error": f"skill not found at {source_path}"}

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        (tdp / "taskspec.json").write_text(json.dumps(
            {"params": params, "start_url": start_url, "output_schema": output_schema},
            ensure_ascii=False), encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, str(skill.resolve()), "taskspec.json"],
                cwd=td, env={**os.environ, "WORKSPACE_DIR": td},
                capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "answer": None, "error": f"timeout after {timeout}s"}

        arp = tdp / "agent_response.json"
        if not arp.exists():
            tail = " ".join((proc.stderr or proc.stdout or "").split())[-400:]
            return {"ok": False, "answer": None,
                    "error": f"no agent_response.json (exit {proc.returncode}); stderr: {tail or '(empty)'}"}
        try:
            answer = json.loads(arp.read_text(encoding="utf-8")).get("retrieved_data")
        except Exception as e:
            return {"ok": False, "answer": None, "error": f"unreadable agent_response.json: {e}"}

        if answer is None or answer == "" or answer == []:
            tail = " ".join((proc.stderr or proc.stdout or "").split())[-400:]
            return {"ok": False, "answer": None,
                    "error": f"skill produced an empty answer; stderr: {tail or '(empty)'}"}
        return {"ok": True, "answer": answer, "error": ""}
