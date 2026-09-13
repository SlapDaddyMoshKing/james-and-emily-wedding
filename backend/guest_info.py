"""Validation shared by the hosted contact form and local preview."""
from datetime import datetime, timezone
import re
import unicodedata
from uuid import UUID

MAX_BODY_BYTES = 16384
FIELDS = {
    "first_initial": (8, True), "last_name": (100, True),
    "email": (254, True), "phone": (40, False),
    "address_line1": (200, True), "address_line2": (200, False),
    "city": (100, True), "region": (100, False),
    "postal_code": (24, False), "country": (100, True),
    "household_members": (1000, False),
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
        if any(unicodedata.category(c).startswith("C") and not (field == "household_members" and c in "\r\n\t") for c in value):
            raise InvalidSubmission("Please remove unsupported characters.", field)
        result[field] = value
    initial = result["first_initial"].removesuffix(".")
    if not initial or not initial[0].isalpha() or any(not unicodedata.category(c).startswith("M") for c in initial[1:]):
        raise InvalidSubmission("Please enter just your first initial, such as E.", "first_initial")
    result["first_initial"] = initial
    if not re.fullmatch(r"[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+", result["email"]):
        raise InvalidSubmission("Please enter a valid email address.", "email")
    if result["country"].casefold() in {"united states", "united states of america", "us", "usa", "u.s.", "u.s.a."}:
        if not result["region"]:
            raise InvalidSubmission("Please enter your state.", "region")
        if not re.fullmatch(r"[0-9]{5}(-[0-9]{4})?", result["postal_code"]):
            raise InvalidSubmission("Please enter a five-digit ZIP code or ZIP+4.", "postal_code")
    return result

def make_record(data):
    return {**data, "schema_version": 1, "submitted_at": datetime.now(timezone.utc).isoformat()}
