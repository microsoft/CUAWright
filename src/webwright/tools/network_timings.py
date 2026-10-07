"""Network timings capture tool for monitoring HTTP requests during page interactions.

Captures request/response metadata including:
- URL, HTTP method, status code
- Request duration and size
- Timing breakdown (DNS, TLS, first byte, etc.)

Stores data as JSON for analysis and exports summary statistics.

Usage:
    python -m webwright.tools.network_timings start --workspace-dir <ws>
    python -m webwright.tools.network_timings get-summary --session-file <file>
    python -m webwright.tools.network_timings export --session-file <file> --output <file.json>
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class NetworkTimingsCapture:
    """Captures and analyzes network timings during page interactions."""

    def __init__(self, workspace_dir: str = ""):
        self.workspace_dir = Path(workspace_dir) if workspace_dir else Path.cwd()
        self.timings: list[dict[str, Any]] = []
        self.is_capturing = False
        self.start_time: float | None = None
        self.session_file: Path | None = None

    def start_capture(self) -> None:
        """Start capturing network timings."""
        self.is_capturing = True
        self.start_time = time.time()
        self.timings = []

    def stop_capture(self) -> None:
        """Stop capturing network timings."""
        self.is_capturing = False

    def log_request(
        self,
        url: str,
        method: str = "GET",
        start_time: float | None = None,
        request_size: int = 0,
    ) -> None:
        """Log the start of a network request."""
        if not self.is_capturing:
            return

        if start_time is None:
            start_time = time.time()

        self.timings.append({
            "type": "request",
            "url": url,
            "method": method,
            "start_time": start_time,
            "request_size": request_size,
            "timestamp": datetime.fromtimestamp(start_time).isoformat(),
        })

    def log_response(
        self,
        url: str,
        status: int = 200,
        end_time: float | None = None,
        response_size: int = 0,
    ) -> None:
        """Log the completion of a network request."""
        if not self.is_capturing:
            return

        if end_time is None:
            end_time = time.time()

        for timing in reversed(self.timings):
            if timing.get("type") == "request" and timing.get("url") == url:
                timing["status"] = status
                timing["end_time"] = end_time
                timing["response_size"] = response_size
                timing["duration_ms"] = (end_time - timing["start_time"]) * 1000
                timing["total_size"] = timing.get("request_size", 0) + response_size
                break

    def get_timings(self) -> list[dict[str, Any]]:
        """Get all captured timings."""
        return self.timings

    def get_summary(self) -> dict[str, Any]:
        """Get summary statistics of captured network timings."""
        if not self.timings:
            return {
                "total_requests": 0,
                "total_time_ms": 0,
                "average_request_time_ms": 0,
                "min_request_time_ms": 0,
                "max_request_time_ms": 0,
                "total_size_bytes": 0,
                "captured_at": datetime.now().isoformat(),
            }

        completed_timings = [
            t for t in self.timings
            if "duration_ms" in t and t.get("end_time") is not None
        ]

        if not completed_timings:
            return {
                "total_requests": len(self.timings),
                "total_time_ms": 0,
                "average_request_time_ms": 0,
                "min_request_time_ms": 0,
                "max_request_time_ms": 0,
                "total_size_bytes": 0,
                "captured_at": datetime.now().isoformat(),
            }

        durations = [t["duration_ms"] for t in completed_timings]
        sizes = [t.get("total_size", 0) for t in completed_timings]

        return {
            "total_requests": len(completed_timings),
            "total_time_ms": sum(durations),
            "average_request_time_ms": sum(durations) / len(durations) if durations else 0,
            "min_request_time_ms": min(durations) if durations else 0,
            "max_request_time_ms": max(durations) if durations else 0,
            "total_size_bytes": sum(sizes),
            "captured_at": datetime.now().isoformat(),
        }

    def export_to_file(self, filename: str = "network_timings.json") -> str:
        """Export captured timings to a JSON file."""
        output_path = self.workspace_dir / filename
        output_path.parent.mkdir(parents=True, exist_ok=True)

        export_data = {
            "metadata": {
                "captured_at": datetime.now().isoformat(),
                "total_entries": len(self.timings),
            },
            "summary": self.get_summary(),
            "timings": self.timings,
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2)

        return str(output_path)


def _cmd_start(args: argparse.Namespace) -> int:
    """Create and initialize network timings capture session."""
    workspace_dir = args.workspace_dir or str(Path.cwd())
    session_file = Path(workspace_dir) / args.out
    session_file.parent.mkdir(parents=True, exist_ok=True)

    capture = NetworkTimingsCapture(workspace_dir)
    capture.start_capture()

    session_data = {
        "id": args.session_id or "default",
        "workspace_dir": str(Path(workspace_dir).resolve()),
        "started_at": datetime.now().isoformat(),
        "is_capturing": True,
    }

    with open(session_file, "w", encoding="utf-8") as f:
        json.dump(session_data, f, indent=2)

    print(f"Network timings capture started")
    print(f"Session file: {session_file}")
    return 0


def _cmd_get_summary(args: argparse.Namespace) -> int:
    """Get summary of network timings from session."""
    session_file = Path(args.session_file)

    if not session_file.exists():
        print(f"error: session file not found: {session_file}")
        return 1

    with open(session_file, encoding="utf-8") as f:
        session_data = json.load(f)

    workspace_dir = session_data.get("workspace_dir", "")
    timings_file = Path(workspace_dir) / "network_timings.json"

    if not timings_file.exists():
        print("No timings data available yet")
        return 0

    with open(timings_file, encoding="utf-8") as f:
        timings_data = json.load(f)

    summary = timings_data.get("summary", {})
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    """Export network timings from session."""
    session_file = Path(args.session_file)

    if not session_file.exists():
        print(f"error: session file not found: {session_file}")
        return 1

    with open(session_file, encoding="utf-8") as f:
        session_data = json.load(f)

    workspace_dir = session_data.get("workspace_dir", "")
    timings_file = Path(workspace_dir) / "network_timings.json"

    if not timings_file.exists():
        print("No timings data available to export")
        return 1

    output_path = Path(args.output) if args.output else timings_file

    with open(timings_file, encoding="utf-8") as f:
        timings_data = json.load(f)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(timings_data, f, indent=2)

    print(f"Timings exported to: {output_path}")
    return 0


def main() -> int:
    """CLI entry point for network timings capture."""
    parser = argparse.ArgumentParser(
        description="Capture and analyze network timings during page interactions"
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to execute")

    start_parser = subparsers.add_parser("start", help="Start network timings capture")
    start_parser.add_argument(
        "--workspace-dir",
        default="",
        help="Workspace directory for storing timings data",
    )
    start_parser.add_argument(
        "--out",
        default=".network_timings_session.json",
        help="Session file name",
    )
    start_parser.add_argument(
        "--session-id",
        default="",
        help="Unique session identifier",
    )
    start_parser.set_defaults(func=_cmd_start)

    summary_parser = subparsers.add_parser("get-summary", help="Get timings summary")
    summary_parser.add_argument(
        "--session-file",
        required=True,
        help="Path to session file",
    )
    summary_parser.set_defaults(func=_cmd_get_summary)

    export_parser = subparsers.add_parser("export", help="Export timings data")
    export_parser.add_argument(
        "--session-file",
        required=True,
        help="Path to session file",
    )
    export_parser.add_argument(
        "--output",
        default="",
        help="Output file path",
    )
    export_parser.set_defaults(func=_cmd_export)

    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    import sys
    sys.exit(main())
