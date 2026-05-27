import pytest
import yaml

from webwright.config import get_config_from_spec
from webwright.tools._model_config import (
    DEFAULT_MERGED_CONFIG_RELPATH,
    _extract_model_block,
    resolve_model_config_path,
)
from webwright.utils.serialize import recursive_merge


def test_model_claude_sets_top_level_anthropic_model() -> None:
    config = recursive_merge(
        get_config_from_spec("base.yaml"),
        get_config_from_spec("model_claude.yaml"),
    )

    assert config["model"]["model_class"] == "anthropic"


def test_model_claude_does_not_declare_per_tool_overrides() -> None:
    config = get_config_from_spec("model_claude.yaml")

    assert "tools" not in config, "model_claude.yaml should rely on the top-level `model:` block"


def test_extract_model_block_reads_top_level_model(tmp_path) -> None:
    config = {"model": {"model_class": "anthropic", "model_name": "claude-opus-4-7"}}

    assert _extract_model_block(config) == config["model"]


def test_extract_model_block_rejects_missing_block() -> None:
    with pytest.raises(ValueError, match="missing a top-level"):
        _extract_model_block({})


def test_resolve_model_config_path_prefers_explicit_arg(tmp_path) -> None:
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text("model: {model_class: anthropic, model_name: claude-opus-4-7}\n")

    snapshot_dir = tmp_path / "ws" / DEFAULT_MERGED_CONFIG_RELPATH.parent
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / DEFAULT_MERGED_CONFIG_RELPATH.name).write_text(
        yaml.safe_dump({"model": {"model_class": "openai"}})
    )

    resolved = resolve_model_config_path(str(explicit), workspace_dir=str(tmp_path / "ws"))

    assert resolved == explicit.resolve()


def test_resolve_model_config_path_falls_back_to_workspace_snapshot(tmp_path) -> None:
    workspace = tmp_path / "ws"
    snapshot_path = workspace / DEFAULT_MERGED_CONFIG_RELPATH
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(
        yaml.safe_dump({"model": {"model_class": "anthropic", "model_name": "claude-opus-4-7"}})
    )

    resolved = resolve_model_config_path("", workspace_dir=str(workspace))

    assert resolved == snapshot_path.resolve()


def test_resolve_model_config_path_raises_when_nothing_found(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="No tool model config found"):
        resolve_model_config_path("", workspace_dir=str(tmp_path))
