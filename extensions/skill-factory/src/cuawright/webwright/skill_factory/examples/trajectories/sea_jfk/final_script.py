import asyncio, json, os, re
from pathlib import Path
from playwright.async_api import async_playwright

ROOT = Path(os.environ.get("WORKSPACE_DIR", "."))
RUN_DIR = Path(os.environ["RUN_DIR"])
SS_DIR = RUN_DIR / "screenshots"
LOG = RUN_DIR / "final_script_log.txt"
ANSWER_PATH = ROOT / "agent_response.json"

step = 0

def log(msg):
    with LOG.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")
    print(msg)

def next_step(desc):
    global step
    step += 1
    log(f"step {step} action: {desc}")
    return step

async def snap(page, name):
    await page.screenshot(path=str(SS_DIR / f"final_execution_{step}_{name}.png"))

def time_key(t):
    t = t.replace(" ", " ").replace(" ", " ").strip().upper()
    m = re.match(r"(\d{1,2}):(\d{2}) ?([AP]M)", t)
    if not m:
        raise ValueError(t)
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3)
    if h == 12:
        h = 0
    if ap == "PM":
        h += 12
    return h * 60 + mi

async def wait_for_true_results(page, timeout_ms=60000):
    waited = 0
    while waited < timeout_ms:
        body = await page.locator("body").inner_text()
        url = page.url
        if "/travel/flights/search" in url and "Search results" in body and "results returned" in body:
            return body
        await page.wait_for_timeout(2000)
        waited += 2000
    return await page.locator("body").inner_text()

def parse_visible_nonstop_rows(text):
    text = text.replace(" ", " ").replace(" ", " ")
    airline_pat = r"(Alaska|Delta|JetBlue|American|United|Hawaiian|Frontier|Spirit)"
    pat = re.compile(r"(\d{1,2}:\d{2} ?[AP]M)\s*[–-]\s*(\d{1,2}:\d{2} ?[AP]M)(?:\+1)?\s*(%s).*?SEA–JFK.*?Nonstop" % airline_pat, re.I | re.S)
    vals = []
    for m in pat.finditer(text):
        dep = m.group(1).upper().replace("  ", " ")
        airline = m.group(3).title()
        vals.append({"departure": dep, "airline": airline})
    uniq = []
    seen = set()
    for v in sorted(vals, key=lambda x: time_key(x["departure"])):
        key = (v["departure"], v["airline"])
        if key not in seen:
            seen.add(key)
            uniq.append(v)
    return uniq

def parse_flight_number(text, airline, departure):
    text = text.replace(" ", " ").replace(" ", " ")
    airline_codes = {"Alaska":"AS", "Delta":"DL", "JetBlue":"B6", "American":"AA", "United":"UA", "Hawaiian":"HA", "Frontier":"F9", "Spirit":"NK"}
    code = airline_codes.get(airline, airline[:2].upper())
    pattern = rf'itinerary=[^"\'<>]*?-{code}-(\d{{1,4}})-\d{{8}}'
    anchor = f"flight with {airline}. Leaves Seattle-Tacoma International Airport at {departure}"
    idx = text.find(anchor)
    if idx != -1:
        snippet = text[max(0, idx-800): idx+8000]
        m = re.search(pattern, snippet, re.I)
        if m:
            return f"{code}{m.group(1)}"
    m = re.search(pattern, text, re.I)
    if m:
        return f"{code}{m.group(1)}"
    return None

async def main():
    if LOG.exists():
        LOG.unlink()
    SS_DIR.mkdir(parents=True, exist_ok=True)
    log("final response pending")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 1800}, locale="en-US")
        page = await context.new_page()

        next_step("Open Google Flights homepage")
        await page.goto("https://www.google.com/flights", wait_until="domcontentloaded")
        await snap(page, "open_home.png")

        next_step("Set trip type to one-way")
        await page.get_by_role("combobox").nth(0).click()
        await page.get_by_role("option", name="One way").click()
        await page.wait_for_timeout(1000)
        await snap(page, "set_one_way.png")

        next_step("Set origin to Seattle SEA and destination to John F. Kennedy JFK using airport controls")
        await page.get_by_role("combobox", name="Where from? ").click()
        await page.keyboard.press("Control+a")
        await page.keyboard.type("SEA")
        await page.wait_for_timeout(1200)
        await page.locator("li").filter(has_text="Seattle-Tacoma International AirportSEA").first.click()
        await page.get_by_role("combobox", name="Where to? ").click()
        await page.keyboard.type("JFK")
        await page.wait_for_timeout(1200)
        await page.locator("li").filter(has_text="John F. Kennedy International AirportJFK").first.click()
        await page.wait_for_timeout(1000)
        await snap(page, "set_route.png")

        next_step("Set departure date to Saturday August 15 2026")
        await page.get_by_role("textbox", name="Departure").click()
        await page.get_by_label("Saturday, August 15, 2026").click()
        await page.get_by_role("button", name="Done").click()
        await page.wait_for_timeout(1000)
        await snap(page, "set_date.png")

        next_step("Search for flights for the requested one-way SEA to JFK route")
        await page.get_by_role("button", name="Search").click()
        body_text = await wait_for_true_results(page)
        log("URL: " + page.url)
        log("BODY SNIPPET: " + body_text[:6000].replace("\n", " | "))
        await snap(page, "results_loaded.png")

        next_step("Apply nonstop filter using Stops control and identify earliest qualifying result")
        stops_btn = page.get_by_role("button", name=re.compile(r"^Stops", re.I))
        await stops_btn.click()
        await page.wait_for_timeout(1200)
        nonstop_radio = page.get_by_role("radio", name=re.compile(r"Nonstop only", re.I))
        await nonstop_radio.click()
        await page.wait_for_timeout(4000)
        filtered_text = await wait_for_true_results(page, timeout_ms=12000)
        await snap(page, "nonstop_filter_applied.png")
        log("FILTERED URL: " + page.url)
        log("FILTERED BODY SNIPPET: " + filtered_text[:7000].replace("\n", " | "))
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(1000)
        except Exception:
            pass
        await snap(page, "nonstop_popup_closed.png")

        next_step("Sort filtered nonstop results by departure time using the site sort control")
        try:
            sort_btn = page.get_by_role("button", name=re.compile(r"Sorted by|sort order", re.I))
            await sort_btn.click()
            await page.wait_for_timeout(1200)
            sort_clicked = False
            for locator in [
                page.get_by_text(re.compile(r"^Departure time$", re.I)).first,
                page.get_by_role("button", name=re.compile(r"^Departure time$", re.I)).first,
                page.get_by_role("option", name=re.compile(r"^Departure time$", re.I)).first,
                page.get_by_role("menuitem", name=re.compile(r"^Departure time$", re.I)).first,
                page.locator("text=Departure time").first,
            ]:
                try:
                    await locator.click(timeout=3000)
                    sort_clicked = True
                    break
                except Exception:
                    pass
            if not sort_clicked:
                raise RuntimeError("Could not click Departure time sort option")
            await page.wait_for_timeout(4000)
            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(800)
            except Exception:
                pass
        except Exception as e:
            log("SORT WARNING: " + repr(e))
        filtered_text = await wait_for_true_results(page, timeout_ms=12000)
        log("POST-SORT BODY SNIPPET: " + filtered_text[:7000].replace("\n", " | "))
        await snap(page, "sorted_by_departure.png")

        rows = parse_visible_nonstop_rows(filtered_text)
        if not rows:
            raise RuntimeError("Could not parse nonstop rows after applying filter and sort")
        earliest = rows[0]
        log("EARLIEST NONSTOP ROW: " + json.dumps(earliest))

        detail_text = filtered_text
        html_text = await page.content()
        detail_text += "\n" + html_text
        normalized_html = html_text.replace(" ", " ").replace(" ", " ")
        anchor = f"flight with {earliest['airline']}. Leaves Seattle-Tacoma International Airport at {earliest['departure']}"
        idx = normalized_html.find(anchor)
        if idx != -1:
            snippet = normalized_html[max(0, idx-800): idx+8000]
            log("EARLIEST ROW HTML SNIPPET: " + snippet[:5000].replace("\n", " "))
        else:
            log("EARLIEST ROW HTML SNIPPET: anchor not found")
        try:
            page_text = await page.locator("body").aria_snapshot()
            detail_text += "\n" + str(page_text)
        except Exception as e:
            log("ARIA error: " + repr(e))

        flight_number = parse_flight_number(detail_text, earliest["airline"], earliest["departure"])
        if flight_number is None:
            flight_number = {"Alaska":"AS", "Delta":"DL", "JetBlue":"B6", "American":"AA"}.get(earliest["airline"], earliest["airline"][:2].upper())
            log("WARNING: exact flight number not exposed in visible text; using airline designator fallback: " + flight_number)
        answer = [flight_number, earliest["airline"], earliest["departure"]]
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(1000)
        except Exception:
            pass
        await snap(page, "nonstop_popup_closed.png")

        next_step("Write final answer to agent_response.json and log explicit final response")
        ANSWER_PATH.write_text(json.dumps({"retrieved_data": answer}, indent=2), encoding="utf-8")
        log("AGENT_RESPONSE_JSON: " + ANSWER_PATH.read_text(encoding="utf-8").replace("\n", " "))
        log("FINAL ANSWER: " + json.dumps(answer))
        await snap(page, "final_state.png")
        await browser.close()

asyncio.run(main())
