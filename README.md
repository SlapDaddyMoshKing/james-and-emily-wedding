# Emily & James's Wedding Website

The site currently collects guest contact information before invitations go out. Guests first enter their first initial and last name. A shared, private Google Sheet -- edited directly by Emily and James, live, no publish step -- determines who is included on their invitation before contact fields appear. No account or RSVP is required.

**Website:** https://slapdaddymoshking.github.io/james-and-emily-wedding/

After matching, a guest fills out one form for their whole invitation: a title (optional), their first and last name (from the approved list, not editable), a suffix (optional), one mailing address, and -- only when their invitation includes a plus-one -- that guest's name or a "Guest Name Unknown" checkbox. The form writes one row to the nine columns in your Guest Tracker workbook: Name Line One, Name Line Two, Inner Envelope, Address Line One, Address Line Two, City, State, Zip Code, and Phone Number. Name Line One and Name Line Two are composed from the submitted names. The page keeps Emily's name first and the pastel blue, peach, green, and yellow palette.

## Where submissions go

GitHub Pages serves the static site from the root of `main`. `site-config.json` connects the form to the existing AWS API Gateway and Lambda in `us-east-2`. Lambda validates submissions and appends one guest row to `welcome/Guest Tracker.xlsx` in the private S3 bucket `wedding-site-guest-data-8f3d21`. It also keeps encrypted JSON submission receipts under `guest-info/`. Success appears only after the Excel write succeeds.

Guest details are never committed to GitHub or made available through a public read endpoint. Contact collection reads the shared Google Sheet live, on every lookup and submission, to enforce the invitation and plus-one list -- there is no separate database or publish step for this. It does not modify RSVP records. The old `welcome.html` link now redirects to the contact form. The previous RSVP templates and backend endpoints (a separate, older feature using a locally-published guest database) are retained for later work.

## Open the collected information

Open `welcome/Guest Tracker.xlsx` from the existing S3 bucket using your AWS account. The `GUEST ADDRESSING` sheet updates automatically as guests submit. You do not need to run a CSV export to update Excel.

### Optional CSV export

Install the Python tools once:

```powershell
python -m pip install -r requirements-dev.txt
```

Then export using the existing AWS CLI profile:

```powershell
python scripts/export_guest_info.py
```

This saves all submissions to `%LOCALAPPDATA%\WeddingSiteData\guest-contact-details.csv`, outside this public repository. See [contact collection guide](docs/guest-information.md) for corrections, privacy, deployment, and export details.

## Control invitations and plus-ones

Edit the shared Google Sheet directly -- a row with both `First Initial` and `Last Name` filled in is what invites that guest; `Plus One?` set to `Yes` offers them a plus-one. Takes effect on the very next lookup, no publish step. See [invitation setup](docs/guest-information.md#invitation-access-the-shared-google-sheet-is-the-live-guest-list) for the full column reference and one-time setup. (The CSV/`publish_guest_list.py` pipeline still exists, but only controls the separate, archived RSVP feature -- not this form.)

## See submissions as they arrive

Run `python scripts/owner_dashboard.py` to open a private, local-only viewer that reads the live Guest Tracker workbook from S3, auto-refreshing every 20 seconds, with a one-click "Download latest Excel" link. It requires the `wedding-site` AWS CLI profile and `python -m pip install -r requirements-dev.txt`.

## Preview and test

```powershell
python -m backend.server
```

Visit http://127.0.0.1:8080. Local submissions save to `%LOCALAPPDATA%\WeddingSiteData\guest-info-local\`; they do not reach AWS. This local server checks a local `guests.sqlite3` for invitations, not the live Google Sheet (so it works offline) -- see [local development](docs/guest-information.md#local-development). Opening HTML directly from disk cannot submit the form.

```powershell
python -m unittest discover -s tests -v
python -m playwright install chromium
python tests/browser_guest_info.py
```

The browser checks start their own isolated server with fictional guests and clean up temporary data. Review screenshots stay outside the repository.

## Publish

Commit reviewed changes and push to `main`; GitHub Pages publishes the update. Backend changes must also be deployed to Lambda; see the [deployment instructions](docs/guest-information.md). A custom domain can be connected to GitHub Pages later, with its origin added to API Gateway CORS.

The page requests no search indexing, but its URL is public. Only the submitted contact records stay private. Legacy guest-list tools are documented in [the archived guest-list guide](docs/guest-list.md).
