from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from webwright.config import CacheConfig


@dataclass(frozen=True)
class CachedScript:
    fingerprint: str
    directory: Path
    script_path: Path
    trajectory_path: Path
    metadata: dict[str, Any]


def _field_value(config: dict[str, Any], field: str) -> Any:
    if field == "task":
        return config.get("run", {}).get("task", "")
    if field == "start_url":
        return config.get("run", {}).get("start_url", "")

    value: Any = config
    for part in field.split("."):
        if not isinstance(value, dict):
            return ""
        value = value.get(part, "")
    return value


def make_fingerprint(config: dict[str, Any]) -> str:
    cache_config = CacheConfig(**config.get("cache", {}))
    payload = {
        field: _field_value(config, field)
        for field in cache_config.fingerprint_fields
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


class ScriptCache:
    def __init__(self, config: dict[str, Any] | CacheConfig | None = None):
        if isinstance(config, CacheConfig):
            self.config = config
        else:
            self.config = CacheConfig(**(config or {}))
        self.directory = self.config.directory.expanduser()

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def _entry_dir(self, fingerprint: str) -> Path:
        return self.directory / fingerprint

    def get(self, fingerprint: str) -> CachedScript | None:
        if not self.enabled:
            return None

        entry_dir = self._entry_dir(fingerprint)
        metadata_path = entry_dir / "metadata.json"
        script_path = entry_dir / "final_script.py"
        trajectory_path = entry_dir / "trajectory.json"
        if not metadata_path.is_file() or not script_path.is_file() or not trajectory_path.is_file():
            return None

        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.invalidate(fingerprint)
            return None

        if self._is_expired(metadata):
            self.invalidate(fingerprint)
            return None

        return CachedScript(
            fingerprint=fingerprint,
            directory=entry_dir,
            script_path=script_path,
            trajectory_path=trajectory_path,
            metadata=metadata,
        )

    def put(
        self,
        fingerprint: str,
        final_script_path: str | Path,
        trajectory_path: str | Path,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> CachedScript | None:
        if not self.enabled:
            return None

        source_script = Path(final_script_path).expanduser()
        source_trajectory = Path(trajectory_path).expanduser()
        if not source_script.is_file() or not source_trajectory.is_file():
            return None

        entry_dir = self._entry_dir(fingerprint)
        entry_dir.mkdir(parents=True, exist_ok=True)
        script_path = entry_dir / "final_script.py"
        trajectory_copy_path = entry_dir / "trajectory.json"
        shutil.copy2(source_script, script_path)
        shutil.copy2(source_trajectory, trajectory_copy_path)

        entry_metadata = {
            **(metadata or {}),
            "fingerprint": fingerprint,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "final_script_source": str(source_script),
            "trajectory_source": str(source_trajectory),
        }
        (entry_dir / "metadata.json").write_text(
            json.dumps(entry_metadata, indent=2),
            encoding="utf-8",
        )
        return CachedScript(
            fingerprint=fingerprint,
            directory=entry_dir,
            script_path=script_path,
            trajectory_path=trajectory_copy_path,
            metadata=entry_metadata,
        )

    def invalidate(self, fingerprint: str) -> None:
        shutil.rmtree(self._entry_dir(fingerprint), ignore_errors=True)

    def validate_url(self, start_url: str | None) -> bool:
        if not self.config.validate_url or not start_url:
            return True
        try:
            response = httpx.head(start_url, follow_redirects=True, timeout=10.0)
        except httpx.HTTPError:
            return False
        return response.status_code < 400

    def _is_expired(self, metadata: dict[str, Any]) -> bool:
        if self.config.ttl_seconds <= 0:
            return False
        raw_created_at = metadata.get("created_at")
        if not isinstance(raw_created_at, str):
            return True
        try:
            created_at = datetime.fromisoformat(raw_created_at)
        except ValueError:
            return True
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - created_at
        return age.total_seconds() > self.config.ttl_seconds
