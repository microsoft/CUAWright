"""route(): judge -> execute -> fallback, one entry, symmetric. Branches: run-success (no agent),
run-fallback (agent, marked), adapt (agent+hint), skip (agent, no hint), and — when agent_fn is
given — the agent branch actually launches. recommend/run_skill/agent are injected.

The last group is an INTEGRATION check: it wires the REAL run_skill executing a REAL failing
skill file, so the fallback path (run -> observable failure -> agent) is exercised end to end,
offline and deterministically — no mock of the executor, no browser, no LLM."""
import textwrap

from cuawright.skill_factory import route as R


def _rec(**kw):
    base = {"verdict": "skip", "skill_id": None, "reason": "", "source_path": "",
            "how_to_reuse": "", "output_schema": {"type": "array"}, "params": {}}
    base.update(kw)
    return lambda task, library: base


def test_run_success_answers_without_agent():
    out = R.route("t", "lib",
                  recommend_fn=_rec(verdict="run", skill_id="flt", source_path="/s",
                                    params={"origin_code": "SEA"}),
                  run_skill_fn=lambda *a, **k: {"ok": True, "answer": ["x", "y"], "error": ""})
    assert out["action"] == "answered" and out["via"] == "skill"
    assert out["answer"] == ["x", "y"] and out["skill_id"] == "flt"


def test_run_failure_falls_back_to_agent_marked():
    out = R.route("t", "lib",
                  recommend_fn=_rec(verdict="run", skill_id="flt", source_path="/s",
                                    how_to_reuse="RUN it", params={"a": "b"}),
                  run_skill_fn=lambda *a, **k: {"ok": False, "answer": None, "error": "timeout after 240s"})
    assert out["action"] == "agent" and out["fell_back"] is True
    assert "timeout" in out["reason"]
    assert "tried and failed" in out["hint"] and "flt" in out["hint"]


def test_run_wrong_shape_falls_back_even_if_ok():
    # skill returned ok but the answer doesn't match the schema -> still fall back
    out = R.route("t", "lib",
                  recommend_fn=_rec(verdict="run", skill_id="flt", source_path="/s",
                                    output_schema={"type": "array"}, params={"a": "b"}),
                  run_skill_fn=lambda *a, **k: {"ok": True, "answer": "not-a-list", "error": ""})
    assert out["action"] == "agent" and out["fell_back"] is True


def test_adapt_hands_to_agent_with_hint_not_marked_fallback():
    out = R.route("t", "lib",
                  recommend_fn=_rec(verdict="adapt", skill_id="flt", source_path="/s",
                                    how_to_reuse="ADAPT: reuse the core"))
    assert out["action"] == "agent" and out["fell_back"] is False
    assert "ADAPT: reuse the core" in out["hint"] and "flt" in out["hint"]
    assert "tried and failed" not in out["hint"]


def test_skip_hands_to_agent_with_no_hint():
    out = R.route("t", "lib", recommend_fn=_rec(verdict="skip", reason="library has no relevant skill"))
    assert out["action"] == "agent" and out["hint"] == "" and out["skill_id"] is None


# ---- symmetric: with an agent_fn, the agent branch actually launches -------------------------

def test_agent_fn_is_launched_on_adapt_and_receives_the_hint():
    seen = {}
    def agent_fn(task, hint):
        seen["task"], seen["hint"] = task, hint
        return {"answer": "from agent"}
    out = R.route("do it", "lib",
                  recommend_fn=_rec(verdict="adapt", skill_id="flt", source_path="/s",
                                    how_to_reuse="ADAPT: reuse the core"),
                  agent_fn=agent_fn)
    assert out["action"] == "agent" and out["launched"] is True
    assert out["result"] == {"answer": "from agent"}
    assert seen["task"] == "do it" and "flt" in seen["hint"]


def test_run_failure_launches_agent_fn_with_fallback_note():
    got = {}
    def agent_fn(task, hint):
        got["hint"] = hint
        return "ok"
    out = R.route("t", "lib",
                  recommend_fn=_rec(verdict="run", skill_id="flt", source_path="/s",
                                    how_to_reuse="RUN it", params={"a": "b"}),
                  run_skill_fn=lambda *a, **k: {"ok": False, "answer": None, "error": "crash"},
                  agent_fn=agent_fn)
    assert out["launched"] is True and out["fell_back"] is True
    assert "tried and failed" in got["hint"]      # the agent is told the direct run failed


def test_run_success_does_not_launch_agent_fn():
    called = []
    out = R.route("t", "lib",
                  recommend_fn=_rec(verdict="run", skill_id="flt", source_path="/s", params={"a": "b"}),
                  run_skill_fn=lambda *a, **k: {"ok": True, "answer": ["x"], "error": ""},
                  agent_fn=lambda *a, **k: called.append(1))
    assert out["action"] == "answered" and not called      # agent never touched on a clean run


# ---- edge: a 'run' verdict with no source_path must not crash — treat as adapt --------------

def test_run_without_source_path_falls_to_agent_with_skill():
    ran = []
    out = R.route("t", "lib",
                  recommend_fn=_rec(verdict="run", skill_id="flt", source_path="",
                                    how_to_reuse="ADAPT"),
                  run_skill_fn=lambda *a, **k: ran.append(1))
    assert not ran                              # never tried to execute a skill with no path
    assert out["action"] == "agent" and out["skill_id"] == "flt" and "flt" in out["hint"]


# ---- the webwright launcher and agent_fn (subprocess stubbed) -------------------------------

def test_run_webwright_builds_the_command(monkeypatch):
    seen = {}
    def fake_run(cmd, **k):
        seen["cmd"] = cmd
        return type("P", (), {"returncode": 0})()
    monkeypatch.setattr(R.subprocess, "run", fake_run)
    rc = R.run_webwright("PROMPT", start_url="http://x", cfg=["base.yaml", "model.yaml"],
                         outputs="/out", task_id="t7")
    cmd = seen["cmd"]
    assert rc == 0
    assert "PROMPT" in cmd and "http://x" in cmd and "t7" in cmd and "/out" in cmd
    assert cmd.count("-c") == 2 and "base.yaml" in cmd and "model.yaml" in cmd


def test_agent_launcher_prepends_hint_and_answer_instruction(monkeypatch):
    seen = {}
    monkeypatch.setattr(R, "run_webwright", lambda prompt, **k: seen.setdefault("p", prompt) or 0)
    fn = R.agent_launcher(start_url="http://x", cfg=[], outputs="/o", task_id="id")
    fn("the task", "HINT-BLOCK")
    assert seen["p"].startswith("HINT-BLOCK") and "the task" in seen["p"]
    assert "$WORKSPACE_DIR/agent_response.json" in seen["p"]   # the answer instruction is appended


def test_agent_launcher_without_hint_still_launches(monkeypatch):
    seen = {}
    monkeypatch.setattr(R, "run_webwright", lambda prompt, **k: seen.setdefault("p", prompt) or 0)
    R.agent_launcher(start_url="http://x")("bare task", "")
    assert seen["p"].startswith("bare task")


# ---- the CLI: --start-url launches the agent branch; without it, inspect only ---------------

def test_cli_launches_agent_when_start_url_given(monkeypatch, capsys):
    monkeypatch.setattr(R, "route",
                        lambda task, lib, *, agent_fn=None, **k: {"action": "agent", "launched": agent_fn is not None,
                                                                  "fell_back": False, "result": 0, "skill_id": "flt",
                                                                  "reason": "r"})
    R.main(["--task", "t", "--library", "L", "--start-url", "http://x", "-c", "m.yaml"])
    assert "agent solve launched" in capsys.readouterr().out


def test_cli_inspects_without_start_url(monkeypatch, capsys):
    captured = {}
    def fake_route(task, lib, *, agent_fn=None, **k):
        captured["agent_fn"] = agent_fn
        return {"action": "agent", "skill_id": "flt", "reason": "r", "hint": "H", "launched": False}
    monkeypatch.setattr(R, "route", fake_route)
    R.main(["--task", "t", "--library", "L"])
    assert captured["agent_fn"] is None            # no launcher wired without --start-url
    assert "handed to the agent" in capsys.readouterr().out


def test_cli_resolves_agent_config_through_agent_cfg(monkeypatch):
    # route and build share one config resolver: route must pipe -c through agent_cfg (which,
    # given no -c + gateway env, builds the whole config so the user types zero -c).
    seen = {}
    monkeypatch.setattr(R, "agent_cfg", lambda cfg: ["RESOLVED", *cfg])
    monkeypatch.setattr(R, "agent_launcher", lambda **k: seen.update(k) or (lambda t, h: 0))
    monkeypatch.setattr(R, "route", lambda *a, **k: {"action": "agent", "launched": True,
                                                     "fell_back": False, "result": 0})
    R.main(["--task", "t", "--library", "L", "--start-url", "http://x", "-c", "m.yaml"])
    assert seen["cfg"] == ["RESOLVED", "m.yaml"]


def test_cli_shows_the_decision_before_the_outcome(monkeypatch, capsys):
    # route calls on_decision(rec) first, then returns the outcome — the CLI must print in that order
    def fake_route(task, lib, *, agent_fn=None, on_decision=None, **k):
        on_decision({"verdict": "adapt", "skill_id": "flt", "reason": "1-stop differs"})
        return {"action": "agent", "skill_id": "flt", "reason": "1-stop differs", "hint": "H",
                "launched": False}
    monkeypatch.setattr(R, "route", fake_route)
    R.main(["--task", "t", "--library", "L"])
    out = capsys.readouterr().out
    assert "route decided: adapt" in out
    assert out.index("route decided") < out.index("handed to the agent")   # decision first


# ---- INTEGRATION: the REAL run_skill executes a REAL failing skill -> fallback must fire -------
# No mock of the executor here: this proves the fallback actually triggers when a skill fails,
# which is the safety net the whole design leans on. `recommend` is injected only so the test is
# deterministic and offline (the decision isn't what we're testing — the fallback is).

_CRASHING_SKILL = "import sys\nsys.exit('boom: this skill is broken')\n"
_EMPTY_SKILL = (
    "import json, os\nfrom pathlib import Path\n"
    "Path(os.environ.get('WORKSPACE_DIR', '.'), 'agent_response.json')"
    ".write_text(json.dumps({'retrieved_data': None}))\n"      # runs cleanly but yields nothing
)


def _lib_with_skill(tmp_path, src):
    skill = tmp_path / "skill.py"
    skill.write_text(textwrap.dedent(src), encoding="utf-8")
    return str(skill)


def test_real_run_of_a_crashing_skill_falls_back_to_agent(tmp_path):
    src = _lib_with_skill(tmp_path, _CRASHING_SKILL)
    launched = {}
    out = R.route(
        "do it", "lib",
        recommend_fn=lambda t, l: {"verdict": "run", "skill_id": "flt", "source_path": src,
                                   "how_to_reuse": "RUN it", "output_schema": None,
                                   "params": {"x": "1"}},
        # NOTE: real run_skill (not injected) actually executes the crashing file
        agent_fn=lambda task, hint: launched.update(task=task, hint=hint) or "agent-answer")
    assert out["action"] == "agent" and out["launched"] is True and out["fell_back"] is True
    assert launched["task"] == "do it" and "tried and failed" in launched["hint"]
    assert out["result"] == "agent-answer"


def test_real_run_of_an_empty_output_skill_falls_back(tmp_path):
    src = _lib_with_skill(tmp_path, _EMPTY_SKILL)
    launched = []
    out = R.route(
        "do it", "lib",
        recommend_fn=lambda t, l: {"verdict": "run", "skill_id": "flt", "source_path": src,
                                   "how_to_reuse": "RUN it", "output_schema": {"type": "array"},
                                   "params": {}},
        agent_fn=lambda task, hint: launched.append(hint) or 0)
    assert out["action"] == "agent" and out["fell_back"] is True and launched  # agent was reached


def test_real_run_that_succeeds_does_not_fall_back(tmp_path):
    good = ("import json, os\nfrom pathlib import Path\n"
            "Path(os.environ.get('WORKSPACE_DIR', '.'), 'agent_response.json')"
            ".write_text(json.dumps({'retrieved_data': ['UA 100', 'United', '6:00 AM']}))\n")
    src = _lib_with_skill(tmp_path, good)
    called = []
    out = R.route(
        "do it", "lib",
        recommend_fn=lambda t, l: {"verdict": "run", "skill_id": "flt", "source_path": src,
                                   "output_schema": {"type": "array"}, "params": {}},
        agent_fn=lambda *a, **k: called.append(1))
    assert out["action"] == "answered" and out["answer"] == ["UA 100", "United", "6:00 AM"]
    assert not called                              # a clean run never reaches the agent
