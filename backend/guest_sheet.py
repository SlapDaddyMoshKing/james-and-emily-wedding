"""Best-effort sync of a submission into the human-maintained Google Sheet.

The private Excel tracker (backend/guest_tracker.py) is the authoritative,
atomic record of every submission. This module updates the matching row in
a separate, manually-maintained planning spreadsheet so it stays roughly
current -- it is read/edited by people, not machine-generated, so a failure
here (a missing row, a renamed column, an expired credential) must never
block or fail the guest's submission. Callers should catch broadly around
this module and only log, never raise to the guest-facing response.
"""
import json
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

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
