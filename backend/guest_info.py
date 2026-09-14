"""Validation shared by the hosted contact form and local preview."""
from datetime import datetime, timezone
import unicodedata
from uuid import UUID

MAX_BODY_BYTES = 16384
TITLE_OPTIONS = {"", "Mr.", "Mrs.", "Ms.", "Miss", "Mx.", "Dr.", "Prof.", "Rev."}
FIELDS = {
    "title": (10, False), "first_name": (100, True), "last_name": (100, True), "suffix": (40, False),
    "plus_one_title": (10, False), "plus_one_first_name": (100, False), "plus_one_last_name": (100, False),
    "plus_one_suffix": (40, False), "address_line1": (200, True),
    "address_line2": (200, False), "city": (100, True),
    "region": (100, True), "postal_code": (24, True), "phone": (40, False),
}

class InvalidSubmission(ValueError):
    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field

def validate_submission(payload):
    if not isinstance(payload, dict) or set(payload) - (set(FIELDS) | {"submission_id", "website", "guest_name_unknown", "sms_consent"}):
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
        if any(unicodedata.category(c).startswith("C") for c in value):
            raise InvalidSubmission("Please remove unsupported characters.", field)
        result[field] = value
    for field in ("title", "plus_one_title"):
        if result[field] not in TITLE_OPTIONS:
            raise InvalidSubmission("Please choose a title from the list.", field)
    unknown = payload.get("guest_name_unknown", False)
    if not isinstance(unknown, bool):
        raise InvalidSubmission("Please send a valid contact form.", "guest_name_unknown")
    has_plus_one_name = bool(result["plus_one_first_name"] or result["plus_one_last_name"])
    if unknown and has_plus_one_name:
        raise InvalidSubmission("Please either name your guest or mark them unknown, not both.", "guest_name_unknown")
    if has_plus_one_name and not result["plus_one_first_name"]:
        raise InvalidSubmission("Please enter your guest's first name.", "plus_one_first_name")
    if has_plus_one_name and not result["plus_one_last_name"]:
        raise InvalidSubmission("Please enter your guest's last name.", "plus_one_last_name")
    result["guest_name_unknown"] = unknown
    sms_consent = payload.get("sms_consent", False)
    if not isinstance(sms_consent, bool):
        raise InvalidSubmission("Please send a valid contact form.", "sms_consent")
    result["sms_consent"] = sms_consent
    return result

def format_name(title, first, last, suffix):
    name = " ".join(part for part in (title, first, last) if part)
    return f"{name}, {suffix}" if suffix else name

def make_record(data):
    if data.get("guest_name_unknown"):
        name_line_two = "and Guest"
    elif data.get("plus_one_first_name"):
        name_line_two = "and " + format_name(data.get("plus_one_title", ""), data["plus_one_first_name"], data["plus_one_last_name"], data.get("plus_one_suffix", ""))
    else:
        name_line_two = ""
    tracker_fields = {
        "name_line_one": format_name(data.get("title", ""), data["first_name"], data["last_name"], data.get("suffix", "")),
        "name_line_two": name_line_two, "inner_envelope": "",
    }
    return {**data, **tracker_fields, "schema_version": 3, "submitted_at": datetime.now(timezone.utc).isoformat()}
