"""Unit tests: how the module's model is chosen from the environment.

No model is ever built for real here — get_model is stubbed — so what is under test is the
precedence llm.py owns: configure_llm beats env, SKILL_MODEL_* beats OPENAI_*, and an unnamed
model says so instead of silently distilling on the class's built-in default.
"""
import pytest

import cuawright.webwright.skill_factory.llm as L


class _FakeCfg:
    def __init__(self, name):
        self.model_name = name


class _FakeModel:
    def __init__(self, cfg):
        self.cfg = cfg
        self.config = _FakeCfg(cfg.get("model_name", "gpt-4o"))   # the class's own fallback


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for v in ("SKILL_MODEL_NAME", "SKILL_MODEL_ENDPOINT", "SKILL_MODEL_CLASS",
              "SKILL_MODEL_TIMEOUT", "OPENAI_MODEL", "OPENAI_ENDPOINT"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(L, "_DEFAULT_MODEL", None)
    monkeypatch.setattr(L, "_WARNED", False)
    monkeypatch.setattr(L, "get_model", _FakeModel)


def test_skill_model_name_beats_openai_model(monkeypatch):
    """Two names, one field: the module-specific one wins so you can point distillation
    somewhere other than whatever else on the machine reads OPENAI_*."""
    monkeypatch.setenv("OPENAI_MODEL", "from-openai")
    monkeypatch.setenv("SKILL_MODEL_NAME", "from-skill")
    assert L._model().cfg["model_name"] == "from-skill"


def test_openai_model_is_used_when_skill_model_name_is_absent(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "from-openai")
    assert L._model().cfg["model_name"] == "from-openai"


def test_skill_model_endpoint_beats_openai_endpoint(monkeypatch):
    monkeypatch.setenv("OPENAI_ENDPOINT", "https://a.example/v1/responses")
    monkeypatch.setenv("SKILL_MODEL_ENDPOINT", "https://b.example/v1/responses")
    assert L._model().cfg["openai_endpoint"] == "https://b.example/v1/responses"


def test_no_endpoint_set_leaves_the_class_default_alone(monkeypatch):
    """An empty openai_endpoint would override the class default with nothing."""
    assert "openai_endpoint" not in L._model().cfg


def test_configure_llm_wins_over_every_var(monkeypatch):
    """In-process, a running agent hands us its own model; env must not second-guess it."""
    monkeypatch.setenv("SKILL_MODEL_NAME", "from-env")
    sentinel = object()
    monkeypatch.setattr(L, "_DEFAULT_MODEL", sentinel)
    assert L._model() is sentinel


def test_an_unnamed_model_warns_and_names_the_fallback(monkeypatch, capsys):
    """Every skill in the library is written by this model; falling back to the class's old
    default is a decision, not something to discover later from a bad skill."""
    L._model()
    err = capsys.readouterr().err
    assert "gpt-4o" in err and "SKILL_MODEL_NAME" in err


def test_the_warning_goes_to_stderr_because_skill_use_prints_json_on_stdout(monkeypatch, capsys):
    L._model()
    assert capsys.readouterr().out == ""


def test_naming_a_model_says_nothing(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.4")
    L._model()
    assert capsys.readouterr().err == ""


def test_the_warning_fires_once_not_per_call(monkeypatch, capsys):
    """learn makes a model call per chunk per draw; one line, not fifty."""
    for _ in range(3):
        L._model()
    assert capsys.readouterr().err.count("no model named") == 1
