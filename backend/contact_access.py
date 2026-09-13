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

def lookup_party(database, identity):
    if not isinstance(identity, dict) or set(identity) != {"first_initial", "last_name"}:
        raise ValueError("Please enter your first initial and last name.")
    initial = normalized(identity["first_initial"]).removesuffix(".")
    last = normalized(identity["last_name"])
    if len(initial) != 1 or not initial.isalpha():
        raise ValueError("Please enter just the first letter of your first name.")
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.create_function("contact_name", 1, normalized, deterministic=True)
        matches = connection.execute("""SELECT guest_id, household_id FROM guests
            WHERE access_approved = 1 AND substr(contact_name(first_name), 1, 1) = ?
            AND contact_name(last_name) = ?""", (initial, last)).fetchall()
        if not matches:
            raise AccessDenied("We couldn't find that name on our invitation list. Check the spelling or contact Emily or James.")
        if len(matches) != 1:
            raise AccessDenied("More than one guest matches that name. Please contact Emily or James so we can help.")
        matched_id, household = matches[0]
        rows = connection.execute("""SELECT guest_id, first_name, last_name FROM guests
            WHERE household_id = ? AND access_approved = 1 ORDER BY guest_id""", (household,)).fetchall()
    return {"matched_guest_id": matched_id, "members": [
        {"guest_id": identifier, "name": f"{first} {last}"} for identifier, first, last in rows]}

def authorize_submission(database, payload):
    if not isinstance(payload, dict) or "lookup" not in payload or "guest_id" not in payload:
        raise AccessDenied("Please check your name before sending your details.")
    party = lookup_party(database, payload["lookup"])
    selected = next((member for member in party["members"] if member["guest_id"] == payload["guest_id"]), None)
    if selected is None:
        raise AccessDenied("That guest is not part of your invitation.")
    if payload.get("name_line_one") != selected["name"]:
        raise AccessDenied("Please use the guest name from your invitation.")
    return {key: value for key, value in payload.items() if key not in {"lookup", "guest_id"}}
