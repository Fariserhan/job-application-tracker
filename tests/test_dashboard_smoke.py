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
        # Single-tracker dashboard. The Reports section carries its own tab strip.
        self.assertGreaterEqual(len(app.tabs), 4)
        visible = " ".join(
            str(el.value) for el in list(app.markdown) + list(app.button) + list(app.caption)
            if getattr(el, "value", None) is not None
        ).lower()
        for banned in ("live job matches", "scan live market", "scan jobs",
                       "market scan", "discovery", "cv gap", "fit score"):
            self.assertNotIn(banned, visible)

    def test_reports_section_is_present(self):
        """The reporting section must surface findings, not source code."""
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "dashboard.py"),
            default_timeout=120,
        )
        app.run()
        self.assertEqual(len(app.exception), 0, [str(e.value) for e in app.exception])
        text = " ".join(str(el.value) for el in list(app.subheader) + list(app.markdown)
                        + list(app.caption) + list(app.info)
                        if getattr(el, "value", None) is not None)
        self.assertIn("Reports", text)
        # It is a reporting view over the data, not a code viewer.
        self.assertNotIn("Analytics engine", text)

        # The report tabs are findings, not tool names.
        labels = " ".join(str(getattr(t, "label", "")) for t in app.tabs)
        for label in ("Funnel", "Where it works", "Waiting", "Export"):
            self.assertIn(label, labels, label)
        # The CV/skills tab is gone along with the feature.
        self.assertNotIn("Skills", labels)
        # ...and the code viewers are gone from the default surface.
        for gone in ("SQL library", "Power BI (DAX)", "Star schema"):
            self.assertNotIn(gone, labels, gone)

    def test_cv_and_fit_feature_stays_deleted(self):
        """Regression guard: the removed CV/fit feature must not creep back in."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertFalse(os.path.exists(os.path.join(root, "matcher.py")))
        self.assertFalse(os.path.exists(os.path.join(root, "cv_profile.txt")))
        for name in ("dashboard.py", "main.py"):
            with open(os.path.join(root, name), encoding="utf-8") as fh:
                src = fh.read()
            for banned in ("fit_score", "matcher.", "cv_profile"):
                self.assertNotIn(banned, src, f"{banned} in {name}")

    def test_bi_pack_targets_this_model(self):
        """DAX/Tableau measures must reference the warehouse's real tables and fields."""
        import bi_export

        measures = bi_export.dax_measures()
        self.assertGreaterEqual(len(measures), 20)
        dax_text = " ".join(measures.values())
        for table in ("fact_application", "dim_date", "dim_platform"):
            self.assertIn(table, dax_text, table)
        # Insurance measures do not belong to this dataset and must not be invented here.
        for wrong in ("loss ratio", "ibnr", "csm"):
            self.assertNotIn(wrong, dax_text.lower(), wrong)

        calcs = bi_export.tableau_calculations()
        self.assertGreaterEqual(len(calcs), 10)
        self.assertIn("days_to_response", " ".join(calcs.values()))

    def test_removed_discovery_module_is_gone(self):
        """The job-search feature must not come back through the CLI or an import."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertFalse(os.path.exists(os.path.join(root, "discovery.py")))
        self.assertFalse(os.path.exists(os.path.join(root, "tests", "test_discovery.py")))
        source = open(os.path.join(root, "main.py"), encoding="utf-8").read()
        self.assertNotIn("scan_market", source)


if __name__ == "__main__":
    unittest.main()
