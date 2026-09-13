import csv
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from guest_list import COLUMNS, REPO_ROOT, import_guests, private_path, read_guests


class GuestListTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.csv = self.folder / "list.csv"
        self.database = self.folder / "guests.sqlite3"

    def write_rows(self, rows):
        with self.csv.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(COLUMNS)
            writer.writerows(rows)

    def test_excel_csv_shared_email_and_unicode(self):
        self.write_rows([
            ["G001", "H001", "Renée", "Example, Jr.", " GUEST@EXAMPLE.COM ", "YES", "yes"],
            ["G002", "H001", "Partner", "Example", "guest@example.com", "no", "NO"],
        ])
        guests = read_guests(self.csv)
        self.assertEqual(guests[0][4], "guest@example.com")
        self.assertEqual(guests[0][3], "Example, Jr.")
        self.assertEqual(guests[0][6], 1)
        self.assertEqual(guests[1][5], 0)
        self.assertEqual(guests[1][6], 0)

    def test_extra_and_reordered_columns_are_tolerated(self):
        with self.csv.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(["last_name", "phone", *COLUMNS[:-1], "plus_one", "city"])
            writer.writerow(["Example", "918-555-0100", "G001", "H001", "Renée", "Example", "guest@example.com", "yes", "yes", "Springdale"])
        guests = read_guests(self.csv)
        self.assertEqual(guests, [("G001", "H001", "Renée", "Example", "guest@example.com", 1, 1)])
        with self.csv.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output)
            writer.writerow([column for column in COLUMNS if column != "plus_one"])
            writer.writerow(["G001", "H001", "Example", "Guest", "guest@example.com", "yes"])
        with self.assertRaisesRegex(ValueError, "plus_one"):
            read_guests(self.csv)

    def test_bad_lists_rejected(self):
        good = ["G001", "H001", "Example", "Guest", "guest@example.com", "yes", "no"]
        cases = [[], [good, good], [good, ["G002", "H002", "Other", "Guest", "guest@example.com", "yes", "no"]]]
        for column, bad_value in ((0, ""), (2, "=1+1"), (4, "not-an-email"), (5, "maybe"), (6, "maybe")):
            bad = good.copy()
            bad[column] = bad_value
            cases.append([bad])
        for rows in cases:
            with self.subTest(rows=rows):
                self.write_rows(rows)
                with self.assertRaises(ValueError):
                    read_guests(self.csv)

    def test_import_revokes_missing_and_updates_existing(self):
        initial = [("G001", "H001", "Example", "Guest", "guest@example.com", 1, 1),
                   ("G002", "H002", "Another", "Guest", None, 1, 0)]
        import_guests(initial, self.database)
        import_guests([("G001", "H001", "Updated", "Guest", "guest@example.com", 0, 0)], self.database)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT guest_id, access_approved FROM guests ORDER BY guest_id").fetchall(), [("G001", 0), ("G002", 0)])
            self.assertEqual(connection.execute("SELECT first_name FROM guests WHERE guest_id = 'G001'").fetchone()[0], "Updated")
            self.assertEqual(connection.execute("SELECT plus_one FROM guests WHERE guest_id = 'G001'").fetchone()[0], 0)

    def test_failed_import_rolls_back_revocation(self):
        guest = ("G001", "H001", "Example", "Guest", None, 1, 0)
        import_guests([guest], self.database)
        with self.assertRaises(sqlite3.IntegrityError):
            import_guests([("G002", "H002", "Bad", "Guest", None, 5, 0)], self.database)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT guest_id, access_approved FROM guests").fetchall(), [("G001", 1)])

    def test_migrates_database_created_before_plus_one_column(self):
        with closing(sqlite3.connect(self.database)) as connection:
            with connection:
                connection.execute("""CREATE TABLE guests (
                    guest_id TEXT PRIMARY KEY, household_id TEXT NOT NULL, first_name TEXT NOT NULL,
                    last_name TEXT NOT NULL, email TEXT, access_approved INTEGER NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
                connection.execute("INSERT INTO guests (guest_id, household_id, first_name, last_name, access_approved) VALUES ('G000', 'H000', 'Old', 'Guest', 1)")
        import_guests([("G001", "H001", "Example", "Guest", None, 1, 1)], self.database)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT plus_one FROM guests WHERE guest_id = 'G001'").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT access_approved FROM guests WHERE guest_id = 'G000'").fetchone()[0], 0)

    def test_private_files_cannot_live_in_website(self):
        with self.assertRaises(ValueError):
            private_path(REPO_ROOT / "private" / "guests.csv")


if __name__ == "__main__":
    unittest.main()
