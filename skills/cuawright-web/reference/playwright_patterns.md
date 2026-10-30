# Playwright Patterns

These are the canonical heredoc patterns the Webwright agent uses. In Claude
Code you run them via the `Bash` tool — no JSON wrapping, no escaping
gymnastics, just one bash command per turn.

## Browser launch skeleton (local mode)

The Webwright skill uses **Playwright Firefox** as its default engine. Some
sites (e.g. cars.com / other Akamai-protected sites) reject Playwright
Chromium with `ERR_HTTP2_PROTOCOL_ERROR` due to TLS/H2 fingerprinting, but
load cleanly under Firefox. Run `playwright install firefox` once before
the first task.

```bash
python - <<'PY'
import asyncio
import os
from pathlib import Path

from playwright.async_api import async_playwright

WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "."))
SCREENSHOTS = WORKSPACE / "screenshots"
SCREENSHOTS.mkdir(parents=True, exist_ok=True)

async def main():
    async with async_playwright() as playwright:
        browser = await playwright.firefox.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 1800})
        page = await context.new_page()

        await page.goto("<START_URL>", wait_until="domcontentloaded")
        await page.screenshot(path=str(SCREENSHOTS / "explore_1_start.png"))

        print("URL:", page.url)
        print("TITLE:", await page.title())

        # Inspect the region you care about with an ARIA snapshot
        snapshot = await page.locator("body").aria_snapshot()
        print("ARIA:", snapshot)

        await browser.close()

asyncio.run(main())
PY
```

Rules:

- **Always** set `viewport={"width": 1280, "height": 1800}`.
- **Never** call `page.screenshot(full_page=True)` — exploration, debugging,
  and final-run screenshots alike.
- Each Playwright run is fresh: navigate from the start URL, reapply
  filters, reconstruct state in code. There is no persistent session.

## Targeting elements with role + name

```python
await page.get_by_role("button", name="Filters").click()
await asyncio.sleep(1)

# Snapshot the *parent* of the control to see siblings/options
panel = page.get_by_role("button", name="Filters").first.locator("..")
print(await panel.aria_snapshot())

await page.get_by_role("checkbox", name="BMW").check()
await asyncio.sleep(1)
```

If a selected state becomes hidden after a drawer/dropdown closes, reopen
it before capturing the verification screenshot.

## Prefer interactive form filling over deep-link URLs

When a task requires parameterizing a search (locations, dates, filters,
query strings), **drive the on-page form interactively** rather than
constructing a deep-link URL with the parameters baked into the query
string. Deep links are convenient for the one specific case the agent
explored, but they are brittle as a CLI surface:

- Sites silently drop parameters they cannot parse, leaving downstream
  fields blank.
- URL parsers vary by locale, A/B bucket, and signed-in state.
- A working deep link for one input set tells you nothing about whether
  another set will populate.

Interactive filling using the same controls a human would click is the
most reliable strategy across input variations. Make it the **primary**
path in the final script; only use a deep link as an opportunistic
shortcut, and always verify the form state afterwards and fall back to
interactive filling when any field is empty or wrong.

```python
# After navigating, read the visible form state and decide.
form_state = await page.locator("input[aria-label]").evaluate_all(
    "els => els.map(e => ({label: e.getAttribute('aria-label'), "
    "value: e.value, hidden: e.offsetParent === null}))"
)
if not form_is_fully_populated(form_state, expected):
    # Type into each field, pick from the suggestion list, fill grouped
    # inputs via their shared modal (Tab between siblings to keep one
    # modal open), then click the submit control.
    await fill_form_interactively(page, expected)
```

Guidelines for the interactive path:

- Use `get_by_role` / `aria-label` selectors, not brittle CSS classes.
- Type the value, wait for the suggestion listbox, then click the option
  whose text contains the canonical token for the input.
- For paired fields rendered inside a single modal (date range pickers,
  stepper groups, etc.), open the modal **once** and `Tab` between fields
  instead of clicking each input separately — clicking the second input
  while the modal is open often gets blocked by the modal's own overlay.
- After filling, click the explicit submit control rather than relying on
  auto-submit.
- Re-read the form state and assert each checkpoint (CP1..CPn) before
  proceeding to results extraction.

## Final-script instrumentation

`final_runs/run_<id>/final_script.py` must:

- write to `final_runs/run_<id>/screenshots/final_execution_<step>_<action>.png`,
- reset and append to `final_runs/run_<id>/final_script_log.txt`,
- print the final datum at the end of the log.

```python
import asyncio, os
from pathlib import Path
from playwright.async_api import async_playwright

RUN_DIR = Path(__file__).parent
SCREENSHOTS = RUN_DIR / "screenshots"
SCREENSHOTS.mkdir(parents=True, exist_ok=True)
LOG = RUN_DIR / "final_script_log.txt"
LOG.write_text("")  # reset

def log(step: int, msg: str) -> None:
    line = f"step {step} action: {msg}\n"
    LOG.open("a").write(line)
    print(line, end="")

async def main():
    async with async_playwright() as playwright:
        browser = await playwright.firefox.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 1800})
        page = await context.new_page()

        await page.goto("<START_URL>", wait_until="domcontentloaded")
        await page.screenshot(path=str(SCREENSHOTS / "final_execution_1_open_start_page.png"))
        log(1, "open start page")

        # ... apply CP1, screenshot, log ...
        # ... apply CP2, screenshot, log ...

        # End of run: capture the final datum visibly and in the log
        final_value = "<extracted price / code / winner>"
        with LOG.open("a") as f:
            f.write(f"\nFINAL_RESPONSE: {final_value}\n")

        await browser.close()

asyncio.run(main())
```

## Inspection commands

```bash
# Latest run tree + log
ls -R final_runs/run_<id>
cat final_runs/run_<id>/final_script_log.txt

# Quick file read
sed -n '1,220p' final_runs/run_<id>/final_script.py
```

For visual checks, use the `Read` tool on individual PNG files inside
`final_runs/run_<id>/screenshots/` rather than calling an external image-QA
service.
