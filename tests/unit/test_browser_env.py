# ABOUTME: Tests that LocalWorkspaceEnvironment._browser_env forwards the right
# ABOUTME: BROWSER_MODE and cloud-browser credentials (Browserbase, Steel) to subprocesses.

from webwright.environments.local_workspace import LocalWorkspaceEnvironment


def test_browser_env_forwards_steel_mode_and_key(monkeypatch) -> None:
    monkeypatch.setenv("STEEL_API_KEY", "sk-steel-test")
    env = LocalWorkspaceEnvironment(browser_mode="steel")

    result = env._browser_env()

    assert result["BROWSER_MODE"] == "steel"
    assert result["STEEL_API_KEY"] == "sk-steel-test"


def test_browser_env_omits_steel_key_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("STEEL_API_KEY", raising=False)
    env = LocalWorkspaceEnvironment(browser_mode="steel")

    result = env._browser_env()

    assert "STEEL_API_KEY" not in result


def test_browser_env_forwards_browserbase_credentials(monkeypatch) -> None:
    monkeypatch.setenv("BROWSERBASE_API_KEY", "bb-key")
    monkeypatch.setenv("BROWSERBASE_PROJECT_ID", "bb-project")
    env = LocalWorkspaceEnvironment(browser_mode="browserbase")

    result = env._browser_env()

    assert result["BROWSER_MODE"] == "browserbase"
    assert result["BROWSERBASE_API_KEY"] == "bb-key"
    assert result["BROWSERBASE_PROJECT_ID"] == "bb-project"


def test_browser_env_defaults_mode_to_browserbase(monkeypatch) -> None:
    env = LocalWorkspaceEnvironment(browser_mode="")

    result = env._browser_env()

    assert result["BROWSER_MODE"] == "browserbase"
