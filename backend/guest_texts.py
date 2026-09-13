"""Best-effort SMS outreach: invite guests who haven't visited the site yet.

Reads the same shared Google Sheet as backend/guest_sheet.py. A guest is
texted only when their row's "Send Text?" column is Yes and they have a
phone number -- so nothing goes out until that's set per row, even once
Twilio credentials and the scheduled run both exist. Among those, a guest
is texted only if they haven't yet completed the site's name lookup
(mark_visited records that, called from backend/lambda_handler.py on every
successful /contact-party), and re-texted at most once every 21 days.

"Visited" and "last texted" are tracked in S3 (sms/<key>.json), not as a
sheet column, so a plain page load doesn't cost a Sheets API write. The
tracking key is a hash of the guest's normalized first initial + last
name -- the same identity the rest of this project matches on -- not the
phone number, so it stays stable even if a phone number changes or is
added later.

A bad number or a Twilio error for one guest (including code 21610,
"unsubscribed recipient", recorded so that guest is never retried) must
never stop the rest of the run: send_invitation_texts catches per-guest
and always finishes evaluating every row.
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from botocore.exceptions import ClientError

from backend.guest_sheet import _access_token, _api_request, _row_as_dict, _sheet_title

SITE_URL = "https://slapdaddymoshking.github.io/james-and-emily-wedding/"
RESEND_AFTER = timedelta(days=21)
TIMEOUT = 10
MESSAGE_TEMPLATE = ("Hi {name}! It's Emily & James's wedding site -- please share your mailing "
    "address here so we can send you an invitation: {link} Reply STOP to opt out.")


class TwilioError(Exception):
    def __init__(self, status, body):
        super().__init__(f"Twilio error {status}: {body}")
        self.status = status
        try:
            self.code = json.loads(body).get("code")
        except ValueError:
            self.code = None


def _to_e164(phone):
    """US-only: 10 digits, or 11 starting with a leading 1. Anything else
    (missing, malformed, international) is treated as unsendable."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return None


def _tracking_key(first_initial, last_name):
    identity = f"{first_initial.strip().casefold()}|{last_name.strip().casefold()}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _tracking_s3_key(key):
    return f"sms/{key}.json"


def _read_tracking(s3, bucket, key):
    try:
        response = s3.get_object(Bucket=bucket, Key=_tracking_s3_key(key))
        return json.loads(response["Body"].read())
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return {}
        raise


def _write_tracking(s3, bucket, key, record):
    s3.put_object(Bucket=bucket, Key=_tracking_s3_key(key), Body=json.dumps(record).encode("utf-8"),
        ContentType="application/json; charset=utf-8", ServerSideEncryption="AES256")


def mark_visited(s3, bucket, first_initial, last_name):
    """Record that a guest has completed the name lookup, if not already
    recorded. Idempotent and safe to call on every lookup."""
    if not first_initial or not last_name:
        return
    key = _tracking_key(first_initial, last_name)
    record = _read_tracking(s3, bucket, key)
    if not record.get("visited_at"):
        record["visited_at"] = datetime.now(timezone.utc).isoformat()
        _write_tracking(s3, bucket, key, record)


def _send_sms(account_sid, auth_token, from_number, to_number, body):
    url = f"https://api.twilio.com/2010-04-01/Accounts/{quote(account_sid)}/Messages.json"
    data = urlencode({"To": to_number, "From": from_number, "Body": body}).encode("utf-8")
    credentials = base64.b64encode(f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")
    request = Request(url, data=data, method="POST", headers={
        "Authorization": f"Basic {credentials}", "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read())
    except HTTPError as error:
        raise TwilioError(error.code, error.read().decode("utf-8", "replace")) from error


def send_invitation_texts(service_account_json, spreadsheet_id, sheet_gid, twilio_credentials, s3, bucket):
    """Evaluate every row and text whoever is due. Returns a small summary
    dict for logging; never raises for a single guest's failure."""
    token = _access_token(service_account_json)
    title = _sheet_title(token, spreadsheet_id, sheet_gid)
    values = _api_request(token, spreadsheet_id, f"/values/{quote(title)}")
    rows = values.get("values", [])
    summary = {"sent": 0, "skipped": 0, "errors": 0}
    if not rows:
        return summary
    header = rows[0]
    now = datetime.now(timezone.utc)
    for row in rows[1:]:
        data = _row_as_dict(header, row)
        if data.get("send text?", "").strip().casefold() != "yes":
            continue
        first_initial = data.get("first initial", "").strip()
        last_name = data.get("last name", "").strip()
        if not first_initial or not last_name:
            continue
        e164 = _to_e164(data.get("phone number", ""))
        if e164 is None:
            summary["skipped"] += 1
            continue
        key = _tracking_key(first_initial, last_name)
        record = _read_tracking(s3, bucket, key)
        if record.get("visited_at") or record.get("opted_out"):
            summary["skipped"] += 1
            continue
        last_texted_at = record.get("last_texted_at")
        if last_texted_at and datetime.fromisoformat(last_texted_at) > now - RESEND_AFTER:
            summary["skipped"] += 1
            continue
        name = data.get("guest first name", "").strip() or first_initial
        body = MESSAGE_TEMPLATE.format(name=name, link=SITE_URL)
        try:
            _send_sms(twilio_credentials["account_sid"], twilio_credentials["auth_token"],
                twilio_credentials["from_number"], e164, body)
            record["last_texted_at"] = now.isoformat()
            _write_tracking(s3, bucket, key, record)
            summary["sent"] += 1
        except TwilioError as error:
            if error.code == 21610:  # unsubscribed recipient: never retry
                record["opted_out"] = True
                _write_tracking(s3, bucket, key, record)
            print(f"Could not text {first_initial}/{last_name}: {error}")
            summary["errors"] += 1
    return summary
