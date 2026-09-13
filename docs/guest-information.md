# Guest contact collection

## Current flow

Share the GitHub Pages root URL directly. Guests submit one form per household with first initial, last name, email, mailing address, and optional phone and other household members. This gathers contact information; it does not confirm attendance or automatically approve an invitation.

A success message appears only after AWS confirms the write. Failed requests keep the form entries. Retrying unchanged data reuses a random UUID and S3's conditional write prevents duplicate writes or replacement of an existing submission. Changing entries creates a new reference. References and entries are held only in page memory, not localStorage, cookies, or URLs.

## Storage and access

- API: `POST https://jfjd4a92w5.execute-api.us-east-2.amazonaws.com/guest-info`.
- Lambda: `wedding-lookup`, handler `backend.lambda_handler.handler`.
- Bucket: `wedding-site-guest-data-8f3d21`, prefix `guest-info/`.
- Each encrypted JSON object includes the form fields, a random `submission_id`, `schema_version`, and UTC `submitted_at`.
- The endpoint only accepts writes. It exposes no search, listing, or contact-reading interface.
- No existing guest-list match is needed. Existing `guests.sqlite3` and `rsvps/` objects are untouched.
- API Gateway's existing throttle and Lambda's per-container per-IP rate limiter apply. The form also has a hidden spam field. These are basic spam controls; anyone who knows the link can submit.
- CORS permits the existing GitHub Pages origin. CORS does not authenticate guests.

The Lambda execution role needs `s3:PutObject` and `s3:GetObject` for `arn:aws:s3:::wedding-site-guest-data-8f3d21/guest-info/*`. Reading is needed only to verify an identical retry. The bucket stays private with public access blocked.

Conditional writes use [S3 If-None-Match](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html). A different payload using an existing reference returns 409 and cannot overwrite it.

## Export for invitations

Run from the repository:

```powershell
python scripts/export_guest_info.py
```

Default output: `%LOCALAPPDATA%\WeddingSiteData\guest-contact-details.csv`. An optional `--output` must point outside the repository. The export uses profile `wedding-site`, handles all S3 pages, sorts records from oldest to newest, and replaces the export only after all reads succeed. It does not change records in AWS.

For Excel, use **Data > From Text/CSV** and treat postal codes and phone numbers as text so leading zeros remain intact. Cells that could be interpreted as formulas receive a leading apostrophe.

Every submission is retained, including corrections. Review newer rows with the same email/name when preparing the invitation list; initials and last names are not unique, so the export deliberately does not merge people automatically. If a guest needs a correction, they can resubmit or contact Emily or James. This CSV is a contact collection export, not the older guest-list importer format.

## Local development

`python -m backend.server` serves the form and a local equivalent of the endpoint without needing a guest database or AWS credentials. Records go into `%LOCALAPPDATA%\WeddingSiteData\guest-info-local`. The development server never serves that directory. Use the default server mode for contact collection; older private-preview tools are for the previous design.

## Deployment

The frontend is plain HTML, CSS, and JavaScript with relative paths for GitHub Pages project hosting. Deploy Lambda before publishing a frontend that depends on a new API route.

1. Run `python -m unittest discover -s tests -v`.
2. Package the Python source files in `backend/` and `scripts/` with those directory names at the ZIP root. Do not include guest data, caches, credentials, or tests. The Python 3.12 Lambda runtime supplies boto3.
3. Run `aws lambda update-function-code --function-name wedding-lookup --zip-file fileb://PATH-TO-ZIP --profile wedding-site --region us-east-2`.
4. The HTTP API `jfjd4a92w5` must have `POST /guest-info` targeting existing integration `kqhhkdl`. Its default stage auto-deploys. The existing Lambda invocation permission covers this route.
5. Check a fictional submission end to end, verify the S3 object, and remove only that test object. If the deployment profile lacks delete permission, mark that object with `"test_record": true` using authenticated S3 access; exports exclude it. The public endpoint rejects this field.
6. Commit reviewed site changes and push to `main`.

When adding a domain, configure it in GitHub Pages and add the exact HTTPS origin to API Gateway CORS. No change to S3 contact storage is needed.

## Previous wedding pages

`welcome.html` now redirects to the contact form so old links work. `templates/welcome.html`, `welcome.css`, `welcome.js`, and the older APIs remain as groundwork for the later invitation/RSVP phase. Running `scripts/publish_welcome_page.py` would restore the old public RSVP page; do not run it for the contact collection phase.
