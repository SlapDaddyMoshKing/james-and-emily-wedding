# Private guest-list groundwork

## Current status

The CSV template, local database importer, and name lookup backend are implemented. The public site has an "Am I invited?" button and form. Its lookup is marked coming soon until the backend is hosted and connected. No guest authentication, email delivery, protected pages, or hosted database has been deployed. Importing the CSV does not change access to the current website.

## Editing your list

Your working file is outside the public website folder:

`%LOCALAPPDATA%\WeddingSiteData\guest-list.csv`

Open it in Excel. Keep the header row and save as **CSV UTF-8 (Comma delimited)**. You can keep an Excel workbook alongside it if preferred, but export to CSV before importing. The importer currently accepts CSV only.

Use one row per named guest, including children. Assign permanent IDs; do not renumber existing guests when sorting the sheet.

| Column | What to enter |
| --- | --- |
| guest_id | Unique ID such as G001; never reuse it for another person |
| household_id | Shared ID such as H001 for guests on the same invitation |
| first_name | Guest's first name |
| last_name | Guest's last name |
| email | One email, or blank for someone without their own email |
| access_approved | yes or no; separate from whether they RSVP yes |

Shared emails are allowed within a household. One email cannot span multiple households. A guest with no email cannot receive an email sign-in code. A named plus-one can have their own row; leave unnamed plus-one capacity for a future invitations table.

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

The visitor opens **Am I invited?**, enters first and last name, and submits the form. The backend checks the private database for an approved exact name match, ignoring case, repeated whitespace, and straight versus curly apostrophes. It does not use partial or fuzzy matching. A matching name confirms only that the name is invited; it does not establish identity or unlock any wedding content. Duplicate names cannot identify an individual; email verification must resolve identity later.

This yes/no lookup deliberately reveals whether a submitted name is approved, as requested. It never returns an email address, a guest or household ID, other household members, or a sign-in session. Requests use POST rather than placing guest names in URLs. A local per-IP limit allows ten attempts per ten minutes.

After importing the full working CSV, start the local backend from the repository root:

```powershell
python -m backend.server
```

Visit http://127.0.0.1:8080. This development server serves only the public entry page and its required assets. It cannot serve the guest list or private ceremony preview. If the database is missing or cannot be read, the lookup returns an unavailable error rather than claiming the guest is uninvited. No fictional guests are added to your working list; tests use isolated temporary data.

The public `site-config.json` currently has `invitationLookupUrl` set to `null`. The published form is disabled and displays a coming-soon message in this state. A file opened directly from disk also displays this state. The local server supplies its own same-origin API configuration, so localhost can use the real lookup after a CSV import.

### Hosting still required

The user has an AWS account; it is not connected on this machine. Deploy a production backend before setting `invitationLookupUrl` to its HTTPS endpoint. GitHub Pages does not execute the Python backend. Do not expose the local development server to the internet.

The hosted API must retain the same request/response contract, keep guest data in private storage, enforce rate limits across instances, and allow browser requests only from the site origin. CORS is not authentication and does not prevent direct callers. Do not log request bodies or use names as authentication credentials. Add managed email verification and enforce current approval on every protected request before publishing ceremony content.

### Selected authentication

**Selected sign-in method: emailed one-time codes.** A visitor enters their email, receives a short-lived code if their email is approved, and verifies that code before accessing wedding content. Knowing a name or email address alone must not grant access. Use a managed authentication service for code generation, expiry, single-use verification, retry limits, and session handling; the CSV importer is not an authentication service.

Only email addresses on currently approved guest rows may sign in. Guests without email can stay in the planning list, but cannot sign in independently. Shared household emails represent a shared login identity; individual guest approval must still be respected. Do not allow unrestricted public sign-up. Check approval before sending a code and again after verification; removing approval during the sign-in process must deny access. Changing an email must remove access through the previous address. Never log codes or return them from public APIs.

Email delivery and the managed authentication service still require account setup. No provider has been selected or connected. Before deployment, verify that an unknown email, expired code, reused code, revoked guest, and unauthenticated direct request to a private asset all fail. Verify that an approved guest can receive a code and sign in successfully.

The hosting backend must authenticate requests and check current approval on every request for wedding details, photos, or RSVP data. Approval changes must apply to existing sessions too. Serve private assets through authenticated routes or short-lived authorized URLs. Return a generic response for email sign-in attempts so the authentication endpoint does not reveal guest email addresses. The separate name lookup above intentionally provides yes/no invitation confirmation.

The default authorization boundary is the individual approved guest. Household grouping alone does not authorize changing anyone else's RSVP; explicitly decide household representative permissions when implementing RSVPs.

The browser must never download the guest CSV or full guest database. The public GitHub repository and generated static assets must never contain protected wedding details. A client-side password overlay does not provide access control.

GitHub Pages can continue serving a generic entry page, but protecting all wedding content requires a backend or moving to a host with server-side authentication. AWS remains an option; no hosting provider or paid service has been provisioned by this groundwork.
