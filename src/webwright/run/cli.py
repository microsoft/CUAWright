from __future__ import annotations

import json
import shlex
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from webwright.agents import get_agent
from webwright.cache import CachedScript, ScriptCache, make_fingerprint
from webwright.config import get_config_from_spec, snapshot_config_specs
from webwright.environments import get_environment
from webwright.models import get_model
from webwright.utils.serialize import UNSET, recursive_merge
from webwright.run.doctor import run_doctor


DEFAULT_CONFIGS = ["base.yaml", "model_openai.yaml"]

app = typer.Typer(no_args_is_help=True, context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
console = Console(highlight=False)


def _timestamped_output_dir(base_dir: str | Path | None, task_id: str | None) -> Path:
    base = Path(base_dir or "outputs").expanduser()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = task_id or "adhoc"
    return base / f"{suffix}_{stamp}"


def _extra_config_specs(args: list[str]) -> list[str]:
    specs: list[str] = []
    index = 0
    while index < len(args):
        raw_arg = args[index]
        if not raw_arg.startswith("--") or "." not in raw_arg:
            raise ValueError(f"Unsupported CLI override: {raw_arg!r}")

        spec = raw_arg[2:]
        if "=" not in spec:
            if index + 1 < len(args) and not args[index + 1].startswith("--"):
                spec = f"{spec}={args[index + 1]}"
                index += 1
            else:
                spec = f"{spec}=true"
        specs.append(spec)
        index += 1
    return specs


def _cache_metadata(config: dict[str, Any], fingerprint: str) -> dict[str, Any]:
    return {
        "fingerprint": fingerprint,
        "task": config.get("run", {}).get("task", ""),
        "start_url": config.get("run", {}).get("start_url", ""),
        "model": {
            "model_class": config.get("model", {}).get("model_class", ""),
            "model_name": config.get("model", {}).get("model_name", ""),
        },
        "environment": {
            "environment_class": config.get("environment", {}).get("environment_class", ""),
        },
    }


def _result_from_cached_trajectory(cached: CachedScript, trajectory: dict[str, Any]) -> dict[str, Any]:
    info = trajectory.get("info", {})
    return {
        "exit_status": info.get("exit_status", "Submitted"),
        "submission": info.get("submission", ""),
        "final_response": info.get("submission", ""),
        "cached": True,
        "cache_fingerprint": cached.fingerprint,
    }


def _write_cached_trajectory(
    *,
    cached: CachedScript,
    config: dict[str, Any],
    replay_output: dict[str, Any],
) -> dict[str, Any]:
    try:
        trajectory = json.loads(cached.trajectory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        trajectory = {
            "info": {
                "exit_status": "Submitted",
                "submission": "Cached script completed.",
                "api_calls": 0,
                "format_errors": 0,
            },
            "messages": [],
            "trajectory_format": "mini-swe-webagent-0.1",
        }

    trajectory["cached"] = True
    trajectory["cache"] = {
        "fingerprint": cached.fingerprint,
        "source": str(cached.directory),
        "script_path": str(cached.script_path),
        "replay_returncode": replay_output.get("returncode"),
    }
    trajectory["replay_observation"] = replay_output.get("observation", {})
    trajectory.setdefault("info", {})["cached"] = True
    trajectory["info"]["api_calls"] = 0

    output_path = Path(config.get("agent", {}).get("output_path", "trajectory.json")).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(trajectory, indent=2), encoding="utf-8")
    return _result_from_cached_trajectory(cached, trajectory)


def _try_replay_cache(
    *,
    config: dict[str, Any],
    fingerprint: str,
    task: str,
    task_id: str | None,
    start_url: str | None,
) -> dict[str, Any] | None:
    cache = ScriptCache(config.get("cache", {}))
    if not cache.enabled:
        return None

    cached = cache.get(fingerprint)
    if cached is None:
        console.print("Cache miss: running agent")
        return None

    if not cache.validate_url(start_url):
        cache.invalidate(fingerprint)
        console.print("Cache miss: running agent")
        return None

    console.print("Cache hit: skipping model loop")
    env = get_environment(config.get("environment", {}))
    try:
        env.prepare(
            task=task,
            task_id=task_id,
            start_url=start_url,
        )
        output = env.execute(
            {"bash_command": f"{shlex.quote(sys.executable)} {shlex.quote(str(cached.script_path))}"}
        )
    finally:
        env.close()

    if output.get("returncode") != 0 or output.get("exception_info"):
        cache.invalidate(fingerprint)
        console.print("Cache miss: running agent")
        return None

    return _write_cached_trajectory(
        cached=cached,
        config=config,
        replay_output=output,
    )


def run_one(
    *,
    task: str | None = None,
    task_id: str | None = None,
    start_url: str | None = None,
    config_spec: list[str] | None = None,
    output_dir: Path | None = None,
    resolved_output_dir: Path | None = None,
    debug: bool = False,
    snapshot_config: bool = True,
) -> Any:
    config_spec = config_spec or DEFAULT_CONFIGS
    configs = [get_config_from_spec(spec) for spec in config_spec]
    config = recursive_merge(*configs)

    run_config = config.get("run", {})
    resolved_task_id = task_id or run_config.get("task_id")
    resolved_task = task or run_config.get("task")
    resolved_start_url = start_url or run_config.get("start_url")

    if not resolved_task:
        raise ValueError("A task is required. Use --task.")

    resolved_output_dir = resolved_output_dir or _timestamped_output_dir(
        output_dir or config.get("environment", {}).get("output_dir") or "outputs",
        resolved_task_id,
    )
    if snapshot_config:
        snapshot_config_specs(config_spec, resolved_output_dir, merged_config=config)

    config = recursive_merge(
        config,
        {
            "run": {
                "task": resolved_task,
                "task_id": resolved_task_id or UNSET,
                "start_url": resolved_start_url or UNSET,
            },
            "environment": {
                "output_dir": str(resolved_output_dir),
                "start_url": resolved_start_url or UNSET,
                "headless": False if debug else UNSET,
                "devtools": True if debug else UNSET,
                "keep_open_on_exit": True if debug else UNSET,
                "prompt_before_close": True if debug else UNSET,
                "slow_mo_ms": 250 if debug else UNSET,
            },
            "model": {
                "error_log_path": str(resolved_output_dir / "runtime_errors.jsonl"),
            },
            "agent": {
                "output_path": str(resolved_output_dir / "trajectory.json"),
            },
        },
    )
    fingerprint = make_fingerprint(config)
    cache_metadata = _cache_metadata(config, fingerprint)
    config = recursive_merge(
        config,
        {
            "agent": {
                "cache": config.get("cache", {}),
                "cache_fingerprint": fingerprint,
                "cache_metadata": cache_metadata,
            }
        },
    )

    cached_result = _try_replay_cache(
        config=config,
        fingerprint=fingerprint,
        task=resolved_task,
        task_id=resolved_task_id,
        start_url=resolved_start_url,
    )
    if cached_result is not None:
        cached_result["_output_dir"] = str(resolved_output_dir)
        console.print(cached_result.get("final_response") or cached_result.get("submission") or "Task finished.")
        return cached_result

    model = get_model(config.get("model", {}))
    env = get_environment(config.get("environment", {}))
    agent = get_agent(model, env, config.get("agent", {}), default_type="default")

    console.print(f"Running task in [bold green]{resolved_output_dir}[/bold green]")
    run_exception: Exception | None = None
    close_exception: Exception | None = None
    result: dict[str, Any] = {}
    try:
        env.prepare(
            task=resolved_task,
            task_id=resolved_task_id,
            start_url=resolved_start_url,
        )
        result = agent.run(
            resolved_task,
            task_id=resolved_task_id or "",
            start_url=resolved_start_url or "",
        )
    except Exception as exc:
        run_exception = exc
        if getattr(agent, "messages", None):
            result = dict(agent.messages[-1].get("extra", {}))
        result.setdefault("exit_status", type(exc).__name__)
        result.setdefault("submission", "")
        result.setdefault("final_response", "")
        result["run_exception"] = str(exc)
    finally:
        try:
            env.close()
        except Exception as exc:
            close_exception = exc
            result.setdefault("exit_status", type(exc).__name__)
            result.setdefault("submission", "")
            result.setdefault("final_response", "")
            result.setdefault("run_exception", str(exc))
            result["close_exception"] = str(exc)
            if run_exception is None:
                run_exception = exc
    result["_output_dir"] = str(resolved_output_dir)
    if close_exception is not None:
        result["_close_exception"] = str(close_exception)
    console.print(
        result.get("final_response") or result.get("submission") or "Task finished."
    )
    if run_exception is not None:
        raise run_exception
    return result


@app.command()
def main(
    ctx: typer.Context,
    task: str = typer.Option(
        ..., "-t", "--task", help="Natural language task description."
    ),
    task_id: str | None = typer.Option(
        None, "--task-id", help="Optional identifier used in the output directory name."
    ),
    start_url: str | None = typer.Option(
        None, "--start-url", help="Optional starting URL for the task."
    ),
    config_spec: list[str] = typer.Option(DEFAULT_CONFIGS, "-c", "--config"),
    output_dir: Path | None = typer.Option(None, "-o", "--output-dir"),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Launch headed local Playwright with devtools and keep it open for inspection.",
    ),
) -> Any:
    resolved_config_spec = list(config_spec) + _extra_config_specs(list(ctx.args))
    return run_one(
        task=task,
        task_id=task_id,
        start_url=start_url,
        config_spec=resolved_config_spec,
        output_dir=output_dir,
        debug=debug,
    )


@app.command()
def doctor():
    """
    Validate local Webwright setup.
    """
    run_doctor()


if __name__ == "__main__":
    app()
