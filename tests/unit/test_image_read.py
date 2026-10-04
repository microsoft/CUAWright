from __future__ import annotations

import base64
import json
from pathlib import Path

from webwright.environments.local_workspace import LocalWorkspaceEnvironment
from webwright.models.openai_model import OpenAIModel
from webwright.tools.image_read import MAX_IMAGE_READ_BYTES, image_read_descriptor, main

_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO0pL1sAAAAASUVORK5CYII="
)


def test_image_read_cli_emits_bounded_magic_typed_descriptor(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    image = tmp_path / "actually-a-png.jpg"
    image.write_bytes(_TINY_PNG)
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))

    assert main(["--path", str(image)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload == image_read_descriptor(str(image), workspace_dir=tmp_path)
    assert payload["media_type"] == "image/png"
    assert payload["size_bytes"] == len(_TINY_PNG)


def test_image_read_rejects_oversized_or_outside_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-image-read.png"
    outside.write_bytes(_TINY_PNG)
    try:
        try:
            image_read_descriptor(str(outside), workspace_dir=tmp_path)
        except ValueError as exc:
            assert "inside the task workspace" in str(exc)
        else:
            raise AssertionError("outside-workspace image was accepted")

        oversized = tmp_path / "oversized.png"
        oversized.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * MAX_IMAGE_READ_BYTES)
        try:
            image_read_descriptor(str(oversized), workspace_dir=tmp_path)
        except ValueError as exc:
            assert "exceeds" in str(exc)
        else:
            raise AssertionError("oversized image was accepted")
    finally:
        outside.unlink(missing_ok=True)


def test_image_read_rejects_noncanonical_and_symlink_paths(tmp_path: Path) -> None:
    image = tmp_path / "state.png"
    image.write_bytes(_TINY_PNG)
    alias = tmp_path / "alias.png"
    alias.symlink_to(image)

    for invalid_path in ("state.png", f"{tmp_path}//state.png", str(alias)):
        try:
            image_read_descriptor(invalid_path, workspace_dir=tmp_path)
        except ValueError as exc:
            assert "canonical" in str(exc)
        else:
            raise AssertionError(f"noncanonical path was accepted: {invalid_path}")


def test_local_workspace_brokers_only_standalone_image_read(tmp_path: Path) -> None:
    runtime_dir = Path(__file__).parents[2] / "src"
    workspace = tmp_path / "workspace"
    env = LocalWorkspaceEnvironment(
        output_dir=workspace,
        credentials_file=None,
        env={"PYTHONPATH": str(runtime_dir)},
        command_timeout_seconds=10,
    )
    env.prepare(task="inspect an image", task_id="image-read")
    image = workspace / "screenshots" / "state.png"
    image.write_bytes(_TINY_PNG)

    result = env.execute({"bash_command": f'python -m webwright.tools.image_read --path "{image}"'})

    assert result["returncode"] == 0
    assert len(result["observation"]["image_attachments"]) == 1
    attachment = result["observation"]["image_attachments"][0]
    assert attachment["path"] == str(image)
    assert attachment["media_type"] == "image/png"

    combined = env.execute({"bash_command": f'python -m webwright.tools.image_read --path "{image}" | cat'})
    assert combined["returncode"] == 0
    assert combined["observation"]["image_attachments"] == []


def test_explicit_image_read_attaches_when_automatic_screenshots_are_disabled(
    tmp_path: Path,
) -> None:
    image = tmp_path / "state.png"
    image.write_bytes(_TINY_PNG)
    descriptor = image_read_descriptor(str(image), workspace_dir=tmp_path)
    model = OpenAIModel(
        openai_api_key="dummy",
        observation_template="Status={{ 'ok' if observation.success else 'error' }}",
        attach_observation_screenshot=False,
    )

    messages = model.format_observation_messages(
        {},
        [
            {
                "observation": {
                    "success": True,
                    "screenshot_path": str(image),
                    "image_attachments": [descriptor],
                }
            }
        ],
    )

    assert [part["type"] for part in messages[0]["content"]] == [
        "input_text",
        "input_image",
    ]
    assert messages[0]["content"][1]["image_url"].startswith("data:image/png;base64,")
