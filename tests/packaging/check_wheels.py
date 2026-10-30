"""Verify optional Skill Factory installation using the two built wheels."""

import argparse
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("core", type=Path)
    parser.add_argument("extension", type=Path)
    options = parser.parse_args()
    core = options.core.resolve()
    extension = options.extension.resolve()
    with tempfile.TemporaryDirectory(prefix="cuawright-packaging-") as scratch:
        env = Path(scratch) / "venv"
        venv.EnvBuilder(with_pip=True).create(env)
        python = env / (
            "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        )

        def run(*args):
            subprocess.run([str(python), *args], check=True, cwd=scratch)

        run("-m", "pip", "install", f"{core}[desktop]")
        run(
            "-I",
            "-c",
            "import importlib.util; "
            "from cuawright.webwright.run.cli import app; "
            "from cuawright.desktop.run.cli import parser; "
            "assert importlib.util.find_spec('cuawright.webwright.skill_factory') is None; "
            "assert importlib.util.find_spec('cuawright.webwright.tools.skill_use') is None",
        )
        run("-I", "-m", "cuawright.webwright.run.cli", "--help")
        run("-I", "-m", "cuawright.desktop", "--help")
        run("-m", "pip", "install", str(extension))
        run(
            "-I",
            "-c",
            "import cuawright.webwright.skill_factory as current; "
            "import webwright.skill_factory as legacy; "
            "from cuawright.webwright.tools.skill_use import recommend; "
            "from cuawright.webwright.skill_factory.library import Library; "
            "assert current is legacy; "
            "assert Library('.') is not None",
        )
        run("-I", "-m", "cuawright.webwright.skill_factory", "--help")
        run("-m", "pip", "uninstall", "-y", "cuawright-skill-factory")
        run(
            "-I",
            "-c",
            "import importlib.util; "
            "assert importlib.util.find_spec('cuawright.webwright.skill_factory') is None; "
            "assert importlib.util.find_spec('cuawright.webwright.tools.skill_use') is None",
        )
        run("-I", "-m", "cuawright.webwright.run.cli", "--help")
        run("-I", "-m", "cuawright.desktop", "--help")
        print(
            "Base install, optional extension, legacy imports, and uninstall verified."
        )


if __name__ == "__main__":
    main()
