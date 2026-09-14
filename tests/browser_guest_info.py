"""Self-contained browser checks against a private temporary guest list."""
import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Thread
from wsgiref.simple_server import make_server, WSGIRequestHandler
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.server import create_app, RateLimit
from scripts.guest_list import import_guests

class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args): pass

output = Path(os.environ["LOCALAPPDATA"]) / "WeddingSiteData" / "review"
output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as directory:
    folder = Path(directory)
    database = folder / "guests.sqlite3"
    import_guests([("G1", "H1", "Jordan", "Sample", None, 1, 1),
        ("G2", "H2", "Solo", "Example", None, 1, 0)], database)
    server = make_server("127.0.0.1", 0, create_app(database, RateLimit(limit=100), submissions_dir=folder / "contacts"), handler_class=QuietHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base)
            assert page.locator('input:visible').count() == 2
            assert page.locator('#guest-info').is_hidden()
            page.screenshot(path=str(output / "name-first-desktop.png"), full_page=True)
            for width in (320, 390, 768):
                page.set_viewport_size({"width": width, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.locator('[name="first_initial"]').fill('X')
            page.locator('#contact-lookup [name="last_name"]').fill('Unknown')
            page.get_by_role('button', name='Find my invitation').click()
            page.wait_for_function("document.querySelector('#lookup-status').classList.contains('error')")
            assert page.locator('#guest-info').is_hidden()
            page.locator('[name="first_initial"]').fill('J.')
            page.locator('#contact-lookup [name="last_name"]').fill('Sample')
            page.get_by_role('button', name='Find my invitation').click()
            page.locator('#guest-info').wait_for(state='visible')
            assert page.locator('[name="first_name"]').input_value() == 'Jordan'
            assert page.locator('#guest-info [name="last_name"]').input_value() == 'Sample'
            assert page.locator('[name="first_name"]').get_attribute('readonly') is not None
            assert page.locator('#plus-one-section').is_visible()
            assert not page.locator('[name="sms_consent"]').is_checked()
            assert 'Reply STOP to opt out' in page.locator('.sms-consent').inner_text()
            assert 'SMS Terms' in page.locator('.sms-policies').inner_text()
            assert 'SMS Privacy' in page.locator('.sms-policies').inner_text()
            page.screenshot(path=str(output / "sms-consent-section.png"), full_page=True)
            fields = {'address_line1': '123 Example Lane', 'city': 'Example City', 'region': 'MA', 'postal_code': '01234'}
            for field, value in fields.items(): page.locator(f'[name="{field}"]').fill(value)
            page.locator('[name="guest_name_unknown"]').check()
            assert page.locator('#plus-one-fields').is_hidden()
            page.locator('[name="guest_name_unknown"]').uncheck()
            assert page.locator('#plus-one-fields').is_visible()
            page.locator('[name="plus_one_first_name"]').fill('Alex')
            page.locator('[name="plus_one_last_name"]').fill('Partner')
            sent = []
            def fail(route):
                sent.append(route.request.post_data_json)
                route.fulfill(status=503, content_type='application/json', body='{"error":"Please retry."}')
            page.route('**/api/guest-info', fail)
            page.get_by_role('button', name='Send my details').click()
            page.wait_for_function("document.querySelector('#form-status').classList.contains('error')")
            assert page.locator('[name="address_line1"]').input_value() == '123 Example Lane'
            page.unroute('**/api/guest-info', fail)
            page.on('request', lambda request: sent.append(request.post_data_json) if request.url.endswith('/api/guest-info') else None)
            page.get_by_role('button', name='Send my details').click()
            page.locator('#confirmation').wait_for(state='visible')
            assert sent[0]['submission_id'] == sent[1]['submission_id']
            assert sent[1]['sms_consent'] is False  # submitted without checking the box
            records = [json.loads(p.read_text()) for p in (folder / 'contacts').glob('*.json')]
            assert records[0]['name_line_one'] == 'Jordan Sample'
            assert records[0]['name_line_two'] == 'and Alex Partner'
            page.locator('#confirmation .change-invitation').click()
            page.locator('[name="first_initial"]').fill('S')
            page.locator('#contact-lookup [name="last_name"]').fill('Example')
            page.get_by_role('button', name='Find my invitation').click()
            page.locator('#guest-info').wait_for(state='visible')
            assert page.locator('#plus-one-section').is_hidden()
            assert 'you only' in page.locator('#party-summary').inner_text()
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
print('Browser checks passed: two-field entry, unknown guest, matched invitation, plus-one toggle, retry, and solo invitation.')
