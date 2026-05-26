from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

from webwright.models import get_model
from webwright.models.base import text_part
from webwright.models.openai_model import _extract_response_text

_RETRYABLE_STATUS_CODES = frozenset({400, 408, 409, 425, 429, 500, 502, 503, 504})


def _build_prompt(question: str) -> str:
    return (
        "Answer the user's question using only visible evidence from the provided image or images. "
        "If the answer is not visible, say so instead of guessing. Return only a JSON object with "
        "string `answer`, string array `evidence`, boolean `unknown`, and number `confidence`.\n\n"
        f"Question: {question.strip()}"
    )


def _high_detail_image_part_from_path(image_path: Path) -> dict[str, Any]:
    mime_type, _ = mimetypes.guess_type(str(image_path))
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return {
        "type": "input_image",
        "image_url": f"data:{mime_type or 'image/png'};base64,{encoded}",
        "detail": "high",
    }


def _resolve_image_path(image_path: str, workspace_dir: str = "") -> Path:
    path = Path(image_path)
    if not path.is_absolute():
        base_dir = Path(workspace_dir) if workspace_dir else Path.cwd()
        path = base_dir / path
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"Image path does not exist: {path}")
    return path


def _normalize_image_paths(
    *,
    image_path: Path | None = None,
    image_paths: list[Path] | tuple[Path, ...] | None = None,
) -> list[Path]:
    normalized = list(image_paths or [])
    if image_path is not None:
        normalized.insert(0, image_path)
    if not normalized:
        raise ValueError("At least one image path is required.")
    return normalized


def _openai_config(args: argparse.Namespace) -> tuple[str, str, str]:
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY.")
    endpoint = args.endpoint or "https://api.openai.com/v1/responses"
    model = args.model or os.environ.get("OPENAI_MODEL", "gpt-5.4")
    return api_key, endpoint, model


def _load_structured_config(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        loaded = yaml.safe_load(text)
    else:
        loaded = json.loads(text)
    if not isinstance(loaded, dict):
        raise ValueError(f"Model config must be an object: {path}")
    return loaded


def _extract_model_config(config: dict[str, Any], *, tool_name: str) -> dict[str, Any]:
    tool_model = (
        config.get("tools", {})
        .get(tool_name, {})
        .get("model")
    )
    if isinstance(tool_model, dict):
        return tool_model
    model_config = config.get("model")
    if isinstance(model_config, dict):
        return model_config
    return config


def _resolve_model_config_path(model_config: str, *, workspace_dir: str = "") -> Path | None:
    candidates: list[Path] = []
    if model_config:
        configured_path = Path(model_config)
        candidates.append(configured_path)
        if workspace_dir and not configured_path.is_absolute():
            candidates.append(Path(workspace_dir) / configured_path)
    env_config = os.environ.get("WEBWRIGHT_TOOL_MODEL_CONFIG", "")
    if env_config:
        candidates.append(Path(env_config))
    if workspace_dir:
        workspace_path = Path(workspace_dir)
        candidates.extend(
            [
                workspace_path / "tool_model_config.json",
                workspace_path / "config_snapshot" / "merged_config.yaml",
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def _load_model_config(model_config: str, *, workspace_dir: str = "", tool_name: str) -> dict[str, Any] | None:
    config_path = _resolve_model_config_path(model_config, workspace_dir=workspace_dir)
    if config_path is None:
        return None
    return _extract_model_config(_load_structured_config(config_path), tool_name=tool_name)


def _model_from_config(
    model_config: dict[str, Any],
    *,
    timeout_seconds: int,
) -> Any:
    resolved = dict(model_config)
    resolved["request_timeout_seconds"] = timeout_seconds
    return get_model(resolved)


def _parse_json_response(raw_text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(raw_text[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("image_qa model response must be a JSON object.")
    return parsed


def _sleep_backoff(attempt: int, base_delay: float) -> float:
    delay = base_delay * (2 ** (attempt - 1))
    delay += random.uniform(0.0, delay * 0.25)
    time.sleep(delay)
    return delay


def _post_with_retry(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict[str, Any],
    max_attempts: int,
    base_delay: float,
) -> httpx.Response:
    last_error: str = ""
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.post(url, headers=headers, json=json_body)
        except httpx.TransportError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt >= max_attempts:
                raise
            delay = _sleep_backoff(attempt, base_delay)
            print(
                f"[image_qa] transport error on attempt {attempt}/{max_attempts}: "
                f"{last_error}; retrying in {delay:.2f}s",
                file=sys.stderr,
            )
            continue

        if response.status_code in _RETRYABLE_STATUS_CODES and attempt < max_attempts:
            snippet = response.text[:500].replace("\n", " ") if response.text else ""
            delay = _sleep_backoff(attempt, base_delay)
            print(
                f"[image_qa] retryable HTTP {response.status_code} on attempt "
                f"{attempt}/{max_attempts}: {snippet}; retrying in {delay:.2f}s",
                file=sys.stderr,
            )
            continue

        response.raise_for_status()
        return response

    raise RuntimeError("image_qa retry loop exited without returning")


def run_image_qa(
    *,
    image_path: Path | None = None,
    image_paths: list[Path] | tuple[Path, ...] | None = None,
    question: str,
    api_key: str = "",
    endpoint: str = "",
    model: str = "",
    model_config: dict[str, Any] | None = None,
    timeout_seconds: int = 60,
    max_attempts: int = 4,
    retry_base_delay: float = 1.0,
) -> dict[str, Any]:
    resolved_image_paths = _normalize_image_paths(image_path=image_path, image_paths=image_paths)
    if model_config is not None:
        model_client = _model_from_config(model_config, timeout_seconds=timeout_seconds)
        raw_text = model_client(
            [
                {
                    "role": "user",
                    "content": [text_part(_build_prompt(question))]
                    + [_high_detail_image_part_from_path(path) for path in resolved_image_paths],
                }
            ],
            max_output_tokens=32000,
        ).strip()
        parsed = _parse_json_response(raw_text)
        result = {
            "image_paths": [str(path) for path in resolved_image_paths],
            "question": question,
            **parsed,
        }
        if len(resolved_image_paths) == 1:
            result["image_path"] = str(resolved_image_paths[0])
        return result

    payload = {
        "model": model,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [text_part(_build_prompt(question))]
                + [_high_detail_image_part_from_path(path) for path in resolved_image_paths],
            }
        ],
        "max_output_tokens": 32000,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "image_qa_answer",
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "answer": {"type": "string"},
                        "evidence": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "unknown": {"type": "boolean"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["answer", "evidence", "unknown", "confidence"],
                },
                "strict": True,
            }
        },
    }

    with httpx.Client(timeout=timeout_seconds) as client:
        response = _post_with_retry(
            client,
            endpoint,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            json_body=payload,
            max_attempts=max_attempts,
            base_delay=retry_base_delay,
        )
        response_payload = response.json()

    raw_text = _extract_response_text(response_payload).strip()
    parsed = json.loads(raw_text)
    result = {
        "image_paths": [str(path) for path in resolved_image_paths],
        "question": question,
        **parsed,
    }
    if len(resolved_image_paths) == 1:
        result["image_path"] = str(resolved_image_paths[0])
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ask a visual question about a local image and print JSON.")
    parser.add_argument(
        "--image",
        required=True,
        action="append",
        help="Path to an image file. Repeat --image to include multiple images.",
    )
    parser.add_argument("--question", required=True, help="Question to answer from the image.")
    parser.add_argument("--workspace-dir", default="", help="Optional base directory for relative image paths.")
    parser.add_argument(
        "--model-config",
        default="",
        help=(
            "Path to a JSON/YAML model config. If omitted, image_qa also checks "
            "WEBWRIGHT_TOOL_MODEL_CONFIG and <workspace-dir>/config_snapshot/merged_config.yaml."
        ),
    )
    parser.add_argument("--model", default="", help="Override the OpenAI model name.")
    parser.add_argument("--endpoint", default="", help="Override the OpenAI Responses endpoint.")
    parser.add_argument("--api-key", default="", help="Override the OpenAI API key.")
    parser.add_argument("--timeout-seconds", type=int, default=60, help="HTTP request timeout.")
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=4,
        help="Max total HTTP attempts before giving up (1 = no retry).",
    )
    parser.add_argument(
        "--retry-base-delay",
        type=float,
        default=1.0,
        help="Base delay (seconds) for exponential backoff between retries.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    image_paths = [_resolve_image_path(image_path, workspace_dir=args.workspace_dir) for image_path in args.image]
    model_config = _load_model_config(
        args.model_config,
        workspace_dir=args.workspace_dir,
        tool_name="image_qa",
    )
    if model_config is not None:
        result = run_image_qa(
            image_paths=image_paths,
            question=args.question,
            model_config=model_config,
            timeout_seconds=args.timeout_seconds,
            max_attempts=args.max_attempts,
            retry_base_delay=args.retry_base_delay,
        )
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0

    api_key, endpoint, model = _openai_config(args)
    result = run_image_qa(
        image_paths=image_paths,
        question=args.question,
        api_key=api_key,
        endpoint=endpoint,
        model=model,
        timeout_seconds=args.timeout_seconds,
        max_attempts=args.max_attempts,
        retry_base_delay=args.retry_base_delay,
    )
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
