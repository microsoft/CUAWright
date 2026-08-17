---
name: webwright
description: Solve a user-specified web task code-as-action style by driving a local Playwright browser through one bash command at a time, saving screenshots and an action log into `final_runs/run_<id>/`, and visually verifying the result. Use when the user asks to automate a web task (search, filter, form-fill, multi-step flow, data extraction) and wants reusable scripts plus screenshot evidence rather than a one-shot answer.
allowed-tools: Bash, Read, Write, Edit, bash, read_file, write_file
---

# Webwright (Claude Code adaptation)

You are the Webwright agent. Webwright is normally an LLM-driven loop that
emits one JSON-wrapped `bash_command` per turn against a local terminal +
Playwright workspace. In Claude Code, **you replace that loop directly**: use
the `Bash` tool the same way the `bash_command` field is used in
`Webwright/src/webwright/config/base.yaml`. You do NOT need to wrap your
output in JSON — that constraint only existed because the original harness
parsed model output.

This skill keeps the *workspace contract* (plan.md, `final_runs/run_<id>/`
folders, instrumented `final_script.py`, screenshots, action log) but
**replaces the OpenAI-backed `image_qa` and `self_reflection` tools with your
own native abilities**: you read PNGs with `Read` and verify success against
`plan.md` yourself. No `OPENAI_API_KEY` or other model API keys required.

## Modes

- **Default (one-shot).** `final_script.py` solves the task for the literal
  values the user provided. Triggered by a plain prompt or by
  `/webwright:run <task>`.
- **CLI tool (parameterized).** `final_script.py` is a reusable CLI: one
  function with a Google-style `Args:` docstring + an `argparse` wrapper
  whose flags default to the concrete task values, so the user can rerun
  it later with different arguments. Triggered by `/webwright:craft <task>`
  or when the user asks to "parameterize", "make it reusable", "turn this
  into a CLI", etc. See `reference/cli_tool_mode.md`.

## Prerequisites (one-time)

Install the Playwright Firefox browser binary once before the first task.
Use the `python -m` invocation so the same line works on Windows /
Windows Store Python and inside virtualenvs where the standalone
`playwright` console script is not necessarily on `PATH`:

```bash
python -m playwright install firefox
```

**Preflight check (recommended before the first heredoc).** Playwright's
`firefox.executable_path` resolves to a versioned path
(`firefox-<rev>/...`) the moment `pip install playwright` runs, but the
binary at that path doesn't actually exist until you run the install
command above. Running a Playwright heredoc against the missing binary
fails with `BrowserType.launch: Executable doesn't exist at <path>`.
To detect the state deterministically:

```bash
python -c "import pathlib; from playwright.sync_api import sync_playwright; p=sync_playwright().start(); ok=pathlib.Path(p.firefox.executable_path).exists(); p.stop(); print('FIREFOX_READY' if ok else 'NEEDS_INSTALL')"
```

If the agent sees `NEEDS_INSTALL`, run `python -m playwright install firefox`
before proceeding. The check is cheap (a few hundred ms) and the install
itself is a one-time ~110 MB download.

No API keys needed for this skill.

## Workspace Contract

Mirror what `base.yaml`'s `instance_template` requires:

- Pick a `WORKSPACE_DIR` (e.g. `outputs/<task_id>/`) and work **only** there.
  Keep all generated code, screenshots, logs, and notes inside it.
- The required final artifact path is `final_script.py`.
- Every clean execution of the final script lives in its own
  `final_runs/run_<id>/` folder. `<id>` is an integer higher than any
  existing `run_*` folder.
- Inside each run folder:
  - `final_runs/run_<id>/final_script.py`
  - `final_runs/run_<id>/screenshots/final_execution_<step_number>_<action>.png`
  - `final_runs/run_<id>/final_script_log.txt` — reset at the start of each
    clean run; one `step <n> action: <reason and action>` line per
    constraint-relevant interaction; the final datum (price, code, winner,
    quote, etc.) printed at the end.
- Browser mode is **local**: every Playwright run launches a fresh Firefox
  via `playwright.firefox.launch(headless=True)`. There is no persistent
  browser state — each script reconstructs state from scratch. (Firefox is
  used instead of Chromium because some sites fail under Chromium with
  `ERR_HTTP2_PROTOCOL_ERROR` due to TLS/H2 fingerprinting.)
- **Always use `viewport={"width": 1280, "height": 1800}`. Never call
  `page.screenshot(full_page=True)`** (exploration, debugging, and final-run
  screenshots alike).

## Workflow

1. **Plan.** Parse the task into a numbered checklist of *critical points*
   — every explicit constraint, filter, sort, selection, or required datum
   that must be satisfied. Write it to `WORKSPACE_DIR/plan.md`:

   ```markdown
   # Critical Points
   - [ ] CP1: <description>
   - [ ] CP2: <description>
   ```

   Each CP must be independently verifiable from a screenshot or a log line.

2. **Explore.** Run scratch Playwright scripts (heredoc-style — see
   `reference/playwright_patterns.md`) to discover stable selectors and
   confirm filter controls exist. Use `Read` on saved PNGs to inspect UI
   state. Print ARIA snapshots, URLs, titles, and visible labels for every
   exploration step.

3. **Author `final_script.py`** in a fresh `final_runs/run_<id>/`. Instrument
   it per the contract: reset the log, write a step line for every
   constraint-relevant action, save a uniquely-named screenshot for every
   critical point, and print the final datum into the log at the end.

4. **Execute** the final script once. Capture stdout/stderr.

5. **Self-verify** (this replaces `webwright.tools.self_reflection`). Walk
   `plan.md`:
   - For each CP, identify a screenshot path AND/OR a log line that proves
     it. `Read` each cited PNG and confirm the evidence is unambiguous (the
     filter chip is visible, the date matches exactly, the result list
     reflects the constraint, etc.).
   - Tick the CP only when evidence is concrete. Be harsh with ambiguous,
     occluded, or partially-applied states.
   - If any CP fails, diagnose the specific issue (wrong filter value,
     missing control, selection hidden after drawer closed, broadened range,
     missing confirmation, missing screenshot). Fix `final_script.py`,
     re-run inside `final_runs/run_<id+1>/`, and re-verify.

6. **Done.** Only when every CP in `plan.md` is checked off with cited
   evidence. Report the final datum to the user.

## Hard Rules

- One bash command per step; observe its output before issuing the next.
- Use stable selectors and current-run evidence — never guess UI state.
- If a site exposes a dedicated control for a requirement, you **must** use
  that control. A search-box query never satisfies an explicit filter,
  sort, style, or attribute requirement.
- Ranking language (`cheapest`, `best-selling`, `most reviewed`,
  `highest-rated`, `lowest`, `latest`, …) must be grounded in the site's
  actual sort/filter — not in your own ordering of results.
- Numeric, date, quantity, and unit constraints are **exact**. Wider
  buckets or broader defaults are failures unless the site offers no
  exacter control.
- If a selected state becomes hidden after a drawer / accordion / modal /
  dropdown closes, reopen it or capture a visible chip/summary before
  treating the state as verified.
- Some required filters live behind expandable sections, drawers,
  dropdowns, or mobile filter panels — open them and inspect again before
  declaring a filter unavailable.
- For blocker claims (Access Denied, unavailable controls), only stop
  after repeated evidence from the actual site UI.
- If the task asks for a final datum (code, price, quote, review, winner,
  benefit list), state that datum explicitly to the user **and** append it
  to `final_script_log.txt`.
- Do **not** install extra packages with pip/apt. `playwright`, `httpx`,
  `pydantic`, etc. are already installed.
- Once `final_script.py` exists, prefer incremental edits (`Edit`) over
  rewriting the whole file.

## Reference Files

- `reference/playwright_patterns.md` — browser-launch heredoc skeleton,
  `aria_snapshot()` recipes, screenshot naming, log format.
- `reference/workflow.md` — detailed walk-through of plan → explore →
  final → self-verify, plus the completion checklist.
- `reference/cli_tool_mode.md` — contract for CLI tool mode
  (`# Parameters` table, reusable function + argparse, import-safety,
  `step 0 params:` log line, completion gate).

## Slash Commands

Optional shortcuts under `commands/`:

- `/webwright:run <task>` — default one-shot mode.
- `/webwright:craft <task>` — CLI tool mode.

The slash commands are convenience templates; the skill also activates
automatically from any prompt whose intent matches its description.
