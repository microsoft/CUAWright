from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import ProxyHandler, Request, build_opener

from pydantic import BaseModel, Field, field_validator

_BROWSER_MODES = {"local_cdp", "local_launch", "local_persistent"}
_DEFAULT_LOCAL_CDP_URL = "http://127.0.0.1:9222"
_DEFAULT_LOCAL_CDP_USER_DATA_DIR = Path("~/.cache/webwright/edge-profile")
_CHROMIUM_EXECUTABLE_CANDIDATES = (
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "~/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "google-chrome-stable",
    "chromium-browser",
    "chromium",
    "chrome",
)
_LOCAL_CDP_OPENER = build_opener(ProxyHandler({}))


def _urlopen_local_cdp(url_or_request: str | Request, *, timeout: float):
    return _LOCAL_CDP_OPENER.open(url_or_request, timeout=timeout)


def _local_cdp_origin(cdp_url: str) -> str:
    parsed = urlparse(cdp_url or _DEFAULT_LOCAL_CDP_URL)
    scheme = parsed.scheme or "http"
    netloc = parsed.netloc or parsed.path
    if not netloc:
        netloc = urlparse(_DEFAULT_LOCAL_CDP_URL).netloc
    return f"{scheme}://{netloc}"


def _local_cdp_port(cdp_url: str) -> int:
    parsed = urlparse(cdp_url or _DEFAULT_LOCAL_CDP_URL)
    if parsed.port is not None:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80


def _is_local_cdp_available(cdp_url: str, *, timeout_seconds: float = 0.5) -> bool:
    try:
        with _urlopen_local_cdp(
            f"{_local_cdp_origin(cdp_url).rstrip('/')}/json/version",
            timeout=timeout_seconds,
        ) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def _local_cdp_json_url(cdp_url: str, path: str) -> str:
    return f"{_local_cdp_origin(cdp_url).rstrip('/')}{path}"


def _local_cdp_page_targets(cdp_url: str, *, timeout_seconds: float = 0.5) -> list[dict[str, Any]]:
    try:
        with _urlopen_local_cdp(
            _local_cdp_json_url(cdp_url, "/json/list"),
            timeout=timeout_seconds,
        ) as response:
            if not 200 <= response.status < 300:
                return []
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    return [
        target
        for target in payload
        if isinstance(target, dict) and target.get("type") == "page"
    ]


def _ensure_local_cdp_page_target(cdp_url: str, *, timeout_seconds: float = 1.0) -> None:
    if _local_cdp_page_targets(cdp_url, timeout_seconds=timeout_seconds):
        return

    target_url = f"{_local_cdp_json_url(cdp_url, '/json/new')}?{quote('about:blank', safe='')}"
    request = Request(target_url, method="PUT")
    with _urlopen_local_cdp(request, timeout=timeout_seconds) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"Could not create a local CDP page target: {cdp_url}")


def _resolve_local_cdp_url(configured_url: str, *, explicit: bool) -> str:
    if explicit and configured_url:
        return configured_url
    return (
        os.environ.get("LOCAL_BROWSER_CDP_URL")
        or os.environ.get("BROWSER_CDP_URL")
        or configured_url
        or _DEFAULT_LOCAL_CDP_URL
    )


def _resolve_user_data_dir(configured_dir: Path, *, explicit: bool) -> Path:
    if explicit:
        return configured_dir
    env_dir = os.environ.get("LOCAL_BROWSER_USER_DATA_DIR") or os.environ.get("BROWSER_USER_DATA_DIR")
    return Path(env_dir).expanduser() if env_dir else _DEFAULT_LOCAL_CDP_USER_DATA_DIR.expanduser()


def _find_chromium_executable(explicit_path: str = "") -> str:
    candidates = []
    if explicit_path:
        candidates.append(explicit_path)
    candidates.extend(
        value
        for value in (os.environ.get("LOCAL_BROWSER_EXECUTABLE"), os.environ.get("BROWSER_EXECUTABLE"))
        if value
    )
    candidates.extend(_CHROMIUM_EXECUTABLE_CANDIDATES)

    for candidate in candidates:
        expanded = os.path.expanduser(candidate)
        if Path(expanded).exists():
            return expanded
        resolved = shutil.which(expanded)
        if resolved:
            return resolved
    raise FileNotFoundError(
        "Could not find Chrome/Chromium. Set local_cdp_executable or LOCAL_BROWSER_EXECUTABLE."
    )


def _macos_open_app_name(executable: str) -> str:
    if sys.platform != "darwin":
        return ""
    if "/Microsoft Edge.app/" in executable:
        return "Microsoft Edge"
    if "/Google Chrome.app/" in executable:
        return "Google Chrome"
    return ""


class LocalBrowserEnvironmentConfig(BaseModel):
    start_url: str | None = None
    browser_mode: str = "local_launch"
    headless: bool = False
    devtools: bool = False
    keep_open_on_exit: bool = False
    prompt_before_close: bool = False
    slow_mo_ms: int = 50
    browser_width: int = 1280
    browser_height: int = 1440
    browser_timeout_ms: int = 10000
    browser_navigation_timeout_ms: int = 30000
    step_execution_timeout_ms: int = 20000
    observation_timeout_ms: int = 5000
    output_dir: Path = Path("outputs/default")
    user_data_dir: Path = _DEFAULT_LOCAL_CDP_USER_DATA_DIR
    launch_args: list[str] = Field(default_factory=list)
    local_cdp_url: str = _DEFAULT_LOCAL_CDP_URL
    local_cdp_new_page: bool = True
    local_cdp_close_page_on_exit: bool = False
    local_cdp_auto_start: bool = True
    local_cdp_executable: str = ""
    local_cdp_startup_timeout_seconds: float = 10
    local_cdp_close_started_browser_on_exit: bool = False

    @field_validator("browser_mode")
    @classmethod
    def validate_browser_mode(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_")
        if normalized not in _BROWSER_MODES:
            raise ValueError(
                f"browser_mode must be one of: {', '.join(sorted(_BROWSER_MODES))}"
            )
        return normalized


class LocalBrowserEnvironment:
    """Live local Playwright browser environment.

    The environment owns the browser/page and executes each model action as an async
    Python snippet with ``page``, ``context``, ``browser``, ``playwright``, and
    ``task`` already available.
    """

    def __init__(self, *, config_class: type = LocalBrowserEnvironmentConfig, **kwargs):
        self.config = config_class(**kwargs)
        fields_set = getattr(self.config, "model_fields_set", None)
        if fields_set is None:
            fields_set = getattr(self.config, "__fields_set__", set())
        self._config_fields_set = set(fields_set)
        self.config.local_cdp_url = _resolve_local_cdp_url(
            self.config.local_cdp_url,
            explicit="local_cdp_url" in self._config_fields_set,
        )
        self.config.output_dir = self.config.output_dir.expanduser()
        self.config.user_data_dir = _resolve_user_data_dir(
            self.config.user_data_dir,
            explicit="user_data_dir" in self._config_fields_set,
        )
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._local_cdp_page = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._local_cdp_process: subprocess.Popen | None = None
        self._connected_over_cdp = False
        self._step_index = 0
        self._prepared_task: dict[str, Any] = {}
        self._console_history: list[str] = []
        self._step_console: list[str] = []
        self._step_python_code = ""
        self._step_python_output = ""

    def _screenshots_dir(self) -> Path:
        return self.config.output_dir / "screenshots"

    def _steps_dir(self) -> Path:
        return self.config.output_dir / "steps"

    def prepare(self, **kwargs) -> None:
        self._prepared_task = dict(kwargs)
        self._step_index = 0
        self._console_history = []
        self._step_console = []
        start_url = kwargs.get("start_url") or self.config.start_url
        if start_url:
            self.config.start_url = str(start_url)

        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        self._steps_dir().mkdir(parents=True, exist_ok=True)
        self._screenshots_dir().mkdir(parents=True, exist_ok=True)
        (self.config.output_dir / "task.json").write_text(
            json.dumps(kwargs, indent=2),
            encoding="utf-8",
        )
        self._run(self._prepare_async())

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _run(self, coro):
        loop = self._ensure_loop()
        return loop.run_until_complete(coro)

    def _ensure_local_cdp_browser(self) -> None:
        if _is_local_cdp_available(self.config.local_cdp_url):
            return
        if not self.config.local_cdp_auto_start:
            raise RuntimeError(
                "Local CDP endpoint is not reachable. Start Chrome/Chromium with "
                f"remote debugging enabled for {self.config.local_cdp_url}."
            )

        self.config.user_data_dir.mkdir(parents=True, exist_ok=True)
        browser_args = list(self.config.launch_args)
        executable = _find_chromium_executable(self.config.local_cdp_executable)
        browser_flags = [
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={_local_cdp_port(self.config.local_cdp_url)}",
            f"--user-data-dir={self.config.user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            *browser_args,
        ]
        app_name = _macos_open_app_name(executable)
        launched_with_open = bool(app_name)
        command = (
            ["open", "-na", app_name, "--args", *browser_flags]
            if launched_with_open
            else [executable, *browser_flags]
        )
        self._local_cdp_process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        deadline = time.monotonic() + self.config.local_cdp_startup_timeout_seconds
        while time.monotonic() < deadline:
            if not launched_with_open and self._local_cdp_process.poll() is not None:
                raise RuntimeError(
                    f"Chrome/Chromium exited before CDP became available: {self.config.local_cdp_url}"
                )
            if _is_local_cdp_available(self.config.local_cdp_url):
                return
            time.sleep(0.2)

        self._local_cdp_process.terminate()
        self._local_cdp_process = None
        raise TimeoutError(f"Timed out waiting for local CDP endpoint: {self.config.local_cdp_url}")

    async def _prepare_async(self) -> None:
        from playwright.async_api import async_playwright

        if self._page is not None and self._context is not None:
            return

        self._playwright = await async_playwright().start()
        chromium = self._playwright.chromium
        launch_args = list(self.config.launch_args)
        if self.config.devtools:
            launch_args.append("--auto-open-devtools-for-tabs")
        launch_kwargs = {
            "headless": self.config.headless,
            "slow_mo": self.config.slow_mo_ms,
            "args": launch_args,
        }

        if self.config.browser_mode == "local_cdp":
            self._ensure_local_cdp_browser()
            _ensure_local_cdp_page_target(self.config.local_cdp_url)
            self._browser = await chromium.connect_over_cdp(self.config.local_cdp_url)
            self._connected_over_cdp = True
            self._context = (
                self._browser.contexts[0]
                if self._browser.contexts
                else await self._browser.new_context(no_viewport=True)
            )
            if self.config.local_cdp_new_page or not self._context.pages:
                self._page = await self._context.new_page()
                self._local_cdp_page = self._page
            else:
                self._page = self._context.pages[0]
        elif self.config.browser_mode == "local_persistent":
            self.config.user_data_dir.mkdir(parents=True, exist_ok=True)
            self._context = await chromium.launch_persistent_context(
                user_data_dir=str(self.config.user_data_dir),
                viewport={
                    "width": self.config.browser_width,
                    "height": self.config.browser_height,
                },
                **launch_kwargs,
            )
            self._browser = self._context.browser
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        else:
            self._browser = await chromium.launch(**launch_kwargs)
            self._context = await self._browser.new_context(
                viewport={
                    "width": self.config.browser_width,
                    "height": self.config.browser_height,
                }
            )
            self._page = await self._context.new_page()

        self._context.set_default_timeout(self.config.browser_timeout_ms)
        self._context.set_default_navigation_timeout(self.config.browser_navigation_timeout_ms)
        self._attach_page_listeners(self._page)
        if self.config.start_url:
            await self._page.goto(self.config.start_url, wait_until="domcontentloaded")

    def _attach_page_listeners(self, page: Any) -> None:
        page.on("console", self._on_console_message)
        page.on("pageerror", self._on_page_error)

    def _on_console_message(self, message: Any) -> None:
        text = getattr(message, "text", "")
        if callable(text):
            text = text()
        line = str(text)
        self._console_history.append(line)
        self._step_console.append(line)

    def _on_page_error(self, error: Any) -> None:
        line = f"Page error: {error}"
        self._console_history.append(line)
        self._step_console.append(line)

    def execute(self, action: dict[str, Any], cwd: str = "") -> dict[str, Any]:
        del cwd
        return self._run(self._execute_async(action))

    async def _execute_async(self, action: dict[str, Any]) -> dict[str, Any]:
        self._step_index += 1
        self._step_console = []
        self._step_python_output = ""
        self._step_python_code = str(action.get("python_code", "") or "")
        self._persist_step_code(self._step_python_code)

        success = True
        exception_text = ""
        try:
            if self._step_python_code.strip():
                await asyncio.wait_for(
                    self._run_python_code(self._step_python_code),
                    timeout=self.config.step_execution_timeout_ms / 1000,
                )
            await self._wait_for_observation_ready()
        except Exception:
            success = False
            exception_text = traceback.format_exc()

        observation = await self._capture_observation(
            success=success,
            exception_text=exception_text,
        )
        return {
            "output": self._step_python_output,
            "returncode": 0 if success else 1,
            "exception_info": exception_text,
            "observation": observation,
        }

    def _persist_step_code(self, python_code: str) -> None:
        step_path = self._steps_dir() / f"step_{self._step_index:04d}.py"
        step_path.write_text(python_code, encoding="utf-8")

        script_path = self.config.output_dir / "script.py"
        with script_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n\n# Step {self._step_index}\n")
            handle.write(python_code)
            handle.write("\n")

    async def _run_python_code(self, python_code: str) -> None:
        if self._page is None or self._context is None or self._playwright is None:
            raise RuntimeError("Browser environment was not prepared.")

        buffer = io.StringIO()
        globals_dict: dict[str, Any] = {"asyncio": asyncio}
        locals_dict: dict[str, Any] = {}
        wrapped = "async def __agent_step__(page, context, browser, playwright, task):\n"
        wrapped += textwrap.indent(python_code, "    ")
        with redirect_stdout(buffer), redirect_stderr(buffer):
            exec(wrapped, globals_dict, locals_dict)
            await locals_dict["__agent_step__"](
                self._page,
                self._context,
                self._browser,
                self._playwright,
                self._prepared_task,
            )
        self._step_python_output = buffer.getvalue()

    async def _wait_for_observation_ready(self) -> None:
        if self._page is None:
            return
        try:
            await self._page.wait_for_load_state(
                "domcontentloaded",
                timeout=self.config.observation_timeout_ms,
            )
        except Exception:
            pass

    async def _capture_observation(self, *, success: bool, exception_text: str) -> dict[str, Any]:
        page = self._page
        url = ""
        title = ""
        aria_snapshot = ""
        screenshot_path: Path | None = None

        if page is not None:
            try:
                url = page.url
            except Exception:
                url = self.config.start_url or ""
            try:
                title = await page.title()
            except Exception:
                title = ""
            try:
                aria_snapshot = await page.locator("body").aria_snapshot(
                    timeout=self.config.observation_timeout_ms,
                )
            except Exception:
                aria_snapshot = ""
            try:
                screenshot_path = self._screenshots_dir() / f"step_{self._step_index:04d}.png"
                await page.screenshot(path=str(screenshot_path), full_page=False)
            except Exception:
                screenshot_path = None

        return {
            "success": success,
            "exception": exception_text,
            "url": url or self.config.start_url or "",
            "title": title,
            "screenshot_path": str(screenshot_path) if screenshot_path is not None else "",
            "aria_snapshot": aria_snapshot,
            "python_code": self._step_python_code,
            "python_output": self._step_python_output,
            "console_output": "\n".join(self._step_console[-20:]),
            "recent_console": "\n".join(self._console_history[-50:]),
        }

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        return {
            "start_url": self.config.start_url or "",
            "output_dir": str(self.config.output_dir.resolve()),
            "browser_mode": self.config.browser_mode,
            "user_data_dir": str(self.config.user_data_dir),
            **kwargs,
        }

    def serialize(self) -> dict[str, Any]:
        return {
            "environment": {
                "config": self.config.model_dump(mode="json"),
                "environment_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
            }
        }

    def close(self) -> None:
        if self.config.prompt_before_close:
            input("Press Enter to close the browser...")
        if self.config.keep_open_on_exit:
            return
        try:
            self._run(self._close_async())
        finally:
            if self._loop is not None and not self._loop.is_closed():
                self._loop.close()
            self._loop = None

    async def _close_async(self) -> None:

        context = self._context
        browser = self._browser
        page = self._local_cdp_page
        playwright = self._playwright
        connected_over_cdp = self._connected_over_cdp
        local_cdp_process = self._local_cdp_process
        self._page = None
        self._local_cdp_page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._connected_over_cdp = False
        self._local_cdp_process = None

        try:
            if connected_over_cdp:
                if page is not None and self.config.local_cdp_close_page_on_exit:
                    try:
                        await page.close()
                    except Exception:
                        pass
                if (
                    local_cdp_process is not None
                    and self.config.local_cdp_close_started_browser_on_exit
                ):
                    local_cdp_process.terminate()
            elif context is not None:
                await context.close()
            elif browser is not None:
                await browser.close()
        finally:
            if playwright is not None:
                await playwright.stop()
