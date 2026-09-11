"""Validate a private guest CSV and atomically import it into a local SQLite database.

This is data preparation, not a login service. No emails are sent.
"""

import argparse
import csv
import os
from pathlib import Path
import re
import sqlite3
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WeddingSiteData"
COLUMNS = ("guest_id", "household_id", "first_name", "last_name", "email", "access_approved")
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
EMAIL = re.compile(r"[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+\Z")


def private_path(path):
    resolved = Path(path).expanduser().resolve()
    if resolved.is_relative_to(REPO_ROOT):
        raise ValueError("Keep working guest lists and databases outside the public website folder.")
    return resolved


def read_guests(path):
    guests = []
    identifiers = set()
    email_households = {}
    with Path(path).open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != list(COLUMNS):
            raise ValueError("CSV headers must be exactly: " + ",".join(COLUMNS))
        for line, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Row {line}: wrong number of columns.")
            row = {key: value.strip() for key, value in row.items()}
            if not any(row.values()):
                continue
            for field in ("guest_id", "household_id"):
                if not IDENTIFIER.fullmatch(row[field]):
                    raise ValueError(f"Row {line}: {field} needs 1–64 letters, numbers, underscores or hyphens.")
            if row["guest_id"] in identifiers:
                raise ValueError(f"Row {line}: duplicate guest_id.")
            identifiers.add(row["guest_id"])
            for field in ("first_name", "last_name"):
                if not row[field] or len(row[field]) > 100:
                    raise ValueError(f"Row {line}: {field} is required and must be 100 characters or fewer.")
                if row[field].startswith(("=", "+", "-", "@")):
                    raise ValueError(f"Row {line}: {field} must be plain text, not a spreadsheet formula.")
            email = row["email"].lower()
            if email:
                if len(email) > 254 or not EMAIL.fullmatch(email) or email.startswith(("=", "+", "-", "@")):
                    raise ValueError(f"Row {line}: enter one valid email address, or leave it blank.")
                if email in email_households and email_households[email] != row["household_id"]:
                    raise ValueError(f"Row {line}: one email cannot belong to different households.")
                email_households[email] = row["household_id"]
            approval = row["access_approved"].lower()
            if approval not in ("yes", "no"):
                raise ValueError(f"Row {line}: access_approved must be yes or no.")
            guests.append((row["guest_id"], row["household_id"], row["first_name"], row["last_name"], email or None, int(approval == "yes")))
    if not guests:
        raise ValueError("The list is empty. No database changes were made.")
    return guests


def import_guests(guests, database):
    database = private_path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        with connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS guests (
                guest_id TEXT PRIMARY KEY,
                household_id TEXT NOT NULL,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                email TEXT,
                access_approved INTEGER NOT NULL CHECK (access_approved IN (0, 1)),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            connection.execute("CREATE INDEX IF NOT EXISTS guests_household ON guests(household_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS guests_email ON guests(email)")
            # Each import is a complete snapshot: omitted guests lose approval.
            # Keep records to preserve stable guest IDs for future RSVP references.
            connection.execute("UPDATE guests SET access_approved = 0, updated_at = CURRENT_TIMESTAMP")
            connection.executemany("""INSERT INTO guests
                (guest_id, household_id, first_name, last_name, email, access_approved)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(guest_id) DO UPDATE SET
                    household_id = excluded.household_id,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    email = excluded.email,
                    access_approved = excluded.access_approved,
                    updated_at = CURRENT_TIMESTAMP""", guests)
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("--import", dest="do_import", action="store_true", help="Apply this complete list to the local database; omitted guests lose approval.")
    parser.add_argument("--database", type=Path, default=DATA_DIR / "guests.sqlite3")
    args = parser.parse_args()
    try:
        source = private_path(args.csv_file)
        guests = read_guests(source)
        approved = sum(guest[5] for guest in guests)
        email_logins = len({guest[4] for guest in guests if guest[4] and guest[5]})
        print(f"Valid list: {len(guests)} guests; {approved} approved; {email_logins} approved email addresses.")
        if args.do_import:
            import_guests(guests, args.database)
            print("Local database updated. Guests omitted from this list are now unapproved.")
        else:
            print("Validation only. No database changes made.")
    except (ValueError, OSError, csv.Error, sqlite3.Error) as error:
        print(f"Guest list error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
