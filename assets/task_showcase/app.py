"""Flask dashboard for repeatable task-showcase JSON.

Each task lives under ``tasks/<short_id>/``. The only required files are:

    task.json    – metadata written by the build script
    report.json  – structured output consumed by the generic template

Optional run artifacts such as ``final_script_log.txt``, ``steps.jsonl``, and
``screenshots/`` are used when present, but the renderer does not require them.

Routes:
    /                              – dashboard listing available tasks
    /task/<short_id>               – per-task report view
    /task/<short_id>/screenshot/.. – optional screenshot file
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from flask import Flask, abort, render_template, send_from_directory

ROOT = Path(__file__).resolve().parent


def _resolve_tasks_dir(value: str | Path | None = None) -> Path:
    if value is None:
        value = os.environ.get("TASK_SHOWCASE_TASKS_DIR") or ROOT / "tasks"
    return Path(value).expanduser().resolve()


TASKS_DIR = _resolve_tasks_dir()

app = Flask(__name__)


# ---------- task metadata ----------

def list_tasks() -> list[dict]:
    out: list[dict] = []
    if not TASKS_DIR.exists():
        return out
    for d in sorted(TASKS_DIR.iterdir()):
        info_path = d / "task.json"
        if not info_path.exists():
            continue
        info = json.loads(info_path.read_text())
        info["short_id"] = d.name
        out.append(info)
    return out


# ---------- log + steps parsing ----------

_STEP_LINE = re.compile(r"^step\s+(\d+)\s+action:\s*(.*)$", re.IGNORECASE)
_FINAL_LINE = re.compile(r"^Final Response:\s*(.*)$", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s'\"<>;,)]+")
_TRAIL_PUNCT = re.compile(r"[.,;:)\]]+$")


def _clean_url(u: str) -> str:
    return _TRAIL_PUNCT.sub("", u)


def parse_log(log_path: Path) -> tuple[dict[int, str], str]:
    """Return ({step_num: action_text}, final_response)."""
    steps: dict[int, str] = {}
    final = ""
    if not log_path.exists():
        return steps, final
    for raw in log_path.read_text(encoding="utf-8").splitlines():
        m = _STEP_LINE.match(raw.strip())
        if m:
            steps[int(m.group(1))] = m.group(2).strip()
            continue
        f = _FINAL_LINE.match(raw.strip())
        if f:
            final = f.group(1).strip()
    return steps, final


def parse_steps_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    for r in rows:
        try:
            r["step_num"] = int(r.get("step_num", 0))
        except (TypeError, ValueError):
            r["step_num"] = 0
    rows.sort(key=lambda r: r["step_num"])
    return rows


def build_steps(task_dir: Path) -> tuple[list[dict], str]:
    log_steps, final = parse_log(task_dir / "final_script_log.txt")
    raw_rows = parse_steps_jsonl(task_dir / "steps.jsonl")

    out: list[dict] = []
    for row in raw_rows:
        n = row["step_num"]
        action_text = log_steps.get(n) or row.get("action", "").strip()
        # the first row of steps.jsonl sometimes contains the full multi-line
        # log dump. If so, take only its first "step N action:" line.
        if action_text and "\nstep " in action_text.lower():
            first_line = action_text.splitlines()[0]
            m = _STEP_LINE.match(first_line)
            if m:
                action_text = m.group(2).strip()
        shot = row.get("screenshot") or ""
        if shot:
            shot = Path(shot).name
        urls = [_clean_url(u) for u in _URL_RE.findall(action_text)]
        seen: set[str] = set()
        urls = [u for u in urls if not (u in seen or seen.add(u))]
        out.append({
            "step_num": n,
            "action": action_text,
            "screenshot": shot,
            "urls": urls,
        })
    return out, final


def _host(u: str) -> str:
    from urllib.parse import urlparse
    try:
        return urlparse(u).netloc.replace("www.", "") or u
    except Exception:
        return u


def collect_pages(steps: list[dict], fallback_site: str | None) -> list[dict]:
    """Build a unique URL list across all steps for the Pages grid."""
    pages: list[dict] = []
    seen: set[str] = set()
    for s in steps:
        for u in s["urls"]:
            if u in seen:
                continue
            seen.add(u)
            from urllib.parse import urlparse
            try:
                pu = urlparse(u)
                tail = pu.path.rstrip("/").split("/")[-1] or pu.netloc
                title = f"{pu.netloc}/{tail}" if tail != pu.netloc else pu.netloc
            except Exception:
                title = u
            pages.append({
                "step": s["step_num"],
                "url": u,
                "title": title,
                "host": _host(u),
                "screenshot": s.get("screenshot") or "",
            })
    if not pages and fallback_site:
        pages.append({
            "step": 1, "url": fallback_site, "title": fallback_site,
            "host": _host(fallback_site), "screenshot": "",
        })
    return pages


def collect_sources(pages: list[dict]) -> list[dict]:
    """Group pages by host -> [{host, count, sample_url}]."""
    from collections import OrderedDict
    grouped: "OrderedDict[str, dict]" = OrderedDict()
    for p in pages:
        h = p["host"]
        if h not in grouped:
            grouped[h] = {"host": h, "count": 0, "sample_url": p["url"]}
        grouped[h]["count"] += 1
    return list(grouped.values())


# ---------- routes ----------

@app.route("/")
def index():
    tasks = list_tasks()
    return render_template("dashboard.html", tasks=tasks)


@app.route("/task/<short_id>")
def task_view(short_id: str):
    task_dir = TASKS_DIR / short_id
    info_path = task_dir / "task.json"
    if not info_path.exists():
        abort(404)
    info = json.loads(info_path.read_text())
    info["short_id"] = short_id
    steps, final = build_steps(task_dir)
    # last-modified timestamp of the run for the "Updated" line
    import datetime as _dt
    log_path = task_dir / "final_script_log.txt"
    if log_path.exists():
        ts = _dt.datetime.fromtimestamp(log_path.stat().st_mtime)
        updated = ts.strftime("%-m/%-d/%Y, %-I:%M:%S %p")
    else:
        updated = ""
    # Per-task structured report lives next to the run artifacts.
    report_path = task_dir / "report.json"
    if report_path.exists():
        report = json.loads(report_path.read_text())
    else:
        report = {}
    sources = report.get("sources", [])
    result = report.get("result", {"sections": []})
    num_steps = len(steps)
    if not num_steps:
        try:
            num_steps = int(info.get("num_steps") or 0)
        except (TypeError, ValueError):
            num_steps = 0
    return render_template(
        "task.html",
        info=info,
        final_response=final,
        sources=sources,
        result=result,
        updated=updated,
        num_steps=num_steps,
    )


@app.route("/task/<short_id>/screenshot/<path:filename>")
def screenshot(short_id: str, filename: str):
    folder = (TASKS_DIR / short_id / "screenshots").resolve()
    target = (folder / filename).resolve()
    if not target.exists():
        abort(404)
    return send_from_directory(folder, filename)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5005)
    parser.add_argument(
        "--tasks-dir",
        type=Path,
        default=None,
        help="Directory containing <short_id>/task.json and report.json folders.",
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    global TASKS_DIR
    TASKS_DIR = _resolve_tasks_dir(args.tasks_dir)
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
