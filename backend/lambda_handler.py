"""AWS Lambda entry point: a hosted version of the invitation name lookup.

This does one thing only -- check a submitted first/last name against the
private guest list in S3 and answer {"invited": true/false}. It does not
grant a session, issue a cookie, or serve any wedding content; the welcome
page continues to live on the public site (GitHub Pages), reached by a plain
client-side redirect after a match. AWS's only job here is to hold the guest
list somewhere the public site's JavaScript can query it over HTTPS.

guests.sqlite3 is re-downloaded from S3 on every invocation (it's tiny), so a
guest-list re-import -- which can revoke access -- takes effect immediately,
matching backend/server.py's documented local behavior.

Configure via environment variables on the Lambda function:
  GUEST_DATA_BUCKET   S3 bucket holding guests.sqlite3
"""

import json
import os
from pathlib import Path

import boto3

from backend.server import RateLimit, is_invited

BUCKET = os.environ["GUEST_DATA_BUCKET"]
DATABASE_PATH = Path("/tmp/wedding-site/guests.sqlite3")

_s3 = boto3.client("s3")
# Per-warm-container only (not shared across concurrent Lambdas); a coarse
# API Gateway throttle provides a global backstop alongside this.
_limiter = RateLimit()

_HEADERS = {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}


def _respond(status, body):
    return {"statusCode": status, "headers": _HEADERS, "body": json.dumps(body)}


def handler(event, context):
    request_context = event.get("requestContext", {})
    http = request_context.get("http", {})
    if http.get("method", "GET") != "POST":
        return _respond(405, {"error": "Use POST."})

    source_ip = http.get("sourceIp", "unknown")
    if not _limiter.allow(source_ip):
        return {**_respond(429, {"error": "Please try again later."}), "headers": {**_HEADERS, "Retry-After": "600"}}

    try:
        payload = json.loads(event.get("body") or "{}")
        if not isinstance(payload, dict) or set(payload) != {"first_name", "last_name"}:
            raise ValueError("Enter a first and last name.")
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _s3.download_file(BUCKET, "guests.sqlite3", str(DATABASE_PATH))
        invited = is_invited(DATABASE_PATH, payload["first_name"], payload["last_name"])
    except (ValueError, UnicodeError):
        return _respond(400, {"error": "Enter a valid first and last name."})
    except Exception:
        return _respond(503, {"error": "Invitation lookup is temporarily unavailable."})

    return _respond(200, {"invited": invited})
