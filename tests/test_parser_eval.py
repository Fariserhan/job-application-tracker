"""The parser's measured quality: corpus shape, the CI gate and the demo mailbox.

* the labelled corpus stays big and hard enough to mean something (>= 160 emails, >= 35%
  hard negatives, valid labels);
* the deterministic rules clear the gate on it (event F1 >= 0.95, end-to-end status
  accuracy >= 0.90) — a regression in any rule shows up here;
* the synthetic demo mailbox, run through the real sync engine, parses perfectly: every
  application thread valid with the right employer and title, every noise thread dropped.

Offline and deterministic; the optional LLM fallback is never involved.
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from jobtracker.parsing import evaluate as ev

CORPUS = Path(__file__).parent / "fixtures" / "email_corpus.jsonl"
STATUSES = {"Applied", "Assessment / OA", "Interview", "Offer", "Rejected"}


class CorpusShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines()
                    if line.strip()]

    def test_size_and_negative_share(self):
        self.assertGreaterEqual(len(self.rows), 160)
        negatives = sum(1 for r in self.rows if not r["label"]["is_application_event"])
        self.assertGreaterEqual(negatives / len(self.rows), 0.35)

    def test_rows_are_well_formed(self):
        ids = [r["id"] for r in self.rows]
        self.assertEqual(len(ids), len(set(ids)), "duplicate ids")
        for row in self.rows:
            with self.subTest(row["id"]):
                for key in ("sender", "subject", "snippet", "body", "label", "tags"):
                    self.assertIn(key, row)
                self.assertTrue(row["tags"], "every row is tagged")
                label = row["label"]
                if label["is_application_event"]:
                    self.assertIn(label["status"], STATUSES)
                else:
                    self.assertEqual(label["status"], "none")

    def test_has_blind_holdout_batch(self):
        splits = {r.get("split", "dev") for r in self.rows}
        self.assertTrue({"dev", "holdout-1", "holdout-2"} <= splits)

    def test_covers_the_hard_cases(self):
        tags = {t for r in self.rows for t in r["tags"]}
        for tag in ("platform:linkedin", "platform:jobstreet", "platform:workday",
                    "malay-english", "quoted-thread", "recruiter-personal",
                    "non-job-application", "offer-word-trap", "high-volume-disclaimer"):
            self.assertIn(tag, tags)


class GateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = ev.evaluate(CORPUS)

    def test_event_detection_gate(self):
        self.assertGreaterEqual(self.report["detection"]["f1"], ev.GATE_EVENT_F1)
        self.assertGreaterEqual(ev.GATE_EVENT_F1, 0.95)

    def test_status_accuracy_gate(self):
        self.assertGreaterEqual(self.report["status"]["accuracy"], ev.GATE_STATUS_ACCURACY)
        self.assertGreaterEqual(ev.GATE_STATUS_ACCURACY, 0.90)

    def test_gate_flag_matches_metrics(self):
        self.assertTrue(self.report["gate"]["passed"])

    def test_main_exit_code_and_report(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ev.main(write_report=False)
        self.assertEqual(code, 0)
        self.assertIn("Event detection", out.getvalue())

    def test_markdown_report_sections(self):
        md = ev.format_markdown(self.report)
        for heading in ("## Methodology", "## Corpus composition", "## Headline metrics",
                        "## Before / after and generalisation", "## Status confusion matrix",
                        "## Breakdown by tag", "## Known failure modes", "## Caveats"):
            self.assertIn(heading, md)

    def test_every_failure_is_listed_with_expected_and_got(self):
        for failure in self.report["failures"]:
            self.assertTrue({"id", "kind", "expected", "got"} <= set(failure))

    def test_end_to_end_status_counts_missed_events_as_wrong(self):
        status = self.report["status"]
        missed = sum(row["(missed)"] for row in status["confusion"].values())
        self.assertEqual(status["n"] - status["n_detected"], missed)
        self.assertLessEqual(status["accuracy"], status["conditional_accuracy"])


class MatchingRuleTests(unittest.TestCase):
    def test_company_normalisation(self):
        self.assertTrue(ev.company_match("Kelip Data Sdn. Bhd.", "Kelip Data Sdn Bhd"))
        self.assertTrue(ev.company_match("", "Unknown"))
        self.assertFalse(ev.company_match("", "Acme"))
        self.assertFalse(ev.company_match("Acme Berhad", "Acme"))
        self.assertTrue(ev.company_match("Acme Berhad", "Acme", lenient=True))

    def test_role_normalisation(self):
        self.assertTrue(ev.role_match("Data Analyst, Risk", "data analyst risk"))
        self.assertTrue(ev.role_match("", ""))
        self.assertFalse(ev.role_match("Data Analyst", "Senior Data Analyst"))
        self.assertTrue(ev.role_match("Data Analyst", "Senior Data Analyst", lenient=True))

    def test_tiny_corpus_metrics(self):
        rows = [
            {"id": "a", "sender": "Acme Careers <careers@acme.com>",
             "subject": "Thank you for applying to Acme for Data Analyst", "snippet": "",
             "body": "", "tags": ["t"],
             "label": {"is_application_event": True, "status": "Applied", "company": "Acme",
                       "role": "Data Analyst"}},
            {"id": "b", "sender": "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
             "subject": "Job Alert: 10 new jobs", "snippet": "", "body": "", "tags": ["t"],
             "label": {"is_application_event": False, "status": "none", "company": "",
                       "role": ""}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.jsonl"
            path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            report = ev.evaluate(path)
        self.assertEqual(report["detection"]["f1"], 1.0)
        self.assertEqual(report["status"]["accuracy"], 1.0)
        self.assertEqual(report["company"]["accuracy"], 1.0)
        self.assertEqual(report["failures"], [])


class DemoMailboxParsingTests(unittest.TestCase):
    """The demo mailbox through the real sync engine: perfect parsing is the bar."""

    def test_demo_threads_parse_perfectly(self):
        from jobtracker import demo as demo_pkg
        from jobtracker.demo.mailbox import COMPANIES, ROLES, DemoMailbox
        from jobtracker.store import Repository

        truth: dict[str, tuple[str, str]] = {}

        class RecordingMailbox(DemoMailbox):
            def _confirmation(self, thread, day, company, domain, role, platform):
                truth[thread] = (company, role)
                super()._confirmation(thread, day, company, domain, role, platform)

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(demo_pkg, "DemoMailbox", RecordingMailbox), \
                mock.patch("jobtracker.warehouse.build", return_value={}):
            result = demo_pkg.build_demo(Path(tmp) / "demo", today=date(2026, 10, 1))
            settings_dir = Path(result["data_dir"])
            from jobtracker.config import Settings
            repo = Repository(Settings(data_dir=settings_dir.resolve()))
            rows = {r["thread_id"]: r for r in repo.applications()}

        self.assertGreater(len(truth), 100)
        names = {name for name, _, _ in COMPANIES}
        titles = {title for title, _ in ROLES}
        app_rows = {t: r for t, r in rows.items() if t.startswith("demo-thread-")}
        self.assertEqual(set(app_rows), set(truth))
        for thread, (company, role) in truth.items():
            row = app_rows[thread]
            with self.subTest(thread):
                self.assertEqual(row["is_valid"], 1, row["filter_reason"])
                self.assertEqual(row["company_name"], company)
                self.assertEqual(row["role_title"], role)
                self.assertIn(row["company_name"], names)
                self.assertIn(row["role_title"], titles)
        noise = [r for t, r in rows.items() if t.startswith("demo-noise-")]
        self.assertTrue(noise)
        for row in noise:
            with self.subTest(row["thread_id"]):
                self.assertEqual(row["is_valid"], 0, row["latest_subject"])


if __name__ == "__main__":
    unittest.main()
