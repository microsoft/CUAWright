"""Deterministic CLI entry shim prepended to every generated skill.

A distilled skill reads its inputs from ``taskspec.json`` (``sys.argv[1]``); that is the
interface replay depends on (`update.py` invokes ``python skill.py taskspec.json``). But that
same contract makes the file unusable by a human: ``python skill.py`` crashes with IndexError
and ``python skill.py --help`` is read as a (missing) taskspec path.

This shim closes that gap WITHOUT touching the skill body or the replay contract. It is a fixed
template with one injected constant (the parameter names, taken from the skill's signature), so
it is fully deterministic and testable with no model in the loop. Prepended at the very top of
the file, it runs before any of the skill's own ``sys.argv[1]`` reads and rewrites ``sys.argv``:

  * ``skill.py taskspec.json``  (one positional, no dashes)  -> left UNTOUCHED. This is exactly
    what replay passes, so replay behaviour is byte-for-byte unchanged.
  * ``skill.py --origin-code LAX --date 2026-08-15``          -> the flags are assembled into a
    taskspec identical to what the file would have deserialized, written to a temp file, and
    ``sys.argv`` is repointed at it. The skill body then runs completely unchanged.
  * ``skill.py`` / ``skill.py --help``                        -> prints the parameters and a
    ready-to-paste taskspec example, then exits (no more IndexError).

The two invocation styles therefore hand the skill body an IDENTICAL taskspec; the flags path is
pure convenience sugar over the file path.
"""
from __future__ import annotations

import json

_SHIM_TEMPLATE = '''\
# --- CLI entry shim (auto-added by skill_factory; do not edit) -------------------------------
# Lets this skill run two ways with IDENTICAL results:
#   python skill.py taskspec.json                       (what replay/programs use)
#   python skill.py {flag_example}   (convenience for humans)
def _skillfactory_cli():
    import sys, json, argparse, tempfile
    _PARAMS = {params!r}
    argv = sys.argv[1:]
    # A single positional, non-flag argument is a taskspec.json path -> original behaviour,
    # sys.argv left untouched. This is the path replay uses, so it must not change.
    if len(argv) == 1 and not argv[0].startswith("-"):
        return
    ap = argparse.ArgumentParser(
        prog="skill.py",
        description="Run this skill directly. Pass --flags, or a taskspec.json path.")
    for _p in _PARAMS:
        ap.add_argument("--" + _p.replace("_", "-"), dest=_p, default=None)
    ap.add_argument("taskspec", nargs="?", help="path to a taskspec.json (instead of --flags)")
    a = ap.parse_args(argv)
    # A taskspec path given alongside/without flags -> honour it, stay untouched.
    if a.taskspec and not any(getattr(a, _p) is not None for _p in _PARAMS):
        sys.argv = [sys.argv[0], a.taskspec]
        return
    params = {{_p: getattr(a, _p) for _p in _PARAMS if getattr(a, _p) is not None}}
    if not params:
        ap.print_help()
        ex = " ".join("--" + _p.replace("_", "-") + " <" + _p + ">" for _p in _PARAMS)
        print("\\n  example:  python skill.py " + ex)
        print("  or:       python skill.py taskspec.json  "
              '(taskspec = {{"params": {{' + ", ".join('"' + _p + '": ...' for _p in _PARAMS)
              + "}}}})")
        raise SystemExit(0)
    spec = {{"params": params}}
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(spec, f)
    f.close()
    sys.argv = [sys.argv[0], f.name]
_skillfactory_cli()
# --- end CLI entry shim ---------------------------------------------------------------------
'''


def cli_shim_src(param_names) -> str:
    """Return the source of the CLI entry shim for a skill with these parameter names.

    ``param_names`` is the skill's signature params (a list of strings). The returned block is
    meant to be prepended to the generated skill source, above everything else.
    """
    params = list(param_names or [])
    flag_example = (
        "--" + params[0].replace("_", "-") + " ..." if params else "--param ..."
    )
    return _SHIM_TEMPLATE.format(params=params, flag_example=flag_example)


def prepend_cli_shim(code: str, param_names) -> str:
    """Prepend the CLI shim to a generated skill's source.

    Idempotent: if the shim marker is already present, the code is returned unchanged, so
    re-distilling or double-processing a skill never stacks two shims.
    """
    if "_skillfactory_cli()" in code:
        return code
    return cli_shim_src(param_names) + "\n" + code
