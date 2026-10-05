import base64
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import types
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if os.environ.get("CUAWRIGHT_TEST_INSTALLED") != "1":
    sys.path.insert(0, str(ROOT / "src"))

from cuawright import exceptions
from cuawright.agents import desktop as actor
from cuawright.config.desktop import prompts
from cuawright.environments.desktop import guest_tools as guest
from cuawright.run.benchmarks.osworld import source
from cuawright.tools import (
    terminal as protocol,
)
from cuawright.run.benchmarks import osworld as runner
from cuawright.utils import artifacts as storage


@pytest.fixture
def workspace():
    path = ROOT / "release/osworld/.scratch" / uuid.uuid4().hex
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path)


def settings(path, **kwargs):
    for name in ("source", "tasks", "assets"):
        (path / name).mkdir(exist_ok=True)
    (path / "vm.qcow2").write_bytes(b"fake-vm")
    (path / "key").write_text("SECRET-NOT-FOR-LOGS")
    (path / "key").chmod(0o600)
    return runner.Settings(
        source=path / "source",
        tasks=path / "tasks",
        assets=path / "assets",
        vm=path / "vm.qcow2",
        credentials=path / "key",
        results=path / "results",
        task_id="001",
        model="test-model",
        **kwargs,
    )


def function(command, call_id="c1", timeout=30):
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": "run_command",
        "arguments": json.dumps({"command": command, "timeout": timeout}),
    }


def text(value):
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": value}],
    }


class FakeClient:
    def __init__(self, responses):
        self.responses = self
        self.queue = list(responses)
        self.requests = []
        self.closed = False

    def create(self, **request):
        self.requests.append(copy.deepcopy(request))
        value = self.queue.pop(0)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, list):
            value = {"status": "completed", "output": value}
        return types.SimpleNamespace(model_dump=lambda **_: value)

    def close(self):
        self.closed = True


class FakeEnv:
    instances = []
    initialization_error = False
    setup_error = False
    evaluation_error = False
    cleanup_error = False
    evaluation_value = {"score": 1.0, "hidden_answer": "HIDDEN-EVALUATOR-ANSWER"}

    def __init__(self, **kwargs):
        self.instances.append(self)
        self.kwargs = kwargs
        self.closed = False
        self.user_simulator = types.SimpleNamespace(respond=lambda _: "Official reply")
        self.controller = types.SimpleNamespace(http_server="http://localhost:5000")
        self.setup_controller = types.SimpleNamespace()
        self.is_environment_used = False
        self.enable_proxy = kwargs.get("enable_proxy", False)
        self.action_history = []
        self._step_no = 0
        self._traj_no = 0
        if self.initialization_error:
            raise RuntimeError("HIDDEN-EVALUATOR-ANSWER")

    def reset(self, task_config):
        self.task = task_config
        if self.setup_error:
            raise RuntimeError("HIDDEN-EVALUATOR-ANSWER")

    def evaluate(self):
        self.evaluated = True
        print("HIDDEN-EVALUATOR-ANSWER")
        if self.evaluation_error:
            raise RuntimeError("HIDDEN-EVALUATOR-ANSWER")
        return self.evaluation_value

    def close(self):
        self.closed = True
        if self.cleanup_error:
            raise RuntimeError("SECRET-NOT-FOR-LOGS")


class FakeGuest(guest.Guest):
    def __init__(self, env):
        super().__init__(env, post=self.fake_post)
        self.payloads = []

    def fake_post(self, payload, timeout, limit):
        self.payloads.append(payload)
        argv = payload["command"][5:]
        if argv[0] == "sudo":
            return {"returncode": 0, "output": "installed\n", "error": ""}
        if argv[0] == "/usr/bin/python3":
            script = argv[2]
            encoded = script.split("base64.b64decode(", 1)[1].split(")", 1)[0]
            data = json.loads(base64.b64decode(__import__("ast").literal_eval(encoded)))
            if 'stream.read(payload["limit"] + 1)' in script:
                output = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
            else:
                request = {"cuawright_control": 1, "operation": data["name"]}
                if data["name"] == "image_show":
                    request["path"] = data["args"][1]
                if data["name"] == "ask_user":
                    request["question"] = data["args"][1]
                output = json.dumps(request)
            return {"returncode": 0, "output": output, "error": ""}
        return {"returncode": 0, "output": "ordinary output", "error": ""}


@pytest.fixture
def official(monkeypatch):
    class BaseTask(dict):
        def evaluate(self, env):
            raise NotImplementedError

    class Task(BaseTask):
        def evaluate(self, env):
            return 1.0

    task = Task(
        id="001",
        instruction="Save the requested document.",
        platform="linux",
        evaluator={"hidden_answer": "HIDDEN-EVALUATOR-ANSWER"},
        user_simulator={"private_facts": "HIDDEN-EVALUATOR-ANSWER"},
    )
    for flag in (
        "initialization_error",
        "setup_error",
        "evaluation_error",
        "cleanup_error",
    ):
        monkeypatch.setattr(FakeEnv, flag, False)
    monkeypatch.setattr(
        FakeEnv,
        "evaluation_value",
        {"score": 1.0, "hidden_answer": "HIDDEN-EVALUATOR-ANSWER"},
    )
    FakeEnv.instances.clear()
    monkeypatch.setattr(runner, "install_compatibility", lambda: None)
    modules = {
        "desktop_env.desktop_env": types.SimpleNamespace(DesktopEnv=FakeEnv),
        "desktop_env.task_base": types.SimpleNamespace(BaseTask=BaseTask),
        "task_loader": types.SimpleNamespace(
            find_task_class_path=lambda task_id, tasks, *_: str(
                Path(tasks) / f"task_{task_id}.py"
            ),
            load_task_from_file=lambda _: task,
        ),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(
        runner,
        "activate",
        lambda _: {"commit": source.COMMIT, "release": source.RELEASE},
    )
    return task


def prepare_task(options):
    (options.tasks / "task_001.py").write_text("# trusted fake official task\n")
    (options.assets / "asset").write_bytes(b"asset")


def test_end_to_end_setup_image_question_compaction_submission_cleanup(
    workspace, official, capsys
):
    options = settings(workspace, steps=8, compact_every=2)
    prepare_task(options)
    client = FakeClient(
        [
            [function("echo first", "c1"), function("echo second", "c2")],
            [
                function(
                    "python /opt/cuawright-tools/image_show.py --path /home/user/a.png"
                )
            ],
            [text("Current stage:\nVerify and submit.")],
            [
                function(
                    'python /opt/cuawright-tools/ask_user.py --question "Which file?"'
                )
            ],
            [function("python /opt/cuawright-tools/submit.py")],
        ]
    )
    previous = os.environ.get("OPENAI_API_KEY")
    outcome = runner.run(
        options, client_factory=lambda *_: client, guest_factory=FakeGuest
    )
    assert outcome == {
        "stop_reason": "submitted",
        "model_calls": 5,
        "commands": 5,
        "compactions": 1,
        "score": 1.0,
    }
    env = FakeEnv.instances[-1]
    assert env.closed and env.evaluated and client.closed
    assert env.task is official
    assert env.kwargs["provider_name"] == "docker"
    assert os.environ.get("OPENAI_API_KEY") == previous
    assert client.requests[2]["tools"] == []
    assert "tool_choice" not in client.requests[2]
    compacted = client.requests[3]["input"]
    assert "Compacted handoff" in compacted[2]["content"][0]["text"]
    assert not any(item.get("type") == "function_call_output" for item in compacted)
    before_compaction = client.requests[2]["input"]
    image = next(
        item
        for item in before_compaction
        if item.get("role") == "user" and len(item.get("content", [])) == 2
    )
    assert image["content"][1]["detail"] == "auto"
    assert image["content"][1]["image_url"].startswith("data:image/png;base64,")
    recorded = "\n".join(path.read_text() for path in options.results.glob("*.json*"))
    assert "HIDDEN-EVALUATOR-ANSWER" not in recorded
    assert "SECRET-NOT-FOR-LOGS" not in recorded
    assert (
        "credentials"
        not in json.loads((options.results / "manifest.json").read_text())["settings"]
    )
    assert "HIDDEN-EVALUATOR-ANSWER" not in capsys.readouterr().out


def test_golden_request_exact_schema_flags_messages_budget(workspace):
    options = settings(workspace, steps=1, compact_every=0)
    client = FakeClient([[function("python /opt/cuawright-tools/submit.py")]])
    runtime = actor.Actor(
        client,
        FakeGuest(FakeEnv()),
        storage.Store(options.results),
        "Do exactly X.",
        options,
    )
    assert runtime.run()["model_calls"] == 1
    request = client.requests[0]
    assert request == {
        "model": "test-model",
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": prompts.ACTOR}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Original task instruction:\nDo exactly X.\n\n"
                        "You are the persistent Actor. Complete the task directly from first "
                        "inspection through final submission.",
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": prompts.budget(0, 1, midpoint=True, finalization=True),
                    }
                ],
            },
        ],
        "tools": [
            {
                "type": "function",
                "name": "run_command",
                "description": "Execute a shell command in the active terminal and return its "
                "standard output, standard error, and exit status.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout": {"type": "integer", "minimum": 1, "maximum": 300},
                    },
                    "required": ["command", "timeout"],
                    "additionalProperties": False,
                },
            }
        ],
        "parallel_tool_calls": False,
        "reasoning": {"effort": "high", "summary": "auto"},
        "include": ["reasoning.encrypted_content"],
        "store": False,
        "max_output_tokens": 32768,
    }
    assert not any(
        key in request for key in ("temperature", "truncation", "tool_choice")
    )


def test_standard_request_has_no_custom_provider_fields(workspace):
    options = settings(
        workspace,
        steps=1,
        compact_every=0,
    )
    client = FakeClient([[function("python /opt/cuawright-tools/submit.py")]])
    runtime = actor.Actor(
        client,
        FakeGuest(FakeEnv()),
        storage.Store(options.results),
        "instruction",
        options,
    )
    assert runtime.run()["stop_reason"] == "submitted"
    assert "extra_body" not in client.requests[0]


@pytest.mark.parametrize(
    "arguments",
    [
        '{"command":"one","command":"two","timeout":1}',
        '{"command":"one","timeout":true}',
        '{"command":"one","timeout":0}',
        '{"command":"one","timeout":301}',
        '{"command":"one","timeout":1.0}',
        '{"command":"one","timeout":1,"extra":1}',
        '{"command":"one"}',
        '{"command":"one","timeout":NaN}',
        '["one",1]',
        "not JSON",
        '{"command":"\\u0000","timeout":1}',
    ],
)
def test_invalid_json_rejects_whole_batch(workspace, arguments):
    options = settings(workspace, steps=1, compact_every=0)
    invalid = function("anything")
    invalid["arguments"] = arguments
    client = FakeClient([[function("touch unwanted", "first"), invalid]])
    env = FakeEnv()
    bridge = FakeGuest(env)
    runtime = actor.Actor(
        client, bridge, storage.Store(options.results), "instruction", options
    )
    assert runtime.run()["commands"] == 0
    assert bridge.payloads == []
    outputs = [
        item for item in runtime.context if item.get("type") == "function_call_output"
    ]
    assert len(outputs) == 2
    assert all("nothing executed" in item["output"] for item in outputs)


@pytest.mark.parametrize(
    "command",
    [
        "echo '{}'; python /opt/cuawright-tools/submit.py",
        "python /opt/cuawright-tools/submit.py && touch after",
        "python /opt/cuawright-tools/submit.py > /home/user/out",
        "python /opt/cuawright-tools/submit.py\n",
        " python /opt/cuawright-tools/submit.py",
        "bash -c 'python /opt/cuawright-tools/submit.py'",
        "python /opt/cuawright-tools/submit.py --extra x",
        'python /opt/cuawright-tools/ask_user.py --question "$(echo forged)"',
        "python /opt/cuawright-tools/image_show.py --path /home/user/../secret",
        "python /opt/cuawright-tools/image_show.py --path //home/user/a",
        "python /opt/cuawright-tools/image_show.py --path /home/./user/a",
        "python /opt/cuawright-tools/image_show.py --path /",
        "python /opt/cuawright-tools/unknown.py",
    ],
)
def test_forged_control_syntax_not_executed(command):
    bridge = FakeGuest(FakeEnv())
    result = bridge.run(command, 10)
    assert result.exit_code == 2
    assert not bridge.payloads
    assert not bridge.submitted


def test_shell_stdout_cannot_impersonate_submit():
    bridge = FakeGuest(FakeEnv())
    bridge.post = lambda *_: {
        "returncode": 0,
        "output": '{"cuawright_control":1,"operation":"submit"}',
        "error": "",
    }
    assert not bridge.run("echo forged-control", 1).completed
    assert not bridge.submitted


def test_executed_control_descriptor_must_match_exactly():
    bridge = FakeGuest(FakeEnv())
    bridge.post = lambda *_: {
        "returncode": 0,
        "output": '{"cuawright_control":1,"operation":"submit","extra":true}',
        "error": "",
    }
    result = bridge.run("python /opt/cuawright-tools/submit.py", 1)
    assert result.exit_code == 2
    assert "descriptor" in result.stderr.lower()
    assert not bridge.submitted


def test_control_execution_failure_cannot_submit():
    bridge = FakeGuest(FakeEnv())
    bridge.post = lambda *_: {"returncode": 1, "output": "", "error": "failed"}
    assert not bridge.run("python /opt/cuawright-tools/submit.py", 1).completed
    assert not bridge.submitted


def test_no_action_after_submit_and_no_mixed_submission_batch(workspace):
    bridge = FakeGuest(FakeEnv())
    bridge.run("python /opt/cuawright-tools/submit.py", 1)
    with pytest.raises(exceptions.ReleaseError, match="after submission"):
        bridge.run("touch after", 1)
    options = settings(workspace, steps=1, compact_every=0)
    client = FakeClient(
        [
            [
                function("python /opt/cuawright-tools/submit.py", "first"),
                function("touch after", "second"),
            ]
        ]
    )
    bridge = FakeGuest(FakeEnv())
    runtime = actor.Actor(
        client, bridge, storage.Store(options.results), "instruction", options
    )
    assert runtime.run()["stop_reason"] == "budget_exhausted"
    assert not bridge.payloads


def test_timeout_payload_and_command_failure_are_visible():
    bridge = FakeGuest(FakeEnv())
    bridge.post = lambda payload, timeout, limit: {
        "returncode": 124,
        "output": "",
        "error": "timeout: sending signal TERM",
    }
    result = bridge.run("sleep 100", 3)
    assert result.exit_code == 124
    assert "TERM" in result.stderr
    bridge = FakeGuest(FakeEnv())
    bridge.run("true", 3)
    assert bridge.payloads[0] == {
        "command": [
            "/usr/bin/timeout",
            "--verbose",
            "--signal=TERM",
            "--kill-after=5s",
            "3s",
            "/bin/bash",
            "-c",
            'export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:'
            "/sbin:/bin:$HOME/.local/bin\"\nexport WEBSITE_HOST_SUFFIX=''\ntrue",
        ],
        "shell": False,
        "timeout": 18,
    }


def test_output_cap_applies_to_entire_serialized_result():
    result = protocol.Result(stdout="A" * 40000, stderr="B" * 40000)
    value = result.text()
    assert len(value) <= protocol.OUTPUT_LIMIT
    assert "OUTPUT TRUNCATED" in value
    assert value.startswith('{"exit_code":0')
    assert value.endswith('"completed":false}')


def test_budget_continuation_and_compaction_use_shared_calls(workspace):
    options = settings(workspace, steps=5, compact_every=2)
    client = FakeClient(
        [
            {
                "status": "incomplete",
                "output": [],
                "incomplete_details": {"reason": "max_output_tokens"},
            },
            [function("echo work")],
            [text("handoff")],
            [function("echo last")],
            [function("python /opt/cuawright-tools/submit.py")],
        ]
    )
    runtime = actor.Actor(
        client,
        FakeGuest(FakeEnv()),
        storage.Store(options.results),
        "instruction",
        options,
    )
    assert runtime.run() == {
        "stop_reason": "submitted",
        "model_calls": 5,
        "commands": 3,
        "compactions": 1,
    }
    assert client.requests[2]["tools"] == []
    assert (
        "2/5 used; 3 remain" in client.requests[3]["input"][-1]["content"][0]["text"]
        or "3/5 used; 2 remain" in client.requests[3]["input"][-1]["content"][0]["text"]
    )


@pytest.mark.parametrize("stage", ["setup", "evaluation", "cleanup", "initialization"])
def test_official_failures_persist_without_hidden_state_and_cleanup(
    workspace, official, monkeypatch, stage
):
    options = settings(workspace, steps=1, compact_every=0)
    prepare_task(options)
    monkeypatch.setattr(FakeEnv, f"{stage}_error", True)
    client = FakeClient([[function("python /opt/cuawright-tools/submit.py")]])
    with pytest.raises(exceptions.ReleaseError):
        runner.run(options, client_factory=lambda *_: client, guest_factory=FakeGuest)
    assert FakeEnv.instances[-1].closed
    payload = json.loads((options.results / "result.json").read_text())
    assert payload["status"] == "failed"
    assert "HIDDEN-EVALUATOR-ANSWER" not in json.dumps(payload)
    if stage not in ("setup", "initialization"):
        assert client.closed


def test_provider_error_is_explicit_and_closes_every_resource(
    workspace, official, monkeypatch
):
    options = settings(workspace, steps=1, compact_every=0)
    prepare_task(options)
    monkeypatch.setattr(actor.time, "sleep", lambda _: None)
    client = FakeClient([RuntimeError("SECRET-NOT-FOR-LOGS")] * 10)
    with pytest.raises(exceptions.ReleaseError, match="actor"):
        runner.run(options, client_factory=lambda *_: client, guest_factory=FakeGuest)
    assert client.closed and FakeEnv.instances[-1].closed
    assert len(client.requests) == 10
    assert all(request == client.requests[0] for request in client.requests)
    result = (options.results / "result.json").read_text()
    assert "SECRET-NOT-FOR-LOGS" not in result
    assert json.loads(result)["actor"]["model_calls"] == 0


def test_provider_retry_resends_identical_request(workspace, monkeypatch):
    options = settings(workspace, steps=1, compact_every=0)
    monkeypatch.setattr(actor.time, "sleep", lambda _: None)
    client = FakeClient(
        [
            RuntimeError("temporary"),
            [function("python /opt/cuawright-tools/submit.py")],
        ]
    )
    runtime = actor.Actor(
        client,
        FakeGuest(FakeEnv()),
        storage.Store(options.results),
        "instruction",
        options,
    )
    assert runtime.run()["stop_reason"] == "submitted"
    assert client.requests[0] == client.requests[1]
    events = [
        json.loads(line)
        for line in (options.results / "trace.jsonl").read_text().splitlines()
    ]
    assert [event["kind"] for event in events].count("transport_retry") == 1


def test_policy_retry_removes_rejected_images(workspace, monkeypatch):
    options = settings(workspace, steps=1, compact_every=0)
    monkeypatch.setattr(actor.time, "sleep", lambda _: None)
    client = FakeClient(
        [
            RuntimeError("content_policy_violation"),
            [function("python /opt/cuawright-tools/submit.py")],
        ]
    )
    runtime = actor.Actor(
        client,
        FakeGuest(FakeEnv()),
        storage.Store(options.results),
        "instruction",
        options,
    )
    runtime.context.append(
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "image"},
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,eA==",
                },
            ],
        }
    )
    assert runtime.run()["stop_reason"] == "submitted"
    assert any(
        part.get("type") == "input_image"
        for item in client.requests[0]["input"]
        for part in item.get("content", [])
        if isinstance(part, dict)
    )
    assert not any(
        part.get("type") == "input_image"
        for item in client.requests[1]["input"]
        for part in item.get("content", [])
        if isinstance(part, dict)
    )
    events = [
        json.loads(line)
        for line in (options.results / "trace.jsonl").read_text().splitlines()
    ]
    retry = next(event for event in events if event["kind"] == "transport_retry")
    assert retry["data"]["removed_images"] == 1


def test_evaluation_retries_status_errors(workspace, monkeypatch):
    class RetryableError(RuntimeError):
        status_code = 400

    attempts = iter((RetryableError("blocked"), {"score": 0.75}))

    def callback():
        value = next(attempts)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    store = storage.Store(workspace / "results")
    assert runner.evaluate(callback, store) == 0.75
    events = [
        json.loads(line)
        for line in (workspace / "results/trace.jsonl").read_text().splitlines()
    ]
    assert [event["kind"] for event in events] == ["evaluation_retry"]


def test_incomplete_tool_call_is_returned_without_execution(workspace):
    options = settings(workspace, steps=2, compact_every=0)
    partial = function("touch /tmp/should-not-run")
    client = FakeClient(
        [
            {
                "status": "incomplete",
                "output": [partial],
                "incomplete_details": {"reason": "max_output_tokens"},
            },
            [function("python /opt/cuawright-tools/submit.py")],
        ]
    )
    bridge = FakeGuest(FakeEnv())
    runtime = actor.Actor(
        client,
        bridge,
        storage.Store(options.results),
        "instruction",
        options,
    )
    assert runtime.run()["stop_reason"] == "submitted"
    assert not any(
        "should-not-run" in json.dumps(payload) for payload in bridge.payloads
    )
    output = next(
        item
        for item in runtime.context
        if item.get("type") == "function_call_output"
        and item.get("call_id") == partial["call_id"]
    )
    assert "output-token limit" in output["output"]


def test_persistence_failure_still_cleans_up(workspace, official, monkeypatch):
    options = settings(workspace, steps=1, compact_every=0)
    prepare_task(options)
    original = storage.Store.write

    def failing(self, name, payload):
        if name == "result.json":
            raise OSError("disk full")
        return original(self, name, payload)

    monkeypatch.setattr(storage.Store, "write", failing)
    client = FakeClient([[function("python /opt/cuawright-tools/submit.py")]])
    with pytest.raises(exceptions.ReleaseError, match="persistence"):
        runner.run(options, client_factory=lambda *_: client, guest_factory=FakeGuest)
    assert client.closed and FakeEnv.instances[-1].closed


@pytest.mark.parametrize("score", [float("nan"), float("inf"), True, -1, 1.1, None])
def test_invalid_evaluation_scores_fail(workspace, official, monkeypatch, score):
    options = settings(workspace, steps=1, compact_every=0)
    prepare_task(options)
    monkeypatch.setattr(FakeEnv, "evaluation_value", {"score": score})
    client = FakeClient([[function("python /opt/cuawright-tools/submit.py")]])
    with pytest.raises(exceptions.ReleaseError, match="evaluation"):
        runner.run(options, client_factory=lambda *_: client, guest_factory=FakeGuest)
    assert FakeEnv.instances[-1].closed and client.closed


def test_source_pin_missing_dirty_and_import_activation_order(workspace, monkeypatch):
    root = workspace / "source"
    root.mkdir()
    with pytest.raises(exceptions.ReleaseError, match="Git"):
        source.activate(root)
    (root / ".git").mkdir()
    monkeypatch.setattr(source, "git", lambda *_: "different")
    with pytest.raises(exceptions.ReleaseError, match="pin"):
        source.activate(root)
    monkeypatch.setattr(
        source,
        "git",
        lambda _, *args: source.COMMIT if args == ("rev-parse", "HEAD") else "dirty",
    )
    with pytest.raises(exceptions.ReleaseError, match="clean"):
        source.activate(root)
    (root / "desktop_env").mkdir()
    (root / "desktop_env/desktop_env.py").write_text("")
    (root / "task_loader.py").write_text("")
    monkeypatch.setattr(
        source,
        "git",
        lambda _, *args: source.COMMIT if args == ("rev-parse", "HEAD") else "",
    )
    monkeypatch.setitem(
        sys.modules,
        "desktop_env",
        types.SimpleNamespace(__file__="/outside/desktop_env.py"),
    )
    with pytest.raises(exceptions.ReleaseError, match="before source activation"):
        source.activate(root)


def test_task_selection_and_config_are_host_only(workspace, official):
    options = settings(workspace)
    prepare_task(options)
    task, instruction, provenance = source.load_task(options.tasks, "001")
    assert task is official
    assert instruction == official["instruction"]
    assert set(provenance) == {"id", "class_sha256", "source_tree", "config_sha256"}
    assert "HIDDEN-EVALUATOR-ANSWER" not in json.dumps(provenance)
    official["evaluator"]["hidden_answer"] = "CHANGED-HIDDEN-ANSWER"
    _, _, changed = source.load_task(options.tasks, "001")
    assert changed["config_sha256"] != provenance["config_sha256"]
    assert "CHANGED-HIDDEN-ANSWER" not in json.dumps(changed)
    with pytest.raises(exceptions.ReleaseError, match="identity"):
        source.load_task(options.tasks, "../001")
    official["proxy"] = True
    task, _, _ = source.load_task(options.tasks, "001")
    assert task["proxy"] is True


@pytest.mark.parametrize(
    "unsupported", ["proxy", "windows", "phases", "legacy", "json"]
)
def test_unsupported_task_surfaces_fail_before_vm(
    workspace, official, monkeypatch, unsupported
):
    options = settings(workspace)
    prepare_task(options)
    if unsupported == "proxy":
        official["proxy"] = True
    elif unsupported == "windows":
        official["platform"] = "windows"
    elif unsupported == "phases":
        official.get_phases = lambda: []
    elif unsupported == "legacy":
        official_type = type(official)
        official_type.evaluate = official_type.__bases__[0].evaluate
    else:
        monkeypatch.setattr(
            sys.modules["task_loader"], "find_task_class_path", lambda *_: None
        )
    with pytest.raises(exceptions.ReleaseError):
        runner.run(
            options, client_factory=lambda *_: pytest.fail("model must not start")
        )
    assert not FakeEnv.instances


def test_proxy_task_uses_external_config(workspace, official, monkeypatch):
    options = settings(workspace)
    prepare_task(options)
    proxy = workspace / "proxy.json"
    proxy.write_text("{}")
    proxy.chmod(0o600)
    official["proxy"] = True
    client = FakeClient([[function("python /opt/cuawright-tools/submit.py")]])
    previous = os.environ.get("PROXY_CONFIG_FILE")
    outcome = runner.run(
        replace(options, proxy_config=proxy),
        client_factory=lambda *_: client,
        guest_factory=FakeGuest,
    )
    assert outcome["score"] == 1.0
    assert FakeEnv.instances[-1].kwargs["enable_proxy"] is True
    assert os.environ.get("PROXY_CONFIG_FILE") == previous
    manifest = json.loads((options.results / "manifest.json").read_text())
    assert manifest["proxy"]["requested"] is True
    assert manifest["proxy"]["config_sha256"] == storage.file_hash(proxy)
    assert "proxy_config" not in manifest["settings"]


def test_multiphase_task_preserves_actor_and_budget(workspace, official):
    options = settings(workspace, steps=4, compact_every=0)
    prepare_task(options)
    setup_calls = []

    def phases():
        return [
            {
                "name": "One",
                "instruction": "Complete phase one.",
                "setup": lambda *_args, **_kwargs: None,
                "evaluate": lambda _env: 0.4,
                "weight": 0.4,
                "gate_min_score": 0.4,
            },
            {
                "name": "Two",
                "instruction": "Complete phase two.",
                "setup": lambda *_args, **_kwargs: setup_calls.append("two"),
                "evaluate": lambda _env: 0.6,
                "weight": 0.6,
            },
        ]

    official.get_phases = phases
    client = FakeClient(
        [
            [function("python /opt/cuawright-tools/submit.py", "phase-one")],
            [function("python /opt/cuawright-tools/submit.py", "phase-two")],
        ]
    )
    outcome = runner.run(
        options,
        client_factory=lambda *_: client,
        guest_factory=FakeGuest,
    )
    assert outcome["score"] == 1.0
    assert outcome["model_calls"] == 2
    assert len(outcome["phases"]) == 2
    assert setup_calls == ["two"]
    assert FakeEnv.instances[-1]._traj_no == 1
    assert any(
        "Complete phase two." in item["content"][0]["text"]
        for item in client.requests[1]["input"]
        if item.get("role") == "user"
    )


def test_credentials_permissions_symlinks_and_safe_settings(workspace):
    options = settings(workspace)
    assert storage.credentials(options.credentials) == "SECRET-NOT-FOR-LOGS"
    options.credentials.chmod(0o644)
    with pytest.raises(exceptions.ReleaseError, match="0600"):
        storage.credentials(options.credentials)
    link = workspace / "key-link"
    link.symlink_to(options.credentials)
    with pytest.raises(OSError):
        storage.credentials(link)
    with pytest.raises(exceptions.ReleaseError, match="credentials"):
        replace(options, base_url="https://secret@example.com/v1").validate()
    with pytest.raises(exceptions.ReleaseError, match="credentials"):
        replace(options, base_url="https://example.com/v1?key=secret").validate()
    options.results.mkdir()
    with pytest.raises(exceptions.ReleaseError, match="already exist"):
        options.validate()


def test_guest_image_script_rejects_symlink_file_and_parent(workspace):
    real = workspace / "real"
    real.mkdir()
    image = real / "a.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    linked_file = real / "link.png"
    linked_file.symlink_to(image)
    linked_dir = workspace / "linked"
    linked_dir.symlink_to(real, target_is_directory=True)
    for path in (image, linked_file, linked_dir / "a.png"):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                guest.materialize(guest.READ_IMAGE, {"path": str(path), "limit": 100}),
            ],
            capture_output=True,
            check=False,
        )
        assert (result.returncode == 0) == (path == image)


def test_guest_read_bridge_rejects_special_and_oversized_files(workspace):
    large = workspace / "large.png"
    large.write_bytes(b"x" * 101)
    fifo = workspace / "fifo"
    os.mkfifo(fifo)
    for path in (large, fifo):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                guest.materialize(guest.READ_IMAGE, {"path": str(path), "limit": 100}),
            ],
            capture_output=True,
            timeout=5,
            check=False,
        )
        assert result.returncode != 0


def test_guest_client_sources_execute_and_exactly_match_commands():
    for name, arguments, extra in [
        ("submit", [], {}),
        ("image_show", ["--path", "/home/user/a.png"], {"path": "/home/user/a.png"}),
        ("ask_user", ["--question", "Which?"], {"question": "Which?"}),
    ]:
        result = subprocess.run(
            [
                sys.executable,
                str(Path(guest.__file__).parent / "control_clients" / f"{name}.py"),
                *arguments,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert json.loads(result.stdout) == {
            "cuawright_control": 1,
            "operation": name,
            **extra,
        }


def test_unified_runtime_has_no_legacy_core_imports():
    import ast

    package = ROOT / "src/cuawright"
    assert not (package / "webwright").exists()
    assert not (package / "desktop").exists()
    for name in ("agents", "config", "environments", "models", "run", "tools", "utils"):
        assert (package / name / "__init__.py").is_file()
    for path in package.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            imports = (
                [item.name for item in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            )
            assert not any(
                name == "webwright" or name.startswith("webwright.") for name in imports
            ), path


def test_help_does_not_import_osworld_or_openai():
    command = (
        "import sys; from cuawright.run.desktop import main; "
        "assert 'desktop_env' not in sys.modules; assert 'openai' not in sys.modules; "
        "main(['--help'])"
    )
    result = subprocess.run(
        [
            sys.executable,
            *(["-I"] if os.environ.get("CUAWRIGHT_TEST_INSTALLED") == "1" else []),
            "-c",
            command,
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--credentials" in result.stdout
    assert "xhigh" in result.stdout


def test_installed_wheel_origin_and_contents_when_available():
    import zipfile

    if os.environ.get("CUAWRIGHT_TEST_INSTALLED") == "1":
        assert ".wheel-env" in str(Path(runner.__file__).resolve())
        assert storage.harness_provenance()["runtime"] == storage.runtime_inventory(
            ROOT / "src/cuawright"
        )
        for name in ("agents", "config", "environments", "models", "run"):
            module = f"cuawright.{name}"
            assert __import__("importlib").util.find_spec(module) is not None
        assert "webwright" not in sys.modules
    wheels = list((ROOT / "release/osworld/dist").glob("*.whl"))
    if not wheels:
        assert os.environ.get("CUAWRIGHT_TEST_INSTALLED") != "1"
        pytest.skip("build wheel before distribution inspection")
    with zipfile.ZipFile(wheels[0]) as wheel:
        python_files = [name for name in wheel.namelist() if name.endswith(".py")]
        expected = {
            path.relative_to(ROOT / "src").as_posix()
            for path in (ROOT / "src").rglob("*.py")
        }
        assert set(python_files) == expected
        assert not any(
            "skill_factory/" in name or name.endswith("skill_use.py")
            for name in python_files
        )
        assert all(
            name.startswith(("cuawright/", "webwright/")) or ".dist-info/" in name
            for name in wheel.namelist()
        )
        for name in python_files:
            assert wheel.read(name) == (ROOT / "src" / name).read_bytes()
        provenance = json.loads(wheel.read("cuawright/build_provenance.json"))
        assert provenance["runtime"] == storage.runtime_inventory(
            ROOT / "src/cuawright"
        )


def test_root_metadata_includes_web_and_desktop():
    import tomllib

    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    project = metadata["project"]
    assert project["name"] == "cuawright"
    assert project["scripts"] == {
        "cuawright": "cuawright.run.cli:main",
        "cuawright-web": "cuawright.run.browser:app",
        "cuawright-desktop": "cuawright.run.desktop:main",
        "webwright": "webwright.run.cli:app",
    }
    assert project["optional-dependencies"]["desktop"] == [
        "openai>=2.0,<3; python_version >= '3.12'"
    ]
    assert project["optional-dependencies"]["skill-factory"] == [
        "cuawright-skill-factory==0.2.0"
    ]
    assert metadata["tool"]["pytest"]["ini_options"]["testpaths"] == [
        "tests",
        "release/osworld/tests",
    ]
    assert (ROOT / "src/cuawright/run/browser.py").is_file()
    assert (ROOT / "licenses/OSWorld-runtime-APACHE-2.0.txt").is_file()


def test_custom_endpoint_and_credentials_are_not_public_settings(workspace):
    options = settings(workspace, base_url="https://private-provider.invalid/v1")
    public = options.public()
    assert "base_url" not in public
    assert "credentials" not in public
    assert "proxy_config" not in public
    assert "private-provider.invalid" not in json.dumps(public)


def test_desktop_source_contains_only_public_or_loopback_urls():
    import ast
    import re
    from urllib.parse import urlsplit

    allowed_hosts = {"api.openai.com", "github.com", "127.0.0.1"}
    package = ROOT / "src/cuawright"
    files = [
        package / name
        for name in (
            "agents/desktop.py",
            "agents/default.py",
            "models/openai_response_model.py",
            "models/responses.py",
            "tools/terminal.py",
            "run/desktop.py",
            "utils/artifacts.py",
        )
    ]
    for directory in ("config/desktop", "environments/desktop", "run/benchmarks"):
        files.extend((package / directory).rglob("*.py"))
    for path in files:
        text = path.read_text()
        for url in re.findall(r"https?://[^\s\"'<>`]+", text):
            assert urlsplit(url).hostname in allowed_hosts, path
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.keyword) and node.arg == "api_key":
                assert not isinstance(node.value, ast.Constant), path
