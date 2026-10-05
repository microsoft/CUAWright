---
description: Craft a reusable Webwright CLI tool by parameterizing a web task.
argument-hint: <natural-language web task with concrete values>
---

You are operating as the Webwright agent in **CLI tool mode**. First read
the `SKILL.md` of the `cuawright-web` skill (the parent directory of this
`commands/` folder) and the `reference/cli_tool_mode.md` next to it,
then parameterize the following task so the resulting `final_script.py`
can be re-run later with different argument values:

$ARGUMENTS

Steps:

1. **Identify parameters.** Extract every requirement the user could
   plausibly vary (search terms, locations, dates, filter values, etc.).
   Items truly fixed for the site (start URL, site name, selector
   strategy) are NOT parameters — keep them hard-coded.

2. **Write `plan.md`.** Add a `# Parameters` table with columns
   `name | type | source phrase | default | allowed/format`, plus the
   usual `# Critical Points` checklist. Defaults must equal the
   concrete task values so `python final_script.py` (no args) reproduces
   the task.

3. **Author `final_script.py`** in a fresh `final_runs/run_<id>/`:
   - One reusable function named after the task domain
     (e.g. `def search_<domain>(arg_a, arg_b, ...): ...`).
   - Google-style docstring with summary, full `Args:` block (name, type,
     meaning, format/units, default), and `Returns:`.
   - `argparse` CLI under `if __name__ == "__main__":` whose flags
     exactly mirror the function arguments and whose defaults equal the
     concrete task values.
   - **Side-effect-free at import time** — no browser launch, no network
     call, no file write at module top-level.
   - First log line after reset must be
     `step 0 params: <name>=<value> <name>=<value> ...`.
   - Same instrumentation as default mode: viewport 1280×1800, headless
     local Firefox, no `full_page=True`, screenshots and final datum
     saved into the run folder.

4. **Reproduce the task with no arguments.** Run
   `python final_runs/run_<id>/final_script.py` and confirm it succeeds
   end-to-end.

5. **Import-safety smoke test.** Load the module in a separate Python
   process and confirm no browser is launched and the reusable function
   is importable.

6. **Self-verify** every critical point against the saved screenshots
   and the action log (replaces `self_reflection`). If any CP fails,
   diagnose, fix the script (preserving the CLI shape), re-run inside
   `final_runs/run_<id+1>/`, and re-verify.

7. **Show the user `--help`.** End by running
   `python final_runs/run_<id>/final_script.py --help` and reporting
   both the final datum and the help text so the user knows how to call
   the tool again with different arguments.

Refer to `reference/cli_tool_mode.md` for the complete contract and
`reference/playwright_patterns.md` for the Playwright skeleton.
