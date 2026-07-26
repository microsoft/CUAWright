import os
import sys
from pathlib import Path

from webwright.run import script_runner


def test_runner_executes_with_webwright_interpreter(monkeypatch):
    captured = {}

    def fake_execve(executable, arguments, environment):
        captured.update(
            executable=executable,
            arguments=arguments,
            environment=environment,
        )

    monkeypatch.setattr(os, "execve", fake_execve)
    monkeypatch.setattr(sys, "argv", ["webwright-python", "-c", "print('ok')"])

    script_runner.main()

    assert captured["executable"] == sys.executable
    assert captured["arguments"] == [sys.executable, "-c", "print('ok')"]


def test_runner_shares_macos_playwright_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    environment = script_runner._runner_environment()

    assert environment["PLAYWRIGHT_BROWSERS_PATH"] == str(
        tmp_path / "Library" / "Caches" / "ms-playwright"
    )


def test_runner_preserves_explicit_browser_cache(monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/custom/browser-cache")

    environment = script_runner._runner_environment()

    assert environment["PLAYWRIGHT_BROWSERS_PATH"] == "/custom/browser-cache"
