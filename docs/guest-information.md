# Guest Tracker integration

After the invitation lookup, the contact form matches `s3://wedding-site-guest-data-8f3d21/welcome/Guest Tracker.xlsx`, sheet **GUEST ADDRESSING**. Each submitted form adds exactly one guest row: one envelope, one mailing address. The form collects a title (optional), first and last name (from the invitation, not editable), a suffix (optional), and one address. If the matched guest's `plus_one` flag is set, the form also offers a plus-one name (title/first/last/suffix, all optional except when named) or a "Guest Name Unknown" checkbox for when the guest hasn't decided who they're bringing yet. The submitted name and plus-one name are combined into "Name Line One" / "Name Line Two" ("and Guest" when unknown) when the row is written to the workbook. Address line one, city, state/region, and ZIP/postal code are required; address line two and phone are optional. There is no per-guest address -- a household with a plus-one still shares one address.

The workbook's nine columns and existing guest data are preserved. Styled empty rows are used after the last row containing data, rather than skipping hundreds of template rows. ZIP codes and phone numbers are stored as text. All submitted values are literal text, never spreadsheet formulas. Names are not used to deduplicate different guests.

## Invitation access

The entry page shows only first initial and last name. `POST /contact-party` matches an approved guest in the private `guests.sqlite3`, ignoring case, extra whitespace, and an optional period after the initial. Surnames match exactly after normalization. Unknown, revoked, or ambiguous initial/surname matches do not reveal any party or open the contact form.

The matched guest's own row determines the invitation: their name and their `plus_one` flag. Unapproved rows never match. There is no free-form add-guest field; a plus-one's name (when given) is only ever collected on the form, not looked up in the guest list.

`POST /guest-info` requires `lookup: {first_initial, last_name}` plus the submitted `first_name`/`last_name`, in addition to the contact fields. The server reloads the current guest database, rechecks the name match, and rejects any plus-one fields unless that guest's `plus_one` flag is set. Bypassing the browser lookup or renaming the guest does not grant another invitation or unlock a plus-one. Each invitation gets one Excel row and one submission reference. Approval is based on a name match as requested; this is not email verification or a login session.

To control the list, edit `%LOCALAPPDATA%/WeddingSiteData/guest-list.csv` with columns `guest_id,household_id,first_name,last_name,email,access_approved,plus_one`. Use permanent guest IDs, shared household IDs only if you also use the archived RSVP tooling (see [guest-list.md](guest-list.md)), and `yes`/`no` for both `access_approved` and `plus_one`. Then run:

```powershell
python scripts/publish_guest_list.py "$env:LOCALAPPDATA\WeddingSiteData\guest-list.csv"
```

The importer publishes the complete list, not incremental additions. Do not put real guest lists in GitHub. Updating the contact tracker does not approve guests; invitation permissions come from this separate private list. The example invitation is already configured in the private database; guest names and IDs are not embedded in the public frontend.

### Editing the list together

Since only one person's machine runs the publish command, keep the actual editing in a shared spreadsheet (a Google Sheet or an Excel Online file, shared privately between the two of you -- not the OneDrive folder this repo lives in). An existing sheet can be reused as-is: `read_guests` only requires the columns above to be *present* by name (`guest_id`, `household_id`, `first_name`, `last_name`, `email`, `access_approved`, `plus_one`); extra columns (phone, address, city...) and any column order are fine and are ignored on import. Add whichever of the required columns are missing directly to that sheet.

One gap is common when adapting an older mailing list: the contact form shows guests their own name pulled from `first_name`, so it needs their actual first name, not just an initial (a "First Initial" column used for an older paper mailing process isn't enough on its own -- add a real `first_name` column alongside it). `access_approved` and `guest_id`/`household_id` are also usually missing from a pre-existing list and need adding (see the column table above for what each holds).

When it's ready to go live, whoever has the AWS CLI set up downloads that sheet as CSV (File > Download > Comma Separated Values), saves it over `%LOCALAPPDATA%/WeddingSiteData/guest-list.csv`, and runs the publish command above. The shared sheet is the working copy; the CSV on disk is only a temporary export used to publish.

## Reliable saves

After rechecking invitation access, `POST /guest-info` retains an encrypted JSON receipt, then updates the workbook. It returns success only after the workbook is saved. If the workbook write fails, submitting the unchanged form retries it using the same reference.

A hidden `_WebsiteSubmissions` sheet stores references, payload hashes, and guest row numbers. It is saved atomically with the new row, preventing a timeout or retry from adding the same submission twice. Keep this hidden sheet when editing the workbook. Different submissions receive separate rows, even with identical names. Contact Emily or James for corrections rather than resubmitting a completed form.

S3 `If-Match` checks the workbook ETag on each write. A simultaneous submission causes a reload and retry instead of overwriting the other guest. After four conflicts the API asks the guest to retry. See [AWS conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html).

The Lambda role needs `s3:GetObject` for `guests.sqlite3`, plus `s3:GetObject` and `s3:PutObject` for `welcome/Guest Tracker.xlsx` and `guest-info/*`. The workbook remains private; it is not fetched by the browser. The existing GitHub Pages origin, API Gateway route, and rate limits remain in use. A private original workbook backup was saved under the bucket's `backups/` prefix before deployment.

When editing the workbook manually, download the latest version and avoid uploading an older copy over incoming guest submissions. Preserve the column headers and hidden reference sheet.

## Access and exports

Open/download `welcome/Guest Tracker.xlsx` in the S3 console using your AWS account. This is the live guest list; no manual export is needed. `python scripts/export_guest_info.py` remains an optional CSV of JSON receipts, which can include a receipt whose workbook update is still awaiting a guest retry. Test receipts marked by authenticated AWS access are excluded. The CSV is not the authoritative workbook.

## Local development

Run `python -m pip install -r requirements-dev.txt`, then `python -m backend.server`. Visit http://127.0.0.1:8080. Local form submissions save JSON only in `%LOCALAPPDATA%/WeddingSiteData/guest-info-local`; they do not change the AWS workbook. Workbook tests use isolated in-memory Excel files.

The local server requires the imported approved guest database for lookup. Run `python -m unittest discover -s tests -v`. Browser checks: `python -m playwright install chromium`, then `python tests/browser_guest_info.py`; this starts its own isolated server and fictional list.

## Deploy

1. Run the tests.
2. Run `python scripts/build_lambda.py`. This packages Python source plus openpyxl and its dependency outside the public repo. The Lambda runtime provides boto3.
3. Deploy the printed ZIP path with `aws lambda update-function-code --function-name wedding-lookup --zip-file fileb://PATH-TO-ZIP --profile wedding-site --region us-east-2`.
4. Ensure `POST /contact-party` is routed to the existing Lambda integration on API `jfjd4a92w5`. Test an approved submission and identical retry, verify exactly one workbook row, then remove only the test row with an ETag-conditional update. Retain real rows. Mark the JSON receipt as test data if delete permission is unavailable.
5. Commit reviewed frontend changes and push to `main` for GitHub Pages.

Do not package only backend source: openpyxl must be included. Changing the workbook columns requires updating the form and `backend/guest_tracker.py`; unexpected headers fail safely instead of writing into the wrong columns.
