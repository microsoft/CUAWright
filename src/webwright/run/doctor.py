from __future__ import annotations

import os
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path
from rich.console import Console
from rich.table import Table

console = Console()


def check_python():
    version = sys.version_info

    if version >= (3, 10):
        return True, f"Python {version.major}.{version.minor}"

    return False, ("Python 3.10+ required\nFix: install Python 3.10 or newer")


def check_playwright():
    if find_spec("playwright") is not None:
        return True, "playwright installed"

    return False, ("playwright not installed\nFix: pip install playwright")


def check_chromium():
    try:
        result = subprocess.run(
            ["playwright", "install", "--dry-run"],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            return True, "chromium available"

        return False, ("chromium missing\nFix: playwright install chromium")

    except Exception as e:
        return False, str(e)


def check_screenshot():
    try:
        from playwright.sync_api import sync_playwright

        screenshot_path = Path("doctor_test.png")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)

            page = browser.new_page()

            page.set_content("<h1>Webwright Doctor</h1>")

            page.screenshot(path=str(screenshot_path))

            browser.close()

        if screenshot_path.exists():
            screenshot_path.unlink(missing_ok=True)

            return True, "screenshot capture working"

        return False, "screenshot file was not created"

    except Exception:
        return False, (
            "unable to launch Chromium for screenshot validation\n"
            "Fix: playwright install"
        )


def check_openai_key():
    if os.getenv("OPENAI_API_KEY"):
        return True, "OPENAI_API_KEY found"

    return False, (
        "OPENAI_API_KEY missing\nFix: set the OPENAI_API_KEY environment variable"
    )


def check_plugin_manifests():
    claude = Path(".claude-plugin/plugin.json")
    codex = Path(".codex-plugin/plugin.json")

    missing = []

    if not claude.exists():
        missing.append("Claude")

    if not codex.exists():
        missing.append("Codex")

    if not missing:
        return True, "plugin manifests found"

    return False, (
        f"missing plugin manifests: {', '.join(missing)}\n"
        "Fix: configure Claude/Codex plugins"
    )


CHECKS = [
    ("Python", check_python),
    ("Playwright", check_playwright),
    ("Chromium", check_chromium),
    ("Screenshot", check_screenshot),
    ("OpenAI Key", check_openai_key),
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
