import unittest

import repair


def make_row(**overrides):
    row = {
        "thread_id": "T1", "company_name": "Prudential", "role_title": "Risk Analyst",
        "source_platform": "JobStreet", "application_date": "2026-01-01",
        "current_status": "Applied", "last_updated": "2026-01-01",
        "job_description_snippet": "", "job_url": "", "latest_subject": "x",
        "is_valid": 1, "filter_reason": "affirmed",
        "job_type": "Full-Time", "seniority_level": "Analyst / Associate",
        "industry": "Insurance",
    }
    row.update(overrides)
    return row


class RepairPlanTests(unittest.TestCase):
    def test_platform_digest_invalidated(self):
        rep = repair.plan_valid_repair(make_row(
            latest_subject="FARIS ERHAN, new activity in jobs you applied for"))
        self.assertEqual(rep["is_valid"], False)
        self.assertIn("digest", rep["reason"])

    def test_verification_email_invalidated(self):
        rep = repair.plan_valid_repair(make_row(latest_subject="Verify your candidate account"))
        self.assertEqual(rep["is_valid"], False)
        self.assertIn("verification", rep["reason"])

    def test_hiredly_housekeeping_invalidated(self):
        rep = repair.plan_valid_repair(make_row(
            latest_subject="Your Resume Has Been Approved!"))
        self.assertEqual(rep["is_valid"], False)

    def test_this_company_repaired_from_subject(self):
        rep = repair.plan_valid_repair(make_row(
            company_name="this", role_title="Claim Executive",
            latest_subject="Application update for Claim Executive at Texchem Corporation Sdn Bhd"))
        self.assertEqual(rep["company"], "Texchem Corporation Sdn Bhd")
        self.assertNotIn("role", rep)

    def test_numeric_role_cleared(self):
        rep = repair.plan_valid_repair(make_row(
            role_title="4460583743",
            latest_subject="Faris, your application was sent to TNG Digital"))
        self.assertEqual(rep["role"], "")

    def test_subject_role_repaired(self):
        rep = repair.plan_valid_repair(make_row(
            company_name="AIA", role_title="Thank you for applying Renewal Business Pricing, Analyst",
            latest_subject="Thank you for applying Renewal Business Pricing, Analyst"))
        self.assertEqual(rep["role"], "Renewal Business Pricing, Analyst")

    def test_good_rows_are_left_alone(self):
        self.assertIsNone(repair.plan_valid_repair(make_row(
            company_name="Prudential", role_title="Risk Analyst",
            latest_subject="Prudential has viewed your application for Risk Analyst")))

    def test_missed_application_recovered(self):
        rep = repair.plan_recovery(make_row(
            is_valid=0, current_status="Ghosted", company_name="", role_title="",
            latest_subject="Your recent application for Associate Analyst",
            job_description_snippet="We would like to acknowledge receipt of your application."))
        self.assertIsNotNone(rep)
        self.assertTrue(rep["is_valid"])
        self.assertIn("Associate Analyst", rep.get("role", ""))

    def test_plain_noise_not_recovered(self):
        rep = repair.plan_recovery(make_row(
            is_valid=0, latest_subject="10 new jobs for you in Kuala Lumpur",
            job_description_snippet="Apply now to these roles."))
        self.assertIsNone(rep)


if __name__ == "__main__":
    unittest.main()
