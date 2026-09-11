"""Import the private guest CSV and push it straight to the hosted lookup.

One command instead of two: runs the same validated --import as
guest_list.py, then uploads the resulting database to the S3 bucket the
AWS Lambda lookup reads from. Requires the AWS CLI and the local
"wedding-site-deploy" profile (see docs/guest-list.md).

    python scripts/publish_guest_list.py "$env:LOCALAPPDATA\\WeddingSiteData\\guest-list.csv"
"""

import argparse
from pathlib import Path
import subprocess
import sys

from guest_list import DATA_DIR, import_guests, private_path, read_guests

BUCKET = "wedding-site-guest-data-8f3d21"
REGION = "us-east-2"
PROFILE = "wedding-site"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("--database", type=Path, default=DATA_DIR / "guests.sqlite3")
    args = parser.parse_args()
    try:
        source = private_path(args.csv_file)
        database = private_path(args.database)
        guests = read_guests(source)
        approved = sum(guest[5] for guest in guests)
        print(f"Valid list: {len(guests)} guests; {approved} approved.")
        import_guests(guests, database)
        print("Local database updated. Guests omitted from this list are now unapproved.")
    except Exception as error:
        print(f"Guest list error: {error}", file=sys.stderr)
        return 1

    result = subprocess.run([
        "aws", "s3", "cp", str(database), f"s3://{BUCKET}/guests.sqlite3",
        "--profile", PROFILE, "--region", REGION, "--sse", "AES256",
    ])
    if result.returncode != 0:
        print("Local database updated, but the upload to AWS failed -- the hosted "
              "lookup still has the previous list. Re-run this script once fixed.", file=sys.stderr)
        return 1
    print("Published: the hosted lookup now reflects this guest list.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
