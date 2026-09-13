from pathlib import Path
import tempfile
import unittest
from backend.contact_access import AccessDenied, authorize_submission, lookup_party
from scripts.guest_list import import_guests

class AccessTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.database = Path(self.folder.name) / "guests.sqlite3"
        self.rows = [
            ("G1", "H1", "Jordan", "Sample", None, 1),
            ("G2", "H1", "Alex", "Partner", None, 1),
            ("G3", "H2", "Solo", "Example", None, 1),
            ("G4", "H1", "Revoked", "Guest", None, 0),
        ]
        import_guests(self.rows, self.database)

    def test_initial_period_case_whitespace_and_named_plus_one(self):
        result = lookup_party(self.database, {"first_initial": " j. ", "last_name": " SAMPLE "})
        self.assertEqual(result, {"matched_guest_id": "G1", "members": [
            {"guest_id": "G1", "name": "Jordan Sample"}, {"guest_id": "G2", "name": "Alex Partner"}]})
        self.assertEqual(lookup_party(self.database, {"first_initial": "A", "last_name": "Partner"})["matched_guest_id"], "G2")

    def test_solo_guest_unknown_and_ambiguous(self):
        self.assertEqual(len(lookup_party(self.database, {"first_initial": "S", "last_name": "Example"})["members"]), 1)
        for identity in [{"first_initial": "R", "last_name": "Guest"}, {"first_initial": "X", "last_name": "Unknown"}]:
            with self.assertRaises(AccessDenied): lookup_party(self.database, identity)
        import_guests(self.rows + [("G5", "H5", "Jamie", "Sample", None, 1)], self.database)
        with self.assertRaisesRegex(AccessDenied, "More than one"):
            lookup_party(self.database, {"first_initial": "J", "last_name": "Sample"})

    def test_identity_validation_and_exact_surname(self):
        for identity in [{}, [], {"first_initial": "Jordan", "last_name": "Sample"}, {"first_initial": [], "last_name": "Sample"}]:
            with self.assertRaises(ValueError): lookup_party(self.database, identity)
        with self.assertRaises(AccessDenied): lookup_party(self.database, {"first_initial": "J", "last_name": "Sam"})

    def test_plus_one_authorization_and_revocation(self):
        payload = {"lookup": {"first_initial": "J", "last_name": "Sample"}, "guest_id": "G2", "name_line_one": "Alex Partner"}
        self.assertEqual(authorize_submission(self.database, payload), {"name_line_one": "Alex Partner"})
        for changes in [{"guest_id": "G3", "name_line_one": "Solo Example"}, {"name_line_one": "Someone Else"}]:
            with self.assertRaises(AccessDenied): authorize_submission(self.database, {**payload, **changes})
        import_guests([self.rows[0], self.rows[2]], self.database)
        with self.assertRaises(AccessDenied): authorize_submission(self.database, payload)
