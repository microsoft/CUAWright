"""Check public entrypoints and legacy browser commands after the package rename."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module",
    ["cuawright.run.browser", "webwright.run.cli", "cuawright"],
)
def test_public_module_help(module):
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "Usage" in result.stdout or "usage" in result.stdout


def test_legacy_image_command_remains_brokered():
    from cuawright.environments.local_workspace import (
        LocalWorkspaceEnvironment,
    )

    for module in (
        "webwright.tools.image_read",
        "cuawright.tools.image_read",
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


def test_legacy_runtime_is_retained_independently():
    from pathlib import Path
    import webwright.agents.default as legacy
    import cuawright.agents.browser as current

    assert legacy is not current
    assert "webwright/agents/default.py" in Path(legacy.__file__).as_posix()
    assert "cuawright/agents/browser.py" in Path(current.__file__).as_posix()
