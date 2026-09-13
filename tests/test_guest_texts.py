from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch

import rsa
from botocore.exceptions import ClientError

from backend.guest_texts import _to_e164, _tracking_key, mark_visited, send_invitation_texts

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
          "Address Line One", "Address Line Two", "City", "State ", "Zip Code", "Send Text?"]

TWILIO_CREDENTIALS = {"account_sid": "ACfake", "auth_token": "tokenfake", "from_number": "+15550001111"}


class FakeS3:
    def __init__(self):
        self.records = {}

    def get_object(self, **kwargs):
        if kwargs["Key"] not in self.records:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": _Body(self.records[kwargs["Key"]])}

    def put_object(self, **kwargs):
        self.records[kwargs["Key"]] = kwargs["Body"]


class _Body:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


class FakeHttpResponse:
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


def fake_urlopen(sheet_rows, twilio_responses, calls):
    """twilio_responses: list of (status, body) consumed in order, one per
    outgoing SMS; a status >= 400 is raised as an HTTPError."""
    twilio_iter = iter(twilio_responses)

    def handler(request, timeout=None):
        url = request.full_url
        calls.append((request.get_method(), url, request.data))
        if url == "https://oauth2.googleapis.com/token":
            return FakeHttpResponse(200, {"access_token": "fake-token", "expires_in": 3600, "token_type": "Bearer"})
        if url.endswith("?fields=sheets.properties"):
            return FakeHttpResponse(200, {"sheets": [{"properties": {"sheetId": 793360834, "title": "Sheet1"}}]})
        if "/values/Sheet1" in url and request.get_method() == "GET":
            return FakeHttpResponse(200, {"values": sheet_rows})
        if "api.twilio.com" in url:
            status, body = next(twilio_iter)
            if status >= 400:
                _raise_twilio_error(url, status, body)
            return FakeHttpResponse(status, body)
        raise AssertionError(f"Unexpected request: {request.get_method()} {url}")
    return handler


def _raise_twilio_error(url, status, body):
    from urllib.error import HTTPError
    import io
    raise HTTPError(url, status, "error", {}, io.BytesIO(json.dumps(body).encode()))


@contextmanager
def patched_urlopen(sheet_rows, twilio_responses, calls):
    """_access_token/_sheet_title/_api_request are imported from
    backend.guest_sheet and call *that* module's urlopen; only the actual
    Twilio call in backend.guest_texts uses this module's own. Both need
    patching to intercept a full send_invitation_texts run."""
    handler = fake_urlopen(sheet_rows, twilio_responses, calls)
    with patch("backend.guest_sheet.urlopen", side_effect=handler), \
            patch("backend.guest_texts.urlopen", side_effect=handler):
        yield


class ToE164Tests(unittest.TestCase):
    def test_formats_and_rejects(self):
        self.assertEqual(_to_e164("918-397-1026"), "+19183971026")
        self.assertEqual(_to_e164("(918) 397-1026"), "+19183971026")
        self.assertEqual(_to_e164("1-918-397-1026"), "+19183971026")
        self.assertIsNone(_to_e164("12345"))
        self.assertIsNone(_to_e164(""))
        self.assertIsNone(_to_e164(None))


class TrackingKeyTests(unittest.TestCase):
    def test_case_and_whitespace_insensitive_but_distinct_identities(self):
        self.assertEqual(_tracking_key(" J ", "Boudreaux"), _tracking_key("j", " BOUDREAUX "))
        self.assertNotEqual(_tracking_key("J", "Boudreaux"), _tracking_key("E", "Wright"))


class MarkVisitedTests(unittest.TestCase):
    def test_sets_once_and_does_not_overwrite(self):
        s3 = FakeS3()
        mark_visited(s3, "bucket", "J", "Boudreaux")
        key = f"sms/{_tracking_key('J', 'Boudreaux')}.json"
        first = json.loads(s3.records[key])
        self.assertIn("visited_at", first)
        mark_visited(s3, "bucket", "J", "Boudreaux")
        second = json.loads(s3.records[key])
        self.assertEqual(first["visited_at"], second["visited_at"])

    def test_ignores_blank_identity(self):
        s3 = FakeS3()
        mark_visited(s3, "bucket", "", "")
        self.assertEqual(s3.records, {})


class SendInvitationTextsTests(unittest.TestCase):
    def row(self, first="J", last="Boudreaux", plus_one="Yes", phone="918-397-1026", send_text="Yes"):
        return [first, last, plus_one, "", "", "", "", phone, "", "", "", "", "", "", send_text]

    def test_sends_to_a_new_guest_and_records_it(self):
        rows = [HEADER, self.row()]
        s3 = FakeS3()
        calls = []
        with patched_urlopen(rows, [(201, {"sid": "SM123"})], calls):
            summary = send_invitation_texts(service_account_json(), "sheet-id", "793360834", TWILIO_CREDENTIALS, s3, "bucket")
        self.assertEqual(summary, {"sent": 1, "skipped": 0, "errors": 0})
        twilio_call = next(body for method, url, body in calls if "api.twilio.com" in url)
        self.assertIn("To=%2B19183971026", twilio_call.decode())
        key = f"sms/{_tracking_key('J', 'Boudreaux')}.json"
        self.assertIn("last_texted_at", json.loads(s3.records[key]))

    def test_skips_rows_not_opted_in_or_missing_a_phone(self):
        rows = [HEADER, self.row(send_text="No"), self.row(first="A", last="Other", phone="")]
        s3 = FakeS3()
        calls = []
        with patched_urlopen(rows, [], calls):
            summary = send_invitation_texts(service_account_json(), "sheet-id", "793360834", TWILIO_CREDENTIALS, s3, "bucket")
        self.assertEqual(summary["sent"], 0)
        self.assertFalse(any("api.twilio.com" in url for _, url, _ in calls))

    def test_skips_a_guest_who_already_visited(self):
        rows = [HEADER, self.row()]
        s3 = FakeS3()
        mark_visited(s3, "bucket", "J", "Boudreaux")
        calls = []
        with patched_urlopen(rows, [], calls):
            summary = send_invitation_texts(service_account_json(), "sheet-id", "793360834", TWILIO_CREDENTIALS, s3, "bucket")
        self.assertEqual(summary, {"sent": 0, "skipped": 1, "errors": 0})
        self.assertFalse(any("api.twilio.com" in url for _, url, _ in calls))

    def test_respects_the_21_day_cooldown_then_resends(self):
        rows = [HEADER, self.row()]
        s3 = FakeS3()
        key = f"sms/{_tracking_key('J', 'Boudreaux')}.json"
        recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        s3.put_object(Key=key, Body=json.dumps({"last_texted_at": recent}).encode())
        calls = []
        with patched_urlopen(rows, [], calls):
            summary = send_invitation_texts(service_account_json(), "sheet-id", "793360834", TWILIO_CREDENTIALS, s3, "bucket")
        self.assertEqual(summary, {"sent": 0, "skipped": 1, "errors": 0})

        stale = (datetime.now(timezone.utc) - timedelta(days=22)).isoformat()
        s3.put_object(Key=key, Body=json.dumps({"last_texted_at": stale}).encode())
        calls = []
        with patched_urlopen(rows, [(201, {"sid": "SM124"})], calls):
            summary = send_invitation_texts(service_account_json(), "sheet-id", "793360834", TWILIO_CREDENTIALS, s3, "bucket")
        self.assertEqual(summary, {"sent": 1, "skipped": 0, "errors": 0})

    def test_unsubscribed_recipient_is_recorded_and_never_retried(self):
        rows = [HEADER, self.row()]
        s3 = FakeS3()
        calls = []
        with patched_urlopen(rows, [(400, {"code": 21610, "message": "unsubscribed"})], calls):
            summary = send_invitation_texts(service_account_json(), "sheet-id", "793360834", TWILIO_CREDENTIALS, s3, "bucket")
        self.assertEqual(summary, {"sent": 0, "skipped": 0, "errors": 1})
        key = f"sms/{_tracking_key('J', 'Boudreaux')}.json"
        self.assertTrue(json.loads(s3.records[key])["opted_out"])
        # A second run must not attempt to text them again.
        calls = []
        with patched_urlopen(rows, [], calls):
            summary = send_invitation_texts(service_account_json(), "sheet-id", "793360834", TWILIO_CREDENTIALS, s3, "bucket")
        self.assertEqual(summary, {"sent": 0, "skipped": 1, "errors": 0})

    def test_one_bad_row_does_not_stop_the_rest(self):
        rows = [HEADER, self.row(first="A", last="Bad", phone="123"),
                self.row(first="J", last="Boudreaux", phone="918-397-1026")]
        s3 = FakeS3()
        calls = []
        with patched_urlopen(rows, [(201, {"sid": "SM125"})], calls):
            summary = send_invitation_texts(service_account_json(), "sheet-id", "793360834", TWILIO_CREDENTIALS, s3, "bucket")
        self.assertEqual(summary, {"sent": 1, "skipped": 1, "errors": 0})


if __name__ == "__main__":
    unittest.main()
