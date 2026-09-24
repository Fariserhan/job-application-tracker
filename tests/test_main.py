"""Sync-engine tests: the run() summary contract must stay intact."""
import unittest

import main


class SyncContractTests(unittest.TestCase):
    def test_run_is_callable(self):
        """The sync entry point must exist and be callable with no arguments."""
        self.assertTrue(callable(main.run))

    def test_summary_fields_are_documented(self):
        """run() must not promise a fit score any more."""
        import inspect
        src = inspect.getsource(main.run)
        self.assertNotIn("fit_scored", src)
        self.assertNotIn("use_ai", src)


class MessageDateTests(unittest.TestCase):
    def test_uses_gmail_internal_date(self):
        from datetime import datetime
        ts = int(datetime(2026, 3, 5, 12, 0).timestamp() * 1000)
        self.assertEqual(main._message_date({"internal_date": ts}), "2026-03-05")

    def test_falls_back_to_date_header(self):
        meta = {"internal_date": 0, "date_header": "Thu, 05 Mar 2026 09:00:00 +0000"}
        self.assertEqual(main._message_date(meta), "2026-03-05")

    def test_undated_mail_is_blank_not_today(self):
        self.assertEqual(main._message_date({"internal_date": 0, "date_header": ""}), "")
        self.assertEqual(main._message_date({"date_header": "not a date"}), "")


if __name__ == "__main__":
    unittest.main()
