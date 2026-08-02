from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from importlib.util import find_spec
from pathlib import Path
from rich.console import Console
from rich.table import Table

console = Console()

# Engines Webwright actually uses. Firefox first: the skill contract pins it
# because Akamai-fronted sites reject Playwright Chromium with
# ERR_HTTP2_PROTOCOL_ERROR on TLS/H2 fingerprinting.
BROWSER_ENGINES = ("firefox", "chromium")

# Every model backend shipped under webwright/models/.
MODEL_BACKEND_ENV_VARS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY")


def check_python():
    version = sys.version_info

    if version >= (3, 10):
        return True, f"Python {version.major}.{version.minor}"

    return False, ("Python 3.10+ required\nFix: install Python 3.10 or newer")


def check_playwright():
    if find_spec("playwright") is not None:
        return True, "playwright installed"

    return False, ("playwright not installed\nFix: pip install playwright")


def _parse_install_locations(output: str) -> dict[str, Path]:
    """Map each Playwright browser key to the directory it installs into.

    ``playwright install --dry-run`` prints a header line per browser followed
    by an indented ``Install location:`` line, and it does so whether or not the
    browser is actually present on disk.
    """
    locations: dict[str, Path] = {}
    current: str | None = None

    for line in output.splitlines():
        header = re.search(r"\(playwright ([a-z0-9-]+) v[^)]+\)", line)

        if header:
            current = header.group(1)
            continue

        if current and "Install location:" in line:
            locations[current] = Path(line.split("Install location:", 1)[1].strip())
            current = None

    return locations


def check_browsers():
    """Verify that a Playwright engine is actually present on disk.

    Two bugs here previously. The command was invoked as a bare ``playwright``
    console script, which Windows cannot resolve -- surfacing a raw
    ``[WinError 2]`` instead of an actionable message. And the check only looked
    at the return code, which is 0 even when no browser is installed, so it
    could never fail for the right reason. Parse the reported install locations
    and stat them instead.
    """
    if find_spec("playwright") is None:
        return False, ("playwright not installed\nFix: pip install playwright")

    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "--dry-run"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as e:
        return False, (
            f"unable to query the Playwright driver: {e}\n"
            "Fix: pip install playwright"
        )

    if result.returncode != 0:
        return False, (
            "playwright driver unavailable\nFix: pip install playwright"
        )

    locations = _parse_install_locations(result.stdout)

    if not locations:
        return False, (
            "could not parse 'playwright install --dry-run' output\n"
            "Fix: playwright install firefox"
        )

    found = [
        engine
        for engine in BROWSER_ENGINES
        if engine in locations and locations[engine].exists()
    ]
    missing = [engine for engine in BROWSER_ENGINES if engine not in found]

    if found:
        detail = f"{', '.join(found)} available"

        if missing:
            detail += f" (not installed: {', '.join(missing)})"

        return True, detail

    return False, (
        "no Playwright browsers installed\nFix: playwright install firefox"
    )


def check_screenshot():
    """Validate real rendering with the engine the skill actually mandates.

    This previously hard-coded Chromium, so a correctly-provisioned Firefox-only
    setup -- which is what ``skills/webwright/reference/playwright_patterns.md``
    tells users to install -- was reported as FAIL. It also wrote
    ``doctor_test.png`` into the caller's working directory; use a temp dir.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, ("playwright not installed\nFix: pip install playwright")

    errors: list[str] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        screenshot_path = Path(tmpdir) / "doctor_test.png"

        try:
            with sync_playwright() as p:
                for engine in BROWSER_ENGINES:
                    try:
                        browser = getattr(p, engine).launch(headless=True)
                    except Exception as e:
                        errors.append(f"{engine}: {type(e).__name__}")
                        continue

                    try:
                        page = browser.new_page()

                        page.set_content("<h1>Webwright Doctor</h1>")

                        page.screenshot(path=str(screenshot_path))
                    finally:
                        browser.close()

                    if screenshot_path.exists():
                        return True, f"screenshot capture working ({engine})"

                    errors.append(f"{engine}: screenshot file was not created")
        except Exception as e:
            errors.append(f"driver: {type(e).__name__}")

    detail = f" [{'; '.join(errors)}]" if errors else ""

    return False, (
        f"unable to capture a screenshot with any installed browser{detail}\n"
        "Fix: playwright install firefox"
    )


def check_model_backend():
    """Accept any backend Webwright ships, and treat plugin mode as keyless.

    Hard-failing on a missing ``OPENAI_API_KEY`` reported FAIL for two entirely
    valid configurations: an Anthropic/OpenRouter CLI run, and the Claude Code /
    Codex plugin path, which needs no key because the host agent drives the loop.
    """
    present = [name for name in MODEL_BACKEND_ENV_VARS if os.getenv(name)]

    if present:
        return True, f"{', '.join(present)} found"

    return False, (
        f"no model API key found (checked {', '.join(MODEL_BACKEND_ENV_VARS)})\n"
        "Fix: set one for CLI mode -- not required when running Webwright as a "
        "Claude Code / Codex plugin"
    )


def _find_manifest_root(start: Path | None = None) -> Path | None:
    """Walk upward from ``start`` looking for the plugin manifest directories.

    The manifests were resolved against the process working directory, so
    ``webwright doctor`` reported FAIL from anywhere but the repo root.
    """
    current = (start or Path.cwd()).resolve()

    for candidate in (current, *current.parents):
        if (candidate / ".claude-plugin").is_dir() or (candidate / ".codex-plugin").is_dir():
            return candidate

    return None


def check_plugin_manifests():
    root = _find_manifest_root()

    if root is None:
        return False, (
            "missing plugin manifests: Claude, Codex\n"
            "Fix: run doctor from inside the Webwright repo, or configure "
            "Claude/Codex plugins"
        )

    missing = [
        label
        for label, relative in (
            ("Claude", ".claude-plugin/plugin.json"),
            ("Codex", ".codex-plugin/plugin.json"),
        )
        if not (root / relative).is_file()
    ]

    if not missing:
        return True, f"plugin manifests found ({root})"

    return False, (
        f"missing plugin manifests: {', '.join(missing)}\n"
        "Fix: configure Claude/Codex plugins"
    )


CHECKS = [
    ("Python", check_python),
    ("Playwright", check_playwright),
    ("Browsers", check_browsers),
    ("Screenshot", check_screenshot),
    ("Model Backend", check_model_backend),
    ("Plugins", check_plugin_manifests),
]


def run_doctor():
    table = Table(title="Webwright Doctor")

    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Details")

    passed = 0

    for name, fn in CHECKS:
        ok, message = fn()

        status = "PASS" if ok else "FAIL"

        table.add_row(
            name,
            status,
            message,
        )

        if ok:
            passed += 1

    console.print(table)

    console.print(f"\n{passed}/{len(CHECKS)} checks passed")


if __name__ == "__main__":
    run_doctor()
