"""Headless smoke test: run the full dashboard with Streamlit's AppTest.

The application database is redirected to a temporary file so no personal data is
read or written. Catches import errors, bad Streamlit APIs and render-time crashes.
"""
import os
import shutil
import tempfile
import unittest

import storage


class DashboardSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jobsuite_dash_")
        self._orig_cwd = os.getcwd()
        self._orig_db = storage.DB_PATH
        self._orig_csv = storage.CSV_PATH
        self._orig_state = storage.STATE_PATH
        # The dashboard resolves its data files relative to the working directory, and it
        # reloads support modules on first render — so run it inside the temp directory.
        os.chdir(self.tmp)
        storage.DB_PATH = os.path.join(self.tmp, "applications.db")
        storage.CSV_PATH = os.path.join(self.tmp, "job_tracker.csv")
        storage.STATE_PATH = os.path.join(self.tmp, "sync_state.json")

    def tearDown(self):
        os.chdir(self._orig_cwd)
        storage.DB_PATH = self._orig_db
        storage.CSV_PATH = self._orig_csv
        storage.STATE_PATH = self._orig_state
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dashboard_renders_without_exceptions(self):
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "dashboard.py"),
            default_timeout=120,
        )
        app.run()
        self.assertEqual(len(app.exception), 0, [str(e.value) for e in app.exception])
        self.assertTrue(any("Job Application Suite" in t.value for t in app.title))
        self.assertEqual(len(app.tabs), 2)


if __name__ == "__main__":
    unittest.main()
