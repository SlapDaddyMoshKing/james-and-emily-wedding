"""The human-maintained Google Sheet: both the live invitation list and the
best-effort destination for submitted contact details.

Approval is presence-based: a row with both First Initial and Last Name
filled in is what makes that guest approved -- there is no separate
access_approved column. lookup_guest_from_sheet/authorize_submission_from_sheet
re-read the sheet on every call (no caching), so an edit to the sheet takes
effect on the very next request -- matching the rest of this project's
"always recheck the current source" trust model. A guest's own row can
already hold an address, phone, or plus-one name filled in by the couple
ahead of time; lookup_guest_from_sheet returns that under "prefill" so the
form can offer it back to the guest instead of asking again.

Once a submission is authorized and safely saved to the private Excel
tracker (backend/guest_tracker.py, still the authoritative, atomic record),
sync_submission updates that same row with what was actually submitted.
That direction is best-effort only: a missing row, a renamed column, or an
expired credential there must never block or fail the guest's submission.
Reading for lookup/authorization is not best-effort -- a failure there
should surface as "temporarily unavailable", the same as it would if
guests.sqlite3 were unreachable.
"""
import json
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from backend.contact_access import AccessDenied, normalized

from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
TIMEOUT = 10

# Sheet header (matched case/whitespace-insensitively) -> submitted field.
# "First Initial" and "Last Name" locate the row; they are never written.
COLUMN_FIELDS = {
    "guest first name": "first_name",
    "guest last name": "last_name",
    "plus one first name": "plus_one_first_name",
    "plus one last name": "plus_one_last_name",
    "phone number": "phone",
    "address line one": "address_line1",
    "address line two": "address_line2",
    "city": "city",
    "state": "region",
    "zip code": "postal_code",
}


class SheetSyncError(ValueError):
    pass


class _UrllibResponse:
    def __init__(self, status, headers, data):
        self.status = status
        self.headers = headers
        self.data = data


def _urllib_transport(url, method="GET", body=None, headers=None, timeout=TIMEOUT, **_ignored):
    """Minimal google-auth Request transport backed by the standard library,
    so this doesn't need the requests/urllib3 dependency."""
    request = Request(url, data=body, headers=dict(headers or {}), method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return _UrllibResponse(response.status, dict(response.getheaders()), response.read())
    except HTTPError as error:
        return _UrllibResponse(error.code, dict(error.headers or {}), error.read())


def _access_token(service_account_json):
    try:
        info = json.loads(service_account_json)
    except ValueError as error:
        raise SheetSyncError("Google service account credentials are not valid JSON.") from error
    credentials = Credentials.from_service_account_info(info, scopes=SCOPES)
    credentials.refresh(_urllib_transport)
    return credentials.token


def _api_request(token, spreadsheet_id, path, method="GET", payload=None):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{quote(spreadsheet_id)}{path}"
    headers = {"Authorization": f"Bearer {token}"}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, method=method, headers=headers)
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read())
    except HTTPError as error:
        raise SheetSyncError(f"Google Sheets API error: {error.read().decode('utf-8', 'replace')}") from error


def _sheet_title(token, spreadsheet_id, sheet_gid):
    metadata = _api_request(token, spreadsheet_id, "?fields=sheets.properties")
    for sheet in metadata.get("sheets", []):
        properties = sheet.get("properties", {})
        if str(properties.get("sheetId")) == str(sheet_gid):
            return properties["title"]
    raise SheetSyncError(f"No sheet tab with gid {sheet_gid} was found.")


def _column_letter(index):
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def find_row(rows, first_initial, last_name):
    """rows[0] is the header row; later rows are data. Returns the 1-based
    sheet row number of the guest matching first_initial/last_name, or None."""
    if not rows:
        raise SheetSyncError("Sheet has no header row.")
    header = [cell.strip().lower() for cell in rows[0]]
    try:
        initial_index = header.index("first initial")
        last_index = header.index("last name")
    except ValueError as error:
        raise SheetSyncError("Sheet is missing a First Initial or Last Name column.") from error
    target_initial = first_initial.strip().casefold()
    target_last = last_name.strip().casefold()
    for offset, row in enumerate(rows[1:], start=2):
        initial = row[initial_index].strip().casefold() if len(row) > initial_index else ""
        last = row[last_index].strip().casefold() if len(row) > last_index else ""
        if initial == target_initial and last == target_last:
            return offset
    return None


def build_updates(header, row_number, data):
    """Return Sheets API valueRanges (without a sheet-title prefix) for the
    columns this submission has data for. Columns with nothing to write are
    left untouched, never blanked -- e.g. Email Address isn't collected here."""
    normalized_header = [cell.strip().lower() for cell in header]
    updates = []
    for label, field in COLUMN_FIELDS.items():
        if label not in normalized_header:
            continue
        value = data.get(field, "")
        if field in ("plus_one_first_name", "plus_one_last_name") and data.get("guest_name_unknown"):
            value = "Unknown"
        if not value:
            continue
        column = _column_letter(normalized_header.index(label))
        updates.append({"range": f"{column}{row_number}", "values": [[value]]})
    return updates


def sync_submission(service_account_json, spreadsheet_id, sheet_gid, data):
    """Update the row matching this submission's guest in the shared sheet.
    Raises SheetSyncError (or lets a network error propagate) on any failure
    -- callers must treat this as best-effort and never fail a submission on it."""
    token = _access_token(service_account_json)
    title = _sheet_title(token, spreadsheet_id, sheet_gid)
    values = _api_request(token, spreadsheet_id, f"/values/{quote(title)}")
    rows = values.get("values", [])
    row_number = find_row(rows, data.get("first_name", "")[:1], data.get("last_name", ""))
    if row_number is None:
        raise SheetSyncError("No row in the sheet matches this guest's first initial and last name.")
    updates = build_updates(rows[0], row_number, data)
    if not updates:
        return
    for update in updates:
        update["range"] = f"{title}!{update['range']}"
    _api_request(token, spreadsheet_id, "/values:batchUpdate", method="POST",
        payload={"valueInputOption": "RAW", "data": updates})


# Sheet header -> prefill field, using the values a guest already submitted
# once already share with those in COLUMN_FIELDS. "Plus One?" and the name
# columns are handled separately below, not through this table.
PREFILL_FIELDS = {
    "phone number": "phone", "address line one": "address_line1", "address line two": "address_line2",
    "city": "city", "state": "region", "zip code": "postal_code",
}


def _row_as_dict(header, row):
    return {label.strip().lower(): (row[index] if index < len(row) else "") for index, label in enumerate(header)}


def find_guest(service_account_json, spreadsheet_id, sheet_gid, first_initial, last_name):
    """Presence-based lookup: a matching row is what makes a guest approved.
    Returns None if no row matches; raises SheetSyncError, or lets a network
    error propagate, if the sheet itself couldn't be read."""
    token = _access_token(service_account_json)
    title = _sheet_title(token, spreadsheet_id, sheet_gid)
    values = _api_request(token, spreadsheet_id, f"/values/{quote(title)}")
    rows = values.get("values", [])
    row_number = find_row(rows, first_initial, last_name)
    if row_number is None:
        return None
    row = _row_as_dict(rows[0], rows[row_number - 1])
    # Fall back to the sheet's own First Initial/Last Name cells (whatever
    # casing the couple typed there), never the normalized/lowercased search
    # terms -- those are for matching only, not for display.
    first = row.get("guest first name", "").strip() or row.get("first initial", "").strip()
    last = row.get("guest last name", "").strip() or row.get("last name", "").strip()
    plus_one_allowed = row.get("plus one?", "").strip().casefold() == "yes"
    plus_one_first = row.get("plus one first name", "").strip()
    plus_one_last = row.get("plus one last name", "").strip()
    guest_name_unknown = plus_one_first.casefold() == "unknown" or plus_one_last.casefold() == "unknown"
    prefill = {field: row.get(label, "").strip() for label, field in PREFILL_FIELDS.items()}
    prefill["guest_name_unknown"] = guest_name_unknown
    prefill["plus_one_first_name"] = "" if guest_name_unknown else plus_one_first
    prefill["plus_one_last_name"] = "" if guest_name_unknown else plus_one_last
    return {"first_name": first, "last_name": last, "plus_one_allowed": plus_one_allowed, "prefill": prefill}


def lookup_guest_from_sheet(service_account_json, spreadsheet_id, sheet_gid, identity):
    """Sheet-backed equivalent of contact_access.lookup_guest."""
    if not isinstance(identity, dict) or set(identity) != {"first_initial", "last_name"}:
        raise ValueError("Please enter your first initial and last name.")
    initial = normalized(identity["first_initial"]).removesuffix(".")
    last = normalized(identity["last_name"])
    if len(initial) != 1 or not initial.isalpha():
        raise ValueError("Please enter just the first letter of your first name.")
    guest = find_guest(service_account_json, spreadsheet_id, sheet_gid, initial, last)
    if guest is None:
        raise AccessDenied("We couldn't find that name on our invitation list. Check the spelling or contact Emily or James.")
    return guest


def authorize_submission_from_sheet(service_account_json, spreadsheet_id, sheet_gid, payload):
    """Sheet-backed equivalent of contact_access.authorize_submission."""
    if not isinstance(payload, dict) or "lookup" not in payload:
        raise AccessDenied("Please check your name before sending your details.")
    guest = lookup_guest_from_sheet(service_account_json, spreadsheet_id, sheet_gid, payload["lookup"])
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
