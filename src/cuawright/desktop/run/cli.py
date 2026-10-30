import argparse
import json
import sys
from pathlib import Path

from .benchmarks.osworld import CustomSettings, Settings, run, run_custom


def common(parser):
    parser.add_argument("--setup", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--vm", type=Path)
    parser.add_argument("--credentials", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--responses-url", default="https://api.openai.com/v1/responses"
    )
    parser.add_argument(
        "--reasoning", choices=("low", "medium", "high", "xhigh", "max"), default="xhigh"
    )
    parser.add_argument("--steps", type=int, default=300)
    parser.add_argument("--compact-every", type=int, default=40)
    parser.add_argument("--max-output-tokens", type=int, default=32768)
    parser.add_argument("--website-host-suffix")


def parser():
    result = argparse.ArgumentParser(
        description="CUAWright desktop tasks in the pinned OSWorld Ubuntu VM."
    )
    commands = result.add_subparsers(dest="command", required=True)
    custom = commands.add_parser(
        "run", help="Run an arbitrary task without an official evaluator."
    )
    common(custom)
    instruction = custom.add_mutually_exclusive_group(required=True)
    instruction.add_argument("--instruction")
    instruction.add_argument("--instruction-file", type=Path)
    reproduce = commands.add_parser(
        "reproduce", help="Run and score an official OSWorld-V2 task."
    )
    common(reproduce)
    reproduce.add_argument(
        "benchmark", choices=("osworld",), nargs="?", default="osworld"
    )
    reproduce.add_argument("--tasks", type=Path)
    reproduce.add_argument("--assets", type=Path)
    reproduce.add_argument("--proxy-config", type=Path)
    reproduce.add_argument("--task-id", required=True)
    return result


def setup_values(path):
    if path is None:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def required_path(args, setup, name):
    value = getattr(args, name, None) or setup.get(name)
    if value is None:
        raise ValueError(f"--{name.replace('_', '-')} or --setup is required")
    return Path(value)


def settings(args):
    configured = setup_values(args.setup)
    shared = {
        "source": required_path(args, configured, "source"),
        "vm": required_path(args, configured, "vm"),
        "credentials": args.credentials,
        "results": args.results,
        "model": args.model,
        "responses_url": args.responses_url,
        "reasoning": args.reasoning,
        "steps": args.steps,
        "compact_every": args.compact_every,
        "max_output_tokens": args.max_output_tokens,
        "website_host_suffix": (
            args.website_host_suffix
            if args.website_host_suffix is not None
            else configured.get("website_host_suffix", "")
        ),
    }
    if args.command == "run":
        instruction = (
            args.instruction
            if args.instruction is not None
            else args.instruction_file.read_text(encoding="utf-8")
        )
        return CustomSettings(instruction=instruction, **shared)
    return Settings(
        tasks=required_path(args, configured, "tasks"),
        assets=required_path(args, configured, "assets"),
        task_id=args.task_id,
        proxy_config=args.proxy_config,
        **shared,
    )


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        parser().print_help()
        return 0
    if argv and argv[0].startswith("-"):
        argv.insert(0, "reproduce")
    if sys.version_info < (3, 12):
        print("CUAWright desktop requires Python 3.12 or newer.", file=sys.stderr)
        return 1
    try:
        options = settings(parser().parse_args(argv))
        outcome = (
            run_custom(options) if isinstance(options, CustomSettings) else run(options)
        )
    except Exception:
        print(
            "Desktop run failed; inspect result.json if created. "
            "External diagnostics are deliberately not logged.",
            file=sys.stderr,
        )
        return 1
    fields = [
        outcome["stop_reason"],
        f"model_calls={outcome['model_calls']}",
    ]
    if "score" in outcome:
        fields.append(f"score={outcome['score']}")
    print("; ".join(fields))
    return 0
