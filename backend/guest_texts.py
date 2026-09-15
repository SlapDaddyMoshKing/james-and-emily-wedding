"""Best-effort SMS outreach: invite guests who haven't visited the site yet.

Sent via each carrier's email-to-SMS gateway (e.g. a text to a Verizon
number is an email to <10digits>@vtext.com), through a Gmail account --
not a paid SMS API. This avoids the US carrier-mandated A2P 10DLC / toll-free
verification process required for automated bulk SMS through providers like
Twilio, at the cost of needing each guest's carrier (a "Carrier" sheet
column) and having no delivery receipts or automatic opt-out handling: a
guest's "STOP" reply lands as a normal email reply in the sending Gmail
inbox, not anywhere this code can see, so opting someone out means manually
setting their row's "Send Text?" to No.

Reads the same shared Google Sheet as backend/guest_sheet.py. A guest is
texted only when their row's "Send Text?" column is Yes, they have a US
phone number, and their carrier is recognized -- so nothing goes out until
that's set per row, even once Gmail credentials and the scheduled run both
exist. Among those, a guest is texted only if they haven't yet completed
the site's name lookup (mark_visited records that, called from
backend/lambda_handler.py on every successful /contact-party), and
re-texted at most once every 21 days.

"Visited" and "last texted" are tracked in S3 (sms/<key>.json), not as a
sheet column, so a plain page load doesn't cost a Sheets API write. The
tracking key is a hash of the guest's normalized first initial + last
name -- the same identity the rest of this project matches on -- not the
phone number, so it stays stable even if a phone number changes or is
added later.

A bad number, an unrecognized carrier, or a send failure for one guest
must never stop the rest of the run: send_invitation_texts catches
per-guest and always finishes evaluating every row.
"""
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
import hashlib
import json
import re
import smtplib
from urllib.parse import quote

from botocore.exceptions import ClientError

from backend.guest_sheet import _access_token, _api_request, _row_as_dict, _sheet_title

SITE_URL = "https://slapdaddymoshking.github.io/james-and-emily-wedding/"
RESEND_AFTER = timedelta(days=21)
TIMEOUT = 10
MESSAGE_TEMPLATE = ("Hi {name}! It's Emily & James's wedding site -- please share your mailing "
    "address here so we can send you an invitation: {link} Reply STOP to opt out.")

# Carrier name (matched case/whitespace-insensitively) -> email-to-SMS gateway
# domain. Covers the major US carriers; an unlisted or MVNO carrier not on
# this list is skipped rather than guessed at.
CARRIER_GATEWAYS = {
    "at&t": "txt.att.net", "att": "txt.att.net",
    "verizon": "vtext.com", "xfinity mobile": "vtext.com", "xfinity": "vtext.com", "visible": "vtext.com",
    "t-mobile": "tmomail.net", "tmobile": "tmomail.net", "mint mobile": "tmomail.net", "metro by t-mobile": "tmomail.net",
    "metropcs": "tmomail.net", "metro": "tmomail.net",
    "sprint": "messaging.sprintpcs.com",
    "boost mobile": "sms.myboostmobile.com", "boost": "sms.myboostmobile.com",
    "cricket": "sms.cricketwireless.net", "cricket wireless": "sms.cricketwireless.net",
    "us cellular": "email.uscc.net", "uscellular": "email.uscc.net",
    "google fi": "msg.fi.google.com", "googlefi": "msg.fi.google.com",
}


def _carrier_domain(carrier):
    return CARRIER_GATEWAYS.get(carrier.strip().casefold())


def _phone_digits(phone):
    """US-only: 10 digits, or 11 starting with a leading 1. Anything else
    (missing, malformed, international) is treated as unsendable."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


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


def send_invitation_texts(service_account_json, spreadsheet_id, sheet_gid, gmail_credentials, s3, bucket):
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
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=TIMEOUT) as server:
        server.login(gmail_credentials["email"], gmail_credentials["app_password"])
        for row in rows[1:]:
            data = _row_as_dict(header, row)
            if data.get("send text?", "").strip().casefold() != "yes":
                continue
            first_initial = data.get("first initial", "").strip()
            last_name = data.get("last name", "").strip()
            if not first_initial or not last_name:
                continue
            digits = _phone_digits(data.get("phone number", ""))
            domain = _carrier_domain(data.get("carrier", ""))
            if digits is None or domain is None:
                summary["skipped"] += 1
                continue
            key = _tracking_key(first_initial, last_name)
            record = _read_tracking(s3, bucket, key)
            if record.get("visited_at"):
                summary["skipped"] += 1
                continue
            last_texted_at = record.get("last_texted_at")
            if last_texted_at and datetime.fromisoformat(last_texted_at) > now - RESEND_AFTER:
                summary["skipped"] += 1
                continue
            name = data.get("guest first name", "").strip() or first_initial
            body = MESSAGE_TEMPLATE.format(name=name, link=SITE_URL)
            message = MIMEText(body)
            message["Subject"] = ""
            message["From"] = gmail_credentials["email"]
            message["To"] = f"{digits}@{domain}"
            try:
                server.sendmail(gmail_credentials["email"], [message["To"]], message.as_string())
                record["last_texted_at"] = now.isoformat()
                _write_tracking(s3, bucket, key, record)
                summary["sent"] += 1
            except smtplib.SMTPException as error:
                print(f"Could not text {first_initial}/{last_name}: {error}")
                summary["errors"] += 1
    return summary
