from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml

from webwright import package_dir

builtin_config_dir = package_dir / "config"


def _nest_key_value(key: str, value: Any) -> dict[str, Any]:
    parts = key.split(".")
    nested: dict[str, Any] = value
    for part in reversed(parts):
        nested = {part: nested}
    return nested


def _resolve_config_path(spec: str) -> Path | None:
    path = Path(spec).expanduser()
    if path.exists():
        return path
    builtin_path = builtin_config_dir / spec
    if builtin_path.exists():
        return builtin_path
    return None


def get_config_from_spec(spec: str) -> dict[str, Any]:
    resolved_path = _resolve_config_path(spec)
    if resolved_path is not None:
        loaded = yaml.safe_load(resolved_path.read_text())
        return loaded or {}

    if "=" not in spec:
        raise ValueError(f"Unsupported config spec: {spec!r}")

    key, raw_value = spec.split("=", 1)
    return _nest_key_value(key, yaml.safe_load(raw_value))


def snapshot_config_specs(
    config_spec: list[str],
    output_dir: str | Path,
    *,
    merged_config: dict[str, Any] | None = None,
) -> Path:
    snapshot_dir = Path(output_dir).expanduser() / "config_snapshot"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    manifest: list[dict[str, Any]] = []
    for index, spec in enumerate(config_spec):
        entry: dict[str, Any] = {
            "index": index,
            "spec": spec,
        }
        resolved_path = _resolve_config_path(spec)
        if resolved_path is None:
            entry["kind"] = "inline_override"
        else:
            saved_copy = snapshot_dir / f"{index:02d}_{resolved_path.name}"
            shutil.copy2(resolved_path, saved_copy)
            entry.update(
                {
                    "kind": "file",
                    "resolved_path": str(resolved_path.resolve()),
                    "saved_copy": str(saved_copy),
                }
            )
        manifest.append(entry)

    (snapshot_dir / "config_spec_manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    if merged_config is not None:
        (snapshot_dir / "merged_config.yaml").write_text(
            yaml.safe_dump(merged_config, sort_keys=False),
            encoding="utf-8",
        )
    return snapshot_dir
