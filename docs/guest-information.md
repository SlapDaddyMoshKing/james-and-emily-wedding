# Guest Tracker integration

The public form matches `s3://wedding-site-guest-data-8f3d21/welcome/Guest Tracker.xlsx`, sheet **GUEST ADDRESSING**. Each submitted form adds a separate guest row. Name line one, address line one, city, state/region, and ZIP/postal code are required. Name line two, inner envelope, address line two, and phone are optional. Guests submit another form for another person; additional name lines describe the same guest's envelope addressing.

The workbook's nine columns and existing guest data are preserved. Styled empty rows are used after the last row containing data, rather than skipping hundreds of template rows. ZIP codes and phone numbers are stored as text. All submitted values are literal text, never spreadsheet formulas. Names are not used to deduplicate different guests.

## Reliable saves

`POST /guest-info` retains an encrypted JSON receipt, then updates the workbook. It returns success only after the workbook is saved. If the workbook write fails, submitting the unchanged form retries it using the same reference.

A hidden `_WebsiteSubmissions` sheet stores references, payload hashes, and guest row numbers. It is saved atomically with the new row, preventing a timeout or retry from adding the same submission twice. Keep this hidden sheet when editing the workbook. Different submissions receive separate rows, even with identical names. Contact Emily or James for corrections rather than resubmitting a completed form.

S3 `If-Match` checks the workbook ETag on each write. A simultaneous submission causes a reload and retry instead of overwriting the other guest. After four conflicts the API asks the guest to retry. See [AWS conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html).

The Lambda role needs `s3:GetObject` and `s3:PutObject` for `welcome/Guest Tracker.xlsx` and `guest-info/*`. The workbook remains private; it is not fetched by the browser. The existing GitHub Pages origin, API Gateway route, and rate limits remain in use. A private original workbook backup was saved under the bucket's `backups/` prefix before deployment.

When editing the workbook manually, download the latest version and avoid uploading an older copy over incoming guest submissions. Preserve the column headers and hidden reference sheet.

## Access and exports

Open/download `welcome/Guest Tracker.xlsx` in the S3 console using your AWS account. This is the live guest list; no manual export is needed. `python scripts/export_guest_info.py` remains an optional CSV of JSON receipts, which can include a receipt whose workbook update is still awaiting a guest retry. Test receipts marked by authenticated AWS access are excluded. The CSV is not the authoritative workbook.

## Local development

Run `python -m pip install -r requirements-dev.txt`, then `python -m backend.server`. Visit http://127.0.0.1:8080. Local form submissions save JSON only in `%LOCALAPPDATA%/WeddingSiteData/guest-info-local`; they do not change the AWS workbook. Workbook tests use isolated in-memory Excel files.

Run `python -m unittest discover -s tests -v`. Browser checks: `python -m playwright install chromium`, then `python tests/browser_guest_info.py` with the local server running.

## Deploy

1. Run the tests.
2. Run `python scripts/build_lambda.py`. This packages Python source plus openpyxl and its dependency outside the public repo. The Lambda runtime provides boto3.
3. Deploy the printed ZIP path with `aws lambda update-function-code --function-name wedding-lookup --zip-file fileb://PATH-TO-ZIP --profile wedding-site --region us-east-2`.
4. Test a fictional submission and identical retry, verify exactly one workbook row, then remove only the test row with an ETag-conditional update. Retain real rows. Mark the JSON receipt as test data if delete permission is unavailable.
5. Commit reviewed frontend changes and push to `main` for GitHub Pages.

Do not package only backend source: openpyxl must be included. Changing the workbook columns requires updating the form and `backend/guest_tracker.py`; unexpected headers fail safely instead of writing into the wrong columns.
