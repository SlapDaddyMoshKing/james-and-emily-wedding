"""Run manually: python tests/browser_guest_info.py (local backend on port 8080)."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

output = Path(os.environ["LOCALAPPDATA"]) / "WeddingSiteData" / "review"
output.mkdir(parents=True, exist_ok=True)
base_url = os.environ.get("WEDDING_TEST_URL", "http://127.0.0.1:8080")
with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base_url)
    page.wait_for_function("!document.querySelector('[type=submit]').disabled")
    page.screenshot(path=str(output / "desktop.png"), full_page=True)
    for width in (320, 390, 768):
        page.set_viewport_size({"width": width, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), width
        if width == 390:
            page.screenshot(path=str(output / "mobile.png"), full_page=True)
    page.get_by_role("button", name="Send my details").click()
    assert page.locator("#form-status").inner_text() == "Please check the highlighted fields."
    fields = {"name_line_one": "Browser Example", "inner_envelope": "Example",
        "address_line1": "123 Example Lane", "city": "Chicago", "region": "IL", "postal_code": "60601"}
    for field, value in fields.items():
        page.locator(f'[name="{field}"]').fill(value)
    requests = []
    def fail(route):
        requests.append(route.request.post_data_json)
        route.fulfill(status=503, content_type="application/json", body='{"error":"Unavailable"}')
    page.route("**/api/guest-info", fail)
    page.get_by_role("button", name="Send my details").click()
    page.wait_for_function("!document.querySelector('[type=submit]').disabled")
    assert page.locator('[name="name_line_one"]').input_value() == "Browser Example"
    assert page.locator("#confirmation").is_hidden()
    page.unroute("**/api/guest-info", fail)
    page.on("request", lambda request: requests.append(request.post_data_json) if request.url.endswith("/api/guest-info") else None)
    page.get_by_role("button", name="Send my details").click()
    page.locator("#confirmation").wait_for(state="visible")
    assert requests[0]["submission_id"] == requests[1]["submission_id"]
    target = Path(os.environ["LOCALAPPDATA"]) / "WeddingSiteData" / "guest-info-local" / (requests[1]["submission_id"] + ".json")
    assert json.loads(target.read_text(encoding="utf-8"))["name_line_one"] == "Browser Example"
    target.unlink()  # Only this test's known fictional submission.
    assert page.evaluate("document.activeElement.id") == "confirmation"
    page.get_by_role("button", name="Send details for another guest").click()
    assert page.locator('[name="name_line_one"]').input_value() == ""
    page.goto(base_url + "/welcome.html")
    page.wait_for_url(base_url + "/")
    assert not errors, errors
    browser.close()
print(f"Browser checks passed: responsive layout, validation, failed save, retry, storage, reset, old link. Screenshots: {output}")
