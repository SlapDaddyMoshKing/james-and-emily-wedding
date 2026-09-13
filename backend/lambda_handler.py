"""AWS Lambda entry point: contact collection and legacy invitation/RSVP APIs.

POST /contact-party and POST /guest-info are read live from the shared
Google Sheet on every call (see backend/guest_sheet.py) -- presence of a row
matching first initial and last name is what makes someone approved, so an
edit to the sheet takes effect on the very next request. No separate
publish step, and no local database, is involved for these two routes.
POST /guest-info rechecks that same row before saving, appends one row to
welcome/Guest Tracker.xlsx (the authoritative record), retains an encrypted
JSON receipt, and then best-effort mirrors what was submitted back onto
that guest's row in the sheet. See docs/guest-information.md.

/lookup, /party, and /rsvp are a separate, older, retained-for-later
feature still backed by guests.sqlite3 (published from a CSV -- see
docs/guest-list.md) and unrelated to the Google Sheet above:
- POST /lookup  {first_name, last_name} -> {"invited": true/false}
  Pure name check. Grants no session, serves no content.
- POST /party   {first_name, last_name} -> {"members": [...]}
  Re-validates the name, then returns everyone sharing that guest's
  household_id (i.e. their party/+1s) along with each person's current RSVP.
- POST /rsvp    {first_name, last_name, responses: [{guest_id, attending}]}
  Re-validates the name and that every guest_id belongs to that same
  household before writing anything.

None of this grants a session or a cookie -- every call rechecks the name
against the current guest list. That's consistent with the rest of this
project's deliberately name-only, low-ceremony trust model (see
docs/guest-list.md); the real gate is that these URLs aren't public.

RSVP responses are stored as one small object per guest
(rsvps/<guest_id>.json) rather than inside guests.sqlite3, specifically so
that re-importing the guest CSV (which replaces the whole guests table)
never wipes out RSVPs already collected.

guests.sqlite3 is re-downloaded from S3 on every /lookup, /party, or /rsvp
invocation (it's tiny), so a guest-list re-import -- which can revoke
access -- takes effect immediately, matching backend/server.py's
documented local behavior.

Invoking this function directly with {"task": "send-invitation-texts"}
(meant for a scheduled EventBridge trigger, not a guest request) runs
backend/guest_texts.py's SMS outreach instead of any HTTP route: it texts
a link to guests whose sheet row has "Send Text?" set to Yes and who
haven't visited the site yet, skipping anyone already texted in the last
21 days. See docs/guest-information.md.

Configure via environment variables on the Lambda function:
  GUEST_DATA_BUCKET   S3 bucket holding guests.sqlite3, rsvps/*, the
                       Google service account key, and the Twilio
                       credentials file
  GOOGLE_SHEET_ID      The shared sheet's ID, from its URL
  GOOGLE_SHEET_GID     The specific tab's gid, from its URL
"""

from contextlib import closing
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from urllib.error import URLError

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from backend.server import RateLimit, is_invited, normalize_name
from backend.guest_info import MAX_BODY_BYTES, InvalidSubmission, make_record, validate_submission
from backend.guest_tracker import save_to_tracker
from backend.guest_sheet import SheetSyncError, authorize_submission_from_sheet, lookup_guest_from_sheet, sync_submission
from backend.guest_texts import mark_visited, send_invitation_texts
from backend.contact_access import AccessDenied

BUCKET = os.environ["GUEST_DATA_BUCKET"]
DATABASE_PATH = Path("/tmp/wedding-site/guests.sqlite3")
MAX_PARTY_SIZE = 20
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_SHEET_GID = os.environ.get("GOOGLE_SHEET_GID")
GOOGLE_SERVICE_ACCOUNT_KEY = "google-service-account.json"
TWILIO_CREDENTIALS_KEY = "twilio-credentials.json"

_s3 = boto3.client("s3")
# Per-warm-container only (not shared across concurrent Lambdas); a coarse
# API Gateway throttle provides a global backstop alongside this.
_limiter = RateLimit()

_HEADERS = {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}


def _respond(status, body, extra_headers=None):
    return {"statusCode": status, "headers": {**_HEADERS, **(extra_headers or {})}, "body": json.dumps(body)}


def _sync_database():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _s3.download_file(BUCKET, "guests.sqlite3", str(DATABASE_PATH))


def _household_members(first_name, last_name):
    """None if the name doesn't match an approved guest; otherwise every
    approved guest sharing that person's household_id, self included."""
    first, last = normalize_name(first_name), normalize_name(last_name)
    with closing(sqlite3.connect(DATABASE_PATH.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.create_function("normalized_name", 1, normalize_name, deterministic=True)
        row = connection.execute("""SELECT household_id FROM guests WHERE access_approved = 1
            AND normalized_name(first_name) = ? AND normalized_name(last_name) = ? LIMIT 1""",
            (first, last)).fetchone()
        if row is None:
            return None
        return connection.execute("""SELECT guest_id, first_name, last_name FROM guests
            WHERE access_approved = 1 AND household_id = ? ORDER BY guest_id""", (row[0],)).fetchall()


def _rsvp_key(guest_id):
    return f"rsvps/{guest_id}.json"


def _read_rsvp(guest_id):
    try:
        response = _s3.get_object(Bucket=BUCKET, Key=_rsvp_key(guest_id))
        return json.loads(response["Body"].read())["attending"]
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return None
        raise


def _write_rsvp(guest_id, attending):
    body = json.dumps({"attending": attending, "updated_at": datetime.now(timezone.utc).isoformat()})
    _s3.put_object(Bucket=BUCKET, Key=_rsvp_key(guest_id), Body=body.encode("utf-8"),
                    ContentType="application/json; charset=utf-8", ServerSideEncryption="AES256")


def _members_payload(rows):
    return [{"guest_id": guest_id, "first_name": first, "last_name": last, "attending": _read_rsvp(guest_id)}
            for guest_id, first, last in rows]


def _lookup(payload):
    if not isinstance(payload, dict) or set(payload) != {"first_name", "last_name"}:
        return _respond(400, {"error": "Enter a valid first and last name."})
    try:
        invited = is_invited(DATABASE_PATH, payload["first_name"], payload["last_name"])
    except (ValueError, UnicodeError):
        return _respond(400, {"error": "Enter a valid first and last name."})
    except sqlite3.Error:
        return _respond(503, {"error": "Invitation lookup is temporarily unavailable."})
    return _respond(200, {"invited": invited})


def _party(payload):
    if not isinstance(payload, dict) or set(payload) != {"first_name", "last_name"}:
        return _respond(400, {"error": "Enter a valid first and last name."})
    try:
        rows = _household_members(payload["first_name"], payload["last_name"])
    except (ValueError, UnicodeError):
        return _respond(400, {"error": "Enter a valid first and last name."})
    except sqlite3.Error:
        return _respond(503, {"error": "Invitation lookup is temporarily unavailable."})
    if rows is None:
        return _respond(403, {"error": "We couldn't confirm your invitation. Please check the spelling and try again."})
    try:
        return _respond(200, {"members": _members_payload(rows)})
    except ClientError:
        return _respond(503, {"error": "RSVP is temporarily unavailable."})


def _rsvp(payload):
    if (not isinstance(payload, dict) or set(payload) != {"first_name", "last_name", "responses"}
            or not isinstance(payload.get("responses"), list) or not 1 <= len(payload["responses"]) <= MAX_PARTY_SIZE):
        return _respond(400, {"error": "Enter a valid RSVP."})
    for response in payload["responses"]:
        if not isinstance(response, dict) or set(response) != {"guest_id", "attending"} \
                or not isinstance(response["guest_id"], str) or not isinstance(response["attending"], bool):
            return _respond(400, {"error": "Enter a valid RSVP."})
    try:
        rows = _household_members(payload["first_name"], payload["last_name"])
    except (ValueError, UnicodeError):
        return _respond(400, {"error": "Enter a valid first and last name."})
    except sqlite3.Error:
        return _respond(503, {"error": "Invitation lookup is temporarily unavailable."})
    if rows is None:
        return _respond(403, {"error": "We couldn't confirm your invitation. Please check the spelling and try again."})
    household_ids = {guest_id for guest_id, _, _ in rows}
    for response in payload["responses"]:
        if response["guest_id"] not in household_ids:
            return _respond(403, {"error": "That guest is not part of your invitation."})
    try:
        for response in payload["responses"]:
            _write_rsvp(response["guest_id"], response["attending"])
        return _respond(200, {"members": _members_payload(rows)})
    except ClientError:
        return _respond(503, {"error": "We couldn't save your RSVP right now. Please try again shortly."})


def _google_sheet_configured():
    return bool(GOOGLE_SHEET_ID and GOOGLE_SHEET_GID)


def _fetch_service_account_key():
    return _s3.get_object(Bucket=BUCKET, Key=GOOGLE_SERVICE_ACCOUNT_KEY)["Body"].read()


def _guest_info(payload):
    """Recheck the current invitation -- live against the Google Sheet, not a
    separately-published database -- before accepting any contact information."""
    if not _google_sheet_configured():
        return _respond(503, {"error": "The invitation list is temporarily unavailable."})
    try:
        service_account_key = _fetch_service_account_key()
    except (ClientError, BotoCoreError, OSError):
        return _respond(503, {"error": "The invitation list is temporarily unavailable."})
    try:
        data = validate_submission(authorize_submission_from_sheet(
            service_account_key, GOOGLE_SHEET_ID, GOOGLE_SHEET_GID, payload))
    except AccessDenied as error:
        return _respond(403, {"error": str(error)})
    except InvalidSubmission as error:
        return _respond(400, {"error": str(error), "field": error.field})
    except ValueError as error:
        return _respond(400, {"error": str(error)})
    except (SheetSyncError, URLError, OSError):
        return _respond(503, {"error": "The invitation list is temporarily unavailable."})
    _mark_visited_best_effort(payload.get("lookup") if isinstance(payload, dict) else None)
    key = f"guest-info/{data['submission_id']}.json"
    record = make_record(data)
    try:
        try:
            _s3.put_object(Bucket=BUCKET, Key=key,
                Body=json.dumps(record, ensure_ascii=False).encode("utf-8"),
                ContentType="application/json; charset=utf-8", ServerSideEncryption="AES256",
                IfNoneMatch="*")
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "PreconditionFailed":
                raise
            # A lost response can be safely retried. A reused ID cannot replace data.
            previous = json.loads(_s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
            if any(previous.get(field) != value for field, value in data.items()):
                return _respond(409, {"error": "Please submit again with a new reference."})
        save_to_tracker(_s3, BUCKET, record)
    except (ClientError, BotoCoreError, OSError, ValueError):
        return _respond(503, {"error": "We couldn't save your details. Please try again shortly."})
    _sync_to_google_sheet(service_account_key, record)
    return _respond(200, {"saved": True, "submission_id": data["submission_id"]})


def _sync_to_google_sheet(service_account_key, record):
    """Best-effort only: the private Excel tracker above is the authoritative
    record, already saved by this point. A human-maintained planning sheet
    can have a missing row, a renamed column, or an expired credential --
    none of that should turn into a failed submission for the guest."""
    try:
        sync_submission(service_account_key, GOOGLE_SHEET_ID, GOOGLE_SHEET_GID, record)
    except Exception as error:  # noqa: BLE001 -- deliberately broad, see docstring
        print(f"Google Sheet sync failed for submission {record.get('submission_id')}: {error}")


def _contact_party(payload):
    if not _google_sheet_configured():
        return _respond(503, {"error": "The invitation list is temporarily unavailable."})
    try:
        service_account_key = _fetch_service_account_key()
        guest = lookup_guest_from_sheet(service_account_key, GOOGLE_SHEET_ID, GOOGLE_SHEET_GID, payload)
    except AccessDenied as error:
        return _respond(403, {"error": str(error)})
    except ValueError as error:
        return _respond(400, {"error": str(error)})
    except (ClientError, BotoCoreError, SheetSyncError, URLError, OSError):
        return _respond(503, {"error": "The invitation list is temporarily unavailable."})
    _mark_visited_best_effort(payload)
    return _respond(200, guest)


def _mark_visited_best_effort(identity):
    """Records that a guest reached the site, for the scheduled SMS run to
    check (backend/guest_texts.py). Best-effort: never fails the lookup or
    submission it's attached to."""
    if not isinstance(identity, dict):
        return
    try:
        mark_visited(_s3, BUCKET, identity.get("first_initial", ""), identity.get("last_name", ""))
    except Exception as error:  # noqa: BLE001 -- deliberately broad, see docstring
        print(f"Could not record site visit: {error}")


def _send_texts_task():
    """Invoked on a schedule (see docs/guest-information.md), not by a guest
    request -- there's no HTTP caller to report errors to, so this only logs.
    Nothing is sent unless GOOGLE_SHEET_ID/GID, a Google key, and a Twilio
    credentials file all already exist; per-row "Send Text?" still gates
    every individual guest (see backend/guest_texts.py)."""
    if not _google_sheet_configured():
        print("Google Sheet not configured; skipping scheduled text run.")
        return {"skipped": True}
    try:
        service_account_key = _fetch_service_account_key()
        twilio_credentials = json.loads(_s3.get_object(Bucket=BUCKET, Key=TWILIO_CREDENTIALS_KEY)["Body"].read())
    except (ClientError, BotoCoreError, OSError, ValueError) as error:
        print(f"Could not load credentials for scheduled text run: {error}")
        return {"error": str(error)}
    try:
        result = send_invitation_texts(service_account_key, GOOGLE_SHEET_ID, GOOGLE_SHEET_GID,
            twilio_credentials, _s3, BUCKET)
        print(f"Invitation text run: {result}")
        return result
    except Exception as error:  # noqa: BLE001 -- scheduled task, nothing to report to
        print(f"Scheduled text run failed: {error}")
        return {"error": str(error)}


_ROUTES = {"/lookup": _lookup, "/party": _party, "/rsvp": _rsvp, "/guest-info": _guest_info, "/contact-party": _contact_party}


def handler(event, context):
    if event.get("task") == "send-invitation-texts":
        return _send_texts_task()

    http = event.get("requestContext", {}).get("http", {})
    if http.get("method", "GET") != "POST":
        return _respond(405, {"error": "Use POST."})

    route = _ROUTES.get(http.get("path", ""))
    if route is None:
        return _respond(404, {"error": "Not found."})

    if not _limiter.allow(http.get("sourceIp", "unknown")):
        return _respond(429, {"error": "Please try again later."}, {"Retry-After": "600"})

    headers = {key.lower(): value for key, value in event.get("headers", {}).items()}
    if route in (_guest_info, _contact_party) and headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        return _respond(415, {"error": "Use JSON."})
    try:
        body = event.get("body") or "{}"
        if len(body) > MAX_BODY_BYTES * 2:
            return _respond(413, {"error": "Request too large."})
        body = base64.b64decode(body, validate=True) if event.get("isBase64Encoded") else body.encode("utf-8")
        if len(body) > MAX_BODY_BYTES:
            return _respond(413, {"error": "Request too large."})
        payload = json.loads(body)
    except (ValueError, UnicodeError, TypeError):
        return _respond(400, {"error": "Enter a valid request."})

    if route in (_lookup, _party, _rsvp):
        try:
            _sync_database()
        except (OSError, ClientError, BotoCoreError):
            return _respond(503, {"error": "Invitation lookup is temporarily unavailable."})

    return route(payload)
