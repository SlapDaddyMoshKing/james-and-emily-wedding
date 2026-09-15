import base64
import csv
import importlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from botocore.exceptions import ClientError
from backend.contact_access import AccessDenied
from backend.guest_info import InvalidSubmission, validate_submission
from backend.server import RateLimit, create_app
from scripts.export_guest_info import export_contacts
from scripts.guest_list import import_guests


def sample():
    return {"submission_id": str(uuid4()), "first_name": "Emma", "last_name": "Example",
        "lookup": {"first_initial": "E", "last_name": "Example"},
        "address_line1": "123 Example Lane", "city": "Chicago", "region": "IL", "postal_code": "60601"}


class MemoryS3:
    def __init__(self):
        self.records = {}
        self.writes = 0
        self.fail = False

    def put_object(self, **kwargs):
        if self.fail:
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
        assert kwargs["ServerSideEncryption"] == "AES256"
        if kwargs.get("IfNoneMatch") == "*" and kwargs["Key"] in self.records:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.records[kwargs["Key"]] = kwargs["Body"]
        self.writes += 1

    def get_object(self, **kwargs):
        if kwargs["Key"] not in self.records:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.records[kwargs["Key"]])}

    def get_paginator(self, operation):
        return self

    def paginate(self, **kwargs):
        # Separate pages exercise pagination, not just the first S3 page.
        prefix = kwargs.get("Prefix", "")
        return [{"Contents": [{"Key": key}]} for key in self.records if key.startswith(prefix)]


class ContactTests(unittest.TestCase):
    """Exercises Lambda orchestration (S3 receipts, tracker writes, rate
    limiting, idempotency) with the Google Sheet lookup/authorization faked
    out. The Sheet-reading logic itself (name matching, presence-based
    approval, plus-one gating) is covered directly in tests/test_guest_sheet.py."""

    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {"GUEST_DATA_BUCKET": "test-bucket", "AWS_DEFAULT_REGION": "us-east-2"}), patch("boto3.client"):
            cls.hosted = importlib.import_module("backend.lambda_handler")

    def setUp(self):
        self.s3 = MemoryS3()
        self.s3.records["google-service-account.json"] = b'{"fake": "credentials"}'
        self.hosted._limiter = RateLimit(limit=100)
        self.s3_patch = patch.object(self.hosted, "_s3", self.s3)
        self.s3_patch.start()
        self.addCleanup(self.s3_patch.stop)
        for name, value in [("GOOGLE_SHEET_ID", "sheet-id"), ("GOOGLE_SHEET_GID", "123")]:
            sheet_patch = patch.object(self.hosted, name, value)
            sheet_patch.start()
            self.addCleanup(sheet_patch.stop)
        self.tracker = patch.object(self.hosted, "save_to_tracker")
        self.tracker_mock = self.tracker.start()
        self.addCleanup(self.tracker.stop)
        self.sheet_sync = patch.object(self.hosted, "sync_submission")
        self.sheet_sync_mock = self.sheet_sync.start()
        self.addCleanup(self.sheet_sync.stop)

        self.approved = True
        self.plus_one_allowed = True

        def fake_lookup(service_account_key, sheet_id, sheet_gid, identity):
            matches = (identity.get("first_initial", "").strip().lower() == "e"
                and identity.get("last_name", "").strip().lower() == "example")
            if not self.approved or not matches:
                raise AccessDenied("We couldn't find that name on our invitation list. Check the spelling or contact Emily or James.")
            return {"first_name": "Emma", "last_name": "Example", "plus_one_allowed": self.plus_one_allowed,
                "prefill": {"phone": "", "address_line1": "", "address_line2": "", "city": "", "region": "",
                    "postal_code": "", "plus_one_first_name": "", "plus_one_last_name": "", "guest_name_unknown": False}}

        def fake_authorize(service_account_key, sheet_id, sheet_gid, payload):
            if not isinstance(payload, dict) or "lookup" not in payload:
                raise AccessDenied("Please check your name before sending your details.")
            guest = fake_lookup(service_account_key, sheet_id, sheet_gid, payload["lookup"])
            if payload.get("first_name") != guest["first_name"] or payload.get("last_name") != guest["last_name"]:
                raise AccessDenied("Please use the guest name from your invitation.")
            if not guest["plus_one_allowed"]:
                plus_one_fields = ("plus_one_title", "plus_one_first_name", "plus_one_last_name", "plus_one_suffix")
                if payload.get("guest_name_unknown") or any(payload.get(field) for field in plus_one_fields):
                    raise AccessDenied("Your invitation does not include a plus-one.")
            return {key: value for key, value in payload.items() if key != "lookup"}

        self.lookup_patch = patch.object(self.hosted, "lookup_guest_from_sheet", side_effect=fake_lookup)
        self.lookup_patch.start()
        self.addCleanup(self.lookup_patch.stop)
        self.authorize_patch = patch.object(self.hosted, "authorize_submission_from_sheet", side_effect=fake_authorize)
        self.authorize_patch.start()
        self.addCleanup(self.authorize_patch.stop)

    def request(self, payload, **overrides):
        event = {"requestContext": {"http": {"method": "POST", "path": "/guest-info", "sourceIp": "192.0.2.1"}},
            "headers": {"content-type": "application/json"}, "body": json.dumps(payload), **overrides}
        response = self.hosted.handler(event, None)
        return response["statusCode"], json.loads(response["body"])

    def test_new_guest_saved_privately_and_retry_is_idempotent(self):
        data = sample()
        status, result = self.request(data)
        self.assertEqual(status, 200)
        self.assertEqual(result, {"saved": True, "submission_id": data["submission_id"]})
        key = f"guest-info/{data['submission_id']}.json"
        stored = json.loads(self.s3.records[key])
        self.assertEqual(stored["name_line_one"], "Emma Example")
        self.assertIn("submitted_at", stored)
        self.assertEqual(self.request(data)[0], 200)
        self.assertEqual(self.s3.writes, 2)  # the receipt, plus a one-time "visited" tracking record
        self.assertEqual(self.request({**data, "city": "Changed"})[0], 409)
        self.assertEqual(json.loads(self.s3.records[key])["name_line_one"], "Emma Example")

    def test_failed_storage_does_not_report_success(self):
        self.s3.fail = True
        status, response = self.request(sample())
        self.assertEqual(status, 503)
        self.assertNotIn("saved", response)
        self.assertEqual(self.s3.writes, 0)

    def test_workbook_failure_can_retry_after_json_was_saved(self):
        data = sample()
        self.tracker_mock.side_effect = ValueError("Tracker busy")
        self.assertEqual(self.request(data)[0], 503)
        self.assertEqual(self.s3.writes, 2)  # the receipt, plus a one-time "visited" tracking record
        self.tracker_mock.side_effect = None
        self.assertEqual(self.request(data)[0], 200)
        self.assertEqual(self.s3.writes, 2)  # unchanged: retry doesn't re-save the receipt or the tracking record
        self.assertEqual(self.tracker_mock.call_count, 2)

    def test_validation_rejects_bad_fields_without_writes(self):
        for changes in [{"postal_code": ""}, {"region": ""},
            {"phone": []}, {"submission_id": "../example"}, {"website": "spam"},
            {"suffix": "x" * 41}, {"unknown": "extra"}, {"guest_name_unknown": "yes"},
            {"plus_one_first_name": "Alex"}, {"sms_consent": "yes"}]:
            with self.subTest(changes=changes):
                self.assertEqual(self.request({**sample(), **changes})[0], 400)
        self.assertEqual(self.s3.writes, 0)

    def test_sms_consent_is_optional_unchecked_by_default_and_recorded(self):
        data = sample()
        self.assertEqual(self.request(data)[0], 200)
        stored = json.loads(self.s3.records[f"guest-info/{data['submission_id']}.json"])
        self.assertIs(stored["sms_consent"], False)  # not sent: defaults to opted out
        data = {**sample(), "sms_consent": True}
        self.assertEqual(self.request(data)[0], 200)
        stored = json.loads(self.s3.records[f"guest-info/{data['submission_id']}.json"])
        self.assertIs(stored["sms_consent"], True)

    def test_international_postal_code(self):
        data = {**sample(), "postal_code": "SW1A 1AA"}
        self.assertEqual(self.request(data)[0], 200)

    def test_submission_cannot_bypass_lookup_or_add_a_guest(self):
        for payload in [ {key: value for key, value in sample().items() if key != "lookup"},
            {**sample(), "first_name": "Someone"}, {**sample(), "last_name": "Else"},
            {**sample(), "lookup": {"first_initial": "U", "last_name": "Unknown"}} ]:
            self.assertEqual(self.request(payload)[0], 403)
        self.assertEqual(self.s3.writes, 0)
        self.tracker_mock.assert_not_called()
        self.approved = False
        self.assertEqual(self.request(sample())[0], 403)

    def test_protocol_size_base64_and_rate_limit(self):
        self.assertEqual(self.request(sample(), headers={})[0], 415)
        self.assertEqual(self.request(None, body="{")[0], 400)
        self.assertEqual(self.request(None, body="x" * 20000)[0], 413)
        self.assertEqual(self.request(None, isBase64Encoded=True, body="!!!!")[0], 400)
        encoded = base64.b64encode(json.dumps(sample()).encode()).decode()
        self.assertEqual(self.request(None, isBase64Encoded=True, body=encoded)[0], 200)
        self.hosted._limiter = RateLimit(limit=1)
        self.assertEqual(self.request(sample())[0], 200)
        self.assertEqual(self.request(sample())[0], 429)
        event = {"requestContext": {"http": {"method": "GET", "path": "/guest-info"}}}
        self.assertEqual(self.hosted.handler(event, None)["statusCode"], 405)

    def test_export_all_pages_preserves_postal_codes_and_neutralizes_formulas(self):
        self.request({**sample(), "plus_one_first_name": "=1+1", "plus_one_last_name": "Guest", "phone": "+15555550100", "postal_code": "01234"})
        self.request(sample())
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "contacts.csv"
            self.assertEqual(export_contacts(self.s3, output), 2)
            with output.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["plus_one_first_name"], "'=1+1")
            self.assertEqual(rows[0]["postal_code"], "01234")
            self.assertEqual(rows[0]["phone"], "'+15555550100")

    def test_google_sheet_required_but_sync_failure_never_blocks_a_submission(self):
        with patch.object(self.hosted, "GOOGLE_SHEET_ID", None):
            self.assertEqual(self.request(sample())[0], 503)
        self.sheet_sync_mock.side_effect = ValueError("boom")
        self.assertEqual(self.request(sample())[0], 200)
        self.sheet_sync_mock.assert_called_once()

    def test_visit_is_recorded_on_lookup_and_submission(self):
        event = {"requestContext": {"http": {"method": "POST", "path": "/contact-party", "sourceIp": "192.0.2.1"}},
            "headers": {"content-type": "application/json"},
            "body": json.dumps({"first_initial": "E", "last_name": "Example"})}
        response = self.hosted.handler(event, None)
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(len(self.s3.records), 2)  # credentials fixture, plus the new visit record

    def test_scheduled_task_dispatches_without_needing_an_http_event(self):
        with patch.object(self.hosted, "GOOGLE_SHEET_ID", None):
            result = self.hosted.handler({"task": "send-invitation-texts"}, None)
        self.assertEqual(result, {"skipped": True})
        self.s3.records["gmail-credentials.json"] = b'{"email": "wedding@example.com", "app_password": "x"}'
        with patch.object(self.hosted, "send_invitation_texts") as texts_mock:
            texts_mock.return_value = {"sent": 1, "skipped": 0, "errors": 0}
            result = self.hosted.handler({"task": "send-invitation-texts"}, None)
        self.assertEqual(result, {"sent": 1, "skipped": 0, "errors": 0})
        texts_mock.assert_called_once()

    def test_plus_one_named_unknown_and_not_allowed(self):
        data = sample()
        status, result = self.request({**data, "plus_one_first_name": "Alex", "plus_one_last_name": "Partner"})
        self.assertEqual(status, 200)
        stored = json.loads(self.s3.records[f"guest-info/{data['submission_id']}.json"])
        self.assertEqual(stored["name_line_two"], "and Alex Partner")
        data = sample()
        status, result = self.request({**data, "guest_name_unknown": True})
        self.assertEqual(status, 200)
        stored = json.loads(self.s3.records[f"guest-info/{data['submission_id']}.json"])
        self.assertEqual(stored["name_line_two"], "and Guest")
        self.plus_one_allowed = False
        self.assertEqual(self.request({**sample(), "guest_name_unknown": True})[0], 403)

    def test_privileged_test_records_are_excluded_from_export(self):
        data = sample()
        self.assertEqual(self.request({**data, "test_record": True})[0], 400)
        self.s3.records["guest-info/test.json"] = json.dumps({**data, "test_record": True}).encode()
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(export_contacts(self.s3, Path(directory) / "contacts.csv"), 0)

    def test_local_form_checks_invitation_and_no_public_records(self):
        """The local dev server (backend.server) is unaffected by the hosted
        Lambda's Google Sheet switch -- it still checks a local guests.sqlite3."""
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            database = folder / "guests.sqlite3"
            import_guests([("G1", "H1", "Emma", "Example", None, 1, 1)], database)
            app = create_app(database, submissions_dir=folder / "contacts")
            data = sample()
            def request(path, method="POST", payload=data):
                body = json.dumps(payload).encode()
                environ = {"PATH_INFO": path, "REQUEST_METHOD": method, "CONTENT_TYPE": "application/json",
                    "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body), "REMOTE_ADDR": "127.0.0.1"}
                status = []
                response = b"".join(app(environ, lambda code, headers: status.append(code)))
                return int(status[0][:3]), response
            self.assertEqual(request("/api/guest-info")[0], 200)
            self.assertEqual(request("/api/guest-info")[0], 200)
            self.assertEqual(len(list((folder / "contacts").glob("*.json"))), 1)
            self.assertEqual(request("/api/guest-info", payload={**data, "city": "Changed"})[0], 409)
            self.assertEqual(request("/guest-info/" + data["submission_id"] + ".json", "GET")[0], 404)
            status, body = request("/site-config.json", "GET")
            self.assertEqual(json.loads(body)["guestInfoUrl"], "/api/guest-info")

if __name__ == "__main__":
    unittest.main()
