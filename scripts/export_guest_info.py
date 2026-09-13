"""Export collected S3 contact details to a private CSV. Requires boto3 and AWS profile wedding-site."""
import argparse
import csv
import json
from pathlib import Path
import sys

# Support both python -m scripts.export_guest_info and python scripts/export_guest_info.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.guest_list import DATA_DIR, private_path
from backend.guest_info import FIELDS

BUCKET = "wedding-site-guest-data-8f3d21"
COLUMNS = ("submission_id", "submitted_at", *FIELDS)

def spreadsheet_cell(value):
    """Keep user-entered text from becoming a spreadsheet formula."""
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value

def export_contacts(s3, output, bucket=BUCKET):
    output = private_path(output)
    rows = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="guest-info/"):
        for item in page.get("Contents", []):
            if item["Key"].endswith(".json"):
                record = json.loads(s3.get_object(Bucket=bucket, Key=item["Key"])["Body"].read())
                # Only an AWS administrator/deployer can mark a test: the public
                # submission validator rejects this field.
                if record.get("test_record") is True:
                    continue
                rows.append({field: spreadsheet_cell(record.get(field, "")) for field in COLUMNS})
    rows.sort(key=lambda row: (row["submitted_at"], row["submission_id"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".csv.tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(output)
    return len(rows)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DATA_DIR / "guest-contact-details.csv")
    parser.add_argument("--profile", default="wedding-site")
    args = parser.parse_args()
    try:
        import boto3
        s3 = boto3.Session(profile_name=args.profile, region_name="us-east-2").client("s3")
        count = export_contacts(s3, args.output)
        print(f"Exported {count} submissions to {private_path(args.output)}")
    except Exception as error:
        print(f"Export failed: {error}", file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
