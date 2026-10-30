"""Unit test: learn's LLM-free plumbing (schema inference, run collection, ledger skip)."""
import json, tempfile
from pathlib import Path
from cuawright.webwright.skill_factory.learn import infer_schema, collect_runs


def run():
    assert infer_schema([1, 2]) == {"type": "array", "items": {"type": "number"}}
    assert infer_schema(["a"]) == {"type": "array", "items": {"type": "string"}}
    assert infer_schema(7) == {"type": "number"}
    assert infer_schema({"k": 1}) == {"type": "object"}
    assert infer_schema("x") == {"type": "string"}

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        d1 = td / "run_a"; d1.mkdir()
        (d1 / "task.json").write_text(json.dumps(
            {"task": "## Skill library\nblah\n---\nCount commits by Jane Additionally, "
                     "write the final answer into $WORKSPACE_DIR/agent_response.json as {...}.",
             "task_id": "a", "start_url": "http://gitlab.example.com/x"}))
        (d1 / "agent_response.json").write_text(json.dumps({"retrieved_data": [3]}))
        d2 = td / "run_b"; d2.mkdir()   # unfinished: no agent_response
        (d2 / "task.json").write_text(json.dumps({"task": "t", "task_id": "b"}))

        runs = collect_runs(td, {"runs": {}})
        assert len(runs) == 1 and runs[0]["task_id"] == "a", runs
        assert runs[0]["task"] == "Count commits by Jane", "hint AND answer-spec must be stripped"
        assert runs[0]["answer"] == [3]

        # ledger makes it idempotent
        runs2 = collect_runs(td, {"runs": {str(d1.resolve()): {}}})
        assert runs2 == [], "already-learned run must be skipped"
    print("test_learn OK")


def run_status_gate():
    """END-TO-END: a run whose own agent reported failure must be rejected by learn's
    gate (status read from agent_response.json), even when the answer is well-formed.
    All runs rejected -> learn returns before any LLM call, so this stays offline."""
    import contextlib, io, json, tempfile
    from cuawright.webwright.skill_factory.learn import learn
    with tempfile.TemporaryDirectory() as td:
        run = Path(td) / "runs" / "r1_20260711_000000"
        run.mkdir(parents=True)
        (run / "task.json").write_text(json.dumps(
            {"task": "find the thing", "task_id": "r1", "start_url": "https://example.com"}))
        (run / "agent_response.json").write_text(json.dumps(
            {"task_type": "RETRIEVE", "status": "NOT_FOUND_ERROR",
             "retrieved_data": ["well-formed but self-admitted failure"]}))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            learn(str(run.parent), str(Path(td) / "lib"))
        out = buf.getvalue()
        assert "agent itself reported NOT_FOUND_ERROR" in out, out
        assert "0/1 runs admitted" in out, out
        assert not (Path(td) / "lib" / ".learned.json").exists() or \
            "r1" not in (Path(td) / "lib" / ".learned.json").read_text()
    print("test_learn status-gate OK")


def run_regressions():
    """F3: grouping-LLM failure must exit with an actionable one-liner, not a traceback."""
    import cuawright.webwright.skill_factory.learn as L
    orig = L.llm_json
    L.llm_json = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("401 unauthorized"))
    try:
        try:
            L.group_chunk([{"task": "t"}], [])
            raise AssertionError("must raise SystemExit")
        except SystemExit as e:
            msg = str(e)
            assert "OPENAI_ENDPOINT" in msg and "401" in msg, msg
    finally:
        L.llm_json = orig
    print("test_learn regressions OK")


# pytest entry point (CI also runs this file as a script)
def run_reject_ledger():
    """A rejected skill must NOT mark its runs as learned (they get another chance)."""
    import contextlib, io, json, tempfile
    from unittest import mock
    import cuawright.webwright.skill_factory.learn as L
    with tempfile.TemporaryDirectory() as td:
        run = Path(td) / "runs" / "r1_x"
        run.mkdir(parents=True)
        (run / "task.json").write_text(json.dumps(
            {"task": "count things", "task_id": "r1", "start_url": "https://example.com"}))
        (run / "agent_response.json").write_text(json.dumps(
            {"status": "SUCCESS", "retrieved_data": [1]}))
        groups = [{"template": "count {{x}}", "members": [{"i": 0, "params": {"x": "things"}}]}]
        with mock.patch.object(L, "group_chunk", lambda *a: groups), \
             mock.patch.object(L, "evolve", lambda *a, **k: {"added": [], "rejected": ["count_x"]}):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                L.learn(str(run.parent), str(Path(td) / "lib"))
        led = Path(td) / "lib" / ".learned.json"
        assert "kept un-learned" in buf.getvalue()
        assert not led.exists() or "r1_x" not in led.read_text(), "rejected runs must stay un-learned"
    print("test_learn reject-ledger OK")


def test_all():
    run()
    run_regressions()
    run_status_gate()
    run_reject_ledger()


if __name__ == "__main__":
    run()
    run_regressions()
    run_status_gate()
    run_reject_ledger()
