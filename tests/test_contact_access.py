from pathlib import Path
import tempfile
import unittest
from backend.contact_access import AccessDenied, authorize_submission, lookup_guest
from scripts.guest_list import import_guests

class AccessTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.database = Path(self.folder.name) / "guests.sqlite3"
        self.rows = [
            ("G1", "H1", "Jordan", "Sample", None, 1, 1),
            ("G2", "H2", "Solo", "Example", None, 1, 0),
            ("G3", "H3", "Revoked", "Guest", None, 0, 0),
        ]
        import_guests(self.rows, self.database)

    def test_initial_period_case_whitespace_and_plus_one_flag(self):
        result = lookup_guest(self.database, {"first_initial": " j. ", "last_name": " SAMPLE "})
        self.assertEqual(result, {"first_name": "Jordan", "last_name": "Sample", "plus_one_allowed": True})
        self.assertFalse(lookup_guest(self.database, {"first_initial": "S", "last_name": "Example"})["plus_one_allowed"])

    def test_revoked_and_unknown(self):
        for identity in [{"first_initial": "R", "last_name": "Guest"}, {"first_initial": "X", "last_name": "Unknown"}]:
            with self.assertRaises(AccessDenied): lookup_guest(self.database, identity)
        import_guests(self.rows + [("G4", "H4", "Jordan", "Sample", None, 1, 0)], self.database)
        with self.assertRaisesRegex(AccessDenied, "More than one"):
            lookup_guest(self.database, {"first_initial": "J", "last_name": "Sample"})

    def test_identity_validation_and_exact_surname(self):
        for identity in [{}, [], {"first_initial": "Jordan", "last_name": "Sample"}, {"first_initial": [], "last_name": "Sample"}]:
            with self.assertRaises(ValueError): lookup_guest(self.database, identity)
        with self.assertRaises(AccessDenied): lookup_guest(self.database, {"first_initial": "J", "last_name": "Sam"})

    def test_name_match_and_revocation(self):
        payload = {"lookup": {"first_initial": "J", "last_name": "Sample"}, "first_name": "Jordan", "last_name": "Sample"}
        self.assertEqual(authorize_submission(self.database, payload), {"first_name": "Jordan", "last_name": "Sample"})
        with self.assertRaises(AccessDenied): authorize_submission(self.database, {**payload, "last_name": "Someone Else"})
        import_guests([self.rows[1], self.rows[2]], self.database)
        with self.assertRaises(AccessDenied): authorize_submission(self.database, payload)

    def test_plus_one_fields_rejected_when_not_allowed(self):
        payload = {"lookup": {"first_initial": "S", "last_name": "Example"}, "first_name": "Solo", "last_name": "Example"}
        for extra in [{"guest_name_unknown": True}, {"plus_one_first_name": "Extra"}]:
            with self.assertRaises(AccessDenied): authorize_submission(self.database, {**payload, **extra})
        self.assertEqual(authorize_submission(self.database, payload), {"first_name": "Solo", "last_name": "Example"})
