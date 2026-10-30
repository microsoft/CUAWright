import pytest
import yaml

from cuawright.webwright.config import get_config_from_spec
from cuawright.webwright.tools._model_config import (
    DEFAULT_MERGED_CONFIG_RELPATH,
    _extract_model_block,
    resolve_model_config_path,
)
from cuawright.webwright.utils.serialize import recursive_merge


def test_model_claude_sets_top_level_anthropic_model() -> None:
    config = recursive_merge(
        get_config_from_spec("base.yaml"),
        get_config_from_spec("model_claude.yaml"),
    )

    assert config["model"]["model_class"] == "anthropic"


def test_model_claude_does_not_declare_per_tool_overrides() -> None:
    config = get_config_from_spec("model_claude.yaml")

    assert (
        "tools" not in config
    ), "model_claude.yaml should rely on the top-level `model:` block"


def test_extract_model_block_reads_top_level_model(tmp_path) -> None:
    config = {"model": {"model_class": "anthropic", "model_name": "claude-opus-4-7"}}

    assert _extract_model_block(config) == config["model"]


def test_extract_model_block_rejects_missing_block() -> None:
    with pytest.raises(ValueError, match="missing a top-level"):
        _extract_model_block({})


def test_resolve_model_config_path_prefers_explicit_arg(tmp_path) -> None:
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(
        "model: {model_class: anthropic, model_name: claude-opus-4-7}\n"
    )

    snapshot_dir = tmp_path / "ws" / DEFAULT_MERGED_CONFIG_RELPATH.parent
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / DEFAULT_MERGED_CONFIG_RELPATH.name).write_text(
        yaml.safe_dump({"model": {"model_class": "openai"}})
    )

    resolved = resolve_model_config_path(
        str(explicit), workspace_dir=str(tmp_path / "ws")
    )

    assert resolved == explicit.resolve()


def test_resolve_model_config_path_falls_back_to_workspace_snapshot(tmp_path) -> None:
    workspace = tmp_path / "ws"
    snapshot_path = workspace / DEFAULT_MERGED_CONFIG_RELPATH
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(
        yaml.safe_dump(
            {"model": {"model_class": "anthropic", "model_name": "claude-opus-4-7"}}
        )
    )

    resolved = resolve_model_config_path("", workspace_dir=str(workspace))

    assert resolved == snapshot_path.resolve()


def test_resolve_model_config_path_raises_when_nothing_found(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="No tool model config found"):
        resolve_model_config_path("", workspace_dir=str(tmp_path))


@pytest.mark.parametrize(
    "model_class,endpoint_key,endpoint",
    [
        ("openai", "responses_url", "https://gateway.example/v1/responses"),
        ("anthropic", "anthropic_endpoint", "https://gateway.example/v1/messages"),
        (
            "openrouter",
            "openrouter_endpoint",
            "https://gateway.example/v1/chat/completions",
        ),
    ],
)
def test_redacted_snapshot_restores_private_connection_for_child_tool(
    tmp_path, monkeypatch, model_class, endpoint_key, endpoint
):
    import os
    import subprocess
    import sys
    from cuawright.webwright.config import snapshot_config_specs
    from cuawright.webwright.tools._model_config import tool_connection

    model = {
        "model_class": model_class,
        endpoint_key: endpoint,
        "api_key": "private-key",
    }
    snapshot = snapshot_config_specs([], tmp_path, merged_config={"model": model})
    saved = (snapshot / "merged_config.yaml").read_text()
    assert endpoint not in saved and "private-key" not in saved
    monkeypatch.setenv("CUAWRIGHT_TOOL_CONNECTION", "previous-value")
    # Exercise the actual child-process inheritance used by terminal tools.
    script = """
import sys
import cuawright.webwright.tools._model_config as tools
tools.get_model = lambda config: config
model = tools.load_tool_model(model_config_arg='', workspace_dir=sys.argv[1], timeout_seconds=10)
assert model[sys.argv[2]] == sys.argv[3]
assert model['api_key'] == 'private-key'
"""
    with tool_connection(model, tmp_path):
        subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), endpoint_key, endpoint],
            check=True,
        )
    assert os.environ["CUAWRIGHT_TOOL_CONNECTION"] == "previous-value"


def test_private_connection_does_not_override_explicit_config_or_other_workspace(
    tmp_path, monkeypatch
):
    from cuawright.webwright.config import snapshot_config_specs
    from cuawright.webwright.tools import _model_config as tools

    monkeypatch.setattr(tools, "get_model", lambda config: config)
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(
        "model: {responses_url: 'https://explicit.example/v1/responses'}\n"
    )
    other = tmp_path / "other"
    snapshot_config_specs([], other, merged_config={"model": {"model_name": "other"}})
    with tools.tool_connection(
        {"responses_url": "https://private.example/v1/responses"}, tmp_path
    ):
        chosen = tools.load_tool_model(
            model_config_arg=str(explicit),
            workspace_dir=str(tmp_path),
            timeout_seconds=10,
        )
        assert chosen["responses_url"] == "https://explicit.example/v1/responses"
        chosen = tools.load_tool_model(
            model_config_arg="", workspace_dir=str(other), timeout_seconds=10
        )
        assert "responses_url" not in chosen


def test_private_connection_is_removed_after_failure(tmp_path, monkeypatch):
    import os
    from cuawright.webwright.tools._model_config import tool_connection

    monkeypatch.delenv("CUAWRIGHT_TOOL_CONNECTION", raising=False)
    with pytest.raises(RuntimeError):
        with tool_connection(
            {"responses_url": "https://private.example/v1/responses"}, tmp_path
        ):
            raise RuntimeError("task failed")
    assert "CUAWRIGHT_TOOL_CONNECTION" not in os.environ


def test_cli_tools_use_live_connection_while_artifacts_stay_redacted(
    tmp_path, monkeypatch
):
    from cuawright.webwright.run import cli
    from cuawright.webwright.tools import _model_config as tools

    endpoint = "https://private.example/v1/responses"
    workspace = tmp_path / "run"

    class Environment:
        def prepare(self, **kwargs):
            pass

        def close(self):
            pass

    class Agent:
        def run(self, *args, **kwargs):
            model = tools.load_tool_model(
                model_config_arg="", workspace_dir=str(workspace), timeout_seconds=10
            )
            assert model._post_url() == endpoint
            assert model.config.openai_api_key == "private-key"
            return {"final_response": "verified"}

    monkeypatch.setattr(cli, "get_model", lambda config: object())
    monkeypatch.setattr(cli, "get_environment", lambda config: Environment())
    monkeypatch.setattr(cli, "get_agent", lambda *args, **kwargs: Agent())
    result = cli.run_one(
        task="verify",
        config_spec=[
            f"model.responses_url={endpoint}",
            "model.openai_api_key=private-key",
        ],
        resolved_output_dir=workspace,
    )
    assert result["final_response"] == "verified"
    for path in (workspace / "config_snapshot").iterdir():
        saved = path.read_text()
        assert endpoint not in saved and "private-key" not in saved
