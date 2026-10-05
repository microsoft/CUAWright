"""Unit test: evolve (growing library, usage-driven). Stubs _refine to stay LLM-free."""
import sys, tempfile
from pathlib import Path
pass
import cuawright.skill_factory.update as U
from cuawright.skill_factory.library import Library, Skill


def run():
    # stub _refine: deterministically "build/widen" a skill for the group's template
    def fake_refine(group, library, verify="off", rounds=2, on_fail="reject", draws=1):
        from cuawright.skill_factory.update import _slug
        sid = _slug(group[0].template)
        library.add(Skill(sid, f"# refined from {len(group)} solves\n",
                          {"template": group[0].template, "provenance": "test-refine"}))
        return [sid]
    U._refine = fake_refine

    with tempfile.TemporaryDirectory() as d:
        lib = Library(d)

        # round 1: template T1 not in lib, skip verdict -> ADD
        t1 = [U.Trace("T1", "code", verdict="skip", correct=True),
              U.Trace("T1", "code", verdict="skip", correct=True)]
        log1 = U.evolve(t1, lib)
        assert log1["added"], f"new template should be added: {log1}"
        assert len(lib.list()) == 1

        # round 2: T1 now exists, all USE success -> library unchanged
        t2 = [U.Trace("T1", "code", used_skill_id="t1", verdict="use", correct=True)]
        log2 = U.evolve(t2, lib)
        assert log2["use"] and not log2["added"] and not log2["adapt_refined"], log2
        assert len(lib.list()) == 1, "pure use must not change library"

        # round 3: T1 exists, an ADAPT happened -> refine back (widen)
        t3 = [U.Trace("T1", "code2", used_skill_id="t1", verdict="adapt", correct=True)]
        log3 = U.evolve(t3, lib)
        assert log3["adapt_refined"], f"adapt should refine back: {log3}"

        # wrong solves are dropped (not fed to refine)
        t4 = [U.Trace("T2", "bad", verdict="skip", correct=False)]
        log4 = U.evolve(t4, lib)
        assert log4["dropped_wrong"] == 1 and not log4["added"], log4

    # manifest: a run missing the gate verdict must fail loudly, never default to admitted
    try:
        U.traces_from_manifest({"template": "T", "runs": [{"dir": "/nonexistent"}]})
        raise AssertionError("missing 'admit' must raise")
    except KeyError:
        pass
    # ... and a hand-written string "false" (truthy!) must be rejected, not admitted
    try:
        U.traces_from_manifest({"template": "T", "runs": [{"dir": "/x", "admit": "false"}]})
        raise AssertionError("non-bool 'admit' must raise")
    except TypeError:
        pass

    # slug: two long templates sharing a 48-char prefix must NOT collide on one skill id
    long_a = "get the value of " + "x" * 60 + " variant one"
    long_b = "get the value of " + "x" * 60 + " variant two"
    assert U._slug(long_a) != U._slug(long_b), "truncated slugs must be disambiguated"
    assert U._slug(long_a) == U._slug(long_a), "slug must stay deterministic"
    assert U._slug("Get the top-n best-selling entity") == "get_the_top_n_best_selling_entity", \
        "short templates keep the plain readable slug"

    # ---- replay verification (real _refine + _replay, only the LLM is faked) ----
    import importlib
    importlib.reload(U)   # drop the fake_refine stub
    import json as _json

    BAD = 'import json\njson.dump({"retrieved_data": [7]}, open("agent_response.json", "w"))\n'
    GOOD = 'import json\njson.dump({"retrieved_data": [42]}, open("agent_response.json", "w"))\n'
    CRASH = 'raise RuntimeError("distillation bug")\n'

    def mktrace():
        return U.Trace("verify template", "solver code", answer=[42], verdict="skip", correct=True,
                       meta={"params": {"k": "v"}, "output_schema": {"type": "array", "items": {"type": "number"}}})

    with tempfile.TemporaryDirectory() as d:
        lib = Library(d)
        # strict: first attempt wrong -> repair returns good -> ADDED with the repaired code
        replies = iter([BAD, GOOD])
        U.llm = lambda *a, **k: next(replies)
        log = U.evolve([mktrace()], lib, verify="strict")
        assert log["added"] and not log["rejected"], log
        assert "[42]" in lib.list()[0].code, "repaired code must be what landed"

    with tempfile.TemporaryDirectory() as d:
        lib = Library(d)
        # strict: wrong twice -> REJECTED, library stays empty
        replies = iter([BAD, BAD])
        U.llm = lambda *a, **k: next(replies)
        log = U.evolve([mktrace()], lib, verify="strict", on_fail="reject")
        assert log["rejected"] and not log["added"], log
        assert lib.list() == [], "rejected skill must not land"

    with tempfile.TemporaryDirectory() as d:
        lib = Library(d)
        # shape: a non-empty, schema-shaped answer passes even if values drifted (live data)
        replies = iter([BAD])
        U.llm = lambda *a, **k: next(replies)
        log = U.evolve([mktrace()], lib, verify="shape")
        assert log["added"], f"shape mode must tolerate value drift: {log}"

    with tempfile.TemporaryDirectory() as d:
        lib = Library(d)
        # shape: a CRASHING skill is caught even in the tolerant mode (reject: nothing lands)
        replies = iter([CRASH, CRASH])
        U.llm = lambda *a, **k: next(replies)
        log = U.evolve([mktrace()], lib, verify="shape", on_fail="reject")
        assert log["rejected"], f"crash must be caught: {log}"

    with tempfile.TemporaryDirectory() as d:
        lib = Library(d)
        # rounds=3: two bad attempts, the third lands
        replies = iter([BAD, BAD, GOOD])
        U.llm = lambda *a, **k: next(replies)
        log = U.evolve([mktrace()], lib, verify="strict", rounds=3)
        assert log["added"], log
        assert lib.list()[0].meta.get("grade") == "executable"

    with tempfile.TemporaryDirectory() as d:
        lib = Library(d)
        # on_fail=reference: failed verification lands as a labeled reference skill
        replies = iter([BAD, BAD])
        U.llm = lambda *a, **k: next(replies)
        log = U.evolve([mktrace()], lib, verify="strict", on_fail="reference")
        assert log["reference"] and not log["rejected"], log
        m = lib.list()[0].meta
        assert m.get("verified") is False and m.get("grade") == "reference", m

    with tempfile.TemporaryDirectory() as d:
        lib = Library(d)
        # guard: a failed refine must NEVER overwrite an existing skill, even with on_fail=reference
        from cuawright.skill_factory.library import Skill as _Skill
        sid = U._slug("verify template")
        lib.add(_Skill(sid, "GOOD OLD CODE", {"template": "verify template", "verified": True,
                                              "grade": "executable"}))
        tr = mktrace(); tr.verdict = "adapt"
        replies = iter([BAD, BAD])
        U.llm = lambda *a, **k: next(replies)
        log = U.evolve([tr], lib, verify="strict", on_fail="reference")
        assert log["rejected"], log
        assert lib.get(sid).code == "GOOD OLD CODE", "old verified skill must survive"

    with tempfile.TemporaryDirectory() as d:
        lib = Library(d)
        # incremental refine must REGRESSION-replay old coverage:
        # land v1 (answers [42]); then a refine whose code only satisfies the NEW instance
        # ([43]) must be rejected because it breaks the stored old example
        replies = iter([GOOD])                       # v1: writes [42]
        U.llm = lambda *a, **k: next(replies)
        assert U.evolve([mktrace()], lib, verify="strict")["added"]
        assert (Path(d) / U._slug("verify template") / "replays.json").exists()
        GOOD43 = 'import json\njson.dump({"retrieved_data": [43]}, open("agent_response.json", "w"))\n'
        t43 = mktrace(); t43.answer = [43]; t43.verdict = "adapt"
        replies = iter([GOOD43, GOOD43])
        U.llm = lambda *a, **k: next(replies)
        log = U.evolve([t43], lib, verify="strict")
        assert log["rejected"], f"refine breaking old coverage must be rejected: {log}"
        assert "[42]" in lib.list()[0].code, "v1 must survive"
        # a refine that answers from the taskspec params passes BOTH old and new -> lands
        PARAM = ('import json\nspec = json.load(open("taskspec.json"))\n'
                 'json.dump({"retrieved_data": [int(spec["params"]["want"])]}, '
                 'open("agent_response.json", "w"))\n')
        t42 = mktrace(); t42.meta["params"] = {"want": "42"}
        # rebuild v1's example with params the general code can use
        import json as _j
        rp = Path(d) / U._slug("verify template") / "replays.json"
        rp.write_text(_j.dumps([{"params": {"want": "42"}, "start_url": "", "output_schema":
                                 {"type": "array", "items": {"type": "number"}}, "answer": [42]}]))
        t43b = mktrace(); t43b.answer = [43]; t43b.verdict = "adapt"; t43b.meta["params"] = {"want": "43"}
        replies = iter([PARAM])
        U.llm = lambda *a, **k: next(replies)
        log = U.evolve([t43b], lib, verify="strict")
        assert log["adapt_refined"], f"general refine passing old+new must land: {log}"

    # update CLI smoke: -m cuawright.skill_factory.update must not NameError on Path (regression)
    with tempfile.TemporaryDirectory() as d:
        mf = Path(d) / "m.json"
        mf.write_text(_json.dumps({"template": "T", "runs": []}))
        assert U.main(["--manifest", str(mf), "--library", str(Path(d) / "lib")]) == 0

    print("test_evolve OK")


# pytest entry point (CI also runs this file as a script)
def test_all():
    run()


if __name__ == "__main__":
    run()


def test_a_fresh_draw_lands_where_repairing_the_bad_one_would_not():
    """--draws is not --verify-rounds: rounds repair the SAME candidate, draws throw it away.
    A draw that is brittle all the way through must not sink the batch when a fresh one works."""
    import tempfile
    import cuawright.skill_factory.update as U
    from cuawright.skill_factory.library import Library

    BAD = "import json,sys\njson.dump({'retrieved_data': ['wrong']}, open('agent_response.json','w'))\n"
    GOOD = "import json,sys\njson.dump({'retrieved_data': ['right']}, open('agent_response.json','w'))\n"
    calls = []

    def llm(system, user, **kw):
        calls.append(user)
        # every repair round of draw 1 stays broken; the fresh draw 2 is fine
        feedback_round = "Replay failures of your previous attempt" in user
        return f"```python\n{BAD if len(calls) <= 2 or feedback_round else GOOD}```"

    U.llm = llm
    with tempfile.TemporaryDirectory() as d:
        lib = Library(d)
        tr = [U.Trace("T", "code", answer=["right"], correct=True,
                      meta={"params": {}, "output_schema": {"type": "array"}})]
        U._refine(tr, lib, verify="strict", rounds=2, draws=1, on_fail="reject")
        assert not lib.list(), "one draw, all rounds broken -> nothing lands"

    calls.clear()
    with tempfile.TemporaryDirectory() as d:
        lib = Library(d)
        tr = [U.Trace("T", "code", answer=["right"], correct=True,
                      meta={"params": {}, "output_schema": {"type": "array"}})]
        U._refine(tr, lib, verify="strict", rounds=2, draws=2)
        assert lib.list(), "a second, fresh draw should land"
        assert lib.list()[0].meta["grade"] == "executable"


def test_every_skill_carries_a_grade_and_the_three_states_are_distinct():
    """`reference` means the replay ran and failed. `unverified` means none ran. Collapsing them
    would claim we tested something we never looked at — and a missing field breaks the first
    caller that indexes it."""
    import tempfile
    import cuawright.skill_factory.update as U
    from cuawright.skill_factory.library import Library

    GOOD = "import json\njson.dump({'retrieved_data': ['right']}, open('agent_response.json','w'))\n"
    BAD = "import sys\nsys.exit(1)\n"

    def refine(verify, on_fail, code):
        U.llm = lambda s, u, **k: f"```python\n{code}```"
        d = tempfile.mkdtemp()
        lib = Library(d)
        tr = [U.Trace("T", "c", answer=["right"], correct=True,
                      meta={"params": {}, "output_schema": {"type": "array"}})]
        U._refine(tr, lib, verify=verify, rounds=1, draws=1, on_fail=on_fail)
        return lib.list()[0].meta if lib.list() else None

    passed = refine("strict", "reject", GOOD)
    assert passed["grade"] == "executable" and passed["verified"] is True

    failed = refine("strict", "reference", BAD)          # replay ran, skill crashed
    assert failed["grade"] == "reference" and failed["verified"] is False

    untested = refine("off", "reject", GOOD)             # no replay at all
    assert untested["grade"] == "unverified", "never tested is not the same as tested and failed"
    assert untested["verified"] is False


# ---------------------------------------------------------------- replay comparison: _norm

def test_norm_folds_how_an_answer_is_written_not_what_it_says():
    """Real case that cost three solves: the page prints AS26, the agent wrote the label as
    "AS 26", and strict then rejected every skill that read the page correctly — no draw could
    ever have passed. Spacing is not a logic error."""
    from cuawright.skill_factory.update import _norm
    assert _norm(["AS26", "Alaska", "7:00 AM"]) == _norm(["AS 26", "Alaska", "7:00 AM"])
    assert _norm(["AS26", "Alaska", "7:00 AM"]) == _norm(["AS26", "alaska", "7:00AM"])


def test_norm_still_fails_a_different_answer():
    """The folding must not buy leniency: strict is the same *answer*, not the same bytes."""
    from cuawright.skill_factory.update import _norm
    assert _norm(["AS26", "Alaska", "7:00 AM"]) != _norm(["AS27", "Alaska", "7:00 AM"])
    assert _norm(["AS26", "Alaska", "7:00 AM"]) != _norm(["AS26", "Alaska", "8:00 AM"])


def test_norm_still_catches_a_mangled_field():
    """The draw that prompted this returned "\\b 434" for "B6 434" — a regex-escape bug that
    normalization must not launder into a pass."""
    from cuawright.skill_factory.update import _norm
    assert _norm(["B6 434", "JetBlue", "6:00 AM"]) != _norm(["\b 434", "JetBlue", "6:00 AM"])


def test_norm_keeps_folding_type_jitter():
    """The behaviour it had before: a solve answers in strings, a skill in numbers."""
    from cuawright.skill_factory.update import _norm
    assert _norm([5]) == _norm(["5"])


# ---------------------------------------------------------------- material: lookups aren't methods

def _trace(code, answer):
    from cuawright.skill_factory.update import Trace
    return Trace(template="t", code=code, answer=answer, meta={})


def test_a_solve_that_recognises_its_answer_is_not_material():
    """The case that cost a whole build: the script ended in
        RESULT = ["UA 729", "United", "12:10 AM"]
        if "UA 729" in text: return "UA 729"
    Its answer is right, so the input gate passes it. But there is no method in it, and
    distillation — told never to copy instance values — has to invent one, so every draw fails
    replay on that instance."""
    from cuawright.skill_factory.update import _memorized_answer
    code = 'RESULT = ["UA 729", "United", "12:10 AM"]\nif "UA 729" in t: return "UA 729"'
    assert _memorized_answer(_trace(code, ["UA 729", "United", "12:10 AM"]))


def test_a_working_solve_may_mention_its_answer_without_being_a_lookup():
    """Measured on all three shipped trajectories: an airline name is a vocabulary entry, a time
    lands in an assertion. "The answer appears in the code" would drop 3 of 3 good solves — the
    signal is every field at once, verbatim."""
    from cuawright.skill_factory.update import _memorized_answer
    code = 'KNOWN_AIRLINES = ["United", "Alaska", "JetBlue"]\nnum = extract_from_tfs(tfs)'
    assert not _memorized_answer(_trace(code, ["UA 729", "United", "12:10 AM"]))


def test_one_field_is_never_evidence():
    """A single-field answer that appears in the code proves nothing — "United" is a word."""
    from cuawright.skill_factory.update import _memorized_answer
    assert not _memorized_answer(_trace('AIRLINES = ["United"]', ["United"]))


def test_evolve_drops_a_lookup_and_says_so(capsys):
    """A drop nobody sees is a mystery; the changelog and the log both name it."""
    with tempfile.TemporaryDirectory() as d:
        lookup = U.Trace("T1", 'RESULT = ["UA 729", "United"]', verdict="skip", correct=True,
                         answer=["UA 729", "United"])
        log = U.evolve([lookup], Library(d))
    assert log["dropped_lookup"] == 1 and log["added"] == []
    assert "recognise the answer" in capsys.readouterr().out
