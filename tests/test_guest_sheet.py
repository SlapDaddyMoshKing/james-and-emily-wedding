import json
import unittest
from unittest.mock import patch

import rsa

from backend.contact_access import AccessDenied
from backend.guest_sheet import (
    SheetSyncError, authorize_submission_from_sheet, build_updates, find_guest, find_row,
    lookup_guest_from_sheet, sync_submission,
)

_KEY = rsa.newkeys(2048)[1]


def service_account_json():
    return json.dumps({
        "type": "service_account",
        "client_email": "test@example.iam.gserviceaccount.com",
        "private_key_id": "abc123",
        "private_key": _KEY.save_pkcs1().decode(),
        "token_uri": "https://oauth2.googleapis.com/token",
    })


HEADER = ["First Initial", "Last Name", "Plus One?", "Guest First Name", "Guest Last Name",
          "Plus One First Name", "Plus One Last Name", "Phone Number", "Email Address",
          "Address Line One", "Address Line Two", "City", "State ", "Zip Code"]


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return self._body

    def getheaders(self):
        return [("Content-Type", "application/json")]


def fake_urlopen(sheet_rows, calls):
    def handler(request, timeout=None):
        url = request.full_url
        calls.append((request.get_method(), url, request.data))
        if url == "https://oauth2.googleapis.com/token":
            return FakeResponse(200, {"access_token": "fake-token", "expires_in": 3600, "token_type": "Bearer"})
        if url.endswith("?fields=sheets.properties"):
            return FakeResponse(200, {"sheets": [{"properties": {"sheetId": 793360834, "title": "Sheet1"}}]})
        if "/values/Sheet1" in url and request.get_method() == "GET":
            return FakeResponse(200, {"values": sheet_rows})
        if url.endswith("/values:batchUpdate"):
            return FakeResponse(200, {"totalUpdatedCells": 1})
        raise AssertionError(f"Unexpected request: {request.get_method()} {url}")
    return handler


class FindRowTests(unittest.TestCase):
    def test_matches_case_and_whitespace_insensitively(self):
        rows = [HEADER, ["j", " Boudreaux ", "Yes"], ["A", "Other", "No"]]
        self.assertEqual(find_row(rows, "J", "Boudreaux"), 2)
        self.assertEqual(find_row(rows, "A", "Other"), 3)
        self.assertIsNone(find_row(rows, "Z", "Nobody"))

    def test_missing_columns_raise(self):
        with self.assertRaises(SheetSyncError):
            find_row([["Something Else"]], "J", "Boudreaux")


class BuildUpdatesTests(unittest.TestCase):
    def test_only_columns_with_data_are_written(self):
        data = {"first_name": "James", "last_name": "Boudreaux", "phone": "918-397-1026",
                "address_line1": "123 Main St", "address_line2": "", "city": "Springdale",
                "region": "Arkansas", "postal_code": "72762",
                "plus_one_first_name": "", "plus_one_last_name": "", "guest_name_unknown": False}
        updates = build_updates(HEADER, 2, data)
        by_range = {update["range"]: update["values"][0][0] for update in updates}
        self.assertEqual(by_range["D2"], "James")
        self.assertEqual(by_range["E2"], "Boudreaux")
        self.assertEqual(by_range["H2"], "918-397-1026")
        self.assertEqual(by_range["J2"], "123 Main St")
        self.assertEqual(by_range["L2"], "Springdale")
        self.assertEqual(by_range["M2"], "Arkansas")
        self.assertEqual(by_range["N2"], "72762")
        self.assertNotIn("K2", by_range)  # address_line2 blank: left untouched
        self.assertNotIn("F2", by_range)  # plus-one name blank: left untouched
        self.assertNotIn("I2", by_range)  # Email Address: never written

    def test_unknown_guest_writes_placeholder(self):
        data = {"first_name": "James", "last_name": "Boudreaux", "guest_name_unknown": True,
                "plus_one_first_name": "", "plus_one_last_name": ""}
        updates = build_updates(HEADER, 2, data)
        by_range = {update["range"]: update["values"][0][0] for update in updates}
        self.assertEqual(by_range["F2"], "Unknown")
        self.assertEqual(by_range["G2"], "Unknown")


class SyncSubmissionTests(unittest.TestCase):
    def data(self, **overrides):
        return {"first_name": "James", "last_name": "Boudreaux", "phone": "918-397-1026",
                "address_line1": "123 Main St", "address_line2": "", "city": "Springdale",
                "region": "Arkansas", "postal_code": "72762", "plus_one_first_name": "",
                "plus_one_last_name": "", "guest_name_unknown": False, **overrides}

    def test_updates_matching_row(self):
        rows = [HEADER, ["J", "Boudreaux", "Yes"]]
        calls = []
        with patch("backend.guest_sheet.urlopen", side_effect=fake_urlopen(rows, calls)):
            sync_submission(service_account_json(), "sheet-id", "793360834", self.data())
        batch = next(body for method, url, body in calls if url.endswith("/values:batchUpdate"))
        payload = json.loads(batch)
        ranges = {entry["range"] for entry in payload["data"]}
        self.assertEqual(ranges, {"Sheet1!D2", "Sheet1!E2", "Sheet1!H2", "Sheet1!J2",
                                   "Sheet1!L2", "Sheet1!M2", "Sheet1!N2"})

    def test_no_matching_row_raises_without_writing(self):
        rows = [HEADER, ["Z", "Nobody", "No"]]
        calls = []
        with patch("backend.guest_sheet.urlopen", side_effect=fake_urlopen(rows, calls)):
            with self.assertRaises(SheetSyncError):
                sync_submission(service_account_json(), "sheet-id", "793360834", self.data())
        self.assertFalse(any(url.endswith("/values:batchUpdate") for _, url, _ in calls))

    def test_no_writable_columns_skips_the_write_call(self):
        minimal_header = ["First Initial", "Last Name"]
        rows = [minimal_header, ["J", "Boudreaux"]]
        calls = []
        with patch("backend.guest_sheet.urlopen", side_effect=fake_urlopen(rows, calls)):
            sync_submission(service_account_json(), "sheet-id", "793360834", self.data())
        self.assertFalse(any(url.endswith("/values:batchUpdate") for _, url, _ in calls))


class FindGuestTests(unittest.TestCase):
    def test_returns_prefill_and_falls_back_to_initial_for_missing_name(self):
        rows = [HEADER, ["J", "Boudreaux", "Yes", "", "", "", "", "918-397-1026", "j@example.com",
                          "123 Main St", "", "Springdale", "Arkansas", "72762"]]
        calls = []
        with patch("backend.guest_sheet.urlopen", side_effect=fake_urlopen(rows, calls)):
            guest = find_guest(service_account_json(), "sheet-id", "793360834", "J", "Boudreaux")
        self.assertEqual(guest["first_name"], "J")  # no Guest First Name filled: falls back to the initial
        self.assertEqual(guest["last_name"], "Boudreaux")
        self.assertTrue(guest["plus_one_allowed"])
        self.assertEqual(guest["prefill"]["phone"], "918-397-1026")
        self.assertEqual(guest["prefill"]["address_line1"], "123 Main St")
        self.assertEqual(guest["prefill"]["region"], "Arkansas")
        self.assertFalse(guest["prefill"]["guest_name_unknown"])

    def test_uses_full_name_and_marks_unknown_plus_one(self):
        rows = [HEADER, ["J", "Boudreaux", "Yes", "James", "Boudreaux", "Unknown", "Unknown",
                          "", "", "", "", "", "", ""]]
        calls = []
        with patch("backend.guest_sheet.urlopen", side_effect=fake_urlopen(rows, calls)):
            guest = find_guest(service_account_json(), "sheet-id", "793360834", "J", "Boudreaux")
        self.assertEqual(guest["first_name"], "James")
        self.assertTrue(guest["prefill"]["guest_name_unknown"])
        self.assertEqual(guest["prefill"]["plus_one_first_name"], "")

    def test_no_match_returns_none(self):
        rows = [HEADER, ["Z", "Nobody", "No", "", "", "", "", "", "", "", "", "", "", ""]]
        calls = []
        with patch("backend.guest_sheet.urlopen", side_effect=fake_urlopen(rows, calls)):
            self.assertIsNone(find_guest(service_account_json(), "sheet-id", "793360834", "J", "Boudreaux"))

    def test_missing_name_falls_back_to_sheets_own_casing_not_the_search_terms(self):
        # A search normalizes to lowercase for matching, but a fallback display
        # name should use whatever casing the couple actually typed in the sheet.
        rows = [HEADER, ["E", "Wright", "No", "", "", "", "", "", "", "", "", "", "", ""]]
        calls = []
        with patch("backend.guest_sheet.urlopen", side_effect=fake_urlopen(rows, calls)):
            guest = find_guest(service_account_json(), "sheet-id", "793360834", "e", "wright")
        self.assertEqual(guest["first_name"], "E")
        self.assertEqual(guest["last_name"], "Wright")


class LookupGuestFromSheetTests(unittest.TestCase):
    def test_valid_identity_returns_guest(self):
        rows = [HEADER, ["J", "Boudreaux", "Yes", "James", "Boudreaux", "", "", "", "", "", "", "", "", ""]]
        calls = []
        with patch("backend.guest_sheet.urlopen", side_effect=fake_urlopen(rows, calls)):
            guest = lookup_guest_from_sheet(service_account_json(), "sheet-id", "793360834",
                {"first_initial": " j. ", "last_name": " BOUDREAUX "})
        self.assertEqual(guest["first_name"], "James")

    def test_unknown_name_raises_access_denied(self):
        rows = [HEADER, ["Z", "Nobody", "No", "", "", "", "", "", "", "", "", "", "", ""]]
        calls = []
        with patch("backend.guest_sheet.urlopen", side_effect=fake_urlopen(rows, calls)):
            with self.assertRaises(AccessDenied):
                lookup_guest_from_sheet(service_account_json(), "sheet-id", "793360834",
                    {"first_initial": "J", "last_name": "Boudreaux"})

    def test_malformed_identity_raises_value_error(self):
        with self.assertRaises(ValueError):
            lookup_guest_from_sheet(service_account_json(), "sheet-id", "793360834",
                {"first_initial": "Jordan", "last_name": "Sample"})


class AuthorizeSubmissionFromSheetTests(unittest.TestCase):
    def rows(self, plus_one="Yes"):
        return [HEADER, ["J", "Boudreaux", plus_one, "James", "Boudreaux", "", "", "", "", "", "", "", "", ""]]

    def test_matching_name_is_authorized(self):
        calls = []
        payload = {"lookup": {"first_initial": "J", "last_name": "Boudreaux"}, "first_name": "James", "last_name": "Boudreaux"}
        with patch("backend.guest_sheet.urlopen", side_effect=fake_urlopen(self.rows(), calls)):
            result = authorize_submission_from_sheet(service_account_json(), "sheet-id", "793360834", payload)
        self.assertEqual(result, {"first_name": "James", "last_name": "Boudreaux"})

    def test_mismatched_name_denied(self):
        calls = []
        payload = {"lookup": {"first_initial": "J", "last_name": "Boudreaux"}, "first_name": "Someone", "last_name": "Else"}
        with patch("backend.guest_sheet.urlopen", side_effect=fake_urlopen(self.rows(), calls)):
            with self.assertRaises(AccessDenied):
                authorize_submission_from_sheet(service_account_json(), "sheet-id", "793360834", payload)

    def test_plus_one_rejected_when_not_allowed(self):
        calls = []
        payload = {"lookup": {"first_initial": "J", "last_name": "Boudreaux"}, "first_name": "James",
                   "last_name": "Boudreaux", "guest_name_unknown": True}
        with patch("backend.guest_sheet.urlopen", side_effect=fake_urlopen(self.rows(plus_one="No"), calls)):
            with self.assertRaises(AccessDenied):
                authorize_submission_from_sheet(service_account_json(), "sheet-id", "793360834", payload)

    def test_missing_lookup_denied(self):
        with self.assertRaises(AccessDenied):
            authorize_submission_from_sheet(service_account_json(), "sheet-id", "793360834", {"first_name": "James"})


if __name__ == "__main__":
    unittest.main()
