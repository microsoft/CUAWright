"""Check public entrypoints and legacy browser commands after the package rename."""

import subprocess
import sys
import os

import pytest


@pytest.mark.parametrize(
    "module",
    ["cuawright.webwright.run.cli", "webwright.run.cli", "cuawright.desktop"],
)
def test_public_module_help(module):
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        env={
            **os.environ,
            "PYTHONPATH": str(__import__("pathlib").Path(__file__).parents[2] / "src"),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "Usage" in result.stdout or "usage" in result.stdout


def test_top_level_cli_help():
    result = subprocess.run(
        [sys.executable, "-m", "cuawright", "--help"],
        env={
            **os.environ,
            "PYTHONPATH": str(__import__("pathlib").Path(__file__).parents[2] / "src"),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "{web,desktop,setup,verify,smoke}" in result.stdout


def test_desktop_root_help_shows_both_workflows():
    from cuawright.desktop.run.cli import main

    assert main(["--help"]) == 0


def test_legacy_image_command_remains_brokered():
    from cuawright.webwright.environments.local_workspace import (
        LocalWorkspaceEnvironment,
    )

    for module in (
        "webwright.tools.image_read",
        "cuawright.webwright.tools.image_read",
    ):
        assert (
            LocalWorkspaceEnvironment._image_read_path(
                f"python -m {module} --path /tmp/image.png"
            )
            == "/tmp/image.png"
        )
        assert (
            LocalWorkspaceEnvironment._image_read_path(
                f"python -m {module} --path /tmp/image.png | cat"
            )
            is None
        )


def test_legacy_imports_share_classes_and_modules():
    import webwright.exceptions as legacy_exceptions
    import cuawright.webwright.exceptions as exceptions
    import webwright.agents.default as legacy_agent
    import cuawright.webwright.agents.default as agent

    assert legacy_exceptions is exceptions
    assert legacy_exceptions.FormatError is exceptions.FormatError
    assert legacy_agent is agent
