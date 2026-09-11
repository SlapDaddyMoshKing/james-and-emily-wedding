import io
import json
from pathlib import Path
import tempfile
import unittest

from backend.server import RateLimit, create_app
from scripts.guest_list import import_guests


class InvitationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.database = Path(self.folder.name) / "guests.sqlite3"
        import_guests([
            ("G001", "H001", "Renée", "O’Example", "private@example.com", 1),
            ("G002", "H002", "Unapproved", "Guest", None, 0),
        ], self.database)
        self.app = create_app(self.database)

    def request(self, body=None, method="POST", path="/api/invitations/lookup", app=None):
        data = json.dumps(body).encode() if body is not None else b"{"
        environment = {"PATH_INFO": path, "REQUEST_METHOD": method,
            "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(data)),
            "REMOTE_ADDR": "127.0.0.1", "wsgi.input": io.BytesIO(data)}
        result = {}
        def start_response(status, headers):
            result.update(status=int(status[:3]), headers=dict(headers))
        result["body"] = b"".join((app or self.app)(environment, start_response))
        return result

    def test_exact_normalized_match_without_private_data_or_session(self):
        result = self.request({"first_name": "  RENÉE ", "last_name": "o'example"})
        self.assertEqual(result["status"], 200)
        self.assertEqual(json.loads(result["body"]), {"invited": True})
        self.assertNotIn("Set-Cookie", result["headers"])
        self.assertEqual(result["headers"]["Cache-Control"], "no-store")

    def test_unapproved_unknown_and_partial_names_do_not_match(self):
        for first, last in [("Unapproved", "Guest"), ("Unknown", "Guest"), ("Ren", "O’Example"), ("' OR 1=1 --", "Guest")]:
            with self.subTest(first=first):
                result = self.request({"first_name": first, "last_name": last})
                self.assertEqual(json.loads(result["body"]), {"invited": False})

    def test_revocation_takes_effect_without_restart(self):
        import_guests([("G002", "H002", "Unapproved", "Guest", None, 0)], self.database)
        self.assertEqual(json.loads(self.request({"first_name": "Renée", "last_name": "O’Example"})["body"]), {"invited": False})

    def test_bad_input_and_missing_database_fail_closed(self):
        for body in [None, [], {}, {"first_name": [], "last_name": "Guest"}, {"first_name": " ", "last_name": "Guest"}]:
            self.assertEqual(self.request(body)["status"], 400)
        missing = create_app(Path(self.folder.name) / "missing.sqlite3")
        self.assertEqual(self.request({"first_name": "Some", "last_name": "Guest"}, app=missing)["status"], 503)

    def test_rate_limit_and_expiry(self):
        now = [0]
        limiter = RateLimit(limit=2, window=600, clock=lambda: now[0])
        app = create_app(self.database, limiter)
        body = {"first_name": "Some", "last_name": "Guest"}
        self.assertEqual(self.request(body, app=app)["status"], 200)
        self.assertEqual(self.request(body, app=app)["status"], 200)
        result = self.request(body, app=app)
        self.assertEqual(result["status"], 429)
        self.assertEqual(result["headers"]["Retry-After"], "600")
        now[0] = 601
        self.assertEqual(self.request(body, app=app)["status"], 200)

    def test_private_files_and_get_lookup_not_served(self):
        for path in ["/guests.sqlite3", "/guest-list.csv", "/preview/index.html", "/wedding-content.json", "/../README.md", "/scripts/guest_list.py"]:
            self.assertEqual(self.request(method="GET", path=path)["status"], 404)
        self.assertEqual(self.request(method="GET")["status"], 405)


if __name__ == "__main__":
    unittest.main()
