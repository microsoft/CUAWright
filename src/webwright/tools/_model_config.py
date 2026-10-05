"""Resolve the model client used by inner tools (image_qa, self_reflection).

The CLI snapshots the fully merged run config to
``<workspace_dir>/config_snapshot/merged_config.yaml``; the tools read that file
(or an explicit ``--model-config`` override) and instantiate the same model the
agent uses.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from webwright.models import get_model

DEFAULT_MERGED_CONFIG_RELPATH = Path("config_snapshot") / "merged_config.yaml"


def _load_structured_config(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        loaded = yaml.safe_load(text)
    else:
        loaded = json.loads(text)
    if not isinstance(loaded, dict):
        raise ValueError(f"Model config must be an object: {path}")
    return loaded


def _extract_model_block(config: dict[str, Any]) -> dict[str, Any]:
    model_block = config.get("model")
    if not isinstance(model_block, dict):
        raise ValueError(
            "Model config is missing a top-level `model:` block; "
            "stack a model_*.yaml (e.g. model_claude.yaml) or pass --model-config <path>."
        )
    return model_block


def resolve_model_config_path(model_config_arg: str, *, workspace_dir: str) -> Path:
    """Return the path to a config containing a top-level ``model:`` block.

    Resolution order:
      1. ``model_config_arg`` (absolute or relative to ``workspace_dir``).
      2. ``<workspace_dir>/config_snapshot/merged_config.yaml`` (written by the CLI).
    """
    candidates: list[Path] = []
    if model_config_arg:
        configured = Path(model_config_arg)
        candidates.append(configured)
        if workspace_dir and not configured.is_absolute():
            candidates.append(Path(workspace_dir) / configured)
    if workspace_dir:
        candidates.append(Path(workspace_dir) / DEFAULT_MERGED_CONFIG_RELPATH)
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError(
        "No tool model config found. Pass --model-config <path> or run via the agent so "
        f"<workspace-dir>/{DEFAULT_MERGED_CONFIG_RELPATH} is available."
    )


def load_tool_model(
    *,
    model_config_arg: str,
    workspace_dir: str,
    timeout_seconds: int,
) -> Any:
    config_path = resolve_model_config_path(model_config_arg, workspace_dir=workspace_dir)
    config = _load_structured_config(config_path)
    model_block = dict(_extract_model_block(config))
    model_block["request_timeout_seconds"] = timeout_seconds
    return get_model(model_block)
