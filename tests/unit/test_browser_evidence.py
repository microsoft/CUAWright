from __future__ import annotations

from pathlib import Path

from cuawright.utils.browser_evidence import (
    append_jsonl,
    format_action_history,
    load_browser_steps,
    trajectory_evidence_digest,
    trajectory_images,
)


def test_trajectory_evidence_preserves_actions_epochs_and_image_order(tmp_path: Path) -> None:
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    first = screenshots / "browser_step_0001.png"
    second = screenshots / "browser_step_0003.png"
    first.write_bytes(b"first-image")
    second.write_bytes(b"second-image")

    manifest = tmp_path / "browser-steps.jsonl"
    append_jsonl(
        manifest,
        {
            "browser_step": 3,
            "agent_step": 5,
            "session_epoch": 2,
            "action": "Open a new tab after the original page failed",
            "success": True,
            "screenshot_path": "screenshots/browser_step_0003.png",
            "url_after": "https://example.com/recovered",
        },
    )
    append_jsonl(
        manifest,
        {
            "browser_step": 1,
            "agent_step": 2,
            "session_epoch": 1,
            "action": "Open the search page",
            "success": True,
            "screenshot_path": "screenshots/browser_step_0001.png",
            "url_after": "https://example.com/search",
        },
    )

    rows = load_browser_steps(tmp_path)
    assert [row["browser_step"] for row in rows] == [1, 3]
    assert [path.name for path, _row in trajectory_images(tmp_path, rows)] == [
        "browser_step_0001.png",
        "browser_step_0003.png",
    ]
    history = format_action_history(rows)
    assert "session epoch 1" in history
    assert "Open a new tab after the original page failed" in history

    before = trajectory_evidence_digest(tmp_path, rows)
    second.write_bytes(b"changed-image")
    after = trajectory_evidence_digest(tmp_path, rows)
    assert before != after


def test_load_browser_steps_preserves_unicode_line_separator(tmp_path: Path) -> None:
    append_jsonl(
        tmp_path / "browser-steps.jsonl",
        {
            "browser_step": 1,
            "agent_step": 2,
            "action": "Inspect the page",
            "python_output": "first section\u2028second section",
            "success": True,
        },
    )

    rows = load_browser_steps(tmp_path)

    assert len(rows) == 1
    assert rows[0]["python_output"] == "first section\u2028second section"


def test_trajectory_digest_covers_unreferenced_task_screenshots(tmp_path: Path) -> None:
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    extra = screenshots / "manual_evidence.png"
    extra.write_bytes(b"first-version")

    before = trajectory_evidence_digest(tmp_path, [])
    extra.write_bytes(b"second-version")
    after = trajectory_evidence_digest(tmp_path, [])

    assert before != after
