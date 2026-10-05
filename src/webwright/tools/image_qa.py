from __future__ import annotations

import argparse
import base64
import json
import mimetypes
from pathlib import Path
from typing import Any

from webwright.models.base import text_part
from webwright.tools._model_config import load_tool_model


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


def _parse_json_response(raw_text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(raw_text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("image_qa model response must be a JSON object.")
    return parsed


def run_image_qa(
    *,
    image_path: Path | None = None,
    image_paths: list[Path] | tuple[Path, ...] | None = None,
    question: str,
    model_client: Any,
) -> dict[str, Any]:
    resolved_image_paths = _normalize_image_paths(image_path=image_path, image_paths=image_paths)
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
            "Path to a JSON/YAML config containing a top-level `model:` block. "
            "If omitted, reads <workspace-dir>/config_snapshot/merged_config.yaml."
        ),
    )
    parser.add_argument("--timeout-seconds", type=int, default=60, help="HTTP request timeout.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    image_paths = [_resolve_image_path(image_path, workspace_dir=args.workspace_dir) for image_path in args.image]
    model_client = load_tool_model(
        model_config_arg=args.model_config,
        workspace_dir=args.workspace_dir,
        timeout_seconds=args.timeout_seconds,
    )
    result = run_image_qa(
        image_paths=image_paths,
        question=args.question,
        model_client=model_client,
    )
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
