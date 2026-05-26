from __future__ import annotations

import json

import httpx

from webwright.cache import ScriptCache, make_fingerprint
from webwright.run.cli import _try_replay_cache


def _fingerprint_config() -> dict:
    return {
        "cache": {"enabled": True},
        "run": {
            "task": "Find red shoes",
            "start_url": "https://example.com",
        },
        "model": {
            "model_class": "openai",
            "model_name": "gpt-4.1",
        },
        "environment": {
            "environment_class": "local_workspace",
        },
    }


def test_make_fingerprint_is_stable_and_changes_on_input_change() -> None:
    config = _fingerprint_config()
    assert make_fingerprint(config) == make_fingerprint(_fingerprint_config())

    changed = _fingerprint_config()
    changed["run"]["task"] = "Find blue shoes"
    assert make_fingerprint(config) != make_fingerprint(changed)


def test_replay_script_error_invalidates_cache_entry(tmp_path) -> None:
    cache_dir = tmp_path / "cache"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    final_script_path = source_dir / "final_script.py"
    final_script_path.write_text("raise SystemExit(7)\n", encoding="utf-8")
    trajectory_path = source_dir / "trajectory.json"
    trajectory_path.write_text(
        json.dumps(
            {
                "info": {
                    "exit_status": "Submitted",
                    "submission": "done",
                    "api_calls": 1,
                    "format_errors": 0,
                },
                "messages": [],
                "trajectory_format": "mini-swe-webagent-0.1",
            }
        ),
        encoding="utf-8",
    )

    config = _fingerprint_config()
    config["cache"] = {
        "enabled": True,
        "directory": str(cache_dir),
        "validate_url": False,
    }
    config["environment"] = {
        "environment_class": "local_workspace",
        "output_dir": str(tmp_path / "replay"),
    }
    config["agent"] = {
        "output_path": str(tmp_path / "replay" / "trajectory.json"),
    }
    fingerprint = make_fingerprint(config)

    cache = ScriptCache(config["cache"])
    cache.put(fingerprint, final_script_path, trajectory_path)

    result = _try_replay_cache(
        config=config,
        fingerprint=fingerprint,
        task=config["run"]["task"],
        task_id=None,
        start_url=config["run"]["start_url"],
    )

    assert result is None
    assert not (cache_dir / fingerprint).exists()


def test_start_url_500_invalidates_cache_entry(tmp_path, monkeypatch) -> None:
    cache_dir = tmp_path / "cache"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    final_script_path = source_dir / "final_script.py"
    final_script_path.write_text("print('should not run')\n", encoding="utf-8")
    trajectory_path = source_dir / "trajectory.json"
    trajectory_path.write_text(
        json.dumps(
            {
                "info": {
                    "exit_status": "Submitted",
                    "submission": "done",
                    "api_calls": 1,
                    "format_errors": 0,
                },
                "messages": [],
                "trajectory_format": "mini-swe-webagent-0.1",
            }
        ),
        encoding="utf-8",
    )

    config = _fingerprint_config()
    config["cache"] = {
        "enabled": True,
        "directory": str(cache_dir),
        "validate_url": True,
    }
    config["environment"] = {
        "environment_class": "local_workspace",
        "output_dir": str(tmp_path / "replay"),
    }
    config["agent"] = {
        "output_path": str(tmp_path / "replay" / "trajectory.json"),
    }
    fingerprint = make_fingerprint(config)

    cache = ScriptCache(config["cache"])
    cache.put(fingerprint, final_script_path, trajectory_path)

    def fake_head(*args, **kwargs) -> httpx.Response:
        return httpx.Response(status_code=500)

    monkeypatch.setattr(httpx, "head", fake_head)

    result = _try_replay_cache(
        config=config,
        fingerprint=fingerprint,
        task=config["run"]["task"],
        task_id=None,
        start_url=config["run"]["start_url"],
    )

    assert result is None
    assert not (cache_dir / fingerprint).exists()
    assert not (tmp_path / "replay").exists()
