import argparse
import sys


SMOKE_TASK = (
    "Create /home/user/Desktop/cuawright-smoke.txt containing exactly "
    "'CUAWright OSWorld smoke passed', read it back to verify the content, "
    "then submit."
)


def parser():
    result = argparse.ArgumentParser(
        prog="cuawright", description="CUAWright browser and desktop agents."
    )
    result.add_argument(
        "command", choices=("web", "desktop", "setup", "verify", "smoke")
    )
    result.add_argument("target", nargs="?")
    return result


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        parser().print_help()
        return 0
    command = argv.pop(0)
    if command == "web":
        from .webwright.run.cli import app

        return app(args=argv, prog_name="cuawright web")
    if command == "desktop":
        from .desktop.run.cli import main as desktop_main

        return desktop_main(argv)
    if command in ("setup", "verify"):
        if not argv or argv.pop(0) != "osworld":
            raise SystemExit(f"usage: cuawright {command} osworld ...")
        from .desktop.setup import main as setup_main

        return setup_main([command, *argv])
    if command == "smoke":
        if not argv or argv.pop(0) != "osworld":
            raise SystemExit("usage: cuawright smoke osworld ...")
        from .desktop.run.cli import main as desktop_main

        return desktop_main(["run", "--instruction", SMOKE_TASK, *argv])
    parser().error(f"unknown command: {command}")
