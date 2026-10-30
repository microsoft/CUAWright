from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
import yaml

from cuawright.webwright import package_dir

builtin_config_dir = package_dir / "config"
_PRIVATE_CONFIG_KEYS = {
    "api_key",
    "credentials",
    "openai_endpoint",
    "anthropic_endpoint",
    "openrouter_endpoint",
    "responses_url",
    "base_url",
}


class CacheConfig(BaseModel):
    enabled: bool = False
    directory: Path = Path("~/.cache/webwright")
    ttl_seconds: int = 604800
    validate_url: bool = True
    fingerprint_fields: list[str] = Field(
        default_factory=lambda: [
            "task",
            "start_url",
            "model.model_name",
            "environment.environment_class",
        ]
    )


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


def public_config(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[redacted]"
                if key.lower() in _PRIVATE_CONFIG_KEYS
                or key.lower().endswith(("_api_key", "_endpoint", "_base_url"))
                else public_config(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [public_config(item) for item in value]
    return value


def public_spec(spec: str) -> str:
    if "=" not in spec:
        return spec
    key, _ = spec.split("=", 1)
    name = key.rsplit(".", 1)[-1].lower()
    if name in _PRIVATE_CONFIG_KEYS or name.endswith(
        ("_api_key", "_endpoint", "_base_url")
    ):
        return f"{key}=[redacted]"
    return spec


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
            "spec": public_spec(spec),
        }
        resolved_path = _resolve_config_path(spec)
        if resolved_path is None:
            entry["kind"] = "inline_override"
        else:
            saved_copy = snapshot_dir / f"{index:02d}_{resolved_path.name}"
            loaded = yaml.safe_load(resolved_path.read_text()) or {}
            saved_copy.write_text(
                yaml.safe_dump(public_config(loaded), sort_keys=False),
                encoding="utf-8",
            )
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
            yaml.safe_dump(public_config(merged_config), sort_keys=False),
            encoding="utf-8",
        )
    return snapshot_dir
