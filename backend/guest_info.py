"""Validation shared by the hosted contact form and local preview."""
from datetime import datetime, timezone
import re
import unicodedata
from uuid import UUID

MAX_BODY_BYTES = 16384
FIELDS = {
    "name_line_one": (200, True), "name_line_two": (200, False),
    "inner_envelope": (200, False), "address_line1": (200, True),
    "address_line2": (200, False), "city": (100, True),
    "region": (100, True), "postal_code": (24, True), "phone": (40, False),
}

class InvalidSubmission(ValueError):
    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field

def validate_submission(payload):
    if not isinstance(payload, dict) or set(payload) - (set(FIELDS) | {"submission_id", "website"}):
        raise InvalidSubmission("Please send a valid contact form.")
    try:
        identifier = str(UUID(payload.get("submission_id", ""), version=4))
        if identifier != payload["submission_id"]:
            raise ValueError()
    except (ValueError, TypeError, AttributeError, KeyError):
        raise InvalidSubmission("Please refresh the page and try again.") from None
    if payload.get("website", "") != "":
        raise InvalidSubmission("Please leave the extra website field empty.")
    result = {"submission_id": identifier}
    for field, (limit, required) in FIELDS.items():
        value = payload.get(field, "")
        if not isinstance(value, str) or len(value) > limit:
            raise InvalidSubmission("Please shorten or correct this field.", field)
        value = unicodedata.normalize("NFC", value.strip())
        if required and not value:
            raise InvalidSubmission("Please fill in this required field.", field)
        if any(unicodedata.category(c).startswith("C")  for c in value):
            raise InvalidSubmission("Please remove unsupported characters.", field)
        result[field] = value
    return result

def make_record(data):
    return {**data, "schema_version": 2, "submitted_at": datetime.now(timezone.utc).isoformat()}
