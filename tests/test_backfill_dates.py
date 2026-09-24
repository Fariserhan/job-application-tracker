"""Date repair tests use only temporary databases created by storage.init_db()."""
import contextlib
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import backfill_dates
import storage


class BackfillDatesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "applications.db")
        self.db_patch = patch.object(storage, "DB_PATH", self.db)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.backfill_patch = patch.object(backfill_dates, "DB", self.db)
        self.backfill_patch.start()
        self.addCleanup(self.backfill_patch.stop)
        self.connections = []
        original_connect = storage._connect

        def tracked_connect():
            conn = original_connect()
            self.connections.append(conn)
            return conn

        connect_patch = patch.object(storage, "_connect", side_effect=tracked_connect)
        connect_patch.start()
        self.addCleanup(connect_patch.stop)
        self.addCleanup(lambda: [conn.close() for conn in self.connections])
        storage.init_db()

    def _application(self, thread_id="T1", **overrides):
        row = {
            "thread_id": thread_id, "company_name": "Acme", "role_title": "Analyst",
            "source_platform": "LinkedIn", "application_date": "2026-01-01",
            "current_status": "Applied", "last_updated": "2026-01-01",
            "latest_subject": "Applied", "is_valid": True, "filter_reason": "affirmed",
        }
        row.update(overrides)
        storage.upsert_application(row)

    def _plan(self):
        with contextlib.closing(backfill_dates._connect()) as conn:
            return backfill_dates.plan(conn)

    def _main(self, *args):
        output = io.StringIO()
        with patch.object(sys, "argv", ["backfill_dates.py", *args]), \
                contextlib.redirect_stdout(output):
            result = backfill_dates.main()
        return result, output.getvalue()

    def test_plan_requires_message_after_both_application_and_current_dates(self):
        self._application("later")
        self._application("same", last_updated="2026-01-10")
        self._application("before", application_date="2026-01-10",
                          last_updated="2026-01-01")
        storage.mark_messages_processed(
            [("M1", "later"), ("M2", "same"), ("M3", "before")],
            dates={"M1": "2026-01-12", "M2": "2026-01-10", "M3": "2026-01-09"})
        changes = self._plan()
        self.assertEqual([change["thread_id"] for change in changes], ["later"])
        self.assertEqual(changes[0]["new_last_updated"], "2026-01-12")

    def test_plan_skips_thread_without_message_date(self):
        self._application()
        storage.mark_messages_processed([("M1", "T1")])
        self.assertEqual(self._plan(), [])

    def test_processing_without_a_date_keeps_cached_date(self):
        storage.mark_messages_processed([("M1", "T1")], dates={"M1": "2026-01-05"})
        storage.mark_messages_processed([("M1", "T1")])
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT message_date FROM processed_messages WHERE message_id='M1'"
            ).fetchone()[0], "2026-01-05")

    def test_status_history_sync_time_does_not_change_plan(self):
        self._application()
        storage.mark_messages_processed([("M1", "T1")], dates={"M1": "2026-01-05"})
        before = self._plan()
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.execute(
                "INSERT INTO status_history (thread_id, changed_at) VALUES (?, ?)",
                ("T1", "2099-01-01 00:00:00"))
            conn.commit()
        self.assertEqual(self._plan(), before)

    def test_dry_run_does_not_write_or_create_backup(self):
        self._application()
        storage.mark_messages_processed([("M1", "T1")], dates={"M1": "2026-01-05"})
        original_dir = os.getcwd()
        try:
            os.chdir(self.tmp.name)
            result, output = self._main()
        finally:
            os.chdir(original_dir)
        self.assertEqual(result, 0)
        self.assertIn("dry run", output)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "backups")))
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT last_updated FROM applications WHERE thread_id='T1'"
            ).fetchone()[0], "2026-01-01")

    def test_apply_backs_up_committed_wal_pages_before_repair(self):
        self._application()
        reader = sqlite3.connect(self.db)
        self.addCleanup(reader.close)
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM applications").fetchone()
        storage.mark_messages_processed([("M1", "T1")], dates={"M1": "2026-01-05"})
        self.assertTrue(os.path.exists(self.db + "-wal"))

        original_dir = os.getcwd()
        try:
            os.chdir(self.tmp.name)
            result, output = self._main("--apply")
        finally:
            os.chdir(original_dir)
        self.assertEqual(result, 0)
        backup_dir = output.split("backup: ", 1)[1].splitlines()[0]
        backup_path = os.path.join(self.tmp.name, backup_dir, "applications.db")
        with contextlib.closing(sqlite3.connect(backup_path)) as conn:
            self.assertEqual(conn.execute(
                "SELECT last_updated FROM applications WHERE thread_id='T1'"
            ).fetchone()[0], "2026-01-01")
            self.assertEqual(conn.execute(
                "SELECT message_date FROM processed_messages WHERE message_id='M1'"
            ).fetchone()[0], "2026-01-05")
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT last_updated FROM applications WHERE thread_id='T1'"
            ).fetchone()[0], "2026-01-05")

    def test_missing_database_returns_nonzero(self):
        for conn in self.connections:
            conn.close()
        os.remove(self.db)
        result, output = self._main()
        self.assertNotEqual(result, 0)
        self.assertIn("no applications.db found", output)


    def test_database_from_before_message_dates_reports_instead_of_crashing(self):
        self._application("T1")
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.execute("ALTER TABLE processed_messages DROP COLUMN message_date")
            conn.commit()

        self.assertEqual(self._plan(), [])
        result, output = self._main()

        self.assertEqual(result, 0)
        self.assertIn("no message dates cached yet", output)
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(processed_messages)")}
        self.assertNotIn("message_date", cols)  # dry run must not migrate/write


if __name__ == "__main__":
    unittest.main()
