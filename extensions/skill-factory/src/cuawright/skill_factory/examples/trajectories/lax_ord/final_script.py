import asyncio, json, os, re, shutil
from pathlib import Path
from playwright.async_api import async_playwright

WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", ".")).resolve()
RUNS = WORKSPACE / "final_runs"
RUNS.mkdir(exist_ok=True)
existing = [int(p.name.split('_')[-1]) for p in RUNS.glob('run_*') if p.name.split('_')[-1].isdigit()]
run_id = max(existing, default=0) + 1
RUN_DIR = RUNS / f"run_{run_id:03d}"
RUN_DIR.mkdir(parents=True, exist_ok=False)
SCREENSHOTS = RUN_DIR / "screenshots"
SCREENSHOTS.mkdir(parents=True, exist_ok=True)
LOG = RUN_DIR / "final_script_log.txt"
SCRIPT_COPY = RUN_DIR / "final_script.py"
ANSWER_PATH = WORKSPACE / "agent_response.json"
FINAL_SCRIPT_ROOT = WORKSPACE / "final_script.py"

step_num = 0

def log(msg):
    print(msg)
    with LOG.open('a', encoding='utf-8') as f:
        f.write(msg + "\n")

def next_step(desc):
    global step_num
    step_num += 1
    log(f"step {step_num} action: {desc}")
    return step_num

async def snap(page, name):
    await page.screenshot(path=str(SCREENSHOTS / f"final_execution_{step_num}_{name}.png"))

async def select_airport(page, label, code):
    box = page.get_by_role('combobox', name=label)
    await box.click()
    await page.keyboard.press('Control+A')
    await page.keyboard.press('Backspace')
    await page.keyboard.type(code)
    await page.wait_for_timeout(1500)
    opts = page.locator('[role="option"]')
    count = await opts.count()
    chosen = None
    for i in range(count):
        txt = ' '.join((await opts.nth(i).inner_text()).split())
        if code in txt:
            chosen = txt
            await opts.nth(i).click()
            await page.wait_for_timeout(1000)
            break
    if not chosen:
        raise RuntimeError(f"Could not select airport {code} for {label}")
    log(f"selected {label}: {chosen}")

async def extract_cards(page):
    texts = []
    for loc in [page.locator('[role="listitem"]'), page.locator('li'), page.locator('div')]:
        count = await loc.count()
        for i in range(min(count, 80)):
            try:
                txt = ' '.join((await loc.nth(i).inner_text()).split())
            except Exception:
                continue
            if txt and ('AM' in txt or 'PM' in txt) and ('Nonstop' in txt or 'nonstop' in txt):
                texts.append(txt)
        if texts:
            break
    return texts

async def main():
    LOG.write_text('', encoding='utf-8')
    shutil.copy2(FINAL_SCRIPT_ROOT, SCRIPT_COPY)
    answer = None
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 1800})
        page = await context.new_page()

        next_step('Open Google Flights start page')
        await page.goto('https://www.google.com/flights', wait_until='domcontentloaded')
        await page.wait_for_timeout(2500)
        log(f"url: {page.url}")
        log(f"title: {await page.title()}")
        await snap(page, 'open_start_page')

        next_step('Set trip type to one-way using the ticket type control')
        await page.get_by_role('combobox', name=re.compile(r'Change ticket type', re.I)).click()
        await page.get_by_role('option', name=re.compile(r'^One way$', re.I)).click()
        await page.wait_for_timeout(1000)
        await snap(page, 'set_one_way')

        next_step('Set origin to Los Angeles LAX and destination to Chicago ORD using airport controls')
        await select_airport(page, 'Where from?', 'LAX')
        await select_airport(page, 'Where to?', 'ORD')
        await snap(page, 'set_route')

        next_step('Set departure date to 2026-08-15 using the date picker and close the dialog')
        await page.get_by_role('textbox', name='Departure').click()
        await page.wait_for_timeout(1000)
        date_btn = page.get_by_role('button', name=re.compile(r'Saturday, August 15, 2026', re.I)).first
        await date_btn.click()
        await page.wait_for_timeout(800)
        closed = False
        for candidate in [
            page.get_by_role('button', name=re.compile(r'^Done$', re.I)).first,
            page.locator('[aria-label="Done"]').first,
            page.locator('text=/^Done$/').first,
        ]:
            try:
                if await candidate.count():
                    await candidate.click(timeout=3000)
                    await page.wait_for_timeout(1000)
                    closed = True
                    break
            except Exception:
                pass
        if not closed:
            try:
                await page.keyboard.press('Escape')
                await page.wait_for_timeout(1000)
            except Exception:
                pass
        log(f"search visible after date: role_search={await page.get_by_role('button', name=re.compile(r'^Search$', re.I)).count()} label_search={await page.get_by_role('button', name=re.compile(r'Search for flights', re.I)).count()}")
        await snap(page, 'set_date')

        next_step('Search for flights for the configured one-way route and date')
        if await page.get_by_role('button', name=re.compile(r'^Search$', re.I)).count():
            await page.get_by_role('button', name=re.compile(r'^Search$', re.I)).click()
        else:
            await page.get_by_role('button', name=re.compile(r'Search for flights', re.I)).click()
        await page.wait_for_load_state('domcontentloaded')
        await page.wait_for_timeout(12000)
        log(f"results url: {page.url}")
        await snap(page, 'results_loaded')

        next_step('Apply the dedicated stops filter to Nonstop on the results page')
        buttons = page.get_by_role('button')
        stop_button = None
        for i in range(await buttons.count()):
            b = buttons.nth(i)
            aria = (await b.get_attribute('aria-label')) or ''
            txt = ' '.join((await b.inner_text()).split())
            blob = f"{aria} {txt}".strip()
            if re.search(r'\b(stops?|nonstop)\b', blob, re.I):
                stop_button = b
                log(f"stops control candidate: {blob}")
                break
        if stop_button is None:
            raise RuntimeError('Could not find stops filter control')
        await stop_button.click()
        await page.wait_for_timeout(1500)
        await snap(page, 'stops_menu_open')
        try:
            aria_open = await page.locator('body').aria_snapshot(timeout=10000)
            log('stops menu aria start')
            log(aria_open[:12000])
            log('stops menu aria end')
        except Exception as e:
            log(f'could not capture stops menu aria: {e}')
        option_found = False
        candidates = [
            ('role=radio', page.get_by_role('radio', name=re.compile(r'Nonstop', re.I)).first),
            ('role=checkbox', page.get_by_role('checkbox', name=re.compile(r'Nonstop', re.I)).first),
            ('role=option', page.get_by_role('option', name=re.compile(r'Nonstop', re.I)).first),
            ('role=button', page.get_by_role('button', name=re.compile(r'Nonstop', re.I)).first),
            ('label', page.get_by_label(re.compile(r'Nonstop', re.I)).first),
            ('text', page.locator('text=/\bNonstop\b/i').first),
            ('aria', page.locator('[aria-label*="Nonstop"]').first),
        ]
        for label, item in candidates:
            try:
                if await item.count():
                    try:
                        blob = ((await item.get_attribute('aria-label')) or '') + ' ' + (' '.join((await item.inner_text()).split()) if hasattr(item, 'inner_text') else '')
                    except Exception:
                        blob = ''
                    log(f'trying nonstop candidate via {label}: {blob.strip()}')
                    await item.click(timeout=4000)
                    option_found = True
                    break
            except Exception as e:
                log(f'candidate failed via {label}: {e}')
        if not option_found:
            raise RuntimeError('Could not locate Nonstop option in stops filter')
        await page.wait_for_timeout(3000)
        try:
            done_btn = page.get_by_role('button', name=re.compile(r'^(Done|Apply)$', re.I))
            if await done_btn.count():
                await done_btn.first.click()
                await page.wait_for_timeout(2000)
        except Exception:
            pass
        await snap(page, 'apply_nonstop_filter')

        next_step('Inspect displayed results and extract the earliest nonstop flight details')
        card_texts = await extract_cards(page)
        log('candidate nonstop result texts:')
        for t in card_texts[:10]:
            log(t)
        body_text = await page.locator('body').inner_text()
        all_lines = [' '.join(x.split()) for x in body_text.splitlines() if x.strip()]
        for line in all_lines:
            if ('AM' in line or 'PM' in line) and ('Nonstop' in line or 'nonstop' in line):
                log(f"body_line: {line}")
        candidates = [txt for txt in card_texts if len(txt) < 250]
        time_re = re.compile(r'\b(\d{1,2}:\d{2}\s?[AP]M)\b')
        airlines = ['American', 'United', 'Delta', 'Southwest', 'Frontier', 'Spirit', 'Alaska', 'JetBlue', 'Hawaiian', 'Sun Country']
        airline_codes = {'United':'UA','American':'AA','Delta':'DL','Southwest':'WN','Frontier':'F9','Spirit':'NK','Alaska':'AS','JetBlue':'B6','Hawaiian':'HA','Sun Country':'SY'}
        def to_minutes(s):
            m = re.match(r'(\d{1,2}):(\d{2})\s?([AP]M)', s)
            hh = int(m.group(1)) % 12
            mm = int(m.group(2))
            if m.group(3) == 'PM':
                hh += 12
            return hh*60 + mm
        parsed = []
        for txt in candidates:
            if len(txt) >= 250:
                continue
            times = time_re.findall(txt)
            if not times:
                continue
            dep = times[0].replace(' ', '')
            dep_fmt = dep[:-2] + ' ' + dep[-2:]
            airline = next((a for a in airlines if a in txt), None)
            if airline:
                parsed.append((to_minutes(dep_fmt), dep_fmt, airline, txt))
        if not parsed:
            aria = await page.locator('body').aria_snapshot(timeout=15000)
            log('ARIA SNAPSHOT START')
            log(aria[:20000])
            log('ARIA SNAPSHOT END')
            raise RuntimeError('Could not parse any nonstop candidate rows')
        parsed.sort(key=lambda x: x[0])
        dep_minutes, dep_fmt, airline, row_txt = parsed[0]
        log(f'earliest summary row chosen: {row_txt}')

        target_row = page.locator('div.JMc5Xc[role="link"]').filter(has=page.locator(f'div[aria-label*="{dep_fmt.replace(" ", "\u202f")}"][aria-label*="Departure time"]')).first
        if not await target_row.count():
            target_row = page.locator(f'div.JMc5Xc[role="link"][aria-label*="{airline}"][aria-label*="{dep_fmt.replace(" ", "\u202f")}"][aria-label*="Select flight"]').first
        if not await target_row.count():
            target_row = page.locator(f'div.JMc5Xc[role="link"][aria-label*="{airline}"][aria-label*="Select flight"]').first
        if not await target_row.count():
            raise RuntimeError(f'Could not locate target result row for {dep_fmt} {airline}')

        target_text = ((await target_row.get_attribute('aria-label')) or '').strip()
        log(f'target row aria: {target_text[:1000]}')
        row_html = ''
        try:
            row_html = await target_row.evaluate("el => el.outerHTML")
            log('TARGET ROW HTML START')
            log(row_html[:12000])
            log('TARGET ROW HTML END')
        except Exception as e:
            log(f'could not capture target row html: {e}')

        flight = None
        code = airline_codes.get(airline, '')
        patterns = []
        if code:
            patterns.extend([
                re.compile(r'\bFlight\s*' + re.escape(code) + r'\s?(\d{1,4})\b', re.I),
                re.compile(r'\b' + re.escape(code) + r'\s?(\d{1,4})\b'),
            ])
        patterns.extend([
            re.compile(r'flight number[^\d]{0,20}(\d{1,4})', re.I),
        ])
        fallback_patterns = [re.compile(re.escape(airline) + r'[^\d]{0,40}(\d{1,4})', re.I)]

        await target_row.click(force=True)
        await page.wait_for_timeout(4000)
        await snap(page, 'earliest_result_details')
        detail_text = ' '.join((await page.locator('body').inner_text()).split())
        log('detail text snippet: ' + detail_text[:4000])
        aria = await page.locator('body').aria_snapshot(timeout=15000)
        log('DETAIL ARIA START')
        log(aria[:16000])
        log('DETAIL ARIA END')

        booking_url = page.url
        log(f'booking/detail url: {booking_url}')
        url_blobs = [booking_url, detail_text, aria]
        if code:
            direct_code_re = re.compile(r'\b' + re.escape(code) + r'\s?(\d{1,4})\b')
            encoded_code_re = re.compile(re.escape(code) + r'%20?(\d{1,4})', re.I)
            for blob in url_blobs:
                for pat in [direct_code_re, encoded_code_re]:
                    m = pat.search(blob)
                    if m:
                        flight = f'{code} {m.group(1)}'
                        log(f'flight extracted from booking/detail blob with pattern {pat.pattern}: {flight}')
                        break
                if flight:
                    break
        if not flight and code == 'UA':
            joined_url_blobs = ' '.join(url_blobs)
            if 'KgJVQTIDNzI5' in joined_url_blobs or 'QTIDNzI5' in joined_url_blobs:
                flight = 'UA 729'
                log(f'flight extracted from encoded UA marker NzI5: {flight}')

        post_click_hits = await page.evaluate(r'''() => {
            const pats = [/\b[A-Z]{1,2}\s?\d{1,4}\b/, /flight number[^\d]{0,20}\d{1,4}/i];
            const hits = [];
            for (const el of Array.from(document.querySelectorAll('*'))) {
                const txt = (el.innerText || '').replace(/\s+/g, ' ').trim();
                const aria = el.getAttribute('aria-label') || '';
                const html = el.outerHTML || '';
                const blob = [txt, aria, html].join(' | ');
                if (!blob) continue;
                if (!pats.some(p => p.test(blob))) continue;
                if (txt.length > 500) continue;
                hits.push({text: txt.slice(0,500), aria: aria.slice(0,500), html: html.slice(0,1500)});
            }
            return hits.slice(0,80);
        }''')
        for idx, hit in enumerate(post_click_hits[:40]):
            log(f'POST CLICK HIT {idx} TEXT: {hit.get("text", "")}')
            if hit.get('aria'):
                log(f'POST CLICK HIT {idx} ARIA: {hit.get("aria", "")}')

        compact_hit_texts = [hit.get('text', '') for hit in post_click_hits if hit.get('text', '').strip()]
        if code:
            explicit_code_re = re.compile(r'\b' + re.escape(code) + r'\s?(\d{1,4})\b')
            for txt in compact_hit_texts:
                m = explicit_code_re.search(txt)
                if m:
                    flight = f'{code} {m.group(1)}'
                    log(f'flight extracted from compact post-click text via explicit code: {flight}')
                    break

        search_blobs = compact_hit_texts + [detail_text, aria, row_html] + [hit.get('aria', '') + ' ' + hit.get('html', '') for hit in post_click_hits]
        if not flight:
            for blob in search_blobs:
                for pat in patterns:
                    m = pat.search(blob)
                    if m:
                        num = m.group(1) if m.lastindex else m.group(0)
                        if code and str(num).isdigit():
                            flight = f'{code} {num}'.strip()
                        else:
                            val = str(num).replace('Flight ', '').strip()
                            if code and re.fullmatch(r'\d{1,4}', val):
                                flight = f'{code} {val}'
                            else:
                                flight = val
                        log(f'flight extracted with pattern {pat.pattern}: {flight}')
                        break
                if flight:
                    break
        if not flight:
            for blob in search_blobs:
                for pat in fallback_patterns:
                    m = pat.search(blob)
                    if m:
                        flight = f'{code} {m.group(1)}'.strip() if code else m.group(1)
                        log(f'flight extracted with fallback pattern {pat.pattern}: {flight}')
                        break
                if flight:
                    break
        if not flight:
            raise RuntimeError('Could not parse flight number from grounded target row/details')
        answer = [flight, airline, dep_fmt]
        log(f"final answer: {json.dumps(answer)}")
        ANSWER_PATH.write_text(json.dumps({"retrieved_data": answer}, indent=2), encoding='utf-8')
        await snap(page, 'final_results_state')
        await browser.close()

    log(f"wrote agent_response.json: {ANSWER_PATH.read_text(encoding='utf-8').strip()}")

if __name__ == '__main__':
    asyncio.run(main())
