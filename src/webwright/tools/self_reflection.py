"""Self-reflection two-stage screenshot judge CLI.

Previously named ``two_stage_judge``; renamed to ``self_reflection``.

Stage 1: for each screenshot, send a (system, user + image) pair to the
configured model and parse a 1-5 ``Score`` with a short ``Reasoning``.

Stage 2: drop every per-image ``Reasoning`` into the caller-provided final
user prompt template (via ``{image_reasonings}``), attach EVERY screenshot,
and make one final call that must end with ``Status: success`` or
``Status: failure``.

The CLI reads all of its config from a single JSON file so the agent can
prepare it in one turn and invoke the tool in the next.

Usage::

    python -m webwright.tools.self_reflection --config self_reflect_config.json

JSON schema (paths relative to ``--workspace-dir`` or the CWD)::

    {
      "images": ["final_runs/run_001/screenshots/final_execution_1.png", ...],
      "image_judge_system_prompt":     "...",
      "image_judge_user_prompt":       "...",           // sent verbatim with each image
            "final_verdict_system_prompt":   "...",
            "final_verdict_user_prompt":     "...{action_history_log}...{image_reasonings}..."
    }

Any of the four prompt fields may instead be supplied via
``<field>_file`` variants pointing to a text file on disk (recommended when
prompts contain many literal braces or newlines).

The output JSON written to ``--output`` (or stdout) contains the per-image
records, the image path list, the final response, and
``predicted_label`` (``1`` for success, ``0`` for failure, ``null`` if the
``Status:`` line could not be parsed). Exit code: 0 if PASS, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from webwright.models.base import text_part
from webwright.tools._model_config import load_tool_model

DEFAULT_IMAGE_PARSE_MAX_RETRIES = 3

_PROMPT_FIELDS = (
    ("image_judge_system_prompt", True),
    ("image_judge_user_prompt", True),
    ("final_verdict_system_prompt", True),
    ("final_verdict_user_prompt", True),
)

_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _resolve_image_path(image_path: str, workspace_dir: str = "") -> Path:
    path = Path(image_path)
    if not path.is_absolute():
        base_dir = Path(workspace_dir) if workspace_dir else Path.cwd()
        path = base_dir / path
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"Image path does not exist: {path}")
    return path


def _final_execution_sort_key(name: str) -> tuple[int, str]:
    match = re.match(r"final_execution_(\d+)_", name)
    if match:
        return (int(match.group(1)), name)
    nums = re.findall(r"\d+", name)
    return (int(nums[0]) if nums else 0, name)


def _run_id_sort_key(name: str) -> tuple[int, str]:
    match = re.search(r"run_(\d+)", name)
    if match:
        return (int(match.group(1)), name)
    return (0, name)


def _sorted_image_paths(image_dir: Path) -> list[Path]:
    if not image_dir.is_dir():
        return []
    return sorted(
        [path for path in image_dir.iterdir() if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES],
        key=lambda path: _final_execution_sort_key(path.name),
    )


def _discover_latest_run_screenshots(
    final_runs_dir: Path,
) -> tuple[Path | None, list[Path]]:
    """Find the highest-numbered ``final_runs/run_<id>/screenshots`` dir and its images.

    Returns ``(run_dir_or_None, sorted_image_paths)``. Empty list if no images found.
    """
    if not final_runs_dir.exists() or not final_runs_dir.is_dir():
        return None, []
    candidates = sorted(
        (d for d in final_runs_dir.iterdir() if d.is_dir() and re.fullmatch(r"run_\d+", d.name)),
        key=lambda p: _run_id_sort_key(p.name),
    )
    # Walk from highest-numbered run downward and pick the first one with any screenshots.
    for run_dir in reversed(candidates):
        screenshots_dir = run_dir / "screenshots"
        images = _sorted_image_paths(screenshots_dir)
        if images:
            return run_dir, images
    return None, []


def _infer_run_dir_from_images(images: list[Path]) -> Path | None:
    run_dirs = {
        path.parent.parent.resolve()
        for path in images
        if path.parent.name == "screenshots"
    }
    if len(run_dirs) == 1:
        return next(iter(run_dirs))
    return None


def _resolve_artifact_dir(
    *,
    images: list[Path],
    discovered_run_dir: Path | None,
    output_path: str,
    workspace_dir: str,
) -> Path | None:
    candidates: list[Path] = []

    inferred_run_dir = _infer_run_dir_from_images(images)
    if inferred_run_dir is not None:
        candidates.append(inferred_run_dir)

    if discovered_run_dir is not None:
        candidates.append(discovered_run_dir.resolve())

    if output_path:
        candidates.append(Path(output_path).resolve().parent)

    base_dir = Path(workspace_dir).resolve() if workspace_dir else Path.cwd().resolve()
    candidates.append(base_dir)

    seen: set[Path] = set()
    ordered_candidates: list[Path] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        ordered_candidates.append(candidate)

    for candidate in ordered_candidates:
        if (candidate / "final_script_log.txt").is_file():
            return candidate

    return ordered_candidates[0] if ordered_candidates else None


def _load_action_history_log(artifact_dir: Path | None) -> str:
    if artifact_dir is None:
        return ""
    log_path = artifact_dir / "final_script_log.txt"
    if not log_path.is_file():
        return ""
    return log_path.read_text(encoding="utf-8").rstrip()


def _render_final_verdict_user_prompt(
    template: str,
    *,
    image_reasonings: str,
    action_history_log: str,
) -> str:
    rendered = template
    if "{image_reasonings}" in template or "{action_history_log}" in template:
        try:
            rendered = template.format(
                image_reasonings=image_reasonings,
                action_history_log=action_history_log,
            )
        except KeyError as exc:
            raise ValueError(
                "Unknown placeholder in final_verdict_user_prompt: "
                f"{exc.args[0]!r}. Supported placeholders are "
                "{image_reasonings} and {action_history_log}; double any literal "
                "braces as {{ and }}."
            ) from exc
    return rendered


def _high_detail_image_part_from_path(image_path: Path) -> dict[str, Any]:
    mime_type, _ = mimetypes.guess_type(str(image_path))
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return {
        "type": "input_image",
        "image_url": f"data:{mime_type or 'image/png'};base64,{encoded}",
        "detail": "high",
    }


# ---------------------------------------------------------------------------
# Model call: plain message list -> text
# ---------------------------------------------------------------------------

def _call_model(
    *,
    model_client: Any,
    system_prompt: str,
    user_content: list[dict[str, Any]],
    max_new_tokens: int,
) -> str:
    return model_client(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        max_output_tokens=max_new_tokens,
    ).strip()


def _model_endpoint(model_client: Any) -> str:
    config = getattr(model_client, "config", None)
    for key in ("openai_endpoint", "anthropic_endpoint", "openrouter_endpoint"):
        value = getattr(config, key, "")
        if value:
            return str(value)
    return ""


# ---------------------------------------------------------------------------
# Parsing helpers (ported from webjudge_online_mind2web_sandbox.py)
# ---------------------------------------------------------------------------

def _parse_image_judge_response(response: str) -> tuple[str, int]:
    score_match = re.search(r"(?is)\bscore\b[^1-5]*([1-5])\b", response)
    reasoning_match = re.search(
        r"(?is)(?:\*\*?\s*reasoning\s*\*\*?|reasoning)\s*[:\-]\s*"
        r"(.*?)(?=\n\s*(?:\d+\.\s*)?(?:\*\*?\s*score\s*\*\*?|score)\s*[:\-]|\Z)",
        response,
    )

    if score_match and reasoning_match:
        reasoning = re.sub(r"\s+", " ", reasoning_match.group(1)).strip()
        return reasoning, int(score_match.group(1))

    try:
        payload = json.loads(response)
    except Exception:
        payload = None

    if isinstance(payload, dict):
        score = payload.get("Score", payload.get("score"))
        reasoning = payload.get("Reasoning", payload.get("reasoning"))
        if (
            isinstance(score, int)
            and 1 <= score <= 5
            and isinstance(reasoning, str)
            and reasoning.strip()
        ):
            return re.sub(r"\s+", " ", reasoning).strip(), score

    raise ValueError("Could not parse image judge response")


def _parse_final_verdict(response: str) -> int | None:
    matches = list(re.finditer(r"(?i)status:\s*", response))
    if not matches:
        return None
    tail = response[matches[-1].end():].strip()
    m = re.match(r"""^[\'\"“”‘’\s]*(success|failure)\b""", tail, re.IGNORECASE)
    if not m:
        return None
    return 1 if m.group(1).lower() == "success" else 0


# ---------------------------------------------------------------------------
# Per-image scoring
# ---------------------------------------------------------------------------

async def _judge_one_image(
    *,
    image_path: Path,
    image_judge_system_prompt: str,
    image_judge_user_prompt: str,
    model_client: Any,
    max_new_tokens: int,
    max_parse_retries: int,
) -> dict[str, Any]:
    user_content = [
        text_part(image_judge_user_prompt),
        _high_detail_image_part_from_path(image_path),
    ]

    last_response = ""
    last_error: BaseException | None = None
    for attempt in range(1, max_parse_retries + 1):
        last_response = await asyncio.to_thread(
            _call_model,
            model_client=model_client,
            system_prompt=image_judge_system_prompt,
            user_content=user_content,
            max_new_tokens=max_new_tokens,
        )
        try:
            reasoning, score = _parse_image_judge_response(last_response)
            return {
                "image_path": str(image_path),
                "Response": last_response,
                "Score": score,
                "Reasoning": reasoning,
                "Attempts": attempt,
                "ParseFailed": False,
            }
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(
                f"[self_reflection] parse attempt {attempt}/{max_parse_retries} failed for "
                f"{image_path}: {exc}",
                file=sys.stderr,
            )

    return {
        "image_path": str(image_path),
        "Response": last_response,
        "Score": 0,
        "Reasoning": "",
        "Attempts": max_parse_retries,
        "ParseFailed": True,
        "ParseError": str(last_error) if last_error is not None else "unknown",
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

@dataclass
class SelfReflectionResult:
    image_records: list[dict[str, Any]]
    image_paths: list[str]
    final_user_text: str
    final_system_msg: str
    final_response: str
    predicted_label: int | None  # 1 success, 0 failure, None unparsed
    model: str = ""
    endpoint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "endpoint": self.endpoint,
            "predicted_label": self.predicted_label,
            "final_response": self.final_response,
            "final_user_text": self.final_user_text,
            "final_system_msg": self.final_system_msg,
            "image_paths": self.image_paths,
            "image_records": self.image_records,
        }


async def run_self_reflection_async(
    *,
    images: list[Path],
    image_judge_system_prompt: str,
    image_judge_user_prompt: str,
    final_verdict_system_prompt: str,
    final_verdict_user_prompt: str,
    action_history_log: str,
    max_image_parse_retries: int,
    final_max_new_tokens: int,
    image_max_new_tokens: int,
    model_client: Any,
) -> SelfReflectionResult:
    model_name = str(getattr(model_client.config, "model_name", ""))
    endpoint = _model_endpoint(model_client)

    if images:
        per_image = await asyncio.gather(
            *(
                _judge_one_image(
                    image_path=path,
                    image_judge_system_prompt=image_judge_system_prompt,
                    image_judge_user_prompt=image_judge_user_prompt,
                    model_client=model_client,
                    max_new_tokens=image_max_new_tokens,
                    max_parse_retries=max_image_parse_retries,
                )
                for path in images
            )
        )
    else:
        per_image = []

    image_paths = [record["image_path"] for record in per_image]
    reasonings = [record["Reasoning"] or "" for record in per_image]

    reasonings_block = "\n".join(
        f"{i + 1}. {text}" for i, text in enumerate(reasonings)
    )

    final_user_text = _render_final_verdict_user_prompt(
        final_verdict_user_prompt,
        image_reasonings=reasonings_block,
        action_history_log=action_history_log,
    )

    user_content: list[dict[str, Any]] = [text_part(final_user_text)]
    for path_str in image_paths:
        user_content.append(_high_detail_image_part_from_path(Path(path_str)))

    final_response = await asyncio.to_thread(
        _call_model,
        model_client=model_client,
        system_prompt=final_verdict_system_prompt,
        user_content=user_content,
        max_new_tokens=final_max_new_tokens,
    )
    predicted_label = _parse_final_verdict(final_response)

    return SelfReflectionResult(
        image_records=list(per_image),
        image_paths=image_paths,
        final_user_text=final_user_text,
        final_system_msg=final_verdict_system_prompt,
        final_response=final_response,
        predicted_label=predicted_label,
        model=model_name,
        endpoint=endpoint,
    )


def run_self_reflection(**kwargs: Any) -> SelfReflectionResult:
    return asyncio.run(run_self_reflection_async(**kwargs))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resolve_prompt(cfg: dict[str, Any], key: str, *, required: bool) -> str | None:
    inline = cfg.get(key)
    file_key = f"{key}_file"
    file_path = cfg.get(file_key)
    if inline is not None and file_path is not None:
        raise ValueError(f"Provide only one of {key!r} or {file_key!r}, not both.")
    if file_path is not None:
        return Path(file_path).read_text(encoding="utf-8")
    if inline is not None:
        return inline
    if required:
        raise ValueError(f"Missing required prompt: {key} (or {file_key}).")
    return None


def _load_config(config_arg: str) -> dict[str, Any]:
    if config_arg == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(config_arg).read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Two-stage screenshot judge. Reads a JSON config describing images and "
            "prompts, calls the configured model, and prints a "
            "JSON result with per-image records and the final verdict."
        )
    )
    parser.add_argument("--config", required=True, help="Path to JSON config, or '-' for stdin.")
    parser.add_argument("--workspace-dir", default="", help="Base directory for relative image paths.")
    parser.add_argument("--output", default="", help="Write JSON result to this path instead of stdout.")
    parser.add_argument(
        "--auto-latest-run",
        default="final_runs",
        help=(
            "When the config has no 'images' list, auto-discover screenshots from the "
            "highest-numbered `<workspace-dir>/<this-value>/run_<id>/screenshots` folder. "
            "Default: 'final_runs'. Pass '' (empty string) to disable auto-discovery."
        ),
    )
    parser.add_argument("--max-image-parse-retries", type=int, default=DEFAULT_IMAGE_PARSE_MAX_RETRIES)
    parser.add_argument("--image-max-new-tokens", type=int, default=1024)
    parser.add_argument("--final-max-new-tokens", type=int, default=8192)
    parser.add_argument(
        "--model-config",
        default="",
        help=(
            "Path to a JSON/YAML config containing a top-level `model:` block. "
            "If omitted, reads <workspace-dir>/config_snapshot/merged_config.yaml."
        ),
    )
    parser.add_argument("--timeout-seconds", type=int, default=120)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    base_dir = Path(args.workspace_dir).resolve() if args.workspace_dir else Path.cwd().resolve()

    cfg = _load_config(args.config)

    prompts = {
        key: _resolve_prompt(cfg, key, required=required)
        for key, required in _PROMPT_FIELDS
    }

    images_config = cfg.get("images") or cfg.get("images_path") or []
    resolved_images = [
        _resolve_image_path(p, workspace_dir=args.workspace_dir) for p in images_config
    ]
    discovered_run_dir = _infer_run_dir_from_images(resolved_images)

    # If config did not provide images, fall back to the latest run's screenshots.
    if not resolved_images:
        discovered: list[Path] = []
        discovered_source = ""
        if args.auto_latest_run:
            auto_root = Path(args.auto_latest_run)
            if not auto_root.is_absolute():
                auto_root = base_dir / auto_root
            auto_root = auto_root.resolve()
            discovered_run_dir, discovered = _discover_latest_run_screenshots(auto_root)
            if discovered_run_dir is not None:
                discovered_source = str(discovered_run_dir / "screenshots")
        if discovered:
            resolved_images = discovered
            print(
                f"[self_reflection] auto-discovered {len(resolved_images)} screenshots from {discovered_source}",
                file=sys.stderr,
            )

    artifact_dir = _resolve_artifact_dir(
        images=resolved_images,
        discovered_run_dir=discovered_run_dir,
        output_path=args.output,
        workspace_dir=args.workspace_dir,
    )
    action_history_log = _load_action_history_log(artifact_dir)

    if not resolved_images:
        print(
            "[self_reflection] warning: no images provided; final stage will run without screenshot attachments.",
            file=sys.stderr,
        )

    if not action_history_log:
        print(
            "[self_reflection] warning: no final_script_log.txt found; final prompt will omit action history content.",
            file=sys.stderr,
        )

    model_client = load_tool_model(
        model_config_arg=args.model_config,
        workspace_dir=args.workspace_dir,
        timeout_seconds=args.timeout_seconds,
    )

    result = run_self_reflection(
        images=resolved_images,
        image_judge_system_prompt=prompts["image_judge_system_prompt"],
        image_judge_user_prompt=prompts["image_judge_user_prompt"],
        final_verdict_system_prompt=prompts["final_verdict_system_prompt"],
        final_verdict_user_prompt=prompts["final_verdict_user_prompt"],
        action_history_log=action_history_log,
        max_image_parse_retries=args.max_image_parse_retries,
        final_max_new_tokens=args.final_max_new_tokens,
        image_max_new_tokens=args.image_max_new_tokens,
        model_client=model_client,
    )

    payload = result.to_dict()
    serialized = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(serialized, encoding="utf-8")
        print(f"Wrote result to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(serialized)
        sys.stdout.write("\n")

    label = result.predicted_label
    if label == 1:
        print("JUDGE VERDICT: PASS", file=sys.stderr)
        return 0
    if label == 0:
        print("JUDGE VERDICT: FAIL", file=sys.stderr)
        return 1
    print("JUDGE VERDICT: UNPARSED (treating as FAIL)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
