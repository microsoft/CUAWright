from pathlib import Path

from webwright.run.doctor import (
    check_api_key,
    check_chromium,
    check_playwright,
    check_plugin_manifests,
    check_python,
    check_screenshot,
)


def test_check_python():
    ok, message = check_python()

    assert isinstance(ok, bool)
    assert isinstance(message, str)


def test_check_playwright():
    ok, message = check_playwright()

    assert isinstance(ok, bool)
    assert isinstance(message, str)


def test_check_chromium():
    ok, message = check_chromium()

    assert isinstance(ok, bool)
    assert isinstance(message, str)


def test_check_screenshot():
    ok, message = check_screenshot()

    assert isinstance(ok, bool)
    assert isinstance(message, str)


def test_check_api_key_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    ok, message = check_api_key()

    assert ok is True
    assert "OPENAI_API_KEY" in message
    assert "found" in message


def test_check_api_key_anthropic(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    ok, message = check_api_key()

    assert ok is True
    assert "ANTHROPIC_API_KEY" in message
    assert "found" in message


def test_check_api_key_openrouter(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    ok, message = check_api_key()

    assert ok is True
    assert "OPENROUTER_API_KEY" in message
    assert "found" in message


def test_check_api_key_missing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    ok, message = check_api_key()

    assert ok is False
    assert "No API key found" in message


def test_plugin_manifests_exist(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    claude_dir = tmp_path / ".claude-plugin"
    codex_dir = tmp_path / ".codex-plugin"

    claude_dir.mkdir()
    codex_dir.mkdir()

    (claude_dir / "plugin.json").write_text("{}")
    (codex_dir / "plugin.json").write_text("{}")

    ok, message = check_plugin_manifests()

    assert ok is True
    assert "found" in message


def test_plugin_manifests_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    ok, message = check_plugin_manifests()

    assert ok is False
    assert "missing" in message


def test_screenshot_file_cleanup():
    screenshot_path = Path("doctor_test.png")

    if screenshot_path.exists():
        screenshot_path.unlink()

    check_screenshot()

    assert not screenshot_path.exists()
