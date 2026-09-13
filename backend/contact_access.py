"""Name-only invitation access for contact collection; never a public guest list."""
from contextlib import closing
import sqlite3
import unicodedata

class AccessDenied(ValueError):
    pass

def normalized(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 100:
        raise ValueError("Please enter your first initial and last name.")
    value = " ".join(unicodedata.normalize("NFKC", value).casefold().split()).replace("’", "'").replace("‘", "'")
    if not value or any(unicodedata.category(c).startswith("C") for c in value):
        raise ValueError("Please enter your first initial and last name.")
    return value

def lookup_guest(database, identity):
    if not isinstance(identity, dict) or set(identity) != {"first_initial", "last_name"}:
        raise ValueError("Please enter your first initial and last name.")
    initial = normalized(identity["first_initial"]).removesuffix(".")
    last = normalized(identity["last_name"])
    if len(initial) != 1 or not initial.isalpha():
        raise ValueError("Please enter just the first letter of your first name.")
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.create_function("contact_name", 1, normalized, deterministic=True)
        matches = connection.execute("""SELECT first_name, last_name, plus_one FROM guests
            WHERE access_approved = 1 AND substr(contact_name(first_name), 1, 1) = ?
            AND contact_name(last_name) = ?""", (initial, last)).fetchall()
    if not matches:
        raise AccessDenied("We couldn't find that name on our invitation list. Check the spelling or contact Emily or James.")
    if len(matches) != 1:
        raise AccessDenied("More than one guest matches that name. Please contact Emily or James so we can help.")
    first, last_name_value, plus_one = matches[0]
    return {"first_name": first, "last_name": last_name_value, "plus_one_allowed": bool(plus_one)}

def authorize_submission(database, payload):
    if not isinstance(payload, dict) or "lookup" not in payload:
        raise AccessDenied("Please check your name before sending your details.")
    guest = lookup_guest(database, payload["lookup"])
    try:
        matches = (normalized(payload.get("first_name", "")) == normalized(guest["first_name"])
            and normalized(payload.get("last_name", "")) == normalized(guest["last_name"]))
    except ValueError:
        matches = False
    if not matches:
        raise AccessDenied("Please use the guest name from your invitation.")
    if not guest["plus_one_allowed"]:
        plus_one_fields = ("plus_one_title", "plus_one_first_name", "plus_one_last_name", "plus_one_suffix")
        if payload.get("guest_name_unknown") or any(payload.get(field) for field in plus_one_fields):
            raise AccessDenied("Your invitation does not include a plus-one.")
    return {key: value for key, value in payload.items() if key != "lookup"}
