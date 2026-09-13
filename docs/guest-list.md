# Private guest-list groundwork

> Archived invitation/RSVP documentation. As of 2026-09-13, the public site collects guest contact information without an invitation check. See [the current contact collection guide](guest-information.md). The older tools and RSVP data remain available for a later phase; the deployment and page descriptions below describe the previous site.

## Current status

The CSV template, local database importer, and name lookup backend are implemented. The public site has an "RSVP" button and form. Its lookup is marked coming soon until the backend is hosted and connected. No guest authentication, email delivery, protected pages, or hosted database has been deployed. Importing the CSV does not change access to the current website.

## Editing your list

Your working file is outside the public website folder:

`%LOCALAPPDATA%\WeddingSiteData\guest-list.csv`

Open it in Excel. Keep the header row and save as **CSV UTF-8 (Comma delimited)**. You can keep an Excel workbook alongside it if preferred, but export to CSV before importing. The importer currently accepts CSV only.

Use one row per named guest, including children. Assign permanent IDs; do not renumber existing guests when sorting the sheet.

| Column | What to enter |
| --- | --- |
| guest_id | Unique ID such as G001; never reuse it for another person |
| household_id | Shared ID such as H001 for guests on the same invitation -- this is also how +1s are grouped for RSVP: give a named +1 the same household_id as the guest they're invited with |
| first_name | Guest's first name |
| last_name | Guest's last name |
| email | One email, or blank for someone without their own email |
| access_approved | yes or no; separate from whether they RSVP yes |
| plus_one | yes or no; whether the contact form offers this guest an (unnamed-until-they-say-so) plus-one. See [contact collection](guest-information.md) -- this is separate from the household_id-based named-plus-one mechanism this document otherwise describes, which remains for the archived RSVP flow only. |

Shared emails are allowed within a household. One email cannot span multiple households. A guest with no email cannot receive an email sign-in code. A named plus-one (for RSVP purposes) can have their own row sharing a household_id; the contact form's `plus_one` column is unrelated and does not require a second row.

Only fictional examples belong in `templates/guest-list.example.csv`. Keep real names, invitations, email addresses, RSVP data, and database backups outside this repository. Git ignore rules are a fallback; they do not protect files already committed. This directory is synced through OneDrive; the working list is deliberately stored in LocalAppData instead. Back it up privately if desired.

## Validate and import

From the website folder in PowerShell:

```powershell
python scripts/guest_list.py "$env:LOCALAPPDATA\WeddingSiteData\guest-list.csv"
python scripts/guest_list.py "$env:LOCALAPPDATA\WeddingSiteData\guest-list.csv" --import
```

The first command checks the entire file. The second imports it into `%LOCALAPPDATA%\WeddingSiteData\guests.sqlite3`. Invalid input makes no database changes. Blank lists are rejected to avoid accidental mass revocation.

Every import represents the **complete guest list**, not a few additional rows. Existing guests missing from a later import become unapproved; their records are retained. Setting `access_approved` to `no` also revokes approval in this data store. No invitation emails are sent. This is a local staging database, not a live backend, and the import does not revoke any live login sessions.

## Access control to implement next

### Invitation lookup implemented

The visitor opens **RSVP**, enters first and last name, and submits the form. The backend checks the private database for an approved exact name match, ignoring case, repeated whitespace, and straight versus curly apostrophes. It does not use partial or fuzzy matching. A matching name confirms only that the name is invited; it does not establish identity or unlock any wedding content. Duplicate names cannot identify an individual; email verification must resolve identity later.

This yes/no lookup deliberately reveals whether a submitted name is approved, as requested. It never returns an email address or a sign-in session. Requests use POST rather than placing guest names in URLs. A local per-IP limit allows ten attempts per ten minutes.

**Household members are exposed by the hosted `/party` endpoint** (see below), a deliberate change from the original design here -- RSVPing for a +1 requires knowing who the +1 is. `/lookup` itself is unchanged and still returns only `{"invited": true/false}`.

After importing the full working CSV, start the local backend from the repository root:

```powershell
python -m backend.server
```

Visit http://127.0.0.1:8080. This development server serves only the public entry page and its required assets. It cannot serve the guest list or private ceremony preview. If the database is missing or cannot be read, the lookup returns an unavailable error rather than claiming the guest is uninvited. No fictional guests are added to your working list; tests use isolated temporary data.

The public `site-config.json` currently has `invitationLookupUrl` set to `null`. Name fields remain editable regardless of backend availability. Without a connected API, submitting shows a coming-soon message and explicitly confirms the name was not checked or saved. A file opened directly from disk also displays this state. The local server supplies its own same-origin API configuration, so localhost can use the real lookup after a CSV import.

### Hosting: connected (2026-09-11)

The name lookup is now hosted on the user's AWS account (region `us-east-2`) and `site-config.json`'s `invitationLookupUrl` points at it:

- **S3** (`wedding-site-guest-data-8f3d21`, private, encrypted, all public access blocked): holds `guests.sqlite3`. Update it with one command instead of the old two-step import-then-upload:
  `python scripts/publish_guest_list.py "$env:LOCALAPPDATA\WeddingSiteData\guest-list.csv"`
  This validates and imports locally (same rules as `guest_list.py --import`) and then uploads to S3, so the hosted lookup reflects it immediately. If the upload step fails, it says so explicitly rather than silently leaving the hosted list stale.
- **Lambda** (`wedding-lookup`, Python 3.12, `backend/lambda_handler.py`): downloads `guests.sqlite3` from S3 on every invocation (so revocation takes effect immediately, matching the local server) and calls the same `is_invited`/`normalize_name` logic as `backend/server.py`. It grants no session and serves no content -- it only answers `{"invited": true/false}`.
- **API Gateway** (HTTP API `jfjd4a92w5`, routes `POST /lookup`, `POST /party`, `POST /rsvp`): fronts the Lambda over HTTPS, restricts CORS to `https://slapdaddymoshking.github.io`, and applies a coarse account-wide throttle (10 req/s, burst 20) as a backstop alongside the Lambda's own in-memory per-IP limiter.
- Deploy credentials live in a local `wedding-site-deploy` AWS CLI profile, scoped by IAM policy to resources named `wedding-*` and the one S3 bucket above -- not admin/root access.
- To redeploy Lambda code after editing `backend/lambda_handler.py` or `backend/server.py`: zip `backend/` and `scripts/` together and run `aws lambda update-function-code --function-name wedding-lookup --zip-file fileb://path/to.zip --profile wedding-site --region us-east-2`.

### RSVP and +1s: connected (2026-09-11)

The welcome page's RSVP section (`welcome.js`, `POST /party`, `POST /rsvp`) uses the existing `household_id` column as the +1 mechanism -- no schema change needed. To make two guests part of the same party, give them the **same `household_id`** in the CSV; each keeps their own `guest_id` and can RSVP independently.

- `POST /party` `{first_name, last_name}` -> re-validates the name (same rules as `/lookup`), then returns every approved guest sharing that household_id, each with their current RSVP status (`true`/`false`/`null` for not yet answered). This is the endpoint that now exposes other household members' names -- see the note above.
- `POST /rsvp` `{first_name, last_name, responses: [{guest_id, attending}]}` -> re-validates the name and that every `guest_id` in `responses` belongs to that same household before writing anything; a `guest_id` from outside the household is rejected with 403.
- RSVP responses are stored as **individual objects in S3** (`rsvps/<guest_id>.json`), not inside `guests.sqlite3`. This is deliberate: a guest-list CSV import replaces the entire `guests` table, and mixing RSVP answers into that table would mean re-importing the list (e.g. to add a guest) silently erases everyone's RSVPs. Keeping them separate means RSVPs persist across any number of future guest-list updates.
- Neither endpoint issues a session; both re-check the submitted name against the current guest list every time, consistent with the name-only trust model above.
- The welcome page carries the guest's name from the RSVP check to itself via `sessionStorage` (not a URL, to keep it out of browser history) so it doesn't have to ask again -- but it degrades gracefully to a small "enter your name" form on `welcome.html` if that's missing (a bookmark, a shared link, or a fresh tab), so there's no dead end.

### Venue map: added (2026-09-11)

`welcome.html`'s map section is a plain Google Maps embed (`google.com/maps?q=...&output=embed` iframe, no API key) built from `wedding-content.json`'s `venue` and `address` fields. No Google Cloud account or billing setup required; if a nicer/branded embed is wanted later, that needs a Google Maps Embed API key instead.

### Selected authentication: name-only, deliberately

**This is a conscious trade-off, not an oversight.** An earlier version of this document planned emailed one-time codes; that was superseded on 2026-09-11. The actual guest-facing flow is: enter first and last name, get a yes/no. A correct name alone grants no session and unlocks nothing by itself.

The real gate is that the URL of the welcome page (see below) is distributed privately -- e.g. via a QR code on the physical invitation -- not linked from the public site or guessable from `guest-lookup.js`. The user explicitly decided this is sufficient for a wedding site: "We don't need to create the most secure system in the world here." If that changes, revisit email verification (SES + a managed code store) before publishing anything more sensitive than what's already public knowledge.

### Welcome page: published (2026-09-11)

`guest-lookup.js` redirects an invited match to `/welcome.html` on the same public site (GitHub Pages). That file (and `assets/engagement.jpg`) is generated from the private `wedding-content.json` and the real photo by:

`python scripts/publish_welcome_page.py`

Unlike `build_private_preview.py`, this writes directly into the repo root, not a local-only preview folder -- it's meant to be committed and pushed. Doing so makes the real ceremony details and photo fetchable by anyone with the URL; that's the accepted trade-off above, made deliberately, not by accident. The script only writes files -- it never runs `git` itself, so review the output before committing. Re-run it whenever `wedding-content.json` or the photo changes, then commit and push.

The browser must never download the guest CSV or full guest database, regardless of the above. AWS holds those, not the public repository or GitHub Pages.
