from pathlib import Path

from webwright.run.doctor import (
    check_firefox,
    check_openai_key,
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


def test_check_firefox():
    ok, message = check_firefox()

    assert isinstance(ok, bool)
    assert isinstance(message, str)


def test_check_screenshot():
    ok, message = check_screenshot()

    assert isinstance(ok, bool)
    assert isinstance(message, str)


def test_check_openai_key_exists(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    ok, message = check_openai_key()

    assert ok is True
    assert "found" in message


def test_check_openai_key_missing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    ok, message = check_openai_key()

    assert ok is False
    assert "missing" in message


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
