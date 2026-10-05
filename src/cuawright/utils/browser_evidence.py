from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


DEFAULT_BROWSER_STEPS_FILE = "browser-steps.jsonl"
TASK_SCREENSHOT_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def browser_steps_path(
    workspace_dir: str | Path,
    manifest: str | Path = DEFAULT_BROWSER_STEPS_FILE,
) -> Path:
    path = Path(manifest)
    if not path.is_absolute():
        path = Path(workspace_dir) / path
    return path.resolve()


def load_browser_steps(
    workspace_dir: str | Path,
    manifest: str | Path = DEFAULT_BROWSER_STEPS_FILE,
) -> list[dict[str, Any]]:
    rows = read_jsonl(browser_steps_path(workspace_dir, manifest))
    return sorted(
        rows,
        key=lambda row: (
            int(row.get("browser_step") or 0),
            int(row.get("agent_step") or 0),
        ),
    )


def resolve_workspace_path(workspace_dir: str | Path, value: str | Path) -> Path:
    workspace = Path(workspace_dir).resolve()
    path = Path(value)
    if not path.is_absolute():
        path = workspace / path
    return path.resolve()


def relative_workspace_path(workspace_dir: str | Path, value: str | Path) -> str:
    workspace = Path(workspace_dir).resolve()
    path = resolve_workspace_path(workspace, value)
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return str(path)


def trajectory_images(
    workspace_dir: str | Path,
    rows: Iterable[dict[str, Any]],
) -> list[tuple[Path, dict[str, Any]]]:
    images: list[tuple[Path, dict[str, Any]]] = []
    seen: set[Path] = set()
    for row in rows:
        raw_path = row.get("screenshot_path")
        if not raw_path:
            continue
        path = resolve_workspace_path(workspace_dir, str(raw_path))
        if not path.is_file() or path in seen:
            continue
        seen.add(path)
        images.append((path, row))
    return images


def task_screenshot_paths(workspace_dir: str | Path) -> list[Path]:
    """Return every supported image directly under the task's screenshots folder."""
    screenshots_dir = Path(workspace_dir).resolve() / "screenshots"
    if not screenshots_dir.is_dir():
        return []
    return sorted(
        (
            path.resolve()
            for path in screenshots_dir.iterdir()
            if path.is_file() and path.suffix.lower() in TASK_SCREENSHOT_SUFFIXES
        ),
        key=lambda path: (path.name.casefold(), path.name),
    )


def format_action_history(rows: Iterable[dict[str, Any]]) -> str:
    lines: list[str] = []
    for row in rows:
        browser_step = int(row.get("browser_step") or 0)
        agent_step = int(row.get("agent_step") or 0)
        epoch = int(row.get("session_epoch") or 0)
        action = str(row.get("action") or "").strip() or "(no action description)"
        status = "success" if row.get("success") else "error"
        details = [
            f"browser step {browser_step}",
            f"agent step {agent_step}",
            f"session epoch {epoch}",
            f"status {status}",
        ]
        error_kind = str(row.get("error_kind") or "").strip()
        if error_kind:
            details.append(f"error kind {error_kind}")
        url_after = str(row.get("url_after") or "").strip()
        if url_after:
            details.append(f"url {url_after}")
        lines.append(f"- {', '.join(details)}: {action}")
    return "\n".join(lines)


def image_context(row: dict[str, Any]) -> str:
    return (
        "Trajectory context for this screenshot:\n"
        f"- Browser step: {int(row.get('browser_step') or 0)}\n"
        f"- Agent step: {int(row.get('agent_step') or 0)}\n"
        f"- Session epoch: {int(row.get('session_epoch') or 0)}\n"
        f"- Action: {str(row.get('action') or '').strip()}\n"
        f"- Step status: {'success' if row.get('success') else 'error'}\n"
        f"- URL after step: {str(row.get('url_after') or '').strip()}"
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def trajectory_evidence_digest(
    workspace_dir: str | Path,
    rows: Iterable[dict[str, Any]],
) -> str:
    workspace = Path(workspace_dir).resolve()
    canonical_rows: list[dict[str, Any]] = []
    for row in rows:
        screenshot_path = str(row.get("screenshot_path") or "")
        screenshot_sha256 = ""
        if screenshot_path:
            resolved = resolve_workspace_path(workspace, screenshot_path)
            if resolved.is_file():
                screenshot_sha256 = _file_sha256(resolved)
        canonical_rows.append(
            {
                "browser_step": int(row.get("browser_step") or 0),
                "agent_step": int(row.get("agent_step") or 0),
                "session_epoch": int(row.get("session_epoch") or 0),
                "action": str(row.get("action") or ""),
                "code_sha256": str(row.get("code_sha256") or ""),
                "success": bool(row.get("success")),
                "url_after": str(row.get("url_after") or ""),
                "title": str(row.get("title") or ""),
                "screenshot_path": relative_workspace_path(workspace, screenshot_path)
                if screenshot_path
                else "",
                "screenshot_sha256": screenshot_sha256,
                "error_kind": str(row.get("error_kind") or ""),
                "error": str(row.get("error") or ""),
            }
        )
    canonical_task_screenshots = [
        {
            "path": relative_workspace_path(workspace, path),
            "sha256": _file_sha256(path),
        }
        for path in task_screenshot_paths(workspace)
    ]
    serialized = json.dumps(
        {
            "rows": canonical_rows,
            "task_screenshots": canonical_task_screenshots,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def optional_file_digest(path: Path) -> str:
    return _file_sha256(path) if path.is_file() else ""


__all__ = [
    "DEFAULT_BROWSER_STEPS_FILE",
    "append_jsonl",
    "browser_steps_path",
    "format_action_history",
    "image_context",
    "load_browser_steps",
    "optional_file_digest",
    "read_jsonl",
    "relative_workspace_path",
    "resolve_workspace_path",
    "task_screenshot_paths",
    "trajectory_evidence_digest",
    "trajectory_images",
]
