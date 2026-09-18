# Guest Tracker integration

After the invitation lookup, the contact form matches `s3://wedding-site-guest-data-8f3d21/welcome/Guest Tracker.xlsx`, sheet **GUEST ADDRESSING**. Each submitted form adds exactly one guest row: one envelope, one mailing address. The form collects a title (optional), first and last name (from the invitation, not editable), a suffix (optional), and one address. If the matched guest's plus-one flag is set, the form also offers a plus-one name (title/first/last/suffix, all optional except when named) or a "Guest Name Unknown" checkbox for when the guest hasn't decided who they're bringing yet. The submitted name and plus-one name are combined into "Name Line One" / "Name Line Two" ("and Guest" when unknown) when the row is written to the workbook. Address line one, city, state/region, and ZIP/postal code are required; address line two and phone are optional. There is no per-guest address -- a household with a plus-one still shares one address.

Next to the phone field, an unchecked `sms_consent` checkbox (A2P 10DLC-compliant wording, with SMS Terms and SMS Privacy text always visible on the same page, not just linked) lets a guest opt in to *future* RSVP-reminder/update texts -- separate from, and never required for, submitting the form itself, sharing an address, or the phone field being filled in. It's recorded on the submission (JSON receipt and CSV export) as `sms_consent: true`/`false`; nothing currently reads it to decide who to text -- see "Texting guests an invitation link" below, which is a separate, one-time outreach to guests who haven't visited yet, sent before any consent could exist.

`sms-terms.html` is a standalone, ungated page reproducing the same checkbox copy plus the full SMS Terms, SMS Privacy Policy, and a step-by-step opt-in description -- linked from this page's own SMS Terms/Privacy text and from the site footer. It exists because a Twilio A2P 10DLC campaign reviewer can't pass the invitation name-lookup gate, so anything shown only inside `#form-content` is unverifiable to them (Twilio errors 30909/30921); this page needs no lookup, no JavaScript, and no match to view. When submitting or resubmitting the campaign in the Twilio Console, use `https://slapdaddymoshking.github.io/james-and-emily-wedding/sms-terms.html` (with `#terms`/`#privacy` anchors) as the opt-in method, Terms and Conditions, and Privacy Policy URLs -- not the root URL, which still requires a name match to reach any SMS-related content.

The workbook's nine columns and existing guest data are preserved. Styled empty rows are used after the last row containing data, rather than skipping hundreds of template rows. ZIP codes and phone numbers are stored as text. All submitted values are literal text, never spreadsheet formulas. Names are not used to deduplicate different guests.

## Invitation access: the shared Google Sheet is the live guest list

`POST /contact-party` and `POST /guest-info` are answered by reading the shared Google Sheet directly, live, on every request -- there is no separate publish step and no local database for this flow. Approval is **presence-based**: a row with both `First Initial` and `Last Name` filled in is what makes that guest approved. There is no separate approved/not-approved column -- adding a row invites someone, and editing or removing one takes effect on their very next lookup attempt. This is deliberately consistent with the rest of this project's "always recheck the current source" trust model (see [guest-list.md](guest-list.md)).

A guest's own row can already hold information the couple knows ahead of time -- an address, a phone number, a plus-one's name -- and the lookup returns that under `prefill` so the contact form can offer it back to the guest to confirm or correct, instead of asking them to type it from scratch. It reads:

| Sheet column | Used for |
| --- | --- |
| `First Initial`, `Last Name` | Matching only (case/whitespace-insensitive, an optional trailing period on the initial). Presence of a matching row is what approves the guest. |
| `Guest First Name`, `Guest Last Name` | The name shown back to the guest on the form. Falls back to the sheet's own `First Initial`/`Last Name` cells if left blank (so an older mailing-list row with only an initial still works, just less nicely). |
| `Plus One?` | `Yes` (case-insensitive) offers the plus-one section on the form; anything else hides it and rejects any plus-one fields submitted. |
| `Plus One First Name`, `Plus One Last Name` | Prefills the plus-one section. The literal value `Unknown` in either pre-checks "Guest Name Unknown" instead. |
| `Phone Number`, `Address Line One`, `Address Line Two`, `City`, `State`, `Zip Code` | Prefills the matching form field. Also used to text an invitation link -- see below. |
| `Send Text?` | `Yes` opts that row in to the scheduled invitation text (see below); blank/anything else means never text them. |
| `Carrier` | Required for the scheduled invitation text (see below) -- one of the major US carriers/MVNOs; blank or unrecognized means never text them, same as a missing phone number. |
| `Email Address` | Never read or written by the site. |

Column names are matched case/whitespace-insensitively, and in any order -- other columns (your own notes, phone, whatever) are ignored. Bypassing the browser lookup or renaming the guest doesn't grant another invitation or unlock a plus-one that isn't on their row: `POST /guest-info` re-reads the sheet itself before accepting a submission, rechecking the name match and the plus-one flag exactly as the lookup did.

**This is not email verification or a login session** -- approval is a name match, as requested for this project.

### Best-effort: submissions write back to the same row

Once a submission is authorized and safely saved to the private Excel tracker above (still the authoritative, atomic record -- see "Reliable saves" below), it also updates that guest's row in the sheet with `Guest First Name`, `Guest Last Name`, `Plus One First Name`/`Last Name` (or `Unknown`), `Phone Number`, and the address columns -- writing only the columns it has data for, never touching `Email Address` or anything else. This direction is best-effort only: a missing row, a renamed column, or an expired credential is logged to CloudWatch and never fails or blocks the guest's submission, since the workbook write has already succeeded by that point.

### Setup

1. In [Google Cloud Console](https://console.cloud.google.com/), enable the **Google Sheets API** and create a **service account**; download its JSON key.
2. Share the Google Sheet with that service account's email (looks like `xxx@yyy.iam.gserviceaccount.com`) as an **Editor**.
3. Upload the key to the private bucket: `aws s3 cp service-account.json s3://wedding-site-guest-data-8f3d21/google-service-account.json --profile wedding-site --sse AES256`.
4. Set two environment variables on the `wedding-lookup` Lambda: `GOOGLE_SHEET_ID` (from the sheet's URL, the long ID between `/d/` and `/edit`) and `GOOGLE_SHEET_GID` (the number after `gid=` in the URL -- each tab has its own).

No redeploy is needed to change the key, sheet, or tab afterward -- it's controlled entirely by those two environment variables and the S3 key. Without them configured, `/contact-party` and `/guest-info` respond "temporarily unavailable" rather than falling back to anything else.

### Texting guests an invitation link (backend/guest_texts.py)

A scheduled, daily check (not tied to any guest visiting the site) can text a link to the site to guests who haven't filled in their details yet. This is sent via each carrier's **email-to-SMS gateway** (e.g. a text to a Verizon number is really an email to `<10digits>@vtext.com`), through a Gmail account -- deliberately not a paid SMS API like Twilio, to avoid the US carrier-mandated A2P 10DLC / toll-free verification process required for automated bulk SMS through any such provider (this is a carrier rule, not specific to one vendor -- switching providers doesn't avoid it). The tradeoff: no delivery receipts, and no automatic opt-out handling -- see below.

Nothing is sent unless **all** of these are true for a given row:

- `Send Text?` is `Yes` -- blank or anything else means never text that guest, so nothing goes out until you set this per row (fill it down for everyone at once when ready).
- The `Phone Number` column has a valid US number (10 digits, or 11 starting with a leading 1 -- other formats are skipped, not guessed at).
- The `Carrier` column matches one this recognizes (see `CARRIER_GATEWAYS` in `backend/guest_texts.py` -- the major US carriers and a few common MVNOs; an unrecognized or blank carrier is skipped, never guessed at).
- That guest hasn't yet completed the site's name lookup. This is tracked privately in S3 (`sms/<hash of first initial + last name>.json`), not as a sheet column, so a plain page load doesn't cost a Sheets API write -- recorded the moment someone successfully looks themselves up, before they even reach the address form.
- They haven't been texted in the last 21 days (or ever).

The message: *"Hi \[first name\]! It's Emily & James's wedding site -- please share your mailing address here so we can send you an invitation: \[link\]. Reply STOP to opt out."* -- the name comes from `Guest First Name` (or the `First Initial` if that's blank).

**About "Reply STOP":** because this goes out as email, a guest's reply lands back in the sending Gmail inbox as a normal email reply -- there's nothing in this codebase reading that inbox automatically. If a guest asks to stop, the manual fix is to set their row's `Send Text?` to `No` (or blank) yourself; check the Gmail inbox occasionally if you're relying on the STOP line.

**Setup** (in addition to the Google Sheet setup above):

1. In the Google Account that will send these (a dedicated one is fine, doesn't need to be the wedding Gmail used elsewhere): turn on 2-Step Verification, then create an [App Password](https://myaccount.google.com/apppasswords) (Google Account > Security > 2-Step Verification > App passwords). Regular account passwords don't work for SMTP.
2. Upload the credentials as one JSON file: `aws s3 cp gmail-credentials.json s3://wedding-site-guest-data-8f3d21/gmail-credentials.json --profile wedding-site --sse AES256`, where the file is `{"email": "you@gmail.com", "app_password": "xxxx xxxx xxxx xxxx"}`.
3. Create a daily schedule that invokes the `wedding-lookup` Lambda with the JSON input `{"task": "send-invitation-texts"}` -- e.g. with [EventBridge Scheduler](https://docs.aws.amazon.com/scheduler/latest/UserGuide/getting-started.html): `aws scheduler create-schedule --name wedding-invitation-texts --schedule-expression "rate(1 day)" --target "{\"Arn\":\"<wedding-lookup function ARN>\",\"RoleArn\":\"<a role EventBridge Scheduler can assume to invoke it>\",\"Input\":\"{\\\"task\\\":\\\"send-invitation-texts\\\"}\"}" --flexible-time-window "{\"Mode\":\"OFF\"}" --profile wedding-site --region us-east-2` (the invoked role needs `lambda:InvokeFunction` on `wedding-lookup`).

This can safely be deployed and scheduled ahead of time: with no Gmail credentials in S3, or no row marked `Send Text?`, the scheduled run does nothing but log that it skipped. Invoking the Lambda directly with `{"task": "send-invitation-texts"}` (e.g. from the AWS Console's Test feature) runs it on demand instead of waiting for the schedule.

Gmail's own sending limits apply (roughly 500 recipients/day for a regular account) -- comfortably enough for a wedding guest list, but worth knowing if this account is used for other bulk mail too.

### Editing the list

Either of you can edit the sheet directly, from any device -- share it with each other's Google account (Share button, Editor access) and there's nothing else to set up for that. There's no CSV, no publish command, and no "whoever has the laptop" step for this flow: an edit is live on the very next lookup.

## Reliable saves

After rechecking invitation access, `POST /guest-info` retains an encrypted JSON receipt, then updates the workbook. It returns success only after the workbook is saved. If the workbook write fails, submitting the unchanged form retries it using the same reference.

A hidden `_WebsiteSubmissions` sheet stores references, payload hashes, and guest row numbers. It is saved atomically with the new row, preventing a timeout or retry from adding the same submission twice. Keep this hidden sheet when editing the workbook. Different submissions receive separate rows, even with identical names. Contact Emily or James for corrections rather than resubmitting a completed form.

S3 `If-Match` checks the workbook ETag on each write. A simultaneous submission causes a reload and retry instead of overwriting the other guest. After four conflicts the API asks the guest to retry. See [AWS conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html).

The Lambda role needs `s3:GetObject` for the Google service account key and the Gmail credentials file, plus `s3:GetObject` and `s3:PutObject` for `welcome/Guest Tracker.xlsx`, `guest-info/*`, and `sms/*` (visit/text tracking). The workbook remains private; it is not fetched by the browser. The existing GitHub Pages origin, API Gateway route, and rate limits remain in use. A private original workbook backup was saved under the bucket's `backups/` prefix before deployment.

When editing the workbook manually, download the latest version and avoid uploading an older copy over incoming guest submissions. Preserve the column headers and hidden reference sheet.

## Access and exports

Open/download `welcome/Guest Tracker.xlsx` in the S3 console using your AWS account, or run `python scripts/owner_dashboard.py` for a private local live view with a one-click download. This is the authoritative record of every submission; no manual export is needed. `python scripts/export_guest_info.py` remains an optional CSV of JSON receipts, which can include a receipt whose workbook update is still awaiting a guest retry. Test receipts marked by authenticated AWS access are excluded. The CSV is not the authoritative workbook.

## Local development

Run `python -m pip install -r requirements-dev.txt`, then `python -m backend.server`. Visit http://127.0.0.1:8080. **The local dev server is intentionally unaffected by the Google Sheet above** -- it still checks a local `guests.sqlite3`, published the older way (see below), so it can be tested offline without live Google credentials. Local form submissions save JSON only in `%LOCALAPPDATA%/WeddingSiteData/guest-info-local`; they do not change the AWS workbook, the hosted Lambda, or the Google Sheet. Workbook tests use isolated in-memory Excel files.

To control who the local server treats as invited, edit `%LOCALAPPDATA%/WeddingSiteData/guest-list.csv` with columns `guest_id,household_id,first_name,last_name,email,access_approved,plus_one` (`read_guests` only requires these columns be *present* by name; extra columns and any order are fine), then run:

```powershell
python scripts/guest_list.py "$env:LOCALAPPDATA\WeddingSiteData\guest-list.csv" --import
```

This is separate from `publish_guest_list.py`, which uploads to S3 for the **archived RSVP feature** (`/lookup`, `/party`, `/rsvp` -- see [guest-list.md](guest-list.md)), not for the hosted contact form.

Run `python -m unittest discover -s tests -v`. Browser checks: `python -m playwright install chromium`, then `python tests/browser_guest_info.py`; this starts its own isolated server and fictional list.

## Deploy

1. Run the tests.
2. Run `python scripts/build_lambda.py`. This packages Python source plus its dependencies (openpyxl, and the pure-Python `google-auth`/`rsa`/`pyasn1` stack used for the Sheets API -- deliberately no `cryptography`, since this is built with `pip install --target` on the developer's machine without cross-platform flags, and a compiled extension built there wouldn't run on Lambda's Linux runtime) outside the public repo. The Lambda runtime provides boto3.
3. Deploy the printed ZIP path with `aws lambda update-function-code --function-name wedding-lookup --zip-file fileb://PATH-TO-ZIP --profile wedding-site --region us-east-2`.
4. A fresh Lambda needs `GUEST_DATA_BUCKET`, `GOOGLE_SHEET_ID`, and `GOOGLE_SHEET_GID` set (see Setup above); redeploying code doesn't touch existing environment variables.
5. Ensure `POST /contact-party` is routed to the existing Lambda integration on API `jfjd4a92w5`. Test an approved submission and identical retry, verify exactly one workbook row, then remove only the test row with an ETag-conditional update. Retain real rows. Mark the JSON receipt as test data if delete permission is unavailable.
6. Commit reviewed frontend changes and push to `main` for GitHub Pages.

Do not package only backend source: openpyxl must be included. Changing the workbook columns requires updating the form and `backend/guest_tracker.py`; unexpected headers fail safely instead of writing into the wrong columns.
