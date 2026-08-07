import sys
from unittest.mock import MagicMock, patch

import pytest

from webwright.models.base import _validate_bash_command


def _make_proc(returncode: int, stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stderr = stderr
    proc.stdout = ""
    return proc


def test_valid_command_passes_on_posix(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with patch("subprocess.run", return_value=_make_proc(0)):
        _validate_bash_command("echo hello")


def test_invalid_syntax_raises_on_posix(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with patch("subprocess.run", return_value=_make_proc(1, "syntax error near unexpected token")):
        with pytest.raises(ValueError, match="Invalid bash_command syntax"):
            _validate_bash_command("if then fi done ;;;")


def test_validation_skipped_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    _validate_bash_command("if then fi done ;;;")


def test_validation_skipped_on_windows_does_not_call_subprocess(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    with patch("subprocess.run") as mock_run:
        _validate_bash_command("echo hi")
        mock_run.assert_not_called()
