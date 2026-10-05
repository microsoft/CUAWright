from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run_cli(workspace: Path, *args: str, code: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "cuawright.tools.browser_session",
            *args,
            "--workspace-dir",
            str(workspace),
        ],
        input=code,
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )


def test_step_without_session_returns_structured_recovery(tmp_path: Path) -> None:
    completed = _run_cli(
        tmp_path,
        "step",
        "--action",
        "Open the page",
        "--code-file",
        "-",
        code="await page.goto('https://example.com')\n",
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["error_kind"] == "session"
    assert "create or replace" in payload["suggested_recovery"]


def test_persistent_cli_keeps_new_tab_across_processes_and_screenshots_are_opt_in(
    tmp_path: Path,
) -> None:
    created = _run_cli(tmp_path, "create", "--backend", "local", "--start-url", "about:blank")
    assert created.returncode == 0, created.stdout + created.stderr
    try:
        first = _run_cli(
            tmp_path,
            "step",
            "--action",
            "Open a new tab with the result",
            "--code-file",
            "-",
            code=(
                "await page.set_content('<title>Original</title><h1>Original</h1>')\n"
                "page = await context.new_page()\n"
                "await page.set_content('<title>Result tab</title><h1>Persistent result</h1>')\n"
            ),
        )
        assert first.returncode == 0, first.stdout + first.stderr
        first_payload = json.loads(first.stdout)
        assert first_payload["screenshot_path"] is None

        second = _run_cli(
            tmp_path,
            "step",
            "--action",
            "Verify the result tab persisted",
            "--screenshot",
            "always",
            "--code-file",
            "-",
            code=(
                "assert await page.title() == 'Result tab', await page.title()\n"
                "assert await page.get_by_role('heading', name='Persistent result').is_visible()\n"
            ),
        )
        assert second.returncode == 0, second.stdout + second.stderr
        second_payload = json.loads(second.stdout)
        assert second_payload["title"] == "Result tab"
        assert second_payload["screenshot_path"] == "screenshots/browser_step_0002.png"
        assert (tmp_path / second_payload["screenshot_path"]).is_file()

        rows = [
            json.loads(line)
            for line in (tmp_path / "browser-steps.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert [row["action"] for row in rows] == [
            "Open a new tab with the result",
            "Verify the result tab persisted",
        ]
        assert [row["session_epoch"] for row in rows] == [1, 1]
    finally:
        closed = _run_cli(tmp_path, "close")
        assert closed.returncode == 0, closed.stdout + closed.stderr


def test_browserbase_persistent_resource_uses_keepalive_and_release(monkeypatch, tmp_path):
    import asyncio
    import httpx
    from cuawright.tools import browser_session as session
    monkeypatch.setenv('BROWSERBASE_API_KEY', 'test-key')
    monkeypatch.setenv('BROWSERBASE_PROJECT_ID', 'test-project')
    requests = []
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {'id': 'session-1', 'connectUrl': 'wss://example.test/cdp'}
    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, **kwargs):
            requests.append((url, kwargs))
            return Response()
    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    args = session._build_parser().parse_args(['create', '--workspace-dir', str(tmp_path)])
    resource = asyncio.run(session._create_persistent_resource(
        backend='browserbase', workspace=tmp_path, epoch=1, args=args))
    assert resource['connect_url'] == 'wss://example.test/cdp'
    assert resource['session_id'] == 'session-1'
    assert requests[0][0] == 'https://api.browserbase.com/v1/sessions'
    assert requests[0][1]['json']['keepAlive'] is True
    assert requests[0][1]['headers']['x-bb-api-key'] == 'test-key'
    assert asyncio.run(session._release_persistent_resource(resource, delete_user_data=True)) == 'release_requested'
    assert requests[1][0].endswith('/session-1')
    assert requests[1][1]['json']['status'] == 'REQUEST_RELEASE'


def test_backend_uses_workspace_browser_mode(monkeypatch):
    from cuawright.tools.browser_session import _resolve_backend
    monkeypatch.delenv('WEBWRIGHT_BROWSER_BACKEND', raising=False)
    monkeypatch.setenv('BROWSER_MODE', 'browserbase')
    assert _resolve_backend(None) == 'browserbase'
    monkeypatch.setenv('BROWSER_MODE', 'local')
    assert _resolve_backend(None) == 'local'
    assert _resolve_backend('browserbase') == 'browserbase'
