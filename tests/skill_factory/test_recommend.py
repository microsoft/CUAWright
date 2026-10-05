"""The decision: promote() turns decide's 'use' into run vs adapt, and recommend() surfaces it
with grade-honest guidance. All LLM pieces (template_gap, fill_params, retrieve, decide) are
stubbed; these tests pin the branching, not the model."""
import tempfile

import importlib

from cuawright.skill_factory.library import Library, Skill

# the package re-exports the `decide` FUNCTION as cuawright.skill_factory.decide, shadowing the
# submodule; import the module object explicitly so monkeypatching promote's globals works.
DEC = importlib.import_module("cuawright.skill_factory.decide")


def _skill(grade, params=("origin_code", "destination_code", "date"),
           template="earliest flight from {origin_code} to {destination_code} on {date}"):
    return Skill("flt", "code", {"grade": grade, "template": template,
                                 "signature": {"params": list(params)}, "summary": "s"})


# ---- promote: the four branches (promote lives with decide now) ------------------------------

def test_promote_non_executable_is_adapt_without_any_llm(monkeypatch):
    # if promote reached the LLM it would raise here — proving the grade gate short-circuits
    monkeypatch.setattr(DEC, "template_gap", lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM!")))
    monkeypatch.setattr(DEC, "fill_params", lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM!")))
    assert DEC.promote("task", _skill("reference"))["verdict"] == "adapt"
    assert "unverified" in DEC.promote("task", _skill(None))["reason"]


def test_promote_unmet_template_requirement_is_adapt(monkeypatch):
    monkeypatch.setattr(DEC, "template_gap", lambda task, tmpl: "under $300")
    monkeypatch.setattr(DEC, "fill_params", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not fill")))
    out = DEC.promote("cheapest under $300 ...", _skill("executable"))
    assert out["verdict"] == "adapt" and "under $300" in out["reason"]


def test_promote_missing_slot_is_adapt_not_a_guess(monkeypatch):
    monkeypatch.setattr(DEC, "template_gap", lambda task, tmpl: "")
    monkeypatch.setattr(DEC, "fill_params",
                        lambda task, names, examples=None: {"origin_code": "SFO",
                                                            "destination_code": "BOS", "date": None})
    out = DEC.promote("cheapest flight from SFO to BOS", _skill("executable"))
    assert out["verdict"] == "adapt" and "date" in out["reason"]


def test_promote_executable_fits_and_fills_is_run(monkeypatch):
    filled = {"origin_code": "SEA", "destination_code": "JFK", "date": "2026-08-15"}
    monkeypatch.setattr(DEC, "template_gap", lambda task, tmpl: "")
    monkeypatch.setattr(DEC, "fill_params", lambda task, names, examples=None: dict(filled))
    out = DEC.promote("earliest flight from SEA to JFK on 2026-08-15", _skill("executable"))
    assert out["verdict"] == "run" and out["params"] == filled


# ---- recommend: integration, grade-honest guidance ------------------------------------------

def _wire(monkeypatch, lib, decision):
    import cuawright.tools.skill_use as T
    from cuawright.skill_factory.retrieve import Candidate
    from cuawright.skill_factory.decide import Decision
    monkeypatch.setattr(T, "retrieve", lambda task, l: [Candidate(lib.get("flt"), 0.9, "stub")])
    monkeypatch.setattr(T, "decide", lambda task, cands: Decision(*decision))
    return T


def test_recommend_run_carries_params_and_run_guidance(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        lib = Library(d); lib.add(_skill("executable"))
        T = _wire(monkeypatch, lib, ("use", "flt", "fits"))
        monkeypatch.setattr(DEC, "template_gap", lambda *a, **k: "")
        monkeypatch.setattr(DEC, "fill_params",
                            lambda task, names, examples=None: {"origin_code": "SEA",
                                                                "destination_code": "JFK",
                                                                "date": "2026-08-15"})
        r = T.recommend("earliest flight from SEA to JFK on 2026-08-15", d)
        assert r["verdict"] == "run"
        assert r["params"]["destination_code"] == "JFK"
        assert "RUN it directly" in r["how_to_reuse"]


def test_recommend_adapt_on_executable_says_it_runs_but_not_as_is(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        lib = Library(d); lib.add(_skill("executable"))
        T = _wire(monkeypatch, lib, ("adapt", "flt", "last step differs"))
        r = T.recommend("something adjacent", d)
        assert r["verdict"] == "adapt" and "params" not in r
        assert "runs standalone" in r["how_to_reuse"]


def test_recommend_adapt_on_reference_says_read_as_prior(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        lib = Library(d); lib.add(_skill("reference"))
        T = _wire(monkeypatch, lib, ("use", "flt", "fits shape"))  # promoted to adapt (not executable)
        r = T.recommend("task", d)
        assert r["verdict"] == "adapt"
        assert "PRIOR" in r["how_to_reuse"] and "not proven to run" in r["how_to_reuse"]
