from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

_EXPORT_RE = re.compile(r"^export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class LocalWorkspaceEnvironmentConfig(BaseModel):
    """Shell-based workspace environment.

    The agent drives a real browser through bash commands it generates inside this
    workspace. Three browser modes are exposed to those generated scripts via
    environment variables:

    * ``browser_mode = "browserbase"`` (default): the agent's scripts should
      create a Browserbase cloud session. ``BROWSERBASE_API_KEY`` and
      ``BROWSERBASE_PROJECT_ID`` are forwarded if present.
    * ``browser_mode = "steel"``: the agent's scripts should create a Steel cloud
      session. ``STEEL_API_KEY`` is forwarded if present.
    * ``browser_mode = "local"``: the agent's scripts should launch a local
      Playwright browser (``playwright.chromium.launch(...)``).

    The selected mode is forwarded to the subprocess via ``BROWSER_MODE`` so the
    generated scripts can branch on it.
    """

    start_url: str | None = None
    output_dir: Path = Path("outputs/sandbox/default")
    command_timeout_seconds: int = 180
    shell: str = "/bin/bash"
    env: dict[str, str] = Field(default_factory=dict)
    credentials_file: Path | None = None
    browser_mode: str = "browserbase"  # "browserbase", "steel", or "local"
    task_metadata_filename: str = "task.json"
    final_script_name: str = "final_script.py"
    output_truncation_chars: int = 12000
    final_script_preview_chars: int = 4000
    recent_files_limit: int = 40


class LocalWorkspaceEnvironment:
    def __init__(self, *, config_class: type = LocalWorkspaceEnvironmentConfig, **kwargs):
        self.config = config_class(**kwargs)
        self.config.output_dir = self.config.output_dir.expanduser()
        self._step_index = 0
        self._prepared_task: dict[str, Any] = {}
        self._credential_env = self._load_credential_env(self.config.credentials_file)

    def _load_credential_env(self, path: Path | None) -> dict[str, str]:
        if path is None:
            return {}
        resolved = Path(path).expanduser()
        if not resolved.exists():
            return {}

        parsed: dict[str, str] = {}
        for raw_line in resolved.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = _EXPORT_RE.match(line)
            if match is None:
                continue
            key, raw_value = match.groups()
            value = raw_value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            parsed[key] = value
        return parsed

    def _workspace_dir(self) -> Path:
        return self.config.output_dir.resolve()

    def _resolve_cwd(self, cwd: str = "") -> Path:
        workspace_dir = self._workspace_dir()
        if not cwd:
            return workspace_dir
        candidate = Path(cwd)
        if not candidate.is_absolute():
            candidate = workspace_dir / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(workspace_dir)
        except ValueError as exc:
            raise ValueError(f"Command cwd must stay inside workspace: {resolved}") from exc
        return resolved

    def _task_metadata_path(self) -> Path:
        return self._workspace_dir() / self.config.task_metadata_filename

    def _final_script_path(self) -> Path:
        return self._workspace_dir() / self.config.final_script_name

    def _steps_dir(self) -> Path:
        return self._workspace_dir() / "steps"

    def _logs_dir(self) -> Path:
        return self._workspace_dir() / "logs"

    def _screenshots_dir(self) -> Path:
        return self._workspace_dir() / "screenshots"

    def _history_path(self) -> Path:
        return self._workspace_dir() / "command_history.sh"

    def _truncate(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        omitted = len(text) - limit
        return f"{text[:limit]}\n\n... [{omitted} characters omitted]"

    def _recent_workspace_files(self) -> list[str]:
        workspace_dir = self._workspace_dir()
        files: list[Path] = [path for path in workspace_dir.rglob("*") if path.is_file()]
        files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        recent = files[: self.config.recent_files_limit]
        return [str(path.relative_to(workspace_dir)) for path in recent]

    def _recent_screenshots(self) -> list[Path]:
        screenshots_dir = self._screenshots_dir()
        if not screenshots_dir.exists():
            return []
        files = [path for path in screenshots_dir.rglob("*") if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES]
        files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return files

    def _persist_step_command(self, command: str) -> Path:
        self._steps_dir().mkdir(parents=True, exist_ok=True)
        step_path = self._steps_dir() / f"step_{self._step_index:04d}.sh"
        step_path.write_text(command.rstrip() + "\n", encoding="utf-8")

        history_path = self._history_path()
        with history_path.open("a", encoding="utf-8") as handle:
            handle.write(f"# Step {self._step_index}\n")
            handle.write(command.rstrip())
            handle.write("\n\n")
        return step_path

    def _write_step_log(self, output: str) -> Path | None:
        if not output:
            return None
        self._logs_dir().mkdir(parents=True, exist_ok=True)
        log_path = self._logs_dir() / f"step_{self._step_index:04d}.log"
        log_path.write_text(output, encoding="utf-8")
        return log_path

    def prepare(self, **kwargs) -> None:
        self._prepared_task = dict(kwargs)
        self._step_index = 0
        start_url = kwargs.get("start_url") or self.config.start_url
        if start_url:
            self.config.start_url = str(start_url)
        workspace_dir = self._workspace_dir()
        workspace_dir.mkdir(parents=True, exist_ok=True)
        self._steps_dir().mkdir(parents=True, exist_ok=True)
        self._logs_dir().mkdir(parents=True, exist_ok=True)
        self._screenshots_dir().mkdir(parents=True, exist_ok=True)
        (workspace_dir / ".tmp").mkdir(parents=True, exist_ok=True)
        self._task_metadata_path().write_text(json.dumps(kwargs, indent=2), encoding="utf-8")

    def _browser_env(self) -> dict[str, str]:
        """Forward cloud-browser / browser-mode hints to the subprocess."""
        env: dict[str, str] = {"BROWSER_MODE": str(self.config.browser_mode or "browserbase")}
        for var in ("BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID", "STEEL_API_KEY"):
            value = self._credential_env.get(var) or os.environ.get(var)
            if value:
                env[var] = value
        return env

    def execute(self, action: dict[str, Any], cwd: str = "") -> dict[str, Any]:
        self._step_index += 1
        command = str(
            action.get("command") or action.get("bash_command") or action.get("python_code") or ""
        ).strip()
        self._persist_step_command(command)
        resolved_cwd = self._resolve_cwd(cwd)

        command_env = os.environ | self._credential_env | self._browser_env() | self.config.env | {
            "WORKSPACE_DIR": str(self._workspace_dir()),
            "OM2W_TASK_JSON": str(self._task_metadata_path()),
            "FINAL_SCRIPT_PATH": str(self._final_script_path()),
            "TMPDIR": str(self._workspace_dir() / ".tmp"),
        }

        try:
            result = subprocess.run(
                command,
                shell=True,
                executable=self.config.shell,
                text=True,
                cwd=resolved_cwd,
                env=command_env,
                timeout=self.config.command_timeout_seconds,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            output = result.stdout
            returncode = result.returncode
            exception_info = ""
        except Exception as exc:
            raw_output = getattr(exc, "output", None)
            output = raw_output.decode("utf-8", errors="replace") if isinstance(raw_output, bytes) else (raw_output or "")
            returncode = -1
            exception_info = f"An error occurred while executing the command: {exc}"

        log_path = self._write_step_log(output)
        observation = self._capture_observation(
            command=command,
            cwd=resolved_cwd,
            output=output,
            returncode=returncode,
            exception_info=exception_info,
            log_path=log_path,
        )
        return {
            "output": output,
            "returncode": returncode,
            "exception_info": exception_info,
            "observation": observation,
        }

    def _capture_observation(
        self,
        *,
        command: str,
        cwd: Path,
        output: str,
        returncode: int,
        exception_info: str,
        log_path: Path | None,
    ) -> dict[str, Any]:
        final_script_path = self._final_script_path()
        recent_screenshot_paths = self._recent_screenshots()
        latest_screenshot = recent_screenshot_paths[0] if recent_screenshot_paths else None
        final_script_preview = ""
        if final_script_path.exists():
            final_script_preview = self._truncate(
                final_script_path.read_text(encoding="utf-8", errors="replace"),
                self.config.final_script_preview_chars,
            )

        workspace_dir = self._workspace_dir()
        recent_screenshots = [str(path.relative_to(workspace_dir)) for path in recent_screenshot_paths[:10]]
        return {
            "success": returncode == 0 and not exception_info,
            "exception": exception_info,
            "command": command,
            "returncode": returncode,
            "workspace_dir": str(workspace_dir),
            "cwd": str(cwd),
            "url": self.config.start_url or "",
            "title": "",
            "aria_snapshot": "",
            "console_output": "",
            "recent_console": "",
            "command_output": self._truncate(output, self.config.output_truncation_chars),
            "log_path": str(log_path) if log_path is not None else "",
            "task_metadata_path": str(self._task_metadata_path()),
            "final_script_path": str(final_script_path) if final_script_path.exists() else "",
            "final_script_exists": final_script_path.exists(),
            "final_script_preview": final_script_preview,
            "screenshot_path": str(latest_screenshot) if latest_screenshot is not None else "",
            "recent_screenshots": recent_screenshots,
            "workspace_files": self._recent_workspace_files(),
        }

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        return {
            "start_url": self.config.start_url or "",
            "output_dir": str(self._workspace_dir()),
            "workspace_dir": str(self._workspace_dir()),
            "task_metadata_path": str(self._task_metadata_path()),
            "final_script_path": str(self._final_script_path()),
            "browser_mode": self.config.browser_mode,
            **kwargs,
        }

    def serialize(self) -> dict:
        return {
            "environment": {
                "config": self.config.model_dump(mode="json"),
                "environment_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                "workspace_dir": str(self._workspace_dir()),
            }
        }

    def close(self) -> None:
        return None
