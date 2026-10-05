"""Unified entry point; backend imports occur only after dispatch."""

import sys


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("--help", "-h"):
        print(
            "Usage: cuawright {web,desktop} [OPTIONS]\n\n"
            "  web       Browser tasks (use 'web main' or 'web doctor')\n"
            "  desktop   Desktop tasks\n\n"
            "Existing cuawright-web, cuawright-desktop and webwright commands remain available."
        )
        return 0
    backend = args.pop(0)
    if backend == "web":
        from .browser import app

        app(args=args, prog_name="cuawright web")
        return 0
    if backend == "desktop":
        from .desktop import main as desktop_main

        return desktop_main(args)
    print(f"Unknown backend: {backend}. Choose web or desktop.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
