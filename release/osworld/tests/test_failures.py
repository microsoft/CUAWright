import contextlib
import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import types
import urllib.error
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if os.environ.get("CUAWRIGHT_TEST_INSTALLED") != "1":
    sys.path.insert(0, str(ROOT / "src"))

from cuawright.desktop import exceptions
from cuawright.desktop.agents import default as actor
from cuawright.desktop.config import prompts
from cuawright.desktop.environments.osworld import guest_tools as guest
from cuawright.desktop.models.utils import (
    actions_toolcall_response as protocol,
)
from cuawright.desktop.run.benchmarks import osworld as runner
from cuawright.desktop.utils import artifacts as storage


@pytest.fixture
def workspace():
    path = ROOT / "release/osworld/.scratch" / uuid.uuid4().hex
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path)


def env():
    return types.SimpleNamespace(
        controller=types.SimpleNamespace(http_server="http://localhost:5000"),
        user_simulator=None,
        is_environment_used=False,
    )


def test_live_local_command_timeout_terminates_process_group():
    def local_post(payload, timeout, limit):
        value = subprocess.run(
            payload["command"], capture_output=True, text=True, timeout=timeout
        )
        return {
            "returncode": value.returncode,
            "output": value.stdout,
            "error": value.stderr,
        }

    bridge = guest.Guest(env(), post=local_post)
    start = time.monotonic()
    result = bridge.run("sleep 30 & wait", 1)
    assert result.exit_code == 124
    assert time.monotonic() - start < 6
    assert "sending signal TERM" in result.stderr


@pytest.mark.parametrize(
    "body",
    [
        b"bad JSON",
        b"[]",
        b'{"returncode":true,"output":"","error":""}',
        b'{"returncode":0,"output":1,"error":""}',
    ],
)
def test_invalid_guest_transport_responses_fail_explicitly(monkeypatch, body):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self, limit):
            return body

    monkeypatch.setattr(guest.urllib.request, "urlopen", lambda *_args, **_: Response())
    result = guest.Guest(env()).run("true", 1)
    assert result.exit_code == -1
    assert "guest" in result.stderr.lower()


def test_http_error_diagnostics_cannot_leak_secret(monkeypatch):
    def failed(*_args, **_kwargs):
        raise urllib.error.URLError("PRIVATE-CREDENTIAL")

    monkeypatch.setattr(guest.urllib.request, "urlopen", failed)
    result = guest.Guest(env()).run("true", 1)
    assert result.exit_code == -1
    assert "PRIVATE-CREDENTIAL" not in result.stderr


def test_executed_client_script_hash_and_symlink_protection(workspace):
    root = workspace / "tools"
    root.mkdir()
    path = root / "submit.py"
    path.write_bytes(guest.CLIENTS["submit"])
    script = guest.EXECUTE_CLIENT.replace(
        'Path("/opt/cuawright-tools")', f"Path({str(root)!r})"
    ).replace("info.st_uid != 0", "info.st_uid != os.getuid()")
    payload = {"name": "submit", "args": [], "sha256": guest.CLIENT_HASHES["submit"]}

    def execute():
        return subprocess.run(
            [sys.executable, "-c", guest.materialize(script, payload)],
            capture_output=True,
            text=True,
        )

    result = execute()
    assert result.returncode == 0
    assert json.loads(result.stdout)["operation"] == "submit"
    path.write_text("print('forged')")
    assert execute().returncode != 0
    path.unlink()
    other = workspace / "other.py"
    other.write_bytes(guest.CLIENTS["submit"])
    path.symlink_to(other)
    assert execute().returncode != 0
    path.unlink()
    path.write_bytes(guest.CLIENTS["submit"])
    path.chmod(0o666)
    assert execute().returncode != 0
    path.chmod(0o644)
    root.chmod(0o777)
    assert execute().returncode != 0
    root.chmod(0o755)


def test_no_user_simulator_is_explicit_control_error():
    bridge = guest.Guest(env())
    bridge.python = lambda *_: protocol.Result(
        stdout='{"cuawright_control":1,"operation":"ask_user","question":"Why?"}'
    )
    result = bridge.run('python /opt/cuawright-tools/ask_user.py --question "Why?"', 1)
    assert result.exit_code == 2
    assert "No official user simulator" in result.stderr


def test_provisioning_failure_is_not_silently_ignored():
    bridge = guest.Guest(env())
    bridge.execute = lambda *_: protocol.Result(1, stderr="PRIVATE-CREDENTIAL")
    with pytest.raises(exceptions.ReleaseError, match="provisioning") as error:
        bridge.provision()
    assert "PRIVATE-CREDENTIAL" not in str(error.value)


def test_control_provisioning_preserves_pre_reset_usage_state():
    environment = env()
    environment.is_environment_used = False
    bridge = guest.Guest(environment)
    bridge.post = lambda *_: {
        "returncode": 0,
        "output": "installed\n",
        "error": "",
    }
    bridge.provision_controls()
    assert environment.is_environment_used is False


@pytest.mark.parametrize("password", [None, "private' $(not-executed) \""])
def test_provisioning_uses_only_configured_administrative_credential(password):
    environment = env()
    environment.client_password = password
    bridge = guest.Guest(environment)
    commands = []

    def execute(argv, timeout):
        commands.append(argv)
        if len(commands) == 1:
            assert timeout == 90
            return protocol.Result(stdout="installed\n")
        if len(commands) == 2:
            assert timeout == 240
            return protocol.Result()
        assert timeout == 30
        return protocol.Result(stdout="6000000\n")

    bridge.execute = execute
    bridge.provision()
    payload = {
        name: guest.base64.b64encode(source).decode()
        for name, source in guest.CLIENTS.items()
    }
    script = guest.materialize(guest.INSTALL, payload)
    if password is None:
        assert commands[0] == ["sudo", "-n", "/usr/bin/python3", "-c", script]
    else:
        assert commands[0][:2] == ["/bin/bash", "-c"]
        assert shlex.split(commands[0][2]) == [
            "printf",
            "%s\\n",
            password,
            "|",
            "sudo",
            "-S",
            "-p",
            "",
            "/usr/bin/python3",
            "-c",
            script,
        ]


@pytest.mark.parametrize("password", ["", 1, "PRIVATE\nCREDENTIAL"])
def test_invalid_administrative_credential_fails_before_guest_execution(password):
    environment = env()
    environment.client_password = password
    bridge = guest.Guest(environment)
    bridge.execute = lambda *_: pytest.fail("invalid credential reached guest")
    with pytest.raises(
        exceptions.ReleaseError, match="administrative credential"
    ) as error:
        bridge.provision()
    assert str(error.value) == "invalid guest administrative credential"


@pytest.mark.parametrize("reasoning", ["high", "xhigh", "max"])
def test_real_openai_sdk_request_uses_standard_fields(workspace, reasoning):
    import httpx
    from openai import OpenAI

    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        command = (
            "echo first"
            if len(captured) == 1
            else "python /opt/cuawright-tools/submit.py"
        )
        return httpx.Response(
            200,
            json={
                "id": "response-id",
                "object": "response",
                "created_at": 0,
                "model": "test-model",
                "status": "completed",
                "output": [
                    {
                        "id": "function-id",
                        "type": "function_call",
                        "call_id": "call-id",
                        "name": "run_command",
                        "arguments": json.dumps({"command": command, "timeout": 1}),
                    }
                ],
            },
        )

    client = OpenAI(
        api_key="FAKE-TEST-KEY",
        base_url="https://local.invalid/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    bridge = guest.Guest(env())
    bridge.post = lambda *_: {"returncode": 0, "output": "ordinary", "error": ""}
    bridge.python = lambda *_: protocol.Result(
        stdout='{"cuawright_control":1,"operation":"submit"}'
    )
    settings = types.SimpleNamespace(
        steps=2,
        compact_every=0,
        model="test-model",
        reasoning=reasoning,
        max_output_tokens=32768,
    )
    try:
        runtime = actor.Actor(
            client, bridge, storage.Store(workspace / "results"), "Do X.", settings
        )
        assert runtime.run()["stop_reason"] == "submitted"
    finally:
        client.close()
    assert len(captured) == 2
    assert set(captured[0]) == {
        "model",
        "input",
        "tools",
        "parallel_tool_calls",
        "reasoning",
        "include",
        "store",
        "max_output_tokens",
    }
    assert captured[0]["tools"] == protocol.TOOLS
    assert captured[0]["reasoning"]["effort"] == reasoning
    assert captured[0]["input"][0] == actor.message("system", prompts.ACTOR)
    previous_call = next(
        item for item in captured[1]["input"] if item.get("type") == "function_call"
    )
    assert "status" not in previous_call
    assert all(value is not None for value in previous_call.values())


def test_retained_prompt_fragments_match_baseline_without_legacy_imports():
    # The minimal reliability condition restores explicit browser capability
    # guidance while retaining the compact Actor and compaction contract.
    expected = {
        "ACTOR": "3943ef31e92bcaa7068e6f74a10695fb896ce92826bd5034a674dac46bff0e90",
        "COMPACTION": "e899c5d37d1f46a5193614b40a32686031ffa7b8f5a8e2f5e124ca73b6b9d739",
    }
    for name, golden in expected.items():
        assert hashlib.sha256(getattr(prompts, name).encode()).hexdigest() == golden


def test_store_atomic_failure_and_trace_failure_are_explicit(workspace, monkeypatch):
    store = storage.Store(workspace / "results")
    store.write("result.json", {"status": "old"})
    original = storage.os.replace

    def failed(*_):
        raise OSError("disk full")

    monkeypatch.setattr(storage.os, "replace", failed)
    with pytest.raises(exceptions.ReleaseError, match="persistence"):
        store.write("result.json", {"status": "new"})
    assert json.loads((store.root / "result.json").read_text())["status"] == "old"
    monkeypatch.setattr(storage.os, "replace", original)
    store.trace.symlink_to(workspace / "outside")
    with pytest.raises(exceptions.ReleaseError, match="trace"):
        store.event("unsafe", {})


def test_quiet_external_suppresses_stdout_stderr_and_restores_logging():
    import logging

    previous = logging.root.manager.disable
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        with pytest.raises(ValueError):
            with runner.quiet_external():
                print("HIDDEN")
                print("HIDDEN", file=sys.stderr)
                raise ValueError("HIDDEN")
    assert not out.getvalue() and not err.getvalue()
    assert logging.root.manager.disable == previous
