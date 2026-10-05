"""The CLI entry shim must make a generated skill runnable by flags WITHOUT changing the
taskspec.json path that replay depends on. The core guarantee tested here: both invocation
styles hand the skill body an IDENTICAL taskspec, and the positional-taskspec path is left
byte-for-byte untouched (so replay is unaffected)."""
import json
import subprocess
import sys
from pathlib import Path

from cuawright.skill_factory.entry_shim import cli_shim_src, prepend_cli_shim

PARAMS = ["origin_city", "origin_code", "destination_code", "date"]

# A minimal skill body shaped like a real one: reads taskspec from sys.argv[1] at module level
# and prints the params it received. Standing in for the browser-driving body so the test is
# fast and offline — what we are checking is the interface the body sees, not the crawl.
BODY = (
    "import sys, json\n"
    "from pathlib import Path\n"
    "TASKSPEC = json.loads(Path(sys.argv[1]).read_text())\n"
    "print(json.dumps(TASKSPEC.get('params', {}), sort_keys=True))\n"
)


def _write_skill(tmp_path) -> Path:
    code = prepend_cli_shim(BODY, PARAMS)
    f = tmp_path / "skill.py"
    f.write_text(code, encoding="utf-8")
    return f


def _run(skill: Path, args):
    return subprocess.run([sys.executable, str(skill), *args],
                          capture_output=True, text=True)


def test_shim_source_is_valid_python():
    compile(cli_shim_src(PARAMS), "shim.py", "exec")


def test_prepend_is_idempotent():
    once = prepend_cli_shim(BODY, PARAMS)
    twice = prepend_cli_shim(once, PARAMS)
    assert once == twice


def test_shim_runs_before_body_reads_argv():
    code = prepend_cli_shim(BODY, PARAMS)
    assert code.index("_skillfactory_cli()") < code.index("sys.argv[1]")


def test_taskspec_path_unchanged_and_flags_match(tmp_path):
    skill = _write_skill(tmp_path)
    spec = {"params": {"origin_code": "LAX", "destination_code": "ORD", "date": "2026-08-15"}}
    ts = tmp_path / "taskspec.json"
    ts.write_text(json.dumps(spec), encoding="utf-8")

    # 1) positional taskspec.json — the path replay uses; must still work
    r_file = _run(skill, [str(ts)])
    assert r_file.returncode == 0, r_file.stderr
    # 2) equivalent --flags — must yield the IDENTICAL params the body sees
    r_flags = _run(skill, ["--origin-code", "LAX", "--destination-code", "ORD",
                           "--date", "2026-08-15"])
    assert r_flags.returncode == 0, r_flags.stderr
    assert json.loads(r_file.stdout) == json.loads(r_flags.stdout)
    assert json.loads(r_flags.stdout) == spec["params"]


def test_bare_run_prints_help_not_crash(tmp_path):
    skill = _write_skill(tmp_path)
    r = _run(skill, [])
    assert r.returncode == 0
    out = r.stdout + r.stderr
    assert "usage" in out.lower()
    for p in PARAMS:                       # every parameter is discoverable
        assert p.replace("_", "-") in out


def test_hyphen_flag_maps_to_underscore_param(tmp_path):
    skill = _write_skill(tmp_path)
    r = _run(skill, ["--origin-city", "Los Angeles"])
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"origin_city": "Los Angeles"}
