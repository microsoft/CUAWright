"""Unit test: deterministic parts of retrieve/decide (no LLM).
The LLM paths are smoke-tested in test_front.py."""
import json
import sys, tempfile
from pathlib import Path
pass
from cuawright.skill_factory.library import Library, Skill
from cuawright.skill_factory.retrieve import retrieve, Candidate
from cuawright.skill_factory.decide import decide, Decision


def _lib(d):
    lib = Library(d)
    lib.add(Skill("bestsellers", "x", {"template": "Get the top best-selling product in period",
                                        "summary": "magento bestsellers report"}))
    lib.add(Skill("reviews", "x", {"template": "Get reviewers who mention something",
                                   "summary": "product page reviews"}))
    return lib


def run():
    with tempfile.TemporaryDirectory() as d:
        lib = _lib(d)

        # retrieve(method="simple"): keyword overlap, deterministic
        cands = retrieve("top best-selling product", lib, method="simple")
        assert cands, "simple retrieve should find the bestsellers skill"
        assert cands[0].skill.skill_id == "bestsellers", "most-overlapping skill ranked first"

        cands2 = retrieve("zzz nonsense quux", lib, method="simple")
        assert cands2 == [], "no overlap -> no candidates"

        # decide with no candidates -> skip (deterministic, no LLM)
        d0 = decide("anything", [])
        assert isinstance(d0, Decision) and d0.verdict == "skip" and d0.skill_id is None

        # skill_use.recommend: a decision pointing OUTSIDE the retrieved candidates (LLM
        # hallucination — even an id that exists in the library) must downgrade to skip
        import cuawright.tools.skill_use as T
        orig_retrieve, orig_decide = T.retrieve, T.decide
        try:
            T.retrieve = lambda task, lib: [Candidate(lib.get("bestsellers"), 0.9, "stub")]
            T.decide = lambda task, cands: Decision("use", "reviews", "hallucinated: not a candidate")
            r = T.recommend("top best-selling product", d)
            assert r["verdict"] == "skip" and r["skill_id"] is None, r
            # a valid candidate is honored; with no grade it can't be run, so 'use' is promoted
            # to 'adapt' (not 'run') — and this path takes NO LLM call (promote returns on grade)
            T.decide = lambda task, cands: Decision("use", "bestsellers", "in candidates")
            r2 = T.recommend("top best-selling product", d)
            assert r2["verdict"] == "adapt" and r2["skill_id"] == "bestsellers", r2
        finally:
            T.retrieve, T.decide = orig_retrieve, orig_decide

    # with_skill_hint resolves the lookup OUT of the agent loop and injects the RESULT, not a
    # command. Mock recommend to pin the three behaviours.
    import os
    from cuawright.skill_factory.prompt import with_skill_hint
    import cuawright.tools.skill_use as SU
    orig = SU.recommend
    try:
        # a useful skill -> its id + source + guidance are prepended, before the task
        SU.recommend = lambda task, lib: {"verdict": "adapt", "skill_id": "flt",
                                          "source_path": "/lib/flt/skill.py",
                                          "how_to_reuse": "ADAPT: reuse the core"}
        h = with_skill_hint("solve it", task="t", library="./some_rel_lib")
        assert "flt" in h and "/lib/flt/skill.py" in h and "ADAPT: reuse the core" in h
        assert h.rstrip().endswith("solve it"), "task must come after the hint"

        # skip -> nothing prepended, prompt unchanged (no tokens, no steps)
        SU.recommend = lambda task, lib: {"verdict": "skip", "skill_id": None}
        assert with_skill_hint("solve it", task="t", library="./x") == "solve it"

        # fail-open: any error in the lookup must not block the solve
        def boom(task, lib):
            raise RuntimeError("gateway 500")
        SU.recommend = boom
        assert with_skill_hint("solve it", task="t", library="./x") == "solve it"
    finally:
        SU.recommend = orig

    # recommend on a MISSING or EMPTY library -> loud skip with a warning (and no mkdir side effect)
    import cuawright.tools.skill_use as T2
    r = T2.recommend("anything", "/nonexistent/skill/lib/path")
    assert r["verdict"] == "skip" and "warning" in r and "empty" in r["warning"], r
    assert not os.path.exists("/nonexistent/skill/lib/path"), "must not mkdir a bogus path"
    with tempfile.TemporaryDirectory() as d2:
        r2 = T2.recommend("anything", d2)   # exists but has no skills
        assert r2["verdict"] == "skip" and "warning" in r2, r2

    # F1: a hard failure inside recommend must surface as an ERROR (loud), not a quiet skip
    import io, contextlib
    import cuawright.tools.skill_use as TU
    orig_rec = TU.recommend
    TU.recommend = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom 401"))
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            TU.main(["--task", "x", "--library", "lib"])
        out = json.loads(buf.getvalue())
        assert out["verdict"] == "skip" and "error" in out, out
        assert "NOT consulted" in out["reason"], out["reason"]
    finally:
        TU.recommend = orig_rec

    print("test_retrieve_decide OK")


# pytest entry point (CI also runs this file as a script)
def test_all():
    run()


if __name__ == "__main__":
    run()
