"""Skill store. A skill = a directory under the library root holding skill.py + meta.json.

Interface (stable — implementations behind it may change):
    Library(root).list() -> [Skill]
    Library(root).get(skill_id) -> Skill | None
    Library(root).add(skill)            # write skill.py + meta.json
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Skill:
    skill_id: str
    code: str                    # source of skill.py
    meta: dict = field(default_factory=dict)   # {template, site, signature, summary, ...}

    @property
    def summary(self) -> str:
        return self.meta.get("summary", "")

    @property
    def signature(self) -> dict:
        return self.meta.get("signature", {})


class Library:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, skill_id: str) -> Path:
        return self.root / skill_id

    def list(self) -> list[Skill]:
        out = []
        for d in sorted(self.root.iterdir()):
            if (d / "meta.json").exists():
                out.append(self.get(d.name))
        return [s for s in out if s]

    def get(self, skill_id: str) -> Skill | None:
        d = self._dir(skill_id)
        if not (d / "meta.json").exists():
            return None
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        code = (d / "skill.py").read_text(encoding="utf-8") if (d / "skill.py").exists() else ""
        return Skill(skill_id=skill_id, code=code, meta=meta)

    def add(self, skill: Skill) -> None:
        d = self._dir(skill.skill_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "skill.py").write_text(skill.code, encoding="utf-8")
        (d / "meta.json").write_text(json.dumps(skill.meta, ensure_ascii=False, indent=2),
                                     encoding="utf-8")

    def path(self, skill_id: str) -> Path:
        return self._dir(skill_id) / "skill.py"
