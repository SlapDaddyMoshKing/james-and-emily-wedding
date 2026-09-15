from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import smtplib
import unittest
from unittest.mock import patch

import rsa
from botocore.exceptions import ClientError

from backend.guest_texts import _carrier_domain, _phone_digits, _tracking_key, mark_visited, send_invitation_texts

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
          "Address Line One", "Address Line Two", "City", "State ", "Zip Code", "Send Text?", "Carrier"]

GMAIL_CREDENTIALS = {"email": "wedding@example.com", "app_password": "app-password"}


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


def fake_urlopen(sheet_rows, calls):
    def handler(request, timeout=None):
        url = request.full_url
        calls.append((request.get_method(), url, request.data))
        if url == "https://oauth2.googleapis.com/token":
            return FakeHttpResponse(200, {"access_token": "fake-token", "expires_in": 3600, "token_type": "Bearer"})
        if url.endswith("?fields=sheets.properties"):
            return FakeHttpResponse(200, {"sheets": [{"properties": {"sheetId": 793360834, "title": "Sheet1"}}]})
        if "/values/Sheet1" in url and request.get_method() == "GET":
            return FakeHttpResponse(200, {"values": sheet_rows})
        raise AssertionError(f"Unexpected request: {request.get_method()} {url}")
    return handler


class FakeSMTP:
    """Stands in for smtplib.SMTP_SSL. fail_recipients: a set of "To"
    addresses whose sendmail() call should raise, to test that one bad
    recipient doesn't stop the rest of a batch."""
    def __init__(self, fail_recipients=()):
        self.login_calls = []
        self.sent = []
        self.fail_recipients = set(fail_recipients)
        self.closed = False

    def __call__(self, host, port, timeout=None):
        self._host, self._port = host, port
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.closed = True
        return False

    def login(self, email, password):
        self.login_calls.append((email, password))

    def sendmail(self, from_addr, to_addrs, message):
        if any(address in self.fail_recipients for address in to_addrs):
            raise smtplib.SMTPRecipientsRefused({to_addrs[0]: (550, b"rejected")})
        self.sent.append((from_addr, to_addrs, message))


@contextmanager
def patched_backends(sheet_rows, calls, smtp):
    """_access_token/_sheet_title/_api_request are imported from
    backend.guest_sheet and call *that* module's urlopen."""
    handler = fake_urlopen(sheet_rows, calls)
    with patch("backend.guest_sheet.urlopen", side_effect=handler), \
            patch("backend.guest_texts.smtplib.SMTP_SSL", side_effect=smtp):
        yield


class CarrierDomainTests(unittest.TestCase):
    def test_known_carriers_case_and_whitespace_insensitive(self):
        self.assertEqual(_carrier_domain(" Verizon "), "vtext.com")
        self.assertEqual(_carrier_domain("AT&T"), "txt.att.net")
        self.assertEqual(_carrier_domain("t-mobile"), "tmomail.net")

    def test_unknown_carrier_returns_none(self):
        self.assertIsNone(_carrier_domain("Some Regional Carrier"))
        self.assertIsNone(_carrier_domain(""))


class PhoneDigitsTests(unittest.TestCase):
    def test_formats_and_rejects(self):
        self.assertEqual(_phone_digits("918-397-1026"), "9183971026")
        self.assertEqual(_phone_digits("(918) 397-1026"), "9183971026")
        self.assertEqual(_phone_digits("1-918-397-1026"), "9183971026")
        self.assertIsNone(_phone_digits("12345"))
        self.assertIsNone(_phone_digits(""))
        self.assertIsNone(_phone_digits(None))


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
    def row(self, first="J", last="Boudreaux", plus_one="Yes", phone="918-397-1026", send_text="Yes", carrier="Verizon"):
        return [first, last, plus_one, "", "", "", "", phone, "", "", "", "", "", "", send_text, carrier]

    def test_sends_to_a_new_guest_and_records_it(self):
        rows = [HEADER, self.row()]
        s3 = FakeS3()
        calls = []
        smtp = FakeSMTP()
        with patched_backends(rows, calls, smtp):
            summary = send_invitation_texts(service_account_json(), "sheet-id", "793360834", GMAIL_CREDENTIALS, s3, "bucket")
        self.assertEqual(summary, {"sent": 1, "skipped": 0, "errors": 0})
        self.assertEqual(smtp.login_calls, [("wedding@example.com", "app-password")])
        from_addr, to_addrs, message = smtp.sent[0]
        self.assertEqual(to_addrs, ["9183971026@vtext.com"])
        self.assertIn("wedding site", message)
        key = f"sms/{_tracking_key('J', 'Boudreaux')}.json"
        self.assertIn("last_texted_at", json.loads(s3.records[key]))
        self.assertTrue(smtp.closed)

    def test_skips_rows_not_opted_in_missing_a_phone_or_unknown_carrier(self):
        rows = [HEADER, self.row(send_text="No"), self.row(first="A", last="Other", phone=""),
                self.row(first="B", last="Third", carrier="Some Regional Carrier")]
        s3 = FakeS3()
        calls = []
        smtp = FakeSMTP()
        with patched_backends(rows, calls, smtp):
            summary = send_invitation_texts(service_account_json(), "sheet-id", "793360834", GMAIL_CREDENTIALS, s3, "bucket")
        self.assertEqual(summary["sent"], 0)
        self.assertFalse(smtp.sent)

    def test_skips_a_guest_who_already_visited(self):
        rows = [HEADER, self.row()]
        s3 = FakeS3()
        mark_visited(s3, "bucket", "J", "Boudreaux")
        calls = []
        smtp = FakeSMTP()
        with patched_backends(rows, calls, smtp):
            summary = send_invitation_texts(service_account_json(), "sheet-id", "793360834", GMAIL_CREDENTIALS, s3, "bucket")
        self.assertEqual(summary, {"sent": 0, "skipped": 1, "errors": 0})
        self.assertFalse(smtp.sent)

    def test_respects_the_21_day_cooldown_then_resends(self):
        rows = [HEADER, self.row()]
        s3 = FakeS3()
        key = f"sms/{_tracking_key('J', 'Boudreaux')}.json"
        recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        s3.put_object(Key=key, Body=json.dumps({"last_texted_at": recent}).encode())
        with patched_backends(rows, [], FakeSMTP()):
            summary = send_invitation_texts(service_account_json(), "sheet-id", "793360834", GMAIL_CREDENTIALS, s3, "bucket")
        self.assertEqual(summary, {"sent": 0, "skipped": 1, "errors": 0})

        stale = (datetime.now(timezone.utc) - timedelta(days=22)).isoformat()
        s3.put_object(Key=key, Body=json.dumps({"last_texted_at": stale}).encode())
        with patched_backends(rows, [], FakeSMTP()):
            summary = send_invitation_texts(service_account_json(), "sheet-id", "793360834", GMAIL_CREDENTIALS, s3, "bucket")
        self.assertEqual(summary, {"sent": 1, "skipped": 0, "errors": 0})

    def test_one_bad_recipient_does_not_stop_the_rest(self):
        rows = [HEADER, self.row(first="A", last="Bad", phone="555-000-1111"),
                self.row(first="J", last="Boudreaux")]
        s3 = FakeS3()
        smtp = FakeSMTP(fail_recipients={"5550001111@vtext.com"})
        with patched_backends(rows, [], smtp):
            summary = send_invitation_texts(service_account_json(), "sheet-id", "793360834", GMAIL_CREDENTIALS, s3, "bucket")
        self.assertEqual(summary, {"sent": 1, "skipped": 0, "errors": 1})


if __name__ == "__main__":
    unittest.main()
