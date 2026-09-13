"""AWS Lambda entry point: contact collection and legacy invitation/RSVP APIs.

POST /contact-party matches first initial and surname to an approved invitation.
POST /guest-info rechecks that invitation and the selected guest before saving.
It appends one row to welcome/Guest Tracker.xlsx and retains an encrypted JSON
receipt. Success is returned only once the workbook write is confirmed.
See docs/guest-information.md for the current guest-facing flow.

Three endpoints, dispatched by path:
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

guests.sqlite3 is re-downloaded from S3 on every invocation (it's tiny), so
a guest-list re-import -- which can revoke access -- takes effect
immediately, matching backend/server.py's documented local behavior.

Configure via environment variables on the Lambda function:
  GUEST_DATA_BUCKET   S3 bucket holding guests.sqlite3 and rsvps/*
"""

from contextlib import closing
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from backend.server import RateLimit, is_invited, normalize_name
from backend.guest_info import MAX_BODY_BYTES, InvalidSubmission, make_record, validate_submission
from backend.guest_tracker import save_to_tracker
from backend.guest_sheet import sync_submission
from backend.contact_access import AccessDenied, authorize_submission, lookup_guest

BUCKET = os.environ["GUEST_DATA_BUCKET"]
DATABASE_PATH = Path("/tmp/wedding-site/guests.sqlite3")
MAX_PARTY_SIZE = 20
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_SHEET_GID = os.environ.get("GOOGLE_SHEET_GID")
GOOGLE_SERVICE_ACCOUNT_KEY = "google-service-account.json"

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


def _guest_info(payload):
    """Recheck the current invitation before accepting any contact information."""
    try:
        data = validate_submission(authorize_submission(DATABASE_PATH, payload))
    except AccessDenied as error:
        return _respond(403, {"error": str(error)})
    except InvalidSubmission as error:
        return _respond(400, {"error": str(error), "field": error.field})
    except ValueError as error:
        return _respond(400, {"error": str(error)})
    except sqlite3.Error:
        return _respond(503, {"error": "The invitation list is temporarily unavailable."})
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
    _sync_to_google_sheet(record)
    return _respond(200, {"saved": True, "submission_id": data["submission_id"]})


def _sync_to_google_sheet(record):
    """Best-effort only: the private Excel tracker above is the authoritative
    record, already saved by this point. A human-maintained planning sheet
    can have a missing row, a renamed column, or an expired credential --
    none of that should turn into a failed submission for the guest."""
    if not (GOOGLE_SHEET_ID and GOOGLE_SHEET_GID):
        return
    try:
        service_account_json = _s3.get_object(Bucket=BUCKET, Key=GOOGLE_SERVICE_ACCOUNT_KEY)["Body"].read()
        sync_submission(service_account_json, GOOGLE_SHEET_ID, GOOGLE_SHEET_GID, record)
    except Exception as error:  # noqa: BLE001 -- deliberately broad, see docstring
        print(f"Google Sheet sync failed for submission {record.get('submission_id')}: {error}")


def _contact_party(payload):
    try:
        return _respond(200, lookup_guest(DATABASE_PATH, payload))
    except AccessDenied as error:
        return _respond(403, {"error": str(error)})
    except ValueError as error:
        return _respond(400, {"error": str(error)})
    except sqlite3.Error:
        return _respond(503, {"error": "The invitation list is temporarily unavailable."})


_ROUTES = {"/lookup": _lookup, "/party": _party, "/rsvp": _rsvp, "/guest-info": _guest_info, "/contact-party": _contact_party}


def handler(event, context):
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

    try:
        _sync_database()
    except (OSError, ClientError, BotoCoreError):
        return _respond(503, {"error": "Invitation lookup is temporarily unavailable."})

    return route(payload)
