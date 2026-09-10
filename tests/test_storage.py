import os
import shutil
import sqlite3
import tempfile
import unittest

import storage


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jobsuite_test_")
        self._orig_db = storage.DB_PATH
        self._orig_csv = storage.CSV_PATH
        self._orig_state = storage.STATE_PATH
        storage.DB_PATH = os.path.join(self.tmp, "applications.db")
        storage.CSV_PATH = os.path.join(self.tmp, "job_tracker.csv")
        storage.STATE_PATH = os.path.join(self.tmp, "sync_state.json")
        storage.init_db()

    def tearDown(self):
        storage.DB_PATH = self._orig_db
        storage.CSV_PATH = self._orig_csv
        storage.STATE_PATH = self._orig_state
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _record(self, **overrides):
        record = {
            "thread_id": "T1", "company_name": "Acme", "role_title": "Data Analyst",
            "source_platform": "LinkedIn", "application_date": "2026-08-01",
            "current_status": "Applied", "last_updated": "2026-08-01",
            "job_description_snippet": "jd", "job_url": "https://example.com/job/1",
            "gmail_link": "https://mail.google.com/x", "latest_subject": "Applied",
            "is_valid": True, "filter_reason": "affirmed",
        }
        record.update(overrides)
        return record

    def _row(self, thread_id="T1"):
        with sqlite3.connect(storage.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM applications WHERE thread_id=?",
                               (thread_id,)).fetchone()
            return dict(row) if row else None


class UpsertTests(StorageTestCase):
    def test_insert_then_update(self):
        self.assertEqual(storage.upsert_application(self._record()), "inserted")
        self.assertEqual(storage.upsert_application(
            self._record(latest_subject="Updated")), "updated")
        self.assertEqual(self._row()["latest_subject"], "Updated")

    def test_older_message_never_regresses_status(self):
        storage.upsert_application(self._record(current_status="Interview",
                                                last_updated="2026-08-10"))
        storage.upsert_application(self._record(current_status="Applied",
                                                last_updated="2026-08-01"))
        self.assertEqual(self._row()["current_status"], "Interview")

    def test_newer_message_advances_status(self):
        storage.upsert_application(self._record(current_status="Applied",
                                                last_updated="2026-08-01"))
        storage.upsert_application(self._record(current_status="Interview",
                                                last_updated="2026-08-05"))
        self.assertEqual(self._row()["current_status"], "Interview")
        history = storage.get_status_history("T1")
        self.assertTrue(any(h["to_status"] == "Interview" for h in history))

    def test_manual_recovery_survives_filtered_messages(self):
        storage.upsert_application(self._record(is_valid=False,
                                                filter_reason="no positive affirmation"))
        storage.set_valid("T1", True, "manual recovery")
        storage.upsert_application(self._record(is_valid=False,
                                                filter_reason="no positive affirmation",
                                                last_updated="2026-08-09"))
        row = self._row()
        self.assertEqual(row["is_valid"], 1)
        self.assertEqual(row["filter_reason"], "manual recovery")

    def test_valid_thread_survives_later_filtered_message(self):
        storage.upsert_application(self._record(is_valid=True, filter_reason="affirmed"))
        storage.upsert_application(self._record(is_valid=False,
                                                filter_reason="no positive affirmation",
                                                last_updated="2026-08-15"))
        self.assertEqual(self._row()["is_valid"], 1)

    def test_valid_message_recovers_invalid_thread(self):
        storage.upsert_application(self._record(is_valid=False,
                                                filter_reason="no positive affirmation"))
        self.assertEqual(self._row()["is_valid"], 0)
        storage.upsert_application(self._record(is_valid=True, filter_reason="affirmed",
                                                last_updated="2026-08-15"))
        self.assertEqual(self._row()["is_valid"], 1)

    def test_manual_noise_is_sticky(self):
        storage.upsert_application(self._record())
        storage.set_valid("T1", False)
        self.assertEqual(self._row()["is_valid"], 0)
        self.assertTrue(self._row()["filter_reason"].startswith("manual"))
        storage.upsert_application(self._record(is_valid=True, filter_reason="affirmed",
                                                last_updated="2026-08-20"))
        row = self._row()
        self.assertEqual(row["is_valid"], 0)
        self.assertTrue(row["filter_reason"].startswith("manual"))

    def test_platform_company_does_not_replace_real_employer(self):
        storage.upsert_application(self._record(company_name="Prudential"))
        storage.upsert_application(self._record(company_name="JobStreet"))
        self.assertEqual(self._row()["company_name"], "Prudential")

    def test_bad_role_does_not_replace_good_role(self):
        storage.upsert_application(self._record(role_title="Data Analyst"))
        storage.upsert_application(self._record(role_title="hi there, you have"))
        self.assertEqual(self._row()["role_title"], "Data Analyst")

    def test_job_url_and_snippet_preserved(self):
        storage.upsert_application(self._record())
        storage.upsert_application(self._record(job_url="", job_description_snippet=""))
        row = self._row()
        self.assertEqual(row["job_url"], "https://example.com/job/1")
        self.assertEqual(row["job_description_snippet"], "jd")

    def test_refine_platform_rows_only_strict_improvements(self):
        storage.upsert_application(self._record(
            company_name="JobStreet",
            latest_subject="Application update for Data Analyst at Prudential"))
        n = storage.refine_platform_rows()
        self.assertGreaterEqual(n, 1)
        self.assertEqual(self._row()["company_name"], "Prudential")

    def test_refine_platform_rows_leaves_real_employers_alone(self):
        storage.upsert_application(self._record(company_name="Prudential"))
        storage.refine_platform_rows()
        self.assertEqual(self._row()["company_name"], "Prudential")


class FitTests(StorageTestCase):
    def test_save_fit_sanitizes_and_stamps_rule(self):
        storage.upsert_application(self._record())
        updated = storage.save_fit_results({
            "T1": {"fit_score": "77.5", "matched_skills": ["Python", None, "SQL"],
                   "missing_skills": 5, "actionable_improvements": ["do x"]},
            "MISSING": {"fit_score": 50},
        }, cv_sig="sig1")
        self.assertEqual(updated, 1)
        row = self._row()
        self.assertEqual(row["fit_score"], 77.5)
        self.assertEqual(row["fit_source"], "rule")
        self.assertEqual(row["fit_cv_sig"], "sig1")
        self.assertIn("Python", row["matched_skills"])

    def test_save_fit_skips_unusable_scores(self):
        storage.upsert_application(self._record())
        updated = storage.save_fit_results({"T1": {"fit_score": "not a number"}})
        self.assertEqual(updated, 0)
        self.assertIsNone(self._row()["fit_score"])

    def test_mark_fit_ai(self):
        storage.upsert_application(self._record())
        storage.mark_fit_ai({"T1": {"fit_score": 120, "matched_skills": ["Python"],
                                    "missing_skills": [], "actionable_improvements": []}},
                            cv_sig="sig2")
        row = self._row()
        self.assertEqual(row["fit_score"], 100.0)
        self.assertEqual(row["fit_source"], "ai")
        self.assertEqual(row["fit_cv_sig"], "sig2")

    def test_get_valid_rows_for_fit_includes_cv_columns(self):
        storage.upsert_application(self._record())
        storage.save_fit_results({"T1": {"fit_score": 50}}, cv_sig="abc")
        rows = storage.get_valid_rows_for_fit()
        self.assertEqual(rows[0]["fit_cv_sig"], "abc")
        self.assertEqual(rows[0]["fit_source"], "rule")


class AiClassificationTests(StorageTestCase):
    def test_valid_status_only(self):
        storage.upsert_application(self._record())
        storage.apply_ai_classification({
            "T1": {"is_valid": True, "company": "Acme", "role": "Data Analyst",
                   "status": "Not A Real Status", "reason": "test"},
        })
        self.assertEqual(self._row()["current_status"], "Applied")
        self.assertEqual(self._row()["ai_classified"], 1)

    def test_manual_recovery_not_invalidated_by_ai(self):
        storage.upsert_application(self._record(is_valid=False,
                                                filter_reason="no positive affirmation"))
        storage.set_valid("T1", True, "manual recovery")
        storage.apply_ai_classification({"T1": {"is_valid": False, "reason": "looks noisy"}})
        self.assertEqual(self._row()["is_valid"], 1)

    def test_platform_names_ignored(self):
        storage.upsert_application(self._record(company_name="Prudential"))
        storage.apply_ai_classification({"T1": {"company": "JobStreet", "role": "hi"}})
        row = self._row()
        self.assertEqual(row["company_name"], "Prudential")
        self.assertEqual(row["role_title"], "Data Analyst")


class BodyCacheTests(StorageTestCase):
    def test_roundtrip(self):
        storage.mark_messages_processed([("M1", "T1")], bodies={"M1": "hello body"})
        self.assertEqual(storage.filter_unprocessed(["M1"]), [])
        self.assertEqual(storage.get_processed_bodies(["M1"]), {"M1": "hello body"})
        self.assertEqual(storage.get_processed_bodies(["M2"]), {})

    def test_empty_body_does_not_erase_cache(self):
        storage.mark_messages_processed([("M1", "T1")], bodies={"M1": "body"})
        storage.mark_messages_processed([("M1", "T1")], bodies={})
        self.assertEqual(storage.get_processed_bodies(["M1"])["M1"], "body")

    def test_body_is_capped(self):
        storage.mark_messages_processed([("M1", "T1")], bodies={"M1": "x" * 100_000})
        self.assertLessEqual(len(storage.get_processed_bodies(["M1"])["M1"]), 8000)

    def test_messages_without_body_are_still_processed(self):
        storage.mark_messages_processed([("M1", "T1")])
        self.assertEqual(storage.filter_unprocessed(["M1"]), [])
        self.assertEqual(storage.get_processed_bodies(["M1"]), {})


class ExportBackupTests(StorageTestCase):
    def test_export_csv_with_extra_columns(self):
        storage.upsert_application(self._record())
        storage.export_csv()
        with open(storage.CSV_PATH, encoding="utf-8-sig") as fh:
            header = fh.readline()
        self.assertIn("company_name", header)
        self.assertIn("fit_source", header)

    def test_backup_all_writes_db(self):
        storage.upsert_application(self._record())
        folder = storage.backup_all(os.path.join(self.tmp, "backups"))
        self.assertTrue(os.path.exists(os.path.join(folder, "applications.db")))

    def test_corrupt_state_falls_back(self):
        with open(storage.STATE_PATH, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        state = storage.load_state()
        self.assertIsNone(state["last_sync_epoch"])

    def test_state_roundtrip(self):
        storage.save_state({"last_sync_epoch": 123, "history_id": "h1"})
        state = storage.load_state()
        self.assertEqual(state["last_sync_epoch"], 123)
        self.assertEqual(state["history_id"], "h1")


class LifecycleTests(StorageTestCase):
    def test_ghosting_and_history(self):
        storage.upsert_application(self._record(last_updated="2020-01-01"))
        n = storage.apply_ghosting(stale_days=21)
        self.assertEqual(n, 1)
        self.assertEqual(self._row()["current_status"], "Ghosted")
        history = storage.get_status_history("T1")
        self.assertTrue(any("auto-ghosted" in (h["note"] or "") for h in history))

    def test_mark_applied_moves_saved_row(self):
        storage.upsert_application(self._record(current_status="Saved / Planning",
                                                application_date="2026-01-01"))
        self.assertEqual(storage.mark_applied("T1"), "applied")
        self.assertEqual(self._row()["current_status"], "Applied")

    def test_note_roundtrip(self):
        storage.upsert_application(self._record())
        self.assertTrue(storage.update_note("T1", "referral from Jenny"))
        self.assertEqual(self._row()["notes"], "referral from Jenny")

    def test_update_company_role(self):
        storage.upsert_application(self._record())
        self.assertTrue(storage.update_company_role("T1", "Real Employer Bhd", "Risk Analyst"))
        row = self._row()
        self.assertEqual(row["company_name"], "Real Employer Bhd")
        self.assertEqual(row["role_title"], "Risk Analyst")
        self.assertFalse(storage.update_company_role("T1", "", "Risk Analyst"))
        self.assertFalse(storage.update_company_role("MISSING", "X", "Y"))

    def test_add_manual_application_and_tracked_keys(self):
        job = {"job_key": "abc123", "title": "Risk Analyst", "company": "Acme",
               "source": "LinkedIn", "url": "https://example.com/x",
               "description": "jd", "fit_score": 55.0,
               "matched_skills": ["Python"], "missing_skills": ["SAS"]}
        self.assertEqual(storage.add_manual_application(job), "added")
        self.assertEqual(storage.add_manual_application(job), "exists")
        self.assertIn("abc123", storage.get_tracked_discovery_keys())
        rows = [r for r in storage.load_discovered()]
        self.assertEqual(rows, [])

    def test_discovered_roundtrip(self):
        jobs = [{"job_key": "k1", "title": "Data Analyst", "company": "Acme",
                 "source": "LinkedIn", "url": "https://x", "description": "d",
                 "fit_score": 70.0, "matched_skills": [], "missing_skills": []}]
        self.assertEqual(storage.save_discovered(jobs), 1)
        got = storage.load_discovered()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["job_key"], "k1")
        storage.clear_discovered()
        self.assertEqual(storage.load_discovered(), [])


if __name__ == "__main__":
    unittest.main()
