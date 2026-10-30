"""Unit test: library store (deterministic, no LLM)."""
import sys, tempfile
from pathlib import Path
pass
from cuawright.webwright.skill_factory.library import Library, Skill


def run():
    with tempfile.TemporaryDirectory() as d:
        lib = Library(d)
        assert lib.list() == [], "empty library should list nothing"
        assert lib.get("nope") is None, "missing skill -> None"

        sk = Skill(skill_id="s1", code="print('hi')\n",
                   meta={"template": "do {x}", "summary": "does x", "signature": {"params": ["x"]}})
        lib.add(sk)

        got = lib.get("s1")
        assert got is not None and got.code == "print('hi')\n", "get returns added code"
        assert got.meta["template"] == "do {x}"
        assert got.summary == "does x"
        assert got.signature["params"] == ["x"]
        assert [s.skill_id for s in lib.list()] == ["s1"], "list shows added skill"
        assert lib.path("s1").name == "skill.py" and lib.path("s1").exists()

        # re-open from disk -> persisted
        lib2 = Library(d)
        assert [s.skill_id for s in lib2.list()] == ["s1"], "persisted across re-open"
    print("test_library OK")


# pytest entry point (CI also runs this file as a script)
def test_all():
    run()


if __name__ == "__main__":
    run()
