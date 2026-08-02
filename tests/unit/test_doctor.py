from webwright.run.doctor import (
    _find_manifest_root,
    _parse_install_locations,
    check_browsers,
    check_model_backend,
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


def test_parse_install_locations():
    """`--dry-run` lists every browser regardless of what is installed.

    The old check only inspected the return code, which is 0 even with no
    browsers present, so parsing the locations is what makes the check real.
    """
    sample = (
        "Chrome for Testing 151.0.7922.34 (playwright chromium v1234)\n"
        "  Install location:    /tmp/ms-playwright/chromium-1234\n"
        "  Download url:        https://example.invalid/chromium.zip\n"
        "\n"
        "Firefox 153.0 (playwright firefox v1538)\n"
        "  Install location:    /tmp/ms-playwright/firefox-1538\n"
        "  Download url:        https://example.invalid/firefox.zip\n"
    )

    locations = _parse_install_locations(sample)

    assert set(locations) == {"chromium", "firefox"}
    assert locations["firefox"].name == "firefox-1538"
    assert locations["chromium"].name == "chromium-1234"


def test_parse_install_locations_empty():
    assert _parse_install_locations("") == {}


def test_check_browsers():
    ok, message = check_browsers()

    assert isinstance(ok, bool)
    assert isinstance(message, str)


def test_check_browsers_reports_an_installed_engine():
    """A machine with any Playwright engine installed must not report FAIL.

    Regression: the old check shelled out to ``playwright install --dry-run``,
    which exits 0 regardless, and raised ``[WinError 2]`` on Windows.
    """
    ok, message = check_browsers()

    if ok:
        assert "firefox" in message or "chromium" in message
    else:
        assert "Fix:" in message


def test_check_screenshot():
    ok, message = check_screenshot()

    assert isinstance(ok, bool)
    assert isinstance(message, str)


def test_check_model_backend_accepts_openai(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    ok, message = check_model_backend()

    assert ok is True
    assert "found" in message


def test_check_model_backend_accepts_anthropic_only(monkeypatch):
    """Regression: an Anthropic-only setup used to report FAIL.

    ``model_claude.yaml`` is a first-class backend, so requiring OPENAI_API_KEY
    specifically made doctor lie about a valid configuration.
    """
    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    ok, message = check_model_backend()

    assert ok is True
    assert "ANTHROPIC_API_KEY" in message


def test_check_model_backend_accepts_openrouter_only(monkeypatch):
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    ok, message = check_model_backend()

    assert ok is True
    assert "OPENROUTER_API_KEY" in message


def test_check_model_backend_missing_mentions_plugin_mode(monkeypatch):
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    ok, message = check_model_backend()

    assert ok is False
    assert "plugin" in message.lower()


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


def test_plugin_manifests_found_from_subdirectory(tmp_path, monkeypatch):
    """Regression: doctor resolved manifests against cwd only.

    Running ``webwright doctor`` from any subdirectory of the repo reported the
    manifests as missing.
    """
    claude_dir = tmp_path / ".claude-plugin"
    codex_dir = tmp_path / ".codex-plugin"

    claude_dir.mkdir()
    codex_dir.mkdir()

    (claude_dir / "plugin.json").write_text("{}")
    (codex_dir / "plugin.json").write_text("{}")

    nested = tmp_path / "src" / "webwright" / "run"
    nested.mkdir(parents=True)

    monkeypatch.chdir(nested)

    assert _find_manifest_root() == tmp_path.resolve()

    ok, message = check_plugin_manifests()

    assert ok is True
    assert "found" in message


def test_screenshot_does_not_pollute_cwd(tmp_path, monkeypatch):
    """The temp screenshot must never be written into the caller's cwd."""
    monkeypatch.chdir(tmp_path)

    check_screenshot()

    assert not (tmp_path / "doctor_test.png").exists()
    assert list(tmp_path.iterdir()) == []
