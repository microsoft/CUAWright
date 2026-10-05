"""Direct-run executor: run_skill executes a skill on params and reports observable success or
failure. A fake skill stands in for the browser-driving body so the test is fast and offline."""
from pathlib import Path

from cuawright.skill_factory.execute import run_skill

# Reads taskspec.json (as replay/the shim feed it) and writes agent_response.json — the contract
# every real skill honours. Behaviour is switched by the params so one file covers every branch.
FAKE = r'''
import sys, json, os, time
from pathlib import Path
ts = json.loads(Path(sys.argv[1]).read_text())
p = ts.get("params", {})
ws = Path(os.environ.get("WORKSPACE_DIR", "."))
mode = p.get("mode")
if mode == "crash":
    raise SystemExit("boom")
if mode == "timeout":
    time.sleep(30)
if mode == "empty":
    (ws / "agent_response.json").write_text(json.dumps({"retrieved_data": None}))
    sys.exit(0)
if mode == "nofile":
    sys.exit(0)
(ws / "agent_response.json").write_text(json.dumps({"retrieved_data": ["UA 729", "United", "12:10 AM"]}))
'''


def _skill(tmp_path) -> str:
    f = tmp_path / "skill.py"
    f.write_text(FAKE, encoding="utf-8")
    return str(f)


def test_run_skill_success_returns_answer(tmp_path):
    r = run_skill(_skill(tmp_path), {"mode": "ok"})
    assert r["ok"] is True and r["answer"] == ["UA 729", "United", "12:10 AM"]


def test_run_skill_accepts_a_directory_path(tmp_path):
    _skill(tmp_path)                       # writes tmp_path/skill.py
    r = run_skill(str(tmp_path), {"mode": "ok"})   # pass the DIR, executor finds skill.py
    assert r["ok"] is True


def test_run_skill_crash_is_observable_failure(tmp_path):
    r = run_skill(_skill(tmp_path), {"mode": "crash"})
    assert r["ok"] is False and r["answer"] is None and "exit" in r["error"]


def test_run_skill_empty_answer_is_failure(tmp_path):
    r = run_skill(_skill(tmp_path), {"mode": "empty"})
    assert r["ok"] is False and "empty" in r["error"]


def test_run_skill_no_output_file_is_failure(tmp_path):
    r = run_skill(_skill(tmp_path), {"mode": "nofile"})
    assert r["ok"] is False and "no agent_response.json" in r["error"]


def test_run_skill_missing_skill_is_failure(tmp_path):
    r = run_skill(str(tmp_path / "nope.py"), {"mode": "ok"})
    assert r["ok"] is False and "not found" in r["error"]


def test_run_skill_timeout_is_failure(tmp_path):
    r = run_skill(_skill(tmp_path), {"mode": "timeout"}, timeout=1)
    assert r["ok"] is False and "timeout" in r["error"]
