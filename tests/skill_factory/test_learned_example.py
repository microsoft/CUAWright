"""Lock the flagship property: the checked-in learned skill was AGGREGATED from
multiple solves (n_solves >= 3) with real lifted parameters — not a single-solve copy."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "extensions/skill-factory/src/cuawright/webwright/skill_factory/examples/learned_library"


def run():
    dirs = [d for d in ROOT.iterdir() if (d / "meta.json").exists()]
    assert dirs, "learned_library example missing"
    for d in dirs:
        meta = json.loads((d / "meta.json").read_text())
        assert meta["n_solves"] >= 3, f"{d.name}: must be aggregated from >=3 solves, got {meta['n_solves']}"
        params = meta["signature"]["params"]
        assert len(params) >= 2, f"{d.name}: parameters must be lifted, got {params}"
        assert "{{" in meta["template"], "template must have {{param}} placeholders"
        assert "Additionally, write" not in meta["template"], "pipeline text must not leak (F7)"
        extras = {f.name for f in d.iterdir()} - {"skill.py", "meta.json", "replays.json", "__pycache__"}
        assert not extras, f"{d.name}: run artifacts must not be committed: {extras}"
        code = (d / "skill.py").read_text()
        compile(code, d.name, "exec")
        # artifacts must never be written next to __file__ (shared library dir)
        assert "Path(__file__).resolve().parent\n" not in code
        for line in code.splitlines():
            if "__file__" in line and "=" in line:
                assert "WORKSPACE_DIR" not in line.split("=")[0], line
                assert not any(k in line for k in ("RUN_DIR", "SCREENSHOT", "LOG")), \
                    f"artifact path anchored to __file__: {line.strip()}"
        for p in params:
            assert p in code, f"param {p} must appear in the skill code"
    print("test_learned_example OK")


# pytest entry point (CI also runs this file as a script)
def test_all():
    run()


if __name__ == "__main__":
    run()
