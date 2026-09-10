import os
import shutil
import tempfile
import unittest

import ai
import main
import matcher
import storage


class MergeVerdictTests(unittest.TestCase):
    def test_later_message_wins_and_validity_ors(self):
        stage1 = [
            ({"id": "m1"}, {"thread_id": "T1"}),
            ({"id": "m2"}, {"thread_id": "T1"}),
        ]
        verdicts = {
            "m1": {"is_valid": False, "company": "", "role": "Risk Analyst",
                   "status": "Applied"},
            "m2": {"is_valid": True, "company": "Acme", "role": "",
                   "status": "Interview"},
        }
        merged = main.merge_thread_verdicts(stage1, verdicts)
        self.assertEqual(set(merged), {"T1"})
        verdict = merged["T1"]
        self.assertTrue(verdict["is_valid"])
        self.assertEqual(verdict["company"], "Acme")
        self.assertEqual(verdict["role"], "Risk Analyst")
        self.assertEqual(verdict["status"], "Interview")

    def test_empty_fields_fall_back_to_earlier_message(self):
        stage1 = [
            ({"id": "m1"}, {"thread_id": "T1"}),
            ({"id": "m2"}, {"thread_id": "T1"}),
        ]
        verdicts = {
            "m1": {"is_valid": True, "status": "Interview", "reason": "invite"},
            "m2": {"is_valid": None, "status": ""},
        }
        verdict = main.merge_thread_verdicts(stage1, verdicts)["T1"]
        self.assertTrue(verdict["is_valid"])
        self.assertEqual(verdict["status"], "Interview")
        self.assertEqual(verdict["reason"], "invite")

    def test_separate_threads_stay_separate(self):
        stage1 = [({"id": "m1"}, {"thread_id": "T1"}),
                  ({"id": "m2"}, {"thread_id": "T2"})]
        verdicts = {"m1": {"is_valid": True}, "m2": {"is_valid": False}}
        merged = main.merge_thread_verdicts(stage1, verdicts)
        self.assertTrue(merged["T1"]["is_valid"])
        self.assertFalse(merged["T2"]["is_valid"])


class FitEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jobsuite_main_")
        self._orig_db = storage.DB_PATH
        storage.DB_PATH = os.path.join(self.tmp, "applications.db")
        storage.init_db()
        storage.upsert_application({
            "thread_id": "T1", "company_name": "Acme", "role_title": "Data Analyst",
            "source_platform": "LinkedIn", "application_date": "2026-08-01",
            "current_status": "Applied", "last_updated": "2026-08-01",
            "job_description_snippet": "Python SQL", "job_url": "",
            "gmail_link": "", "latest_subject": "Applied", "is_valid": True,
            "filter_reason": "affirmed",
        })
        self._orig_get_cv = matcher.get_cv_text
        matcher.get_cv_text = lambda: "CV TEXT"
        self._orig_available = ai.available
        self._orig_model = ai.active_model
        self._orig_fit = ai.fit_score_batch
        ai.available = lambda: True
        ai.active_model = lambda: "fake-model"
        self.calls = []

        def fake_fit(items, cv_text=None, model=None):
            self.calls.append([item["id"] for item in items])
            return {item["id"]: {"fit_score": 88, "matched_skills": ["Python"],
                                 "missing_skills": [], "actionable_improvements": []}
                    for item in items}

        ai.fit_score_batch = fake_fit

    def tearDown(self):
        matcher.get_cv_text = self._orig_get_cv
        ai.available = self._orig_available
        ai.active_model = self._orig_model
        ai.fit_score_batch = self._orig_fit
        storage.DB_PATH = self._orig_db
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ai_scores_persist_and_are_not_overwritten(self):
        first = main.run_fit_evaluation(use_ai_fit=True)
        self.assertEqual(first["scored"], 1)
        row = storage.get_valid_rows_for_fit()[0]
        self.assertEqual(row["fit_source"], "ai")
        self.assertEqual(row["fit_score"], 88)
        self.assertEqual(row["fit_cv_sig"], ai.cv_signature("CV TEXT"))

        second = main.run_fit_evaluation(use_ai_fit=True)
        self.assertEqual(second["scored"], 0)
        self.assertEqual(len(self.calls), 1, "AI must not be called for unchanged CV")
        row = storage.get_valid_rows_for_fit()[0]
        self.assertEqual(row["fit_source"], "ai", "fallback must not overwrite AI scores")
        self.assertEqual(row["fit_score"], 88)

    def test_cv_change_triggers_ai_rescore(self):
        main.run_fit_evaluation(use_ai_fit=True)
        matcher.get_cv_text = lambda: "A DIFFERENT CV"
        result = main.run_fit_evaluation(use_ai_fit=True)
        self.assertEqual(result["scored"], 1)
        self.assertEqual(len(self.calls), 2)

    def test_ai_failure_falls_back_to_rules(self):
        def broken(items, cv_text=None, model=None):
            raise RuntimeError("provider down")

        ai.fit_score_batch = broken
        result = main.run_fit_evaluation(use_ai_fit=True)
        self.assertEqual(result["scored"], 1)
        row = storage.get_valid_rows_for_fit()[0]
        self.assertEqual(row["fit_source"], "rule")
        self.assertIsNotNone(row["fit_score"])


if __name__ == "__main__":
    unittest.main()
