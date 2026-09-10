import os
import tempfile
import unittest

import matcher


class AnalyzeTests(unittest.TestCase):
    def test_bounds_and_shape(self):
        result = matcher.analyze("Data Analyst", "Python, SQL and Excel required")
        for key in ("fit_score", "matched_skills", "missing_skills",
                    "actionable_improvements", "breakdown"):
            self.assertIn(key, result)
        self.assertGreaterEqual(result["fit_score"], matcher.FLOOR_SCORE)
        self.assertLessEqual(result["fit_score"], 98.0)
        self.assertIsInstance(result["matched_skills"], list)
        self.assertLessEqual(len(result["actionable_improvements"]), 3)

    def test_matched_and_missing_skills(self):
        result = matcher.analyze(
            "Data Analyst", "Must know Python, SQL and Power BI dashboards")
        self.assertIn("Python", result["matched_skills"])
        self.assertIn("Power BI", result["missing_skills"])

    def test_manager_role_is_capped(self):
        result = matcher.analyze(
            "VP, Risk Analytics",
            "Python SQL R Excel Power BI Tableau SAS 15+ years experience leading teams")
        self.assertLessEqual(result["fit_score"], 55.0)
        self.assertLessEqual(result["breakdown"]["_cap"], 55.0)

    def test_senior_role_is_capped(self):
        result = matcher.analyze(
            "Senior Data Analyst", "Python SQL R Excel statistics modelling")
        self.assertLessEqual(result["fit_score"], 70.0)

    def test_years_experience_cap(self):
        result = matcher.analyze(
            "Data Analyst", "Python SQL R Excel. 6+ years of experience required")
        self.assertLessEqual(result["fit_score"], 60.0)

    def test_title_alignment(self):
        self.assertEqual(matcher._title_alignment("Actuarial Analyst"), 1.0)
        self.assertLess(matcher._title_alignment("Marketing Executive"), 0.9)

    def test_empty_text_still_scores(self):
        result = matcher.analyze("", "")
        self.assertIsInstance(result["fit_score"], float)


class CvProfileTests(unittest.TestCase):
    def setUp(self):
        self._orig = matcher.CV_PATH
        handle, self.tmp = tempfile.mkstemp(suffix=".txt")
        os.close(handle)
        matcher.CV_PATH = self.tmp
        os.remove(self.tmp)

    def tearDown(self):
        matcher.CV_PATH = self._orig
        if os.path.exists(self.tmp):
            os.remove(self.tmp)

    def test_default_when_missing(self):
        self.assertEqual(matcher.get_cv_text(), matcher.DEFAULT_CV_TEXT)

    def test_roundtrip(self):
        matcher.save_cv_text("MY CUSTOM CV WITH Kubernetes")
        self.assertEqual(matcher.get_cv_text(), "MY CUSTOM CV WITH Kubernetes")
        self.assertTrue(os.path.exists(self.tmp))


class MiscTests(unittest.TestCase):
    def test_extract_requirements(self):
        text = ("Requirements: proficiency in Python and SQL; degree in mathematics. "
                "You will build dashboards.")
        reqs = matcher.extract_requirements(text)
        self.assertTrue(reqs)
        self.assertTrue(any("Python" in r for r in reqs))

    def test_owned_regex_and_top_missing(self):
        self.assertTrue(matcher.owned_regex("Power BI"))
        self.assertEqual(matcher.top_missing([], "[\"SAS\"]"), "SAS")
        self.assertEqual(matcher.top_missing(["Tableau"]), "Tableau")
        self.assertEqual(matcher.top_missing([], "not json"), "—")

    def test_title_targets_cover_target_roles(self):
        for title in ("Actuarial Analyst", "Quantitative Risk Analyst",
                      "Data Analyst", "Business Intelligence Analyst"):
            self.assertEqual(matcher._title_alignment(title), 1.0)


if __name__ == "__main__":
    unittest.main()
