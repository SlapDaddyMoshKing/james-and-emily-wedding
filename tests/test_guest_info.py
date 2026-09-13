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
from backend.guest_info import InvalidSubmission, validate_submission
from backend.server import RateLimit, create_app
from scripts.export_guest_info import export_contacts
from scripts.guest_list import import_guests


def sample():
    return {"submission_id": str(uuid4()), "name_line_one": "Emma Example",
        "lookup": {"first_initial": "E", "last_name": "Example"}, "guest_id": "G1", "address_line1": "123 Example Lane", "city": "Chicago",
        "region": "IL", "postal_code": "60601"}


class MemoryS3:
    def __init__(self):
        self.records = {}
        self.writes = 0
        self.fail = False

    def put_object(self, **kwargs):
        if self.fail:
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
        assert kwargs["ServerSideEncryption"] == "AES256"
        assert kwargs["IfNoneMatch"] == "*"
        if kwargs["Key"] in self.records:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.records[kwargs["Key"]] = kwargs["Body"]
        self.writes += 1

    def get_object(self, **kwargs):
        return {"Body": io.BytesIO(self.records[kwargs["Key"]])}

    def get_paginator(self, operation):
        return self

    def paginate(self, **kwargs):
        # Separate pages exercise pagination, not just the first S3 page.
        return [{"Contents": [{"Key": key}]} for key in self.records]


class ContactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {"GUEST_DATA_BUCKET": "test-bucket", "AWS_DEFAULT_REGION": "us-east-2"}), patch("boto3.client"):
            cls.hosted = importlib.import_module("backend.lambda_handler")

    def setUp(self):
        self.s3 = MemoryS3()
        self.hosted._limiter = RateLimit(limit=100)
        self.s3_patch = patch.object(self.hosted, "_s3", self.s3)
        self.s3_patch.start()
        self.addCleanup(self.s3_patch.stop)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        database = Path(self.folder.name) / "guests.sqlite3"
        import_guests([("G1", "H1", "Emma", "Example", None, 1)], database)
        self.database = database
        self.db_patch = patch.object(self.hosted, "DATABASE_PATH", database)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.sync = patch.object(self.hosted, "_sync_database")
        self.tracker = patch.object(self.hosted, "save_to_tracker")
        self.tracker_mock = self.tracker.start()
        self.addCleanup(self.tracker.stop)
        self.sync.start()
        self.addCleanup(self.sync.stop)

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
        self.assertEqual(self.s3.writes, 1)
        self.assertEqual(self.request({**data, "city": "Changed"})[0], 409)
        self.assertEqual(json.loads(self.s3.records[key])["name_line_one"], "Emma Example")

    def test_failed_storage_does_not_report_success(self):
        self.s3.fail = True
        status, response = self.request(sample())
        self.assertEqual(status, 503)
        self.assertNotIn("saved", response)
        self.assertFalse(self.s3.records)

    def test_workbook_failure_can_retry_after_json_was_saved(self):
        data = sample()
        self.tracker_mock.side_effect = ValueError("Tracker busy")
        self.assertEqual(self.request(data)[0], 503)
        self.assertEqual(self.s3.writes, 1)
        self.tracker_mock.side_effect = None
        self.assertEqual(self.request(data)[0], 200)
        self.assertEqual(self.s3.writes, 1)
        self.assertEqual(self.tracker_mock.call_count, 2)

    def test_validation_rejects_bad_fields_without_writes(self):
        for changes in [{"postal_code": ""}, {"region": ""},
            {"phone": []}, {"submission_id": "../example"}, {"website": "spam"},
            {"inner_envelope": "x" * 201}, {"unknown": "extra"}]:
            with self.subTest(changes=changes):
                self.assertEqual(self.request({**sample(), **changes})[0], 400)
        self.assertFalse(self.s3.records)

    def test_international_postal_code(self):
        data = {**sample(), "postal_code": "SW1A 1AA"}
        self.assertEqual(self.request(data)[0], 200)

    def test_submission_cannot_bypass_lookup_or_add_a_guest(self):
        for payload in [ {key: value for key, value in sample().items() if key != "lookup"},
            {**sample(), "guest_id": "someone-else"}, {**sample(), "name_line_one": "Unapproved Guest"},
            {**sample(), "lookup": {"first_initial": "U", "last_name": "Unknown"}} ]:
            self.assertEqual(self.request(payload)[0], 403)
        self.assertEqual(self.s3.writes, 0)
        self.tracker_mock.assert_not_called()
        import_guests([("G1", "H1", "Emma", "Example", None, 0)], self.database)
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
        self.request({**sample(), "name_line_two": "=1+1", "phone": "+15555550100", "postal_code": "01234"})
        self.request(sample())
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "contacts.csv"
            self.assertEqual(export_contacts(self.s3, output), 2)
            with output.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["name_line_two"], "'=1+1")
            self.assertEqual(rows[0]["postal_code"], "01234")
            self.assertEqual(rows[0]["phone"], "'+15555550100")

    def test_privileged_test_records_are_excluded_from_export(self):
        data = sample()
        self.assertEqual(self.request({**data, "test_record": True})[0], 400)
        self.s3.records["guest-info/test.json"] = json.dumps({**data, "test_record": True}).encode()
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(export_contacts(self.s3, Path(directory) / "contacts.csv"), 0)

    def test_local_form_checks_invitation_and_no_public_records(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            app = create_app(self.database, submissions_dir=folder / "contacts")
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
