"""CLI for managing a long-lived, reusable local Chromium browser session.

Local-browser counterpart to ``browserbase_session.py``. Launches a
detached headless Chromium subprocess via the Playwright-bundled binary
with ``--remote-debugging-port=0`` and a per-session ``--user-data-dir``,
parses the printed ``DevTools listening on ws://...`` URL, and persists
``{id, pid, connectUrl, userDataDir}`` to a JSON file on disk so any
later Python/bash step can attach via
``playwright.chromium.connect_over_cdp(connectUrl)`` and end with
``await browser.disconnect()`` (NEVER ``browser.close()``) to keep the
browser alive across steps.

Subcommands:

    * ``create``  -> spawn detached Chromium, write JSON, print id.
    * ``info``    -> print whether the saved PID is still alive plus
                     the persisted JSON.
    * ``release`` -> SIGTERM (then SIGKILL) the PID, optionally remove
                     the user-data-dir and the JSON file.

Usage:
    python -m webwright.tools.persistent_local_browser create  --workspace-dir <ws> --out .lb_session.json
    python -m webwright.tools.persistent_local_browser info    --workspace-dir <ws> --session-file .lb_session.json
    python -m webwright.tools.persistent_local_browser release --workspace-dir <ws> --session-file .lb_session.json --delete-file
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

DEFAULT_SESSION_FILE = ".lb_session.json"
DEFAULT_USER_DATA_SUBDIR = ".lb_user_data"
_DEVTOOLS_RE = re.compile(r"DevTools listening on (ws://\S+)")


def _resolve_path(path_str: str, workspace_dir: str = "") -> Path:
    path = Path(path_str)
    if not path.is_absolute():
        base = Path(workspace_dir) if workspace_dir else Path.cwd()
        path = base / path
    return path


def _chromium_executable() -> str:
    """Locate the Playwright-bundled Chromium executable."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - import guard
        raise SystemExit(f"error: playwright is not installed: {exc}")
    with sync_playwright() as p:
        path = p.chromium.executable_path
    if not path or not Path(path).exists():
        raise SystemExit(
            "error: Playwright chromium binary not found. Run `playwright install chromium`."
        )
    return path


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_devtools_url(proc: subprocess.Popen, timeout: float) -> str:
    """Read Chromium's stderr until the DevTools ws:// URL appears."""
    assert proc.stderr is not None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            tail = ""
            try:
                tail = proc.stderr.read() or ""
            except Exception:  # noqa: BLE001
                pass
            raise SystemExit(
                f"error: chromium exited early (code={proc.returncode}); stderr tail:\n{tail}"
            )
        line = proc.stderr.readline()
        if not line:
            time.sleep(0.05)
            continue
        match = _DEVTOOLS_RE.search(line)
        if match:
            return match.group(1).strip()
    raise SystemExit(
        f"error: timed out after {timeout:.1f}s waiting for 'DevTools listening on ws://...' line"
    )


def _cmd_create(args: argparse.Namespace) -> int:
    workspace_dir = args.workspace_dir or str(Path.cwd())
    out_path = _resolve_path(args.out, workspace_dir)
    user_data_dir = _resolve_path(
        args.user_data_dir or DEFAULT_USER_DATA_SUBDIR, workspace_dir
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    user_data_dir.mkdir(parents=True, exist_ok=True)

    chromium = _chromium_executable()
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

    # Detach into its own process group so it survives the parent shell exit.
    popen_kwargs: dict = {
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
    except SystemExit:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
        raise

    session = {
        "id": uuid.uuid4().hex,
        "pid": proc.pid,
        "connectUrl": connect_url,
        "userDataDir": str(user_data_dir),
        "executablePath": chromium,
        "headless": bool(args.headless),
        "createdAt": int(time.time()),
    }
    out_path.write_text(json.dumps(session, indent=2) + "\n", encoding="utf-8")

    print(f"LB_SESSION_ID={session['id']}")
    print(f"LB_SESSION_PID={session['pid']}")
    print(f"LB_SESSION_FILE={out_path}")
    print(f"LB_CONNECT_URL={connect_url}")
    return 0


def _cmd_info(args: argparse.Namespace) -> int:
    session_path = _resolve_path(args.session_file, args.workspace_dir)
    if not session_path.exists():
        print(f"LB_INFO_MISSING file={session_path}")
        return 1
    session = json.loads(session_path.read_text(encoding="utf-8"))
    session["alive"] = _pid_alive(int(session.get("pid", 0)))
    print(json.dumps(session, indent=2))
    return 0


def _terminate_pid(pid: int, kill_timeout: float) -> str:
    if pid <= 0 or not _pid_alive(pid):
        return "not_running"
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return "already_gone"
    deadline = time.monotonic() + kill_timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return "terminated"
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return "already_gone"
    time.sleep(0.2)
    return "killed" if not _pid_alive(pid) else "still_alive"


def _cmd_release(args: argparse.Namespace) -> int:
    session_path = _resolve_path(args.session_file, args.workspace_dir)
    if not session_path.exists():
        print(f"LB_RELEASE_SKIPPED missing={session_path}")
        return 0
    session = json.loads(session_path.read_text(encoding="utf-8"))
    pid = int(session.get("pid", 0))
    status = _terminate_pid(pid, args.kill_timeout)
    print(f"LB_RELEASE_REQUESTED pid={pid} status={status}")

    if args.delete_user_data:
        udd = session.get("userDataDir", "")
        if udd and Path(udd).exists():
            try:
                shutil.rmtree(udd)
                print(f"LB_USER_DATA_DELETED {udd}")
            except OSError as exc:
                print(f"LB_USER_DATA_DELETE_FAILED {udd} {exc}")

    if args.delete_file:
        try:
            session_path.unlink()
            print(f"LB_SESSION_FILE_DELETED {session_path}")
        except OSError as exc:
            print(f"LB_SESSION_FILE_DELETE_FAILED {session_path} {exc}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m webwright.tools.persistent_local_browser",
        description="Manage a keep-alive local Chromium session shared across bash steps.",
    )
    parser.add_argument(
        "--workspace-dir",
        default="",
        help="Resolve --out / --session-file / --user-data-dir relative to this directory.",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Launch a detached Chromium and persist its connectUrl.")
    create.add_argument("--out", default=DEFAULT_SESSION_FILE, help="Where to write the session JSON.")
    create.add_argument(
        "--user-data-dir",
        default="",
        help=f"Per-session Chromium user-data-dir (default: <workspace>/{DEFAULT_USER_DATA_SUBDIR}).",
    )
    create.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Launch Chromium headless (default: True).",
    )
    create.add_argument(
        "--no-sandbox",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pass --no-sandbox (often required in containers/CI).",
    )
    create.add_argument("--window-width", type=int, default=1280)
    create.add_argument("--window-height", type=int, default=1800)
    create.add_argument(
        "--startup-timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for the DevTools URL to appear on stderr.",
    )
    create.add_argument(
        "--chromium-arg",
        action="append",
        default=[],
        help="Extra Chromium command-line argument; repeat for multiple.",
    )
    create.set_defaults(func=_cmd_create)

    info = sub.add_parser("info", help="Print the persisted session JSON and liveness.")
    info.add_argument("--session-file", default=DEFAULT_SESSION_FILE)
    info.set_defaults(func=_cmd_info)

    release = sub.add_parser("release", help="Terminate the persisted session.")
    release.add_argument("--session-file", default=DEFAULT_SESSION_FILE)
    release.add_argument(
        "--delete-file",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Also delete the session JSON file after release.",
    )
    release.add_argument(
        "--delete-user-data",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Also remove the per-session Chromium user-data-dir.",
    )
    release.add_argument(
        "--kill-timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for SIGTERM before sending SIGKILL.",
    )
    release.set_defaults(func=_cmd_release)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
