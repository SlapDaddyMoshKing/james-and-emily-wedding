# Emily & James's Wedding Website

The site currently collects guest contact information before invitations go out. Guests open the link directly; no preloaded guest list, invitation check, account, or RSVP is required.

**Website:** https://slapdaddymoshking.github.io/james-and-emily-wedding/

The form collects first initial, last name, email, and mailing address, plus optional phone and other household members. US addresses require a state and ZIP; international addresses can omit a region or postal code where not applicable. The page keeps Emily's name first and the pastel blue, peach, green, and yellow palette.

## Where submissions go

GitHub Pages serves the static site from the root of `main`. `site-config.json` connects the form to the existing AWS API Gateway and Lambda in `us-east-2`. Lambda validates submissions and saves encrypted JSON objects under `guest-info/<random-reference>.json` in the private S3 bucket `wedding-site-guest-data-8f3d21`.

Guest details are never committed to GitHub or made available through a public read endpoint. Contact collection does not read or modify the existing guest database or RSVP records. The old `welcome.html` link now redirects to the contact form. The previous RSVP templates and backend endpoints are retained for later work.

## Download the collected information

Install the Python tools once:

```powershell
python -m pip install -r requirements-dev.txt
```

Then export using the existing AWS CLI profile:

```powershell
python scripts/export_guest_info.py
```

This saves all submissions to `%LOCALAPPDATA%\WeddingSiteData\guest-contact-details.csv`, outside this public repository. See [contact collection guide](docs/guest-information.md) for corrections, privacy, deployment, and export details.

## Preview and test

```powershell
python -m backend.server
```

Visit http://127.0.0.1:8080. Local submissions save to `%LOCALAPPDATA%\WeddingSiteData\guest-info-local\`; they do not reach AWS. Use this server for a complete local walkthrough. Opening HTML directly from disk cannot submit the form.

```powershell
python -m unittest discover -s tests -v
python -m playwright install chromium
python tests/browser_guest_info.py
```

The browser checks require the local server above. They use fictional data, remove their own test submission, and save review screenshots outside the repository.

## Publish

Commit reviewed changes and push to `main`; GitHub Pages publishes the update. Backend changes must also be deployed to Lambda; see the [deployment instructions](docs/guest-information.md). A custom domain can be connected to GitHub Pages later, with its origin added to API Gateway CORS.

The page requests no search indexing, but its URL is public. Only the submitted contact records stay private. Legacy guest-list tools are documented in [the archived guest-list guide](docs/guest-list.md).
