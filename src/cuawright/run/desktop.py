import argparse
import sys
from pathlib import Path

from .benchmarks.osworld import Settings, run


def parser():
    result = argparse.ArgumentParser(
        description="CUAWright desktop: one official OSWorld-V2 task on Ubuntu via Docker."
    )
    for flag in ("source", "tasks", "assets", "vm", "credentials", "results"):
        result.add_argument("--" + flag, required=True, type=Path)
    result.add_argument("--proxy-config", type=Path)
    result.add_argument("--task-id", required=True)
    result.add_argument("--model", required=True)
    result.add_argument("--base-url", default="https://api.openai.com/v1")
    result.add_argument(
        "--reasoning", choices=("low", "medium", "high", "xhigh", "max"), default="high"
    )
    result.add_argument("--steps", type=int, default=300)
    result.add_argument("--compact-every", type=int, default=40)
    result.add_argument("--max-output-tokens", type=int, default=32768)
    result.add_argument("--website-host-suffix", default="")
    return result


def main(argv=None):
    if sys.version_info < (3, 12):
        print("CUAWright desktop requires Python 3.12 or newer.", file=sys.stderr)
        return 1
    settings = Settings(**vars(parser().parse_args(argv)))
    try:
        outcome = run(settings)
    except Exception:
        print(
            "OSWorld release failed; inspect result.json if created. "
            "External diagnostics are deliberately not logged.",
            file=sys.stderr,
        )
        return 1
    print(
        f"{outcome['stop_reason']}; score={outcome['score']}; "
        f"model_calls={outcome['model_calls']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
