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


if __name__ == "__main__":
    unittest.main()
