from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

IMAGE_READ_DESCRIPTOR_VERSION = 1
MAX_IMAGE_READ_BYTES = 20 * 1024 * 1024


class ImageReadError(ValueError):
    pass


def _canonical_absolute_path(value: str) -> Path:
    if not value or "\x00" in value or not value.startswith("/"):
        raise ImageReadError("path must be a canonical absolute path")
    candidate = Path(value)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ImageReadError(f"could not resolve image path ({type(exc).__name__})") from None
    if value != str(resolved):
        raise ImageReadError("path must be canonical and must not traverse a symlink")
    if not resolved.is_file():
        raise ImageReadError("path must name a regular file")
    return resolved


def _workspace_root(value: str | os.PathLike[str] | None) -> Path | None:
    if value is None or not str(value).strip():
        return None
    try:
        return Path(value).resolve(strict=True)
    except OSError as exc:
        raise ImageReadError(f"could not resolve workspace ({type(exc).__name__})") from None


def _require_workspace_path(path: Path, workspace_dir: Path | None) -> None:
    if workspace_dir is None:
        return
    try:
        path.relative_to(workspace_dir)
    except ValueError:
        raise ImageReadError("image path must stay inside the task workspace") from None


def image_media_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise ImageReadError("unsupported or invalid image; expected PNG, JPEG, GIF, or WebP")


def image_read_descriptor(
    path_value: str,
    *,
    workspace_dir: str | os.PathLike[str] | None = None,
    max_bytes: int = MAX_IMAGE_READ_BYTES,
) -> dict[str, Any]:
    path = _canonical_absolute_path(path_value)
    workspace = _workspace_root(workspace_dir)
    _require_workspace_path(path, workspace)
    with path.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ImageReadError(f"image exceeds the {max_bytes}-byte limit")
    media_type = image_media_type(data)
    return {
        "webwright_image_read": IMAGE_READ_DESCRIPTOR_VERSION,
        "path": str(path),
        "media_type": media_type,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Attach one workspace image to the persistent agent's next observation."
    )
    parser.add_argument("--path", required=True, help="Canonical absolute workspace image path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        descriptor = image_read_descriptor(
            args.path,
            workspace_dir=os.environ.get("WORKSPACE_DIR"),
        )
    except (ImageReadError, OSError) as exc:
        print(
            json.dumps(
                {
                    "webwright_image_read": IMAGE_READ_DESCRIPTOR_VERSION,
                    "status": "error",
                    "error": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1
    print(
        json.dumps(
            descriptor,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
