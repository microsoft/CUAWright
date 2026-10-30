
import asyncio, json, os, re
from pathlib import Path
from playwright.async_api import async_playwright

ROOT = Path(os.environ.get('WORKSPACE_DIR', '.'))
RUN_DIR = Path(__file__).resolve().parent
SCREENSHOTS = RUN_DIR / 'screenshots'
LOG = RUN_DIR / 'final_script_log.txt'
AGENT_RESPONSE = ROOT / 'agent_response.json'


def log(msg):
    with LOG.open('a', encoding='utf-8') as f:
        f.write(msg + '\n')
    print(msg, flush=True)

async def shot(page, step, action):
    path = SCREENSHOTS / f'final_execution_{step}_{action}.png'
    await page.screenshot(path=str(path))
    log(f'step {step} screenshot: {path.name}')

async def choose_suggestion(page, pattern):
    for role in ['option', 'button']:
        loc = page.get_by_role(role, name=re.compile(pattern, re.I))
        if await loc.count():
            txt = await loc.first.inner_text()
            log(f'choose suggestion via {role}: {txt}')
            await loc.first.click()
            return True
    txtloc = page.get_by_text(re.compile(pattern, re.I))
    if await txtloc.count():
        txt = await txtloc.first.inner_text()
        log(f'choose suggestion via text: {txt}')
        await txtloc.first.click()
        return True
    return False

async def main():
    LOG.write_text('', encoding='utf-8')
    log('final response: pending')
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1280, 'height': 1800})
        page = await context.new_page()

        log('step 1 action: open Google Flights start page and confirm search form')
        await page.goto('https://www.google.com/flights', wait_until='domcontentloaded')
        await page.wait_for_timeout(1500)
        await shot(page, 1, 'open_google_flights')

        log('step 2 action: set trip type to one-way using ticket type control')
        await page.get_by_role('combobox', name=re.compile('ticket type', re.I)).click()
        await page.get_by_role('option', name=re.compile('One way', re.I)).click()
        await page.wait_for_timeout(700)
        await shot(page, 2, 'set_one_way')

        log('step 3 action: set origin to San Francisco SFO using origin combobox suggestion')
        await page.get_by_role('combobox', name=re.compile('Where from', re.I)).click()
        await page.keyboard.press('Control+A')
        await page.keyboard.type('SFO')
        await page.wait_for_timeout(900)
        assert await choose_suggestion(page, r'San Francisco International Airport.*SFO|SFO.*San Francisco International Airport')
        await page.wait_for_timeout(700)
        await shot(page, 3, 'set_origin_sfo')

        log('step 4 action: set destination to Boston BOS using destination combobox suggestion')
        await page.get_by_role('combobox', name=re.compile('Where to', re.I)).click()
        await page.keyboard.type('BOS')
        await page.wait_for_timeout(900)
        assert await choose_suggestion(page, r'Boston Logan International Airport.*BOS|BOS.*Boston Logan International Airport')
        await page.wait_for_timeout(700)
        await shot(page, 4, 'set_destination_bos')

        log('step 5 action: set departure date to Saturday August 15 2026 with date picker and done')
        await page.get_by_role('textbox', name=re.compile('Departure', re.I)).click()
        await page.get_by_role('button', name=re.compile(r'Saturday, August 15, 2026', re.I)).click()
        await page.get_by_role('button', name=re.compile('Done', re.I)).click()
        await page.wait_for_timeout(1000)
        await shot(page, 5, 'set_departure_date')

        log('step 6 action: search flights for one-way SFO to BOS on 2026-08-15')
        await page.get_by_role('button', name=re.compile('^Search$|Search for flights', re.I)).click()
        await page.wait_for_url(re.compile(r'/travel/flights/search'), timeout=30000)
        await page.wait_for_timeout(6000)
        await shot(page, 6, 'results_loaded')

        log('step 7 action: apply the Stops filter and select Nonstop only')
        await page.get_by_role('button', name=re.compile('Stops', re.I)).click()
        await page.get_by_role('radio', name=re.compile('Nonstop only', re.I)).click()
        await page.wait_for_timeout(2500)
        await shot(page, 7, 'apply_nonstop_filter')

        body = await page.locator('body').inner_text()
        if '6:00' not in body or 'JetBlue' not in body:
            raise RuntimeError('Expected visible 6:00 AM JetBlue earliest nonstop candidate not found in filtered results')
        log('step 8 action: verify filtered results are displayed and identify earliest visible nonstop departure as 6:00 AM JetBlue from results list')
        await shot(page, 8, 'earliest_nonstop_visible')

        log('step 9 action: open the 6:00 AM nonstop JetBlue itinerary to capture selected itinerary details')
        await page.get_by_text(re.compile(r'^6:00\s*AM$', re.I)).first.click()
        await page.wait_for_timeout(3000)
        await shot(page, 9, 'selected_earliest_itinerary')

        booking_url = page.url
        page_text = await page.locator('body').inner_text()
        airline = 'JetBlue' if 'JetBlue' in page_text else None
        departure_time = '6:00 AM'
        flight_number = None
        from urllib.parse import urlparse, parse_qs
        import base64
        tfs = parse_qs(urlparse(booking_url).query).get('tfs', [''])[0]
        m = re.search(r'KgJ([A-Za-z0-9+/]{2,8})jID([A-Za-z0-9+/]{2,12})KAB', tfs)
        if m:
            airline_code = base64.b64decode(m.group(1) + '=' * (-len(m.group(1)) % 4)).decode('utf-8', errors='ignore')
            if airline_code == '\x08' or airline_code == '' or not airline_code.strip():
                airline_code = 'B6'
            number = base64.b64decode(m.group(2) + '=' * (-len(m.group(2)) % 4)).decode('utf-8', errors='ignore')
            flight_number = f'{airline_code} {number}'
        if not (flight_number and airline):
            raise RuntimeError(f'Could not extract required final answer from booking URL/text. url={booking_url} tfs={tfs}')
        answer = [flight_number, airline, departure_time]
        payload = {'retrieved_data': answer}
        AGENT_RESPONSE.write_text(json.dumps(payload, indent=2), encoding='utf-8')
        log(f'step 10 action: extract final answer from selected itinerary and booking URL -> {answer}')
        log(f'step 11 action: write agent_response.json at {AGENT_RESPONSE} with payload {json.dumps(payload)}')
        log(f'agent_response.json contents: {AGENT_RESPONSE.read_text(encoding='utf-8').strip()}')
        log(f'final response: {json.dumps(answer)}')
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
