"""Slot filling edges: fill_params never invents, coerces blanks to None, and degrades safely on
a malformed model reply. The LLM is stubbed; these pin the plumbing around it."""
import cuawright.skill_factory.fill as F


def test_no_param_names_returns_empty_without_calling_the_model(monkeypatch):
    monkeypatch.setattr(F, "llm_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM!")))
    assert F.fill_params("any task", []) == {}
    assert F.fill_params("any task", None) == {}


def test_values_are_read_through_for_named_params(monkeypatch):
    monkeypatch.setattr(F, "llm_json",
                        lambda s, u: {"params": {"origin_code": "SEA", "date": "2026-08-15"}})
    assert F.fill_params("t", ["origin_code", "date"]) == {"origin_code": "SEA", "date": "2026-08-15"}


def test_unstated_slot_comes_back_none_not_invented(monkeypatch):
    monkeypatch.setattr(F, "llm_json", lambda s, u: {"params": {"origin_code": "SEA"}})
    got = F.fill_params("t", ["origin_code", "date"])
    assert got == {"origin_code": "SEA", "date": None}      # missing key -> None


def test_blank_and_null_strings_coerce_to_none(monkeypatch):
    monkeypatch.setattr(F, "llm_json",
                        lambda s, u: {"params": {"a": "", "b": "null", "c": "None", "d": "x"}})
    assert F.fill_params("t", ["a", "b", "c", "d"]) == {"a": None, "b": None, "c": None, "d": "x"}


def test_malformed_reply_degrades_to_all_none(monkeypatch):
    monkeypatch.setattr(F, "llm_json", lambda s, u: ["not", "a", "dict"])
    assert F.fill_params("t", ["a", "b"]) == {"a": None, "b": None}
    monkeypatch.setattr(F, "llm_json", lambda s, u: {"no_params_key": 1})
    assert F.fill_params("t", ["a"]) == {"a": None}


def test_examples_are_shown_to_the_model_for_form(monkeypatch):
    seen = {}
    def cap(system, user):
        seen["user"] = user
        return {"params": {"a": "1"}}
    monkeypatch.setattr(F, "llm_json", cap)
    F.fill_params("t", ["a"], examples=[{"a": "sample-form"}])
    assert "sample-form" in seen["user"]        # the example value is offered as a form hint
