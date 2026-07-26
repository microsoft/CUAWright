from __future__ import annotations

import os
import sys
from pathlib import Path


def _runner_environment() -> dict[str, str]:
    environment = os.environ.copy()
    if sys.platform == "darwin":
        environment.setdefault(
            "PLAYWRIGHT_BROWSERS_PATH",
            str(Path.home() / "Library" / "Caches" / "ms-playwright"),
        )
    return environment


def main() -> None:
    """Run a Python script or module with Webwright's installed interpreter."""
    os.execve(
        sys.executable,
        [sys.executable, *sys.argv[1:]],
        _runner_environment(),
    )


if __name__ == "__main__":
    main()
