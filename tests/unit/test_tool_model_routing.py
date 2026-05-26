from pathlib import Path

import yaml

from webwright.config import get_config_from_spec
from webwright.tools.image_qa import _extract_model_config
from webwright.utils.serialize import recursive_merge


def test_model_claude_routes_inner_tools_to_anthropic() -> None:
    config = recursive_merge(
        get_config_from_spec("base.yaml"),
        get_config_from_spec("model_claude.yaml"),
    )

    assert config["model"]["model_class"] == "anthropic"
    assert config["tools"]["image_qa"]["model"]["model_class"] == "anthropic"
    assert config["tools"]["self_reflection"]["model"]["model_class"] == "anthropic"


def test_tool_model_config_can_fall_back_to_top_level_model(tmp_path: Path) -> None:
    merged_config_path = tmp_path / "merged_config.yaml"
    merged_config_path.write_text(
        yaml.safe_dump({"model": {"model_class": "anthropic", "model_name": "claude-opus-4-7"}}),
        encoding="utf-8",
    )

    config = yaml.safe_load(merged_config_path.read_text(encoding="utf-8"))

    assert _extract_model_config(config, tool_name="image_qa") == {
        "model_class": "anthropic",
        "model_name": "claude-opus-4-7",
    }
