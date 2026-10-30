"""Unit tests: build (spec -> concrete tasks -> learn) and init (need -> spec skeleton).

Everything here is LLM-free and site-free: the solve subprocess and the one LLM call in init
are stubbed, so what is under test is the logic those two commands actually own — template
substitution, policy precedence, resume detection, and the shape of the drafted spec.
"""
import json
import re
import tempfile
from pathlib import Path

import pytest
import yaml

import cuawright.webwright.skill_factory.build as B
import cuawright.webwright.skill_factory.init as I
from cuawright.webwright.skill_factory.route import agent_cfg   # config resolution shared by build and route


# ---------------------------------------------------------------- build: substitution

def test_fill_substitutes_every_hole():
    got = B._fill("earliest flight from {origin} to {dest} on {date}",
                  {"origin": "SEA", "dest": "JFK", "date": "2026-08-15"})
    assert got == "earliest flight from SEA to JFK on 2026-08-15"


def test_fill_reports_the_missing_value_instead_of_guessing():
    # a hole with no value would otherwise be solved as the literal "{date}"
    with pytest.raises(SystemExit) as e:
        B._fill("flight on {date} from {origin}", {"origin": "SEA"})
    assert "date" in str(e.value)


def test_fill_ignores_extra_columns():
    assert B._fill("hi {a}", {"a": "x", "unused": "y"}) == "hi x"


# ---------------------------------------------------------------- build: policy precedence

def test_policy_precedence_cli_over_spec_over_default():
    assert B._pick("shape", "strict", "strict") == "shape"   # CLI wins
    assert B._pick(None, "shape", "strict") == "shape"       # spec beats the default
    assert B._pick(None, None, "strict") == "strict"         # default when nobody said


# ---------------------------------------------------------------- build: resume

def _run_dir(outputs: Path, name: str, task: str, answer=True):
    d = outputs / name
    d.mkdir(parents=True)
    (d / "task.json").write_text(json.dumps({"task": task}), encoding="utf-8")
    if answer:
        (d / "agent_response.json").write_text('{"retrieved_data": ["x"]}', encoding="utf-8")
    return d


def test_resume_finds_a_prior_run_that_produced_an_answer():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        # the real prompt wraps the task in a skill hint + answer instruction, so the match
        # must be a containment, not equality
        _run_dir(out, "build_00_2026", "## Skill library ... --- cheapest widget on Acme. Also write...")
        assert B._already_solved(out, "cheapest widget on Acme") is not None
        assert B._already_solved(out, "cheapest gadget on Acme") is None


def test_resume_ignores_a_run_that_never_wrote_an_answer():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        _run_dir(out, "build_00_2026", "cheapest widget on Acme", answer=False)
        assert B._already_solved(out, "cheapest widget on Acme") is None


# ---------------------------------------------------------------- build: spec validation + flow

def _spec(tmp: Path, **over) -> Path:
    spec = {"task": "cheapest {product} on Acme",
            "start_url": "https://acme.example",
            "instances": [{"product": "widget"}, {"product": "gadget"}]}
    spec.update(over)
    p = tmp / "skill.yaml"
    p.write_text(yaml.safe_dump(spec), encoding="utf-8")
    return p


@pytest.mark.parametrize("missing", ["task", "start_url", "instances"])
def test_spec_must_have_the_three_things_build_cannot_invent(missing):
    with tempfile.TemporaryDirectory() as d:
        p = _spec(Path(d), **{missing: "" if missing != "instances" else []})
        with pytest.raises(SystemExit):
            B.build(str(p), str(Path(d) / "lib"), [], dry_run=True)


def test_dry_run_neither_solves_nor_learns():
    calls = []
    with tempfile.TemporaryDirectory() as d:
        p = _spec(Path(d))
        B._solve = lambda *a, **k: calls.append("solve")
        B.learn = lambda *a, **k: calls.append("learn")
        assert B.build(str(p), str(Path(d) / "lib"), [], dry_run=True) == 0
        assert calls == []


def test_build_solves_each_instance_then_hands_the_batch_to_learn(monkeypatch):
    seen, learned = [], {}
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        p = _spec(tmp)
        outputs = tmp / "build_outputs"

        def fake_solve(core_task, start_url, library, out, task_id, cfg, log_path=None):
            seen.append(core_task)
            _run_dir(Path(out), f"{task_id}_2026", core_task)
            return 0

        def fake_learn(runs_dir, lib, **kw):
            learned.update({"runs_dir": runs_dir, **kw})

        monkeypatch.setattr(B, "_solve", fake_solve)
        monkeypatch.setattr(B, "learn", fake_learn)
        B.build(str(p), str(tmp / "lib"), [], assume_yes=True, verify="shape", verify_rounds=3)

        assert seen == ["cheapest widget on Acme", "cheapest gadget on Acme"]
        assert learned["runs_dir"] == str(outputs)
        # the flags the user chose must reach learn, not be silently dropped
        assert learned["verify"] == "shape" and learned["rounds"] == 3


def test_an_already_solved_instance_is_not_solved_again(monkeypatch):
    """Solving is the expensive half; a re-run must not re-spend it."""
    seen = []
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        p = _spec(tmp)
        outputs = tmp / "build_outputs"
        _run_dir(outputs, "build_00_2026", "cheapest widget on Acme")   # widget already done

        monkeypatch.setattr(B, "_solve", lambda ct, *a, **k: (seen.append(ct), 0)[1])
        monkeypatch.setattr(B, "learn", lambda *a, **k: None)
        B.build(str(p), str(tmp / "lib"), [], assume_yes=True)

        assert seen == ["cheapest gadget on Acme"], "the solved instance should have been skipped"


def test_a_solve_that_wrote_its_answer_counts_even_if_it_exited_non_zero(monkeypatch, capsys):
    """learn reads the artifact, so build must not call it a failure."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        p = _spec(tmp, instances=[{"product": "widget"}])

        def killed_but_wrote(core_task, start_url, library, out, task_id, cfg, log_path=None):
            _run_dir(Path(out), f"{task_id}_2026", core_task)
            return -15   # SIGTERM

        monkeypatch.setattr(B, "_solve", killed_but_wrote)
        monkeypatch.setattr(B, "learn", lambda *a, **k: None)
        B.build(str(p), str(tmp / "lib"), [], assume_yes=True)
        assert "solved 1/1" in capsys.readouterr().out


# ---------------------------------------------------------------- init: the drafted spec

def _fake_llm(payload):
    return lambda system, user, **kw: payload


def test_init_drafts_a_spec_whose_holes_match_its_instance_columns(monkeypatch):
    monkeypatch.setattr(I, "llm_json", _fake_llm({
        "task": "cheapest {product} on Acme", "params": ["product"],
        "start_url": "https://acme.example", "drifts": True}))
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "skill.yaml"
        I.init("cheapest stuff on Acme", str(out))
        spec = yaml.safe_load(out.read_text(encoding="utf-8"))   # must be valid YAML
        assert set(spec["instances"][0]) == {"product"}, "columns must match the template's holes"
        assert spec["start_url"] == "https://acme.example"


def test_init_drafts_valid_yaml_for_a_multi_param_task(monkeypatch):
    """A one-column row needs no separator, so single-param specs cannot catch a broken
    flow mapping. Most real tasks have several params — that is where it bites."""
    monkeypatch.setattr(I, "llm_json", _fake_llm({
        "task": "earliest flight from {origin} to {dest} on {date}",
        "params": ["origin", "dest", "date"],
        "start_url": "https://flights.example", "drifts": False}))
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "skill.yaml"
        I.init("earliest flight between two cities", str(out))
        spec = yaml.safe_load(out.read_text(encoding="utf-8"))   # would raise on a bad flow map
        assert set(spec["instances"][0]) == {"origin", "dest", "date"}
        # and the drafted spec must be something build can actually consume
        B._fill(spec["task"], {"origin": "SEA", "dest": "JFK", "date": "2026-08-15"})


def test_init_falls_back_to_blank_rows_when_nothing_is_proposed(monkeypatch):
    """When the model proposes no instances (e.g. an account-scoped task), init leaves ____
    rows for the user rather than inventing plausible-looking wrong values."""
    monkeypatch.setattr(I, "llm_json", _fake_llm({
        "task": "cheapest {product} on Acme", "params": ["product"],
        "start_url": "https://acme.example", "drifts": True, "instances": []}))
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "skill.yaml"
        I.init("cheapest stuff on Acme", str(out), rows=3)
        text = out.read_text(encoding="utf-8")
        spec = yaml.safe_load(text)
        assert len(spec["instances"]) == 3
        assert all(i["product"] == "____" for i in spec["instances"])
        assert "PROPOSED" not in text            # no review banner when nothing was proposed


def test_init_proposes_values_and_marks_them_for_review(monkeypatch):
    """The model proposes real, varied values; init writes them AND flags them as guesses so
    the user reviews before paying for a build."""
    monkeypatch.setattr(I, "llm_json", _fake_llm({
        "task": "earliest flight from {origin} to {dest} on {date}",
        "params": ["origin", "dest", "date"], "start_url": "https://flights.example",
        "drifts": False,
        "instances": [{"origin": "SEA", "dest": "JFK", "date": "2026-08-15"},
                      {"origin": "LAX", "dest": "ORD", "date": "2026-09-01"}]}))
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "skill.yaml"
        I.init("earliest flight", str(out), rows=2)
        text = out.read_text(encoding="utf-8")
        spec = yaml.safe_load(text)
        assert spec["instances"][0] == {"origin": "SEA", "dest": "JFK", "date": "2026-08-15"}
        assert "PROPOSED" in text and "REVIEW" in text   # marked as guesses to check
        # a proposed row must be something build can actually consume
        B._fill(spec["task"], spec["instances"][1])


def test_init_rows_sets_the_count_and_pads_short_proposals(monkeypatch):
    """--rows is how many instances to propose; if the model returns fewer, the rest are ____."""
    monkeypatch.setattr(I, "llm_json", _fake_llm({
        "task": "cheapest {product} on Acme", "params": ["product"],
        "start_url": "https://acme.example", "drifts": True,
        "instances": [{"product": "widget"}, {"product": "gadget"}]}))
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "skill.yaml"
        I.init("cheapest stuff", str(out), rows=4)
        spec = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert len(spec["instances"]) == 4                       # rows honoured
        assert [i["product"] for i in spec["instances"]] == ["widget", "gadget", "____", "____"]


def test_init_ignores_malformed_proposed_instances(monkeypatch):
    """A non-dict instance, or one naming no known param, is dropped rather than written."""
    monkeypatch.setattr(I, "llm_json", _fake_llm({
        "task": "cheapest {product} on Acme", "params": ["product"],
        "start_url": "https://acme.example", "drifts": True,
        "instances": ["not-a-dict", {"unknown": "x"}, {"product": "widget"}]}))
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "skill.yaml"
        I.init("cheapest stuff", str(out), rows=3)
        spec = yaml.safe_load(out.read_text(encoding="utf-8"))
        products = [i["product"] for i in spec["instances"]]
        assert products == ["widget", "____", "____"]            # only the well-formed one kept


@pytest.mark.parametrize("drifts,expected", [(True, "shape"), (False, "strict")])
def test_init_picks_the_verify_mode_from_whether_the_answer_drifts(monkeypatch, drifts, expected):
    """strict on a drifting answer rejects a working skill — the default must follow the task."""
    monkeypatch.setattr(I, "llm_json", _fake_llm({
        "task": "x {p}", "params": ["p"], "start_url": "https://a.example", "drifts": drifts}))
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "skill.yaml"
        I.init("something", str(out))
        assert yaml.safe_load(out.read_text(encoding="utf-8"))["build"]["verify"] == expected


def test_init_refuses_a_need_with_nothing_varying(monkeypatch):
    """No holes means one task, not a task type — there is no reusable skill in it."""
    monkeypatch.setattr(I, "llm_json", _fake_llm({
        "task": "today's top headline on Example News", "params": [],
        "start_url": "https://news.example", "drifts": True}))
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "skill.yaml"
        with pytest.raises(SystemExit) as e:
            I.init("today's headline", str(out))
        assert "varies" in str(e.value)
        assert not out.exists()


def test_init_will_not_clobber_an_existing_spec(monkeypatch):
    monkeypatch.setattr(I, "llm_json", _fake_llm({
        "task": "x {p}", "params": ["p"], "start_url": "https://a.example"}))
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "skill.yaml"
        out.write_text("# my filled-in values", encoding="utf-8")
        with pytest.raises(SystemExit):
            I.init("something", str(out))
        assert out.read_text(encoding="utf-8") == "# my filled-in values"


def test_the_readme_spec_shows_every_key_init_writes():
    """The README's skill.yaml is the only spec a reader ever sees, so a key missing from it is a
    knob they never learn they have — they'd think it was CLI-only. This drifted twice, silently:
    once when --draws was added, once when --verify-rounds was. Nothing pinned the docs to the
    code, so pin them."""
    readme = Path(__file__).resolve().parents[2] / "extensions" / "skill-factory" / "src" / "cuawright" / "webwright" / "skill_factory" / "README.md"
    text = readme.read_text(encoding="utf-8")
    start = text.index("build:", text.index("# skill.yaml"))
    shown = set(yaml.safe_load(text[start:text.index("```", start)])["build"])
    written = set(yaml.safe_load(I._yaml_skeleton(
        "x {p}", ["p"], "https://a.example", 1, drifts=True, reason="r"))["build"])
    assert shown == written, f"README shows {sorted(shown)}, init writes {sorted(written)}"


def test_the_spec_init_writes_is_exactly_what_build_reads(monkeypatch):
    """A key init writes that build ignores is a lie; a key build reads that init omits is a knob
    the user never learns they have. Adding --draws broke the second half — pin both directions."""
    import inspect
    monkeypatch.setattr(I, "llm_json", _fake_llm({
        "task": "x {p}", "params": ["p"], "start_url": "https://a.example", "drifts": False}))
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "skill.yaml"
        I.init("something", str(out))
        written = set(yaml.safe_load(out.read_text(encoding="utf-8"))["build"])

    read = set(re.findall(r'policy\.get\("([a-z_]+)"\)', inspect.getsource(B)))
    assert written == read, f"init writes {sorted(written)}, build reads {sorted(read)}"


# ---------------------------------------------------------------- build: the agent's backend

def _gw_spec(tmp: Path) -> Path:
    p = tmp / "skill.yaml"
    p.write_text(yaml.safe_dump({"task": "cheapest {product} on Acme",
                                 "start_url": "https://acme.example",
                                 "instances": [{"product": "widget"}]}), encoding="utf-8")
    return p


def test_a_named_gateway_reaches_the_agent_without_being_asked(monkeypatch):
    """The trap this closes: the agent's model is a yaml and never reads OPENAI_*, so exporting a
    gateway used to send the module there and every solve to api.openai.com."""
    monkeypatch.setenv("OPENAI_ENDPOINT", "https://gw.example/api/responses")
    monkeypatch.setenv("OPENAI_MODEL", "some-model")
    got = agent_cfg([])
    assert "model.openai_endpoint=https://gw.example/api/responses" in got
    assert "model.model_name=some-model" in got


def test_the_cli_defaults_come_along_because_c_replaces_them(monkeypatch):
    """-c is `config_spec or DEFAULT_CONFIGS` (cli.py), not an addition — overrides alone would
    drop base.yaml. And they're imported, not copied, so they can't drift."""
    from cuawright.webwright.run.cli import DEFAULT_CONFIGS
    monkeypatch.setenv("OPENAI_ENDPOINT", "https://gw.example/api/responses")
    got = agent_cfg([])
    assert got[:len(DEFAULT_CONFIGS)] == list(DEFAULT_CONFIGS)


def test_an_explicit_config_with_base_is_left_alone(monkeypatch):
    """You named a base; the env doesn't get a vote and base isn't doubled."""
    monkeypatch.setenv("OPENAI_ENDPOINT", "https://gw.example/api/responses")
    assert agent_cfg(["base.yaml", "mine.yaml"]) == ["base.yaml", "mine.yaml"]


def test_bare_model_config_gets_base_prepended():
    """-c REPLACES defaults, so `-c model.yaml` alone drops base.yaml and the agent has no
    system/instance template. Re-add base so the common `-c model.yaml` just works."""
    assert agent_cfg(["model_gateway.yaml"]) == ["base.yaml", "model_gateway.yaml"]
    assert agent_cfg(["m.yaml", "model.max_output_tokens=16000"]) == \
        ["base.yaml", "m.yaml", "model.max_output_tokens=16000"]


def test_the_env_path_carries_a_usable_output_budget(monkeypatch):
    """The env path stands in for a model yaml, which set max_output_tokens: 16000. base.yaml's
    4000 truncates the agent mid-script when it reuses a big skill, and the run loops re-emitting
    it. Forwarding endpoint/model but not the budget — the bug this pins — quietly quartered it."""
    monkeypatch.delenv("SKILL_AGENT_MAX_TOKENS", raising=False)
    monkeypatch.setenv("OPENAI_ENDPOINT", "https://gw.example/api/responses")
    assert "model.max_output_tokens=16000" in agent_cfg([])


def test_the_output_budget_is_overridable(monkeypatch):
    monkeypatch.setenv("OPENAI_ENDPOINT", "https://gw.example/api/responses")
    monkeypatch.setenv("SKILL_AGENT_MAX_TOKENS", "32000")
    assert "model.max_output_tokens=32000" in agent_cfg([])


def test_no_gateway_means_no_opinion(monkeypatch):
    """Nothing named, nothing invented: the CLI's own defaults still apply."""
    monkeypatch.delenv("OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert agent_cfg([]) == []


def test_the_gateway_actually_reaches_the_solve(monkeypatch):
    """The unit above is only worth something if build hands it to the subprocess."""
    seen = {}
    monkeypatch.setenv("OPENAI_ENDPOINT", "https://gw.example/api/responses")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)

        def fake_solve(core_task, start_url, library, out, task_id, cfg, log_path=None):
            seen["cfg"] = cfg
            _run_dir(Path(out), f"{task_id}_2026", core_task)
            return 0

        monkeypatch.setattr(B, "_solve", fake_solve)
        monkeypatch.setattr(B, "learn", lambda *a, **k: None)
        B.build(str(_gw_spec(tmp)), str(tmp / "lib"), [], assume_yes=True)
    assert "model.openai_endpoint=https://gw.example/api/responses" in seen["cfg"]


def test_dry_run_shows_which_backend_the_solves_would_use(monkeypatch, capsys):
    """--dry-run is the free look before spending agent time; the backend is part of the plan."""
    monkeypatch.setenv("OPENAI_ENDPOINT", "https://gw.example/api/responses")
    with tempfile.TemporaryDirectory() as d:
        B.build(str(_gw_spec(Path(d))), str(Path(d) / "lib"), [], dry_run=True)
    assert "gw.example" in capsys.readouterr().out
