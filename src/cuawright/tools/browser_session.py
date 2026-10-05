"""Backend-agnostic browser-session helper and persistent incremental CLI.

This module hides *how* a CDP-attachable browser is created so that the
generated trajectory data never contains backend-specific boilerplate
(e.g. the Browserbase REST `create_browserbase_session` block). Every
Playwright step simply does::

    from cuawright.tools.browser_session import open_browser_session

    async with async_playwright() as playwright:
        browser = await open_browser_session(playwright)
        try:
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()
            ...
        finally:
            await browser.close()

The concrete backend is chosen at runtime via the ``WEBWRIGHT_BROWSER_BACKEND``
environment variable (default ``browserbase``) so different users can swap
in their own session provider WITHOUT changing a single line of the agent's
Playwright code — keeping the recorded trajectory agnostic:

    * ``browserbase`` — create a fresh Browserbase cloud session and connect
      over CDP. Requires ``BROWSERBASE_API_KEY`` / ``BROWSERBASE_PROJECT_ID``.
    * ``local``       — launch a Playwright-bundled headless Chromium locally
      and connect over CDP (no cloud account needed).
    * ``cdp``         — connect to an already-running browser whose CDP / ws
      endpoint the user supplies via ``WEBWRIGHT_BROWSER_CDP_URL``. This is the
      fully provider-agnostic escape hatch: the caller owns the browser
      lifecycle and we only attach to it.

A persistent CLI lets local-workspace agents execute short Playwright fragments
against one reusable browser without embedding provider boilerplate::

    python -m cuawright.tools.browser_session create --workspace-dir "$WORKSPACE_DIR"
    python -m cuawright.tools.browser_session step --workspace-dir "$WORKSPACE_DIR" \
      --action "Open the filters" --code-file -
    python -m cuawright.tools.browser_session close --workspace-dir "$WORKSPACE_DIR"
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import time
import traceback
import uuid
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from cuawright.utils.browser_evidence import (
    DEFAULT_BROWSER_STEPS_FILE,
    append_jsonl,
    load_browser_steps,
    relative_workspace_path,
    resolve_workspace_path,
)

DEFAULT_BACKEND = "browserbase"
_BROWSERBASE_API_URL = "https://api.browserbase.com/v1/sessions"
_SESSION_LOG_NAME = "browser_sessions.jsonl"
DEFAULT_PERSISTENT_SESSION_FILE = ".browser-session.json"
DEFAULT_SESSION_EVENTS_FILE = "browser-sessions.jsonl"
_PERSISTENT_SESSION_SCHEMA_VERSION = 1


def _session_log_path() -> str | None:
    """Return the JSONL path where opened sessions are recorded, or ``None``.

    Honors ``WEBWRIGHT_BROWSER_SESSION_LOG`` for an explicit path; otherwise places
    the log inside the task workspace exposed to agent steps via
    ``WORKSPACE_DIR``. Returns ``None`` when neither is available so recording
    silently no-ops outside a managed run.
    """
    explicit = os.environ.get("WEBWRIGHT_BROWSER_SESSION_LOG")
    if explicit:
        return explicit
    workspace = os.environ.get("WORKSPACE_DIR")
    if workspace:
        return os.path.join(workspace, _SESSION_LOG_NAME)
    return None


def _record_session(meta: dict[str, Any]) -> None:
    """Append one JSON line describing a freshly opened session to the task dir.

    Best-effort: any I/O error is swallowed so recording never breaks the
    agent's Playwright step. The line is a dict like
    ``{"backend": "browserbase", "id": "...", "ts": 1234567890.0}``.
    """
    path = _session_log_path()
    backend = meta.get("backend")
    if not path or not backend:
        return
    record: dict[str, Any] = {"backend": backend, "ts": time.time()}
    if meta.get("id"):
        record["id"] = meta["id"]
    if meta.get("pid"):
        record["pid"] = meta["pid"]
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError:
        pass


def _resolve_backend(backend: str | None) -> str:
    value = (backend or os.environ.get("WEBWRIGHT_BROWSER_BACKEND") or os.environ.get("BROWSER_MODE") or DEFAULT_BACKEND).strip().lower()
    if value not in {"browserbase", "local", "cdp"}:
        raise ValueError(
            f"unknown browser backend {value!r}; expected one of browserbase|local|cdp"
        )
    return value


def _require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"missing required env var {name} for the selected browser backend")
    return value


async def _create_connect_url(
    backend: str, playwright: Any | None = None
) -> tuple[str, dict[str, Any]]:
    """Return ``(connectUrl, metadata)`` for the chosen backend."""
    if backend == "cdp":
        url = _require_env("WEBWRIGHT_BROWSER_CDP_URL")
        return url, {"backend": "cdp"}

    if backend == "browserbase":
        import httpx

        api_key = _require_env("BROWSERBASE_API_KEY")
        project_id = _require_env("BROWSERBASE_PROJECT_ID")
        timeout = int(os.environ.get("WEBWRIGHT_BROWSERBASE_TIMEOUT", "720"))
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                os.environ.get("WEBWRIGHT_BROWSERBASE_API_URL", _BROWSERBASE_API_URL),
                headers={"x-bb-api-key": api_key, "Content-Type": "application/json"},
                json={
                    "projectId": project_id,
                    "proxies": True,
                    "browserSettings": {"advancedStealth": True},
                    "timeout": timeout,
                },
            )
            response.raise_for_status()
            body = response.json()
        connect_url = body.get("connectUrl", "")
        if not connect_url:
            raise RuntimeError(f"Browserbase response missing connectUrl: {body!r}")
        return connect_url, {"backend": "browserbase", "id": body.get("id", "")}

    # backend == "local": reuse the local-chromium spawner so the same code
    # path is shared with the persistent local-browser tool.
    from cuawright.tools import persistent_local_browser as lbs

    if playwright is not None:
        chromium = playwright.chromium.executable_path
    else:
        chromium = lbs._chromium_executable()  # noqa: SLF001 - intentional reuse
    import subprocess

    width = int(os.environ.get("WEBWRIGHT_BROWSER_WIDTH", "1280"))
    height = int(os.environ.get("WEBWRIGHT_BROWSER_HEIGHT", "1800"))
    args = [
        chromium,
        "--remote-debugging-port=0",
        "--headless=new",
        "--no-sandbox",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=TranslateUI,MediaRouter",
        f"--window-size={width},{height}",
    ]
    popen_kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "text": True,
        "bufsize": 1,
        "close_fds": True,
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(args, **popen_kwargs)  # noqa: S603
    connect_url = lbs._wait_for_devtools_url(proc, 30.0)  # noqa: SLF001 - intentional reuse
    return connect_url, {"backend": "local", "pid": proc.pid}


async def open_browser_session(playwright: Any, *, backend: str | None = None) -> Any:
    """Create/attach a browser via the configured backend and return it connected.

    The returned object is a Playwright ``Browser`` already attached over CDP.
    Callers obtain a context/page from it as usual and should end with
    ``await browser.close()`` (for cloud/local backends this also ends the
    fresh session; for the ``cdp`` backend it only detaches the connection).

    Each opened session is also appended (one JSON line) to the task's
    ``browser_sessions.jsonl`` log so the run can release any cloud session
    afterwards via :func:`release_recorded_sessions`, even if a step crashes
    before ``browser.close()`` runs.
    """
    resolved = _resolve_backend(backend)
    connect_url, meta = await _create_connect_url(resolved, playwright)
    _record_session(meta)
    return await playwright.chromium.connect_over_cdp(connect_url)


def _read_session_log(path: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return records


async def _release_browserbase_session(client: Any, session_id: str) -> tuple[str, bool, str]:
    api_key = os.environ.get("BROWSERBASE_API_KEY", "")
    project_id = os.environ.get("BROWSERBASE_PROJECT_ID", "")
    if not api_key or not project_id:
        return session_id, False, "missing BROWSERBASE_API_KEY/BROWSERBASE_PROJECT_ID"
    base = os.environ.get("WEBWRIGHT_BROWSERBASE_API_URL", _BROWSERBASE_API_URL)
    try:
        resp = await client.post(
            f"{base}/{session_id}",
            headers={"x-bb-api-key": api_key, "Content-Type": "application/json"},
            json={"projectId": project_id, "status": "REQUEST_RELEASE"},
        )
    except Exception as exc:  # network error, etc.
        return session_id, False, str(exc)
    if resp.status_code < 400:
        return session_id, True, ""
    return session_id, False, f"HTTP {resp.status_code}: {resp.text[:200]}"


async def _release_recorded_sessions_async(records: list[dict[str, Any]]) -> list[tuple[str, bool, str]]:
    import httpx

    results: list[tuple[str, bool, str]] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=30) as client:
        for record in records:
            if record.get("backend") != "browserbase":
                continue
            session_id = str(record.get("id") or "")
            if not session_id or session_id in seen:
                continue
            seen.add(session_id)
            results.append(await _release_browserbase_session(client, session_id))
    return results


def release_recorded_sessions(
    log_path: str | os.PathLike[str] | None = None,
) -> list[tuple[str, bool, str]]:
    """Release every cloud session recorded by :func:`open_browser_session`.

    Reads the JSONL log written during the run and asks the provider
    (currently Browserbase, via ``POST /v1/sessions/{id}`` with
    ``status=REQUEST_RELEASE``) to end each session. This guarantees cleanup
    even when an agent step aborted before reaching ``await browser.close()``,
    so a cloud session is not left running until its server-side ``timeout``.

    Best-effort and idempotent: returns a list of ``(session_id, ok, error)``
    tuples; releasing an already-ended session simply succeeds.
    """
    path = str(log_path) if log_path else _session_log_path()
    if not path or not os.path.exists(path):
        return []
    records = _read_session_log(path)
    if not records:
        return []
    return asyncio.run(_release_recorded_sessions_async(records))


async def _cmd_create_url(args: argparse.Namespace) -> int:
    backend = _resolve_backend(args.backend)
    connect_url, meta = await _create_connect_url(backend)
    print(f"BROWSER_BACKEND={meta.get('backend', backend)}")
    print(f"BROWSER_CONNECT_URL={connect_url}")
    if meta.get("id"):
        print(f"BROWSER_SESSION_ID={meta['id']}")
    if meta.get("pid"):
        print(f"BROWSER_SESSION_PID={meta['pid']}")
    return 0


def _workspace_dir(value: str) -> Path:
    return Path(value or os.environ.get("WORKSPACE_DIR") or Path.cwd()).resolve()


def _workspace_file(workspace: Path, value: str) -> Path:
    path = resolve_workspace_path(workspace, value)
    if not path.is_relative_to(workspace):
        raise ValueError(f"path must stay inside workspace {workspace}: {path}")
    return path


def _session_file(args: argparse.Namespace, workspace: Path) -> Path:
    return _workspace_file(workspace, args.session_file)


def _session_events_file(args: argparse.Namespace, workspace: Path) -> Path:
    return _workspace_file(workspace, args.session_events_file)


def _steps_manifest_file(args: argparse.Namespace, workspace: Path) -> Path:
    return _workspace_file(workspace, args.steps_manifest)


def _atomic_write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _load_session(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(
            f"persistent browser session does not exist at {path}; run the create command first"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read persistent browser session {path}: {exc}") from exc
    if not isinstance(payload, dict) or not payload.get("connect_url"):
        raise RuntimeError(f"invalid persistent browser session manifest: {path}")
    return payload


@contextmanager
def _session_lock(session_path: Path):
    lock_path = session_path.with_suffix(f"{session_path.suffix}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _next_session_epoch(events_path: Path) -> int:
    highest = 0
    if events_path.is_file():
        for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            highest = max(highest, int(row.get("session_epoch") or 0))
    return highest + 1


def _append_session_event(events_path: Path, *, event: str, session: dict[str, Any], **extra: Any) -> None:
    row: dict[str, Any] = {
        "event": event,
        "timestamp": time.time(),
        "session_epoch": int(session.get("session_epoch") or 0),
        "backend": str(session.get("backend") or ""),
        "session_id": str(session.get("session_id") or ""),
        "owned": bool(session.get("owned")),
    }
    row.update(extra)
    append_jsonl(events_path, row)


def _local_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_local_pid(pid: int, timeout_seconds: float = 10.0) -> str:
    if not _local_pid_alive(pid):
        return "not_running"
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return "already_gone"
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    while time.monotonic() < deadline:
        if not _local_pid_alive(pid):
            return "terminated"
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return "already_gone"
    time.sleep(0.2)
    return "killed" if not _local_pid_alive(pid) else "still_alive"


async def _create_persistent_resource(
    *,
    backend: str,
    workspace: Path,
    epoch: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    if backend == "cdp":
        connect_url = os.environ.get("WEBWRIGHT_BROWSER_CDP_URL", "")
        if not connect_url:
            raise RuntimeError("missing required env var WEBWRIGHT_BROWSER_CDP_URL for cdp backend")
        return {
            "backend": backend,
            "connect_url": connect_url,
            "session_id": f"external-{uuid.uuid4().hex}",
            "owned": False,
        }

    if backend == "browserbase":
        import httpx

        api_key = _require_env("BROWSERBASE_API_KEY")
        project_id = _require_env("BROWSERBASE_PROJECT_ID")
        api_url = os.environ.get("WEBWRIGHT_BROWSERBASE_API_URL", _BROWSERBASE_API_URL)
        timeout = int(os.environ.get("WEBWRIGHT_BROWSERBASE_TIMEOUT", str(args.timeout)))
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            response = await client.post(
                api_url,
                headers={"x-bb-api-key": api_key, "Content-Type": "application/json"},
                json={
                    "projectId": project_id,
                    "proxies": args.proxies,
                    "keepAlive": True,
                    "browserSettings": {"advancedStealth": True},
                    "timeout": timeout,
                },
            )
            response.raise_for_status()
            body = response.json()
        connect_url = str(body.get("connectUrl") or "")
        session_id = str(body.get("id") or "")
        if not connect_url or not session_id:
            raise RuntimeError(f"Browserbase response missing id/connectUrl: {body!r}")
        return {
            "backend": backend,
            "connect_url": connect_url,
            "session_id": session_id,
            "owned": True,
            "project_id": project_id,
            "api_url": api_url,
        }

    from cuawright.tools.persistent_local_browser import _wait_for_devtools_url
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        chromium = playwright.chromium.executable_path
    user_data_dir = workspace / ".browser-profiles" / f"epoch_{epoch:04d}"
    user_data_dir.mkdir(parents=True, exist_ok=True)
    chromium_args = [
        chromium,
        "--remote-debugging-port=0",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=TranslateUI,MediaRouter",
        f"--window-size={args.window_width},{args.window_height}",
    ]
    if args.headless:
        chromium_args.append("--headless=new")
    if args.no_sandbox:
        chromium_args.append("--no-sandbox")
    chromium_args.extend(args.chromium_arg or [])
    popen_kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "text": True,
        "bufsize": 1,
        "close_fds": True,
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(chromium_args, **popen_kwargs)  # noqa: S603
    try:
        connect_url = _wait_for_devtools_url(proc, args.startup_timeout)
    except BaseException:
        try:
            proc.terminate()
        except OSError:
            pass
        raise
    return {
        "backend": backend,
        "connect_url": connect_url,
        "session_id": uuid.uuid4().hex,
        "owned": True,
        "pid": proc.pid,
        "user_data_dir": str(user_data_dir),
        "executable_path": chromium,
    }


async def _connect_persistent(session: dict[str, Any], playwright: Any) -> Any:
    return await playwright.chromium.connect_over_cdp(
        str(session["connect_url"]),
        timeout=int(session.get("connect_timeout_ms") or 30000),
    )


async def _page_target_id(context: Any, page: Any) -> str:
    cdp_session = await context.new_cdp_session(page)
    try:
        response = await cdp_session.send("Target.getTargetInfo")
        return str(response.get("targetInfo", {}).get("targetId") or "")
    finally:
        await cdp_session.detach()


async def _select_page(context: Any, session: dict[str, Any]) -> Any:
    pages = [page for page in context.pages if not page.is_closed()]
    if not pages:
        return None
    requested_target = str(session.get("active_target_id") or "")
    if requested_target:
        for page in pages:
            try:
                if await _page_target_id(context, page) == requested_target:
                    return page
            except Exception:
                continue
    requested = int(session.get("active_page_index") or -1)
    if 0 <= requested < len(context.pages):
        page = context.pages[requested]
        if not page.is_closed():
            return page
    return pages[-1]


async def _initialize_persistent_session(session: dict[str, Any], start_url: str) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await _connect_persistent(session, playwright)
        try:
            if not browser.contexts:
                raise RuntimeError("CDP browser did not expose a default browser context")
            context = browser.contexts[0]
            page = await _select_page(context, session)
            if page is None:
                page = await context.new_page()
            await page.set_viewport_size(
                {
                    "width": int(session["window_width"]),
                    "height": int(session["window_height"]),
                }
            )
            if start_url:
                await page.goto(start_url, wait_until="domcontentloaded")
            session["active_page_index"] = context.pages.index(page)
            session["active_target_id"] = await _page_target_id(context, page)
            session["last_url"] = page.url
        finally:
            await browser.close()


async def _release_persistent_resource(session: dict[str, Any], *, delete_user_data: bool) -> str:
    if not session.get("owned"):
        return "detached_borrowed_session"
    backend = str(session.get("backend") or "")
    if backend == "browserbase":
        import httpx

        api_key = _require_env("BROWSERBASE_API_KEY")
        project_id = str(session.get("project_id") or _require_env("BROWSERBASE_PROJECT_ID"))
        api_url = str(session.get("api_url") or _BROWSERBASE_API_URL).rstrip("/")
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            response = await client.post(
                f"{api_url}/{session['session_id']}",
                headers={"x-bb-api-key": api_key, "Content-Type": "application/json"},
                json={"projectId": project_id, "status": "REQUEST_RELEASE"},
            )
            response.raise_for_status()
        return "release_requested"
    if backend == "local":
        status = _terminate_local_pid(int(session.get("pid") or 0))
        user_data_dir = Path(str(session.get("user_data_dir") or ""))
        if delete_user_data and user_data_dir.is_dir():
            shutil.rmtree(user_data_dir, ignore_errors=True)
        return status
    return "no_release_handler"


async def _cmd_persistent_create(args: argparse.Namespace) -> int:
    workspace = _workspace_dir(args.workspace_dir)
    workspace.mkdir(parents=True, exist_ok=True)
    session_path = _session_file(args, workspace)
    events_path = _session_events_file(args, workspace)
    with _session_lock(session_path):
        if session_path.exists():
            if not args.replace:
                existing = _load_session(session_path)
                raise RuntimeError(
                    "a persistent browser session already exists "
                    f"(epoch={existing.get('session_epoch')}, state={existing.get('state')}); "
                    "reuse it or pass --replace"
                )
            existing = _load_session(session_path)
            try:
                release_status = await _release_persistent_resource(
                    existing, delete_user_data=args.delete_replaced_user_data
                )
            except Exception as exc:  # best effort when replacing a broken session
                release_status = f"release_failed: {exc}"
            _append_session_event(
                events_path,
                event="replaced",
                session=existing,
                release_status=release_status,
            )

        epoch = _next_session_epoch(events_path)
        backend = _resolve_backend(args.backend)
        session = await _create_persistent_resource(
            backend=backend,
            workspace=workspace,
            epoch=epoch,
            args=args,
        )
        session.update(
            {
                "schema_version": _PERSISTENT_SESSION_SCHEMA_VERSION,
                "session_epoch": epoch,
                "state": "active",
                "created_at": time.time(),
                "workspace_dir": str(workspace),
                "window_width": args.window_width,
                "window_height": args.window_height,
                "connect_timeout_ms": args.connect_timeout_ms,
                "active_page_index": -1,
            }
        )
        try:
            await _initialize_persistent_session(session, args.start_url)
        except BaseException:
            try:
                await _release_persistent_resource(session, delete_user_data=True)
            except Exception:
                pass
            raise
        _atomic_write_private_json(session_path, session)
        _append_session_event(events_path, event="created", session=session)

    print(
        json.dumps(
            {
                "success": True,
                "state": "active",
                "backend": backend,
                "session_epoch": epoch,
                "session_file": relative_workspace_path(workspace, session_path),
                "url": session.get("last_url", ""),
            },
            ensure_ascii=False,
        )
    )
    return 0


def _read_step_code(args: argparse.Namespace) -> str:
    if args.code_file == "-":
        return sys.stdin.read()
    return Path(args.code_file).read_text(encoding="utf-8")


async def _run_step_code(
    code: str,
    *,
    page: Any,
    context: Any,
    browser: Any,
    playwright: Any,
    task: dict[str, Any],
    workspace: Path,
) -> tuple[Any, str]:
    body = code.strip() or "pass"
    wrapped = (
        "async def __agent_browser_step__(page, context, browser, playwright, task, workspace):\n"
        f"{textwrap.indent(body, '    ')}\n"
        "    return locals().get('page', page)\n"
    )
    namespace: dict[str, Any] = {}
    output = io.StringIO()
    try:
        with redirect_stdout(output), redirect_stderr(output):
            exec(wrapped, {"__builtins__": __builtins__}, namespace)
            selected_page = await namespace["__agent_browser_step__"](
                page,
                context,
                browser,
                playwright,
                task,
                workspace,
            )
    except BaseException as exc:
        setattr(exc, "mwa_python_output", output.getvalue().strip())
        raise
    return selected_page, output.getvalue().strip()


def _load_task(workspace: Path) -> dict[str, Any]:
    configured = os.environ.get("OM2W_TASK_JSON", "")
    candidates = [Path(configured)] if configured else []
    candidates.append(workspace / "task.json")
    for path in candidates:
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return {}


def _classify_step_error(error: BaseException, browser: Any | None, page: Any | None) -> str:
    text = str(error).lower()
    connected = True
    if browser is not None:
        try:
            connected = bool(browser.is_connected())
        except Exception:
            connected = False
    if not connected or any(
        marker in text
        for marker in (
            "browser has been closed",
            "browser disconnected",
            "connection closed",
            "websocket error",
            "connect_over_cdp",
        )
    ):
        return "session"
    page_closed = False
    if page is not None:
        try:
            page_closed = bool(page.is_closed())
        except Exception:
            page_closed = True
    if page_closed or "target page, context or browser has been closed" in text:
        return "page"
    return "action"


def _should_screenshot(policy: str, success: bool) -> bool:
    return policy == "always" or (policy == "on-success" and success) or (
        policy == "on-error" and not success
    )


def _next_browser_step(workspace: Path, manifest_path: Path) -> int:
    rows = load_browser_steps(workspace, manifest_path)
    return max((int(row.get("browser_step") or 0) for row in rows), default=0) + 1


async def _cmd_step(args: argparse.Namespace) -> int:
    workspace = _workspace_dir(args.workspace_dir)
    session_path = _session_file(args, workspace)
    events_path = _session_events_file(args, workspace)
    manifest_path = _steps_manifest_file(args, workspace)
    code = _read_step_code(args)
    if not code.strip():
        raise RuntimeError("step code is empty")

    with _session_lock(session_path):
        session = _load_session(session_path)
        if session.get("state") != "active":
            raise RuntimeError(
                f"persistent browser session is {session.get('state')!r}; create a replacement session"
            )
        browser_step = _next_browser_step(workspace, manifest_path)
        agent_step = int(args.agent_step or os.environ.get("MWA_AGENT_STEP") or 0)
        code_path = workspace / "steps" / f"browser_step_{browser_step:04d}.py"
        code_path.parent.mkdir(parents=True, exist_ok=True)
        code_path.write_text(code.rstrip() + "\n", encoding="utf-8")
        code_sha256 = hashlib.sha256(code.encode("utf-8")).hexdigest()

        started_at = time.time()
        success = False
        error_text = ""
        error_traceback = ""
        error_kind = ""
        python_output = ""
        screenshot_path: Path | None = None
        screenshot_error = ""
        url_before = ""
        url_after = ""
        title = ""
        browser = None
        page = None

        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as playwright:
                browser = await _connect_persistent(session, playwright)
                try:
                    if not browser.contexts:
                        raise RuntimeError("CDP browser did not expose a default browser context")
                    context = browser.contexts[0]
                    page = await _select_page(context, session)
                    if page is None:
                        page = await context.new_page()
                    await page.set_viewport_size(
                        {
                            "width": int(session["window_width"]),
                            "height": int(session["window_height"]),
                        }
                    )
                    url_before = page.url
                    selected_page, python_output = await asyncio.wait_for(
                        _run_step_code(
                            code,
                            page=page,
                            context=context,
                            browser=browser,
                            playwright=playwright,
                            task=_load_task(workspace),
                            workspace=workspace,
                        ),
                        timeout=args.timeout_seconds,
                    )
                    if selected_page is not None and selected_page in context.pages:
                        page = selected_page
                    elif page.is_closed():
                        page = await _select_page(context, session)
                    if page is None:
                        raise RuntimeError(
                            "the active tab was closed and no tabs remain; open a new tab in the next step"
                        )
                    success = True
                except Exception as exc:
                    python_output = str(getattr(exc, "mwa_python_output", python_output))
                    error_text = str(exc)
                    error_traceback = "".join(
                        traceback.format_exception(type(exc), exc, exc.__traceback__)
                    ).strip()
                    error_kind = _classify_step_error(exc, browser, page)

                if page is not None and not page.is_closed():
                    if args.settle_ms > 0:
                        await asyncio.sleep(args.settle_ms / 1000)
                    try:
                        url_after = page.url
                        title = await page.title()
                        session["active_page_index"] = context.pages.index(page)
                        session["active_target_id"] = await _page_target_id(context, page)
                        session["last_url"] = url_after
                    except Exception as exc:
                        if success:
                            success = False
                            error_text = str(exc)
                            error_traceback = "".join(
                                traceback.format_exception(type(exc), exc, exc.__traceback__)
                            ).strip()
                            error_kind = _classify_step_error(exc, browser, page)

                if _should_screenshot(args.screenshot, success) and page is not None and not page.is_closed():
                    if args.screenshot_path:
                        screenshot_path = _workspace_file(workspace, args.screenshot_path)
                    else:
                        screenshot_path = workspace / "screenshots" / f"browser_step_{browser_step:04d}.png"
                    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        await page.screenshot(
                            path=str(screenshot_path),
                            full_page=False,
                            timeout=args.screenshot_timeout_ms,
                        )
                    except Exception as exc:
                        screenshot_error = str(exc)
                        screenshot_path = None
                        if success:
                            success = False
                            error_text = f"end-of-step screenshot failed: {exc}"
                            error_traceback = "".join(
                                traceback.format_exception(type(exc), exc, exc.__traceback__)
                            ).strip()
                            error_kind = "capture"
                await browser.close()
        except Exception as exc:
            if not error_text:
                error_text = str(exc)
                error_traceback = "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                ).strip()
                error_kind = _classify_step_error(exc, browser, page)

        if error_kind == "session":
            session["state"] = "broken"
            _append_session_event(
                events_path,
                event="broken",
                session=session,
                browser_step=browser_step,
                error=error_text,
            )
        session["updated_at"] = time.time()
        _atomic_write_private_json(session_path, session)

        record = {
            "browser_step": browser_step,
            "agent_step": agent_step,
            "session_epoch": int(session.get("session_epoch") or 0),
            "action": args.action.strip(),
            "code_path": relative_workspace_path(workspace, code_path),
            "code_sha256": code_sha256,
            "success": success,
            "url_before": url_before,
            "url_after": url_after,
            "title": title,
            "python_output": python_output,
            "screenshot_path": relative_workspace_path(workspace, screenshot_path)
            if screenshot_path is not None
            else None,
            "screenshot_error": screenshot_error or None,
            "error_kind": error_kind or None,
            "error": error_text or None,
            "traceback": error_traceback or None,
            "started_at": started_at,
            "finished_at": time.time(),
            "duration_ms": int((time.time() - started_at) * 1000),
        }
        append_jsonl(manifest_path, record)

    result = {
        "success": success,
        "browser_step": browser_step,
        "agent_step": agent_step,
        "session_epoch": record["session_epoch"],
        "session_state": session["state"],
        "action": record["action"],
        "url": url_after,
        "title": title,
        "python_output": python_output,
        "screenshot_path": record["screenshot_path"],
        "error_kind": record["error_kind"],
        "error": record["error"],
        "suggested_recovery": (
            "create a replacement persistent browser session"
            if error_kind == "session"
            else "open a new tab in the existing browser session"
            if error_kind == "page"
            else "inspect the action and retry in the existing tab"
            if error_kind
            else ""
        ),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if success else 1


async def _cmd_status(args: argparse.Namespace) -> int:
    workspace = _workspace_dir(args.workspace_dir)
    session_path = _session_file(args, workspace)
    with _session_lock(session_path):
        session = _load_session(session_path)
        provider_alive: bool | None = None
        tabs = 0
        error = ""
        if session.get("backend") == "local":
            provider_alive = _local_pid_alive(int(session.get("pid") or 0))
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as playwright:
                browser = await _connect_persistent(session, playwright)
                tabs = sum(len(context.pages) for context in browser.contexts)
                await browser.close()
            provider_alive = True
        except Exception as exc:
            provider_alive = False
            error = str(exc)
    print(
        json.dumps(
            {
                "success": bool(provider_alive),
                "state": session.get("state"),
                "backend": session.get("backend"),
                "session_epoch": session.get("session_epoch"),
                "tabs": tabs,
                "url": session.get("last_url", ""),
                "error": error or None,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if provider_alive else 1


async def _close_persistent_session_files(
    *,
    session_path: Path,
    events_path: Path,
    delete_user_data: bool,
    delete_session_file: bool,
) -> dict[str, Any]:
    if not session_path.exists():
        return {"success": True, "state": "missing", "released": False}
    with _session_lock(session_path):
        session = _load_session(session_path)
        try:
            release_status = await _release_persistent_resource(
                session, delete_user_data=delete_user_data
            )
            success = True
            error = ""
        except Exception as exc:
            release_status = "release_failed"
            success = False
            error = str(exc)
        session["state"] = "closed" if success else "broken"
        session["updated_at"] = time.time()
        _append_session_event(
            events_path,
            event=session["state"],
            session=session,
            release_status=release_status,
            error=error or None,
        )
        if success and delete_session_file:
            session_path.unlink(missing_ok=True)
        else:
            _atomic_write_private_json(session_path, session)
    return {
        "success": success,
        "state": session["state"],
        "backend": session.get("backend"),
        "session_epoch": session.get("session_epoch"),
        "release_status": release_status,
        "error": error or None,
    }


def close_persistent_session(
    workspace_dir: str | os.PathLike[str],
    *,
    session_file: str = DEFAULT_PERSISTENT_SESSION_FILE,
    session_events_file: str = DEFAULT_SESSION_EVENTS_FILE,
) -> dict[str, Any]:
    """Best-effort harness cleanup for a CLI-owned persistent session."""
    workspace = Path(workspace_dir).resolve()
    return asyncio.run(
        _close_persistent_session_files(
            session_path=_workspace_file(workspace, session_file),
            events_path=_workspace_file(workspace, session_events_file),
            delete_user_data=True,
            delete_session_file=True,
        )
    )


async def _cmd_close(args: argparse.Namespace) -> int:
    workspace = _workspace_dir(args.workspace_dir)
    result = await _close_persistent_session_files(
        session_path=_session_file(args, workspace),
        events_path=_session_events_file(args, workspace),
        delete_user_data=args.delete_user_data,
        delete_session_file=args.delete_session_file,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["success"] else 1


def _add_persistent_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--workspace-dir",
        default="",
        help="Workspace containing session, step, screenshot, and evidence artifacts.",
    )
    parser.add_argument("--session-file", default=DEFAULT_PERSISTENT_SESSION_FILE)
    parser.add_argument("--session-events-file", default=DEFAULT_SESSION_EVENTS_FILE)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cuawright.tools.browser_session",
        description="Backend-agnostic browser-session helper (browserbase|local|cdp).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="Create a persistent browser session.")
    _add_persistent_paths(create)
    create.add_argument(
        "--backend",
        default="",
        help="Override WEBWRIGHT_BROWSER_BACKEND (browserbase|local|cdp).",
    )
    create.add_argument("--replace", action="store_true", help="Release/replace an existing session.")
    create.add_argument("--start-url", default="")
    create.add_argument("--window-width", type=int, default=1280)
    create.add_argument("--window-height", type=int, default=1800)
    create.add_argument("--connect-timeout-ms", type=int, default=30000)
    create.add_argument("--timeout", type=int, default=3600, help="Provider session lifetime in seconds.")
    create.add_argument("--startup-timeout", type=float, default=30.0)
    create.add_argument("--proxies", action=argparse.BooleanOptionalAction, default=True)
    create.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    create.add_argument("--no-sandbox", action=argparse.BooleanOptionalAction, default=True)
    create.add_argument("--chromium-arg", action="append", default=[])
    create.add_argument(
        "--delete-replaced-user-data", action=argparse.BooleanOptionalAction, default=True
    )
    create.set_defaults(func=_cmd_persistent_create)

    step = sub.add_parser("step", help="Run one incremental Playwright fragment.")
    _add_persistent_paths(step)
    step.add_argument("--steps-manifest", default=DEFAULT_BROWSER_STEPS_FILE)
    step.add_argument("--action", required=True, help="Natural-language description of this step.")
    step.add_argument("--code-file", default="-", help="Python fragment path, or '-' for stdin.")
    step.add_argument("--agent-step", type=int, default=0)
    step.add_argument("--timeout-seconds", type=float, default=60.0)
    step.add_argument("--settle-ms", type=int, default=250)
    step.add_argument("--screenshot-timeout-ms", type=int, default=10000)
    step.add_argument(
        "--screenshot",
        choices=("none", "always", "on-success", "on-error"),
        default="none",
        help="End-of-step viewport screenshot policy (default: none).",
    )
    step.add_argument("--screenshot-path", default="")
    step.set_defaults(func=_cmd_step)

    status = sub.add_parser("status", help="Inspect the current persistent session.")
    _add_persistent_paths(status)
    status.set_defaults(func=_cmd_status)

    close = sub.add_parser("close", help="Release the current persistent session.")
    _add_persistent_paths(close)
    close.add_argument("--delete-user-data", action=argparse.BooleanOptionalAction, default=True)
    close.add_argument("--delete-session-file", action=argparse.BooleanOptionalAction, default=True)
    close.set_defaults(func=_cmd_close)

    create_url = sub.add_parser(
        "create-url", help="Legacy diagnostic: create/attach once and print its connectUrl."
    )
    create_url.add_argument("--backend", default="")
    create_url.set_defaults(func=_cmd_create_url)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return asyncio.run(args.func(args))
    except Exception as exc:
        command = str(getattr(args, "command", "") or "")
        error_kind = "session" if command in {"step", "status"} else "cli"
        payload = {
            "success": False,
            "command": command,
            "error_kind": error_kind,
            "error": str(exc),
            "suggested_recovery": (
                "create or replace the persistent browser session"
                if error_kind == "session"
                else "inspect the CLI arguments and provider configuration"
            ),
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
