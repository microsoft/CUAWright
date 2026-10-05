# --- CLI entry shim (auto-added by skill_factory; do not edit) -------------------------------
# Lets this skill run two ways with IDENTICAL results:
#   python skill.py taskspec.json                       (what replay/programs use)
#   python skill.py --origin-city ...   (convenience for humans)
def _skillfactory_cli():
    import sys, json, argparse, tempfile
    _PARAMS = ['origin_city', 'origin_code', 'destination_city', 'destination_code', 'date']
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
    params = {_p: getattr(a, _p) for _p in _PARAMS if getattr(a, _p) is not None}
    if not params:
        ap.print_help()
        ex = " ".join("--" + _p.replace("_", "-") + " <" + _p + ">" for _p in _PARAMS)
        print("\n  example:  python skill.py " + ex)
        print("  or:       python skill.py taskspec.json  "
              '(taskspec = {"params": {' + ", ".join('"' + _p + '": ...' for _p in _PARAMS)
              + "}})")
        raise SystemExit(0)
    spec = {"params": params}
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(spec, f)
    f.close()
    sys.argv = [sys.argv[0], f.name]
_skillfactory_cli()
# --- end CLI entry shim ---------------------------------------------------------------------

import asyncio
import base64
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from playwright.async_api import async_playwright


# ----------------------------
# Workspace / IO
# ----------------------------

WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", ".")).resolve()
TASKSPEC_PATH = Path(sys.argv[1]).resolve()
TASKSPEC = json.loads(TASKSPEC_PATH.read_text(encoding="utf-8"))
PARAMS = TASKSPEC.get("params", {}) or {}
START_URL = TASKSPEC.get("start_url") or "https://www.google.com/flights"
OUTPUT_PATH = WORKSPACE / "agent_response.json"

RUNS_DIR = WORKSPACE / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)
existing = [
    int(p.name.split("_")[-1])
    for p in RUNS_DIR.glob("run_*")
    if p.name.split("_")[-1].isdigit()
]
RUN_ID = max(existing, default=0) + 1
RUN_DIR = RUNS_DIR / f"run_{RUN_ID:03d}"
RUN_DIR.mkdir(parents=True, exist_ok=False)
SCREENSHOTS_DIR = RUN_DIR / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = RUN_DIR / "skill_log.txt"

step_num = 0


# ----------------------------
# Logging / helpers
# ----------------------------

def log(msg: str) -> None:
    print(msg, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")


def next_step(desc: str) -> None:
    global step_num
    step_num += 1
    log(f"step {step_num}: {desc}")


async def snap(page, name: str) -> None:
    try:
        await page.screenshot(
            path=str(SCREENSHOTS_DIR / f"{step_num:02d}_{name}.png"),
            full_page=True,
        )
    except Exception as e:
        log(f"screenshot warning: {e!r}")


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u202f", " ").replace("\xa0", " ")).strip()


def date_aria_label(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime("%A, %B ") + str(dt.day) + dt.strftime(", %Y")


def time_key(t: str) -> int:
    s = normalize_space(t).upper()
    m = re.match(r"(\d{1,2}):(\d{2})\s*([AP]M)", s)
    if not m:
        raise ValueError(f"Unparseable time: {t}")
    h = int(m.group(1)) % 12
    minute = int(m.group(2))
    if m.group(3) == "PM":
        h += 12
    return h * 60 + minute


def canonical_airline_name(name: str) -> str:
    n = normalize_space(name).lower()
    mapping = {
        "alaska": "Alaska",
        "american": "American",
        "delta": "Delta",
        "frontier": "Frontier",
        "hawaiian": "Hawaiian",
        "jetblue": "JetBlue",
        "jet blue": "JetBlue",
        "spirit": "Spirit",
        "sun country": "Sun Country",
        "southwest": "Southwest",
        "united": "United",
    }
    return mapping.get(n, name.strip())


def airline_code_map():
    return {
        "Alaska": "AS",
        "American": "AA",
        "Delta": "DL",
        "Frontier": "F9",
        "Hawaiian": "HA",
        "JetBlue": "B6",
        "Spirit": "NK",
        "Sun Country": "SY",
        "Southwest": "WN",
        "United": "UA",
    }


def code_to_airline_map():
    return {v: k for k, v in airline_code_map().items()}


def known_airlines_pattern() -> str:
    airlines = sorted(airline_code_map().keys(), key=len, reverse=True)
    return "(" + "|".join(re.escape(a) for a in airlines) + ")"


def ensure_output_schema(answer):
    if not isinstance(answer, list):
        raise RuntimeError("retrieved_data must be a list")
    if len(answer) != 3:
        raise RuntimeError(f"retrieved_data must have length 3, got {len(answer)}")
    if not all(isinstance(x, str) for x in answer):
        raise RuntimeError("retrieved_data items must all be strings")


def normalize_flight_number(code: str, number: str) -> str:
    code = normalize_space(code).upper()
    number = normalize_space(number)
    if code in {"AS"}:
        return f"{code}{number}"
    return f"{code} {number}"


# ----------------------------
# Playwright primitives
# ----------------------------

async def open_homepage(page, start_url: str) -> None:
    await page.goto(start_url, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)


async def set_one_way(page) -> None:
    candidates = [
        page.get_by_role("combobox", name=re.compile(r"ticket type|change ticket type", re.I)).first,
        page.get_by_role("combobox").first,
    ]
    for combo in candidates:
        try:
            if await combo.count():
                await combo.click()
                await page.wait_for_timeout(500)
                opt = page.get_by_role("option", name=re.compile(r"^One way$", re.I)).first
                if await opt.count():
                    await opt.click()
                    await page.wait_for_timeout(800)
                    return
        except Exception:
            pass
    raise RuntimeError("Could not set trip type to one-way")


async def choose_airport(page, field_label_regex: str, airport_code: str) -> None:
    box = page.get_by_role("combobox", name=re.compile(field_label_regex, re.I))
    await box.click()
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Backspace")
    await page.keyboard.type(airport_code)
    await page.wait_for_timeout(1500)

    option_sets = [
        page.locator('[role="option"]'),
        page.locator("li"),
        page.locator('[role="listbox"] [role="button"]'),
    ]
    for options in option_sets:
        count = min(await options.count(), 40)
        for i in range(count):
            try:
                txt = normalize_space(await options.nth(i).inner_text())
            except Exception:
                continue
            if airport_code.upper() in txt.upper():
                try:
                    await options.nth(i).click(timeout=3000)
                    await page.wait_for_timeout(1000)
                    log(f"selected airport {airport_code}: {txt}")
                    return
                except Exception:
                    continue
    raise RuntimeError(f"Could not select airport {airport_code}")


async def set_departure_date(page, date_str: str) -> None:
    await page.get_by_role("textbox", name=re.compile(r"Departure", re.I)).click()
    await page.wait_for_timeout(800)
    label = date_aria_label(date_str)

    candidates = [
        page.get_by_role("button", name=re.compile(rf"^{re.escape(label)}$", re.I)).first,
        page.get_by_label(re.compile(rf"^{re.escape(label)}$", re.I)).first,
        page.locator(f'[aria-label="{label}"]').first,
        page.locator(f'text="{label}"').first,
    ]
    clicked = False
    for c in candidates:
        try:
            if await c.count():
                await c.click(timeout=4000)
                clicked = True
                break
        except Exception:
            pass
    if not clicked:
        raise RuntimeError(f"Could not select date {label}")

    await page.wait_for_timeout(800)
    for done in [
        page.get_by_role("button", name=re.compile(r"^(Done|Apply)$", re.I)).first,
        page.locator('[aria-label="Done"]').first,
        page.locator("text=/^Done$/i").first,
    ]:
        try:
            if await done.count():
                await done.click(timeout=3000)
                await page.wait_for_timeout(1000)
                return
        except Exception:
            pass
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)
    except Exception:
        pass


async def click_search(page) -> None:
    for btn in [
        page.get_by_role("button", name=re.compile(r"^Search$", re.I)).first,
        page.get_by_role("button", name=re.compile(r"Search for flights", re.I)).first,
    ]:
        try:
            if await btn.count():
                await btn.click(timeout=5000)
                return
        except Exception:
            pass
    raise RuntimeError("Could not find Search button")


async def wait_for_results(page, timeout_ms: int = 60000) -> str:
    waited = 0
    while waited < timeout_ms:
        body = normalize_space(await page.locator("body").inner_text())
        url = page.url
        if (
            "/travel/flights/search" in url
            or "google.com/travel/flights" in url
            or "google.com/flights" in url
        ) and any(
            token in body.lower()
            for token in ["results", "search results", "nonstop", "best departing flights", "top flights"]
        ):
            return body
        await page.wait_for_timeout(2000)
        waited += 2000
    return normalize_space(await page.locator("body").inner_text())


async def apply_nonstop_filter(page) -> None:
    buttons = page.get_by_role("button")
    stop_button = None
    for i in range(min(await buttons.count(), 100)):
        b = buttons.nth(i)
        try:
            blob = normalize_space(((await b.get_attribute("aria-label")) or "") + " " + (await b.inner_text()))
        except Exception:
            continue
        if re.search(r"\b(stops?|nonstop)\b", blob, re.I):
            stop_button = b
            log(f"stops button candidate: {blob}")
            break
    if stop_button is None:
        raise RuntimeError("Could not find Stops filter control")

    await stop_button.click()
    await page.wait_for_timeout(1200)

    nonstop_candidates = [
        page.get_by_role("radio", name=re.compile(r"Nonstop only|Nonstop", re.I)).first,
        page.get_by_role("checkbox", name=re.compile(r"Nonstop only|Nonstop", re.I)).first,
        page.get_by_role("option", name=re.compile(r"Nonstop only|Nonstop", re.I)).first,
        page.get_by_role("button", name=re.compile(r"Nonstop only|Nonstop", re.I)).first,
        page.get_by_label(re.compile(r"Nonstop only|Nonstop", re.I)).first,
        page.locator('[aria-label*="Nonstop"]').first,
        page.locator("text=/\\bNonstop( only)?\\b/i").first,
    ]
    chosen = False
    for item in nonstop_candidates:
        try:
            if await item.count():
                await item.click(timeout=4000)
                chosen = True
                await page.wait_for_timeout(2500)
                break
        except Exception:
            pass
    if not chosen:
        raise RuntimeError("Could not choose Nonstop filter")

    for done in [
        page.get_by_role("button", name=re.compile(r"^(Done|Apply)$", re.I)).first,
    ]:
        try:
            if await done.count():
                await done.click(timeout=2500)
                await page.wait_for_timeout(1500)
                break
        except Exception:
            pass

    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)
    except Exception:
        pass


async def sort_by_departure_time(page) -> None:
    try:
        sort_candidates = [
            page.get_by_role("button", name=re.compile(r"Sorted by|sort order|Sort", re.I)).first,
            page.get_by_text(re.compile(r"Sorted by", re.I)).first,
        ]
        sort_btn = None
        for cand in sort_candidates:
            try:
                if await cand.count():
                    sort_btn = cand
                    break
            except Exception:
                pass
        if sort_btn is None:
            return

        await sort_btn.click(timeout=4000)
        await page.wait_for_timeout(1000)

        for loc in [
            page.get_by_text(re.compile(r"^Departure time$", re.I)).first,
            page.get_by_role("button", name=re.compile(r"^Departure time$", re.I)).first,
            page.get_by_role("option", name=re.compile(r"^Departure time$", re.I)).first,
            page.get_by_role("menuitem", name=re.compile(r"^Departure time$", re.I)).first,
            page.locator("text=Departure time").first,
        ]:
            try:
                if await loc.count():
                    await loc.click(timeout=3000)
                    await page.wait_for_timeout(3000)
                    break
            except Exception:
                continue
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
    except Exception as e:
        log(f"sort warning: {e!r}")


# ----------------------------
# Extraction primitives
# ----------------------------

def parse_nonstop_candidates_from_text(text: str, origin_code: str, destination_code: str):
    txt = normalize_space(text)
    airline_pat = known_airlines_pattern()
    route_pat = rf"{re.escape(origin_code)}\s*[–-]\s*{re.escape(destination_code)}"
    patterns = [
        re.compile(
            rf"(\d{{1,2}}:\d{{2}}\s*[AP]M)\s*[–-]\s*(\d{{1,2}}:\d{{2}}\s*[AP]M)(?:\+1)?\s*{airline_pat}.*?{route_pat}.*?Nonstop",
            re.I | re.S,
        ),
        re.compile(
            rf"(\d{{1,2}}:\d{{2}}\s*[AP]M)\s*[–-]\s*(\d{{1,2}}:\d{{2}}\s*[AP]M)(?:\+1)?\s*{airline_pat}.*?Nonstop.*?{route_pat}",
            re.I | re.S,
        ),
    ]
    rows = []
    for pat in patterns:
        for m in pat.finditer(txt):
            dep = normalize_space(m.group(1)).upper()
            airline = canonical_airline_name(m.group(3))
            rows.append({"departure_time": dep, "airline": airline, "source": normalize_space(m.group(0))})
    dedup = []
    seen = set()
    for row in sorted(rows, key=lambda r: time_key(r["departure_time"])):
        key = (row["departure_time"], row["airline"])
        if key not in seen:
            seen.add(key)
            dedup.append(row)
    return dedup


async def collect_page_blobs(page):
    blobs = []
    try:
        blobs.append(normalize_space(await page.locator("body").inner_text()))
    except Exception:
        pass
    try:
        blobs.append(await page.content())
    except Exception:
        pass
    blobs.append(page.url)
    try:
        aria = await page.locator("body").aria_snapshot(timeout=8000)
        blobs.append(str(aria))
    except Exception:
        pass
    return blobs


def _decode_tfs_base64_chunks(tfs: str):
    out = []
    for m in re.finditer(r'([A-Za-z0-9+/]{2,20})', tfs or ""):
        token = m.group(1)
        if len(token) < 2:
            continue
        try:
            padded = token + "=" * (-len(token) % 4)
            val = base64.b64decode(padded).decode("utf-8", errors="ignore")
            if val:
                out.append(val)
        except Exception:
            pass
    return out


def extract_flight_number_from_tfs(tfs: str, expected_airline: str | None = None, expected_departure: str | None = None):
    tfs = tfs or ""
    code_map = airline_code_map()
    reverse = code_to_airline_map()

    # 1) Explicit itinerary=-XX-123-YYYYMMDD
    for code, airline in reverse.items():
        if expected_airline and airline != expected_airline:
            continue
        m = re.search(rf'itinerary=[^"\'<>]*?-{re.escape(code)}-(\d{{1,4}})-\d{{8}}', tfs, re.I)
        if m:
            return normalize_flight_number(code, m.group(1))

    decoded = " ".join(_decode_tfs_base64_chunks(tfs))
    decoded_norm = normalize_space(decoded)
    if decoded_norm:
        for code, airline in reverse.items():
            if expected_airline and airline != expected_airline:
                continue
            m = re.search(rf"\b{re.escape(code)}\s?(\d{{1,4}})\b", decoded_norm, re.I)
            if m:
                return normalize_flight_number(code, m.group(1))

    # 2) Specific marker pattern seen in Google Flights tfs URLs.
    m = re.search(r'KgJ([A-Za-z0-9+/]{2,8})jID([A-Za-z0-9+/]{2,12})KAB', tfs)
    if m:
        airline_chunk = m.group(1)
        number_chunk = m.group(2)
        try:
            airline_raw = base64.b64decode(airline_chunk + "=" * (-len(airline_chunk) % 4)).decode("utf-8", errors="ignore")
        except Exception:
            airline_raw = ""
        try:
            number_raw = base64.b64decode(number_chunk + "=" * (-len(number_chunk) % 4)).decode("utf-8", errors="ignore")
        except Exception:
            number_raw = ""
        number = re.sub(r"[^\d]", "", number_raw)
        airline_code = normalize_space(airline_raw).upper()
        if airline_code not in reverse:
            if expected_airline:
                airline_code = code_map.get(expected_airline, airline_code)
        if airline_code == "" or airline_code == "\x08":
            if expected_airline:
                airline_code = code_map.get(expected_airline, airline_code)
        if airline_code in reverse and number:
            return normalize_flight_number(airline_code, number)

    # 3) Encoded / unquoted fallback
    uq = unquote(tfs)
    for code, airline in reverse.items():
        if expected_airline and airline != expected_airline:
            continue
        m = re.search(rf"\b{re.escape(code)}\s?(\d{{1,4}})\b", uq, re.I)
        if m:
            return normalize_flight_number(code, m.group(1))

    return None


def extract_flight_number_from_blobs(blobs, airline: str, departure_time: str | None = None):
    code = airline_code_map().get(airline, airline[:2].upper())
    joined = "\n".join(str(b) for b in blobs if b)
    joined_norm = normalize_space(joined)

    patterns = [
        re.compile(rf"\bFlight\s*{re.escape(code)}\s?(\d{{1,4}})\b", re.I),
        re.compile(rf"\b{re.escape(code)}\s?(\d{{1,4}})\b"),
        re.compile(rf'itinerary=[^"\'<>]*?-{re.escape(code)}-(\d{{1,4}})-\d{{8}}', re.I),
    ]

    for pat in patterns:
        m = pat.search(joined_norm)
        if m:
            return normalize_flight_number(code, m.group(1))

    # Search around airline/departure anchor if present.
    if departure_time:
        departure_time = normalize_space(departure_time)
        anchor_variants = [
            f"at {departure_time}",
            departure_time,
            f"{airline}. Leaves",
        ]
        for anchor in anchor_variants:
            idx = joined_norm.find(anchor)
            if idx != -1:
                snippet = joined_norm[max(0, idx - 1200): idx + 8000]
                for pat in patterns:
                    m = pat.search(snippet)
                    if m:
                        return normalize_flight_number(code, m.group(1))

    # Parse tfs from any URLs in blobs.
    for blob in blobs:
        s = str(blob)
        for match in re.finditer(r'https?://[^\s"\'<>]+', s):
            url = match.group(0)
            try:
                tfs = parse_qs(urlparse(url).query).get("tfs", [""])[0]
            except Exception:
                tfs = ""
            if tfs:
                val = extract_flight_number_from_tfs(tfs, expected_airline=airline, expected_departure=departure_time)
                if val:
                    return val

    # Also parse page.url-like raw text directly.
    for blob in blobs:
        s = str(blob)
        try:
            tfs = parse_qs(urlparse(s).query).get("tfs", [""])[0]
        except Exception:
            tfs = ""
        if tfs:
            val = extract_flight_number_from_tfs(tfs, expected_airline=airline, expected_departure=departure_time)
            if val:
                return val

    return None


async def find_and_open_earliest_row(page, earliest):
    dep = earliest["departure_time"]
    airline = earliest["airline"]
    dep_nbsp = dep.replace(" ", "\u202f")

    locators = [
        page.locator(f'div[role="link"][aria-label*="{airline}"][aria-label*="{dep_nbsp}"]').first,
        page.locator(f'div[role="link"][aria-label*="{airline}"][aria-label*="{dep}"]').first,
        page.locator(f'[role="link"][aria-label*="{dep_nbsp}"]').first,
        page.locator(f'[role="link"][aria-label*="{dep}"]').first,
        page.get_by_text(re.compile(rf"^{re.escape(dep)}$", re.I)).first,
    ]

    for idx, loc in enumerate(locators):
        try:
            if await loc.count():
                await loc.scroll_into_view_if_needed(timeout=2000)
                await page.wait_for_timeout(300)
                await loc.click(force=True, timeout=4000)
                await page.wait_for_timeout(3500)
                log(f"opened row via locator {idx}")
                return True
        except Exception as e:
            log(f"row open locator {idx} failed: {e!r}")

    # JS fallback: click an element containing the departure time.
    try:
        clicked = await page.evaluate(
            """(dep) => {
                const norm = s => (s || '').replace(/\\s+/g, ' ').trim();
                const els = Array.from(document.querySelectorAll('*'));
                for (const el of els) {
                    const txt = norm(el.innerText);
                    const aria = norm(el.getAttribute('aria-label'));
                    const role = el.getAttribute('role') || '';
                    if ((txt === dep || aria.includes(dep)) && (role === 'link' || el.closest('[role="link"]'))) {
                        const target = role === 'link' ? el : el.closest('[role="link"]');
                        if (target) { target.click(); return true; }
                    }
                }
                return false;
            }""",
            dep,
        )
        if clicked:
            await page.wait_for_timeout(3500)
            log("opened row via JS fallback")
            return True
    except Exception as e:
        log(f"row open JS fallback failed: {e!r}")

    return False


# ----------------------------
# Thin task layer
# ----------------------------

async def retrieve_earliest_nonstop_flight(page, params):
    next_step("open Google Flights homepage")
    await open_homepage(page, START_URL)
    await snap(page, "open_home")

    next_step("set trip to one-way")
    await set_one_way(page)
    await snap(page, "set_one_way")

    next_step("set route airports")
    await choose_airport(page, r"Where from", params["origin_code"])
    await choose_airport(page, r"Where to", params["destination_code"])
    await snap(page, "set_route")

    next_step("set departure date")
    await set_departure_date(page, params["date"])
    await snap(page, "set_date")

    next_step("search for flights")
    await click_search(page)
    body = await wait_for_results(page)
    log("results url: " + page.url)
    log("results body snippet: " + body[:5000])
    await snap(page, "results_loaded")

    next_step("apply nonstop filter")
    await apply_nonstop_filter(page)
    filtered_body = await wait_for_results(page, timeout_ms=20000)
    log("filtered body snippet: " + filtered_body[:6000])
    await snap(page, "nonstop_applied")

    next_step("sort by departure time when available")
    await sort_by_departure_time(page)
    sorted_body = await wait_for_results(page, timeout_ms=15000)
    log("post-sort body snippet: " + sorted_body[:6000])
    await snap(page, "sorted")

    next_step("parse visible nonstop rows and choose earliest")
    # Sorting on-page is optional (we sort the parsed rows ourselves) and sometimes collapses the
    # results view, so parse whichever body actually has rows: post-sort, then the filtered body
    # (kept before the sort), then a fresh read. The first that yields rows wins.
    rows = []
    for src in (sorted_body, filtered_body,
                normalize_space(await page.locator("body").inner_text())):
        rows = parse_nonstop_candidates_from_text(src, params["origin_code"], params["destination_code"])
        if rows:
            break
    if not rows:
        raise RuntimeError("Could not parse any nonstop rows from results text")

    earliest = sorted(rows, key=lambda r: time_key(r["departure_time"]))[0]
    log("parsed nonstop candidates: " + json.dumps(rows[:10]))
    log("earliest row parsed: " + json.dumps(earliest))

    next_step("open earliest row to improve flight number extraction")
    opened = await find_and_open_earliest_row(page, earliest)
    log(f"opened earliest row: {opened}")
    await snap(page, "earliest_row")

    next_step("extract flight number from detail/page blobs")
    blobs = await collect_page_blobs(page)
    flight_number = extract_flight_number_from_blobs(blobs, earliest["airline"], earliest["departure_time"])

    # If opening row failed or no flight found, retry from results page blobs too.
    if not flight_number:
        try:
            await page.goto(page.url, wait_until="domcontentloaded")
            await page.wait_for_timeout(2500)
        except Exception:
            pass
        retry_blobs = await collect_page_blobs(page)
        flight_number = extract_flight_number_from_blobs(retry_blobs, earliest["airline"], earliest["departure_time"])

    if not flight_number:
        raise RuntimeError("Could not extract flight number from page details")

    answer = [flight_number, earliest["airline"], earliest["departure_time"]]
    ensure_output_schema(answer)
    return answer


async def main():
    LOG_PATH.write_text("", encoding="utf-8")

    required = ["origin_city", "origin_code", "destination_city", "destination_code", "date"]
    missing = [k for k in required if not PARAMS.get(k)]
    if missing:
        raise RuntimeError(f"Missing required params: {missing}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 1800},
            locale="en-US",
        )
        page = await context.new_page()
        answer = await retrieve_earliest_nonstop_flight(page, PARAMS)
        await browser.close()

    payload = {"retrieved_data": answer}
    ensure_output_schema(payload["retrieved_data"])
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log("final answer: " + json.dumps(answer))
    log("wrote: " + str(OUTPUT_PATH))


if __name__ == "__main__":
    asyncio.run(main())
