"""Local-only WSGI invitation lookup. Run: python -m backend.server

Serve using a production platform with HTTPS and distributed rate limiting
before connecting this API to the public website. Never expose this dev server.
"""

from collections import deque
from contextlib import closing
import json
import sqlite3
from threading import Lock
import time
import unicodedata
from wsgiref.simple_server import make_server

from scripts.guest_list import DATA_DIR, REPO_ROOT, private_path
from backend.guest_info import MAX_BODY_BYTES, InvalidSubmission, make_record, validate_submission
from backend.contact_access import AccessDenied, authorize_submission, lookup_party

PUBLIC_FILES = {"/": ("index.html", "text/html; charset=utf-8"),
                "/index.html": ("index.html", "text/html; charset=utf-8"),
                "/styles.css": ("styles.css", "text/css; charset=utf-8"),
                "/guest-info.js": ("guest-info.js", "text/javascript; charset=utf-8"),
                "/welcome.html": ("welcome.html", "text/html; charset=utf-8"),
                "/guest-lookup.js": ("guest-lookup.js", "text/javascript; charset=utf-8")}


def normalize_name(value):
    if not isinstance(value, str) or len(value) > 100:
        raise ValueError("Enter a first and last name of up to 100 characters each.")
    normalized = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    normalized = normalized.replace("’", "'").replace("‘", "'")
    if not normalized or any(unicodedata.category(char).startswith("C") for char in normalized):
        raise ValueError("Enter a valid first and last name.")
    return normalized


def is_invited(database, first_name, last_name):
    first, last = normalize_name(first_name), normalize_name(last_name)
    database = private_path(database)
    # mode=ro must fail when the guest list has not been imported, not create an empty list.
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.create_function("normalized_name", 1, normalize_name, deterministic=True)
        return connection.execute("""SELECT 1 FROM guests WHERE access_approved = 1
            AND normalized_name(first_name) = ? AND normalized_name(last_name) = ? LIMIT 1""",
            (first, last)).fetchone() is not None


class RateLimit:
    def __init__(self, limit=10, window=600, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self.attempts = {}
        self.lock = Lock()

    def allow(self, address):
        now = self.clock()
        with self.lock:
            for key in list(self.attempts):
                bucket = self.attempts[key]
                while bucket and bucket[0] <= now - self.window:
                    bucket.popleft()
                if not bucket:
                    del self.attempts[key]
            if address not in self.attempts and len(self.attempts) >= 10000:
                return False
            bucket = self.attempts.setdefault(address, deque())
            if len(bucket) >= self.limit:
                return False
            bucket.append(now)
            return True


def create_app(database=DATA_DIR / "guests.sqlite3", limiter=None, submissions_dir=None):
    database = private_path(database)
    limiter = limiter or RateLimit()
    submissions_dir = private_path(submissions_dir or DATA_DIR / "guest-info-local")
    submission_lock = Lock()

    def application(environ, start_response):
        def respond(status, value, content_type="application/json; charset=utf-8", extra=()):
            body = value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
            start_response(status, [("Content-Type", content_type), ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                ("Referrer-Policy", "no-referrer"), *extra])
            return [body]

        path = environ.get("PATH_INFO", "/")
        method = environ.get("REQUEST_METHOD", "GET")
        if path == "/api/contact-party":
            if method != "POST":
                return respond("405 Method Not Allowed", {"error": "Use POST."})
            if not limiter.allow(environ.get("REMOTE_ADDR", "unknown")):
                return respond("429 Too Many Requests", {"error": "Please try again later."})
            if environ.get("CONTENT_TYPE", "").split(";")[0].strip().lower() != "application/json":
                return respond("415 Unsupported Media Type", {"error": "Use JSON."})
            try:
                length = int(environ.get("CONTENT_LENGTH", "0") or "0")
                if not 1 <= length <= MAX_BODY_BYTES:
                    return respond("413 Content Too Large", {"error": "Invalid request size."})
                return respond("200 OK", lookup_party(database, json.loads(environ["wsgi.input"].read(length))))
            except AccessDenied as error:
                return respond("403 Forbidden", {"error": str(error)})
            except (ValueError, UnicodeError):
                return respond("400 Bad Request", {"error": "Enter a valid first initial and last name."})
            except (OSError, sqlite3.Error):
                return respond("503 Service Unavailable", {"error": "The invitation list is temporarily unavailable."})
        if path == "/api/guest-info":
            if method != "POST":
                return respond("405 Method Not Allowed", {"error": "Use POST."}, extra=[("Allow", "POST")])
            if not limiter.allow(environ.get("REMOTE_ADDR", "unknown")):
                return respond("429 Too Many Requests", {"error": "Please try again later."}, extra=[("Retry-After", "600")])
            if environ.get("CONTENT_TYPE", "").split(";")[0].strip().lower() != "application/json":
                return respond("415 Unsupported Media Type", {"error": "Use JSON."})
            try:
                length = int(environ.get("CONTENT_LENGTH", "0") or "0")
                if not 1 <= length <= MAX_BODY_BYTES:
                    return respond("413 Content Too Large", {"error": "Invalid request size."})
                data = validate_submission(authorize_submission(database, json.loads(environ["wsgi.input"].read(length))))
                with submission_lock:
                    submissions_dir.mkdir(parents=True, exist_ok=True)
                    target = submissions_dir / (data["submission_id"] + ".json")
                    if target.exists():
                        previous = json.loads(target.read_text(encoding="utf-8"))
                        if any(previous.get(field) != value for field, value in data.items()):
                            return respond("409 Conflict", {"error": "Please submit again with a new reference."})
                    else:
                        temporary = target.with_suffix(".tmp")
                        temporary.write_text(json.dumps(make_record(data), ensure_ascii=False), encoding="utf-8")
                        temporary.replace(target)
            except AccessDenied as error:
                return respond("403 Forbidden", {"error": str(error)})
            except InvalidSubmission as error:
                return respond("400 Bad Request", {"error": str(error), "field": error.field})
            except (ValueError, UnicodeError):
                return respond("400 Bad Request", {"error": "Enter a valid request."})
            except (OSError, sqlite3.Error):
                return respond("503 Service Unavailable", {"error": "We couldn't save your details. Please try again shortly."})
            return respond("200 OK", {"saved": True, "submission_id": data["submission_id"]})
        if path == "/api/invitations/lookup":
            if method != "POST":
                return respond("405 Method Not Allowed", {"error": "Use POST."}, extra=[("Allow", "POST")])
            if not limiter.allow(environ.get("REMOTE_ADDR", "unknown")):
                return respond("429 Too Many Requests", {"error": "Please try again later."}, extra=[("Retry-After", "600")])
            if environ.get("CONTENT_TYPE", "").split(";")[0].strip().lower() != "application/json":
                return respond("415 Unsupported Media Type", {"error": "Use JSON."})
            try:
                length = int(environ.get("CONTENT_LENGTH", "0") or "0")
                if length < 1 or length > 2048:
                    return respond("413 Content Too Large", {"error": "Invalid request size."})
                payload = json.loads(environ["wsgi.input"].read(length))
                if not isinstance(payload, dict) or set(payload) != {"first_name", "last_name"}:
                    raise ValueError("Enter a first and last name.")
                invited = is_invited(database, payload["first_name"], payload["last_name"])
            except (ValueError, UnicodeError):
                return respond("400 Bad Request", {"error": "Enter a valid first and last name."})
            except (sqlite3.Error, OSError):
                return respond("503 Service Unavailable", {"error": "Invitation lookup is temporarily unavailable."})
            # Deliberately no guest IDs, emails, sessions, household counts, or private content.
            return respond("200 OK", {"invited": invited})
        if method != "GET":
            return respond("405 Method Not Allowed", {"error": "Use GET."}, extra=[("Allow", "GET")])
        if path == "/site-config.json":
            return respond("200 OK", {"contactPartyUrl": "/api/contact-party", "guestInfoUrl": "/api/guest-info", "invitationLookupUrl": "/api/invitations/lookup"})
        if path in PUBLIC_FILES:
            filename, content_type = PUBLIC_FILES[path]
            return respond("200 OK", (REPO_ROOT / filename).read_bytes(), content_type)
        # No directory serving: never expose local guest data, source, or ceremony preview.
        return respond("404 Not Found", {"error": "Not found."})

    return application


if __name__ == "__main__":
    with make_server("127.0.0.1", 8080, create_app()) as server:
        print("Local invitation lookup: http://127.0.0.1:8080", flush=True)
        server.serve_forever()
