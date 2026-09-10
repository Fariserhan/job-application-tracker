import unittest

import parser


def make_meta(**overrides):
    meta = {
        "thread_id": "T1", "id": "M1", "internal_date": 1735689600000,
        "snippet": "", "from": "Acme Careers <careers@acme.com>",
        "subject": "Thank you for applying to Acme for Data Analyst",
        "date_header": "Wed, 01 Jan 2026 10:00:00 +0000",
        "gmail_link": "https://mail.google.com/mail/u/0/#all/T1",
    }
    meta.update(overrides)
    return meta


class MetaTestCase(unittest.TestCase):
    """Base class giving every parse test the shared metadata factory."""

    def _meta(self, **overrides):
        return make_meta(**overrides)


class ClassifyMessageTests(unittest.TestCase):
    def test_application_confirmation_is_valid(self):
        valid, reason = parser.classify_message(
            "jobs@linkedin.com", "Thank you for applying to Acme for Data Analyst")
        self.assertTrue(valid)
        self.assertEqual(reason, "affirmed")

    def test_application_received_is_valid(self):
        valid, _ = parser.classify_message(
            "no-reply@myworkday.com", "Application Received - Data Analyst at Acme")
        self.assertTrue(valid)

    def test_job_alert_is_noise(self):
        valid, reason = parser.classify_message(
            "jobalert@linkedin.com", "Job Alert: 10 new Data Analyst jobs")
        self.assertFalse(valid)
        self.assertIn("blacklist", reason)

    def test_digest_is_noise(self):
        valid, _ = parser.classify_message(
            "digest@jobstreet.com", "Your weekly digest of recommended jobs")
        self.assertFalse(valid)

    def test_rejection_is_valid(self):
        valid, _ = parser.classify_message(
            "hr@acme.com", "We regret to inform you that you were unsuccessful")
        self.assertTrue(valid)

    def test_offer_is_valid(self):
        valid, _ = parser.classify_message(
            "hr@acme.com", "Congratulations! We are pleased to offer you the role")
        self.assertTrue(valid)

    def test_non_job_commercial_is_vetoed(self):
        valid, reason = parser.classify_message(
            "info@shop.com",
            "Your application has been received: claim your trade-in discount now")
        self.assertFalse(valid)

    def test_platform_loose_tier(self):
        valid, reason = parser.classify_message(
            "careers@myworkday.com", "Assistant Manager, BI at DFI Retail Group")
        self.assertTrue(valid)
        self.assertIn("loose", reason)

    def test_subscription_thanks_is_not_valid(self):
        valid, _ = parser.classify_message(
            "hello@saas.com", "Thank you for subscribing to our newsletter")
        self.assertFalse(valid)

    def test_purchase_thanks_is_not_valid(self):
        valid, _ = parser.classify_message("shop@store.com", "Thank you for your purchase")
        self.assertFalse(valid)

    def test_job_board_marketing_subjects_are_noise(self):
        for subject in ("Jobs picked for you at Acme",
                        "New jobs for you in Kuala Lumpur",
                        "Meet the team hiring now",
                        "Virtual career fair this week"):
            valid, _ = parser.classify_message("digest@jobstreet.com", subject)
            self.assertFalse(valid, subject)

    def test_verification_emails_are_noise(self):
        for subject in ("Please verify your email address for your account",
                        "Verify your candidate account",
                        "Confirm your identity for job PROTEGE Apprentice - 32927",
                        "Faris, please verify your new device"):
            valid, _ = parser.classify_message("careers@workday.com", subject)
            self.assertFalse(valid, subject)

    def test_platform_digests_and_task_notices_are_noise(self):
        for subject in ("FARIS ERHAN, new activity in jobs you applied for",
                        "PwC Application: You have pending tasks",
                        "A Task Awaits You: Questions for External Candidates",
                        "Your Resume Has Been Approved!"):
            valid, _ = parser.classify_message("no-reply@myworkday.com", subject)
            self.assertFalse(valid, subject)

    def test_acknowledge_receipt_is_valid(self):
        valid, _ = parser.classify_message(
            "", "Your recent application for Data Analyst",
            "We would like to acknowledge receipt of your application for the role.")
        self.assertTrue(valid)


class ExtractionQualityTests(MetaTestCase):
    def test_numeric_roles_rejected(self):
        self.assertFalse(parser.looks_like_role("4460583743"))
        self.assertTrue(parser.looks_like_role("MCTF 2026"))

    def test_role_subject_cleanup(self):
        self.assertEqual(
            parser._clean_role_name("Thank you for applying Renewal Business Pricing, Analyst"),
            "Renewal Business Pricing, Analyst")
        self.assertEqual(
            parser._clean_role_name("Your Job Application Has Been Received - Hong Leong Bank"),
            "")
        self.assertEqual(
            parser._clean_role_name("Thanks for applying to RHB Bank"), "Thanks for applying to RHB Bank")

    def test_company_prose_cleanup(self):
        self.assertEqual(parser._clean_company_name("ITA Asia Limited. Unfortunately"),
                         "ITA Asia Limited")
        self.assertEqual(parser._clean_company_name("us"), "")
        self.assertEqual(parser._clean_company_name("this"), "")
        self.assertEqual(parser._clean_company_name("CryoCord Sdn Bhd."), "CryoCord Sdn Bhd")

    def test_sent_confirmation_has_no_numeric_role(self):
        rec = parser.parse_message(
            self._meta(**{"from": "LinkedIn <jobs-noreply@linkedin.com>",
                          "subject": "Faris, your application was sent to TNG Digital"}))
        self.assertEqual(rec["role_title"], "")
        self.assertIn("TNG", rec["company_name"])

    def test_hiredly_housekeeping_is_invalid(self):
        for subject in ("Your Resume Has Been Approved!", "We Are Reviewing Your Resume"):
            rec = parser.parse_message(
                self._meta(**{"from": "Ashley from Hiredly <ashley@hiredly.com>",
                              "subject": subject}))
            self.assertFalse(rec["is_valid"], subject)

    def test_thank_you_for_applying_role_extracted(self):
        rec = parser.parse_message(
            self._meta(**{"from": "AIA <careers@aia.com>",
                          "subject": "Thank you for applying Renewal Business Pricing, Analyst"}))
        self.assertEqual(rec["role_title"], "Renewal Business Pricing, Analyst")


class StatusPrecisionTests(MetaTestCase):
    def test_confirmation_is_applied_not_interview(self):
        rec = parser.parse_message(
            self._meta(subject="Your application to Acme",
                       snippet="If shortlisted, we will invite you for an interview"),
            body_text=("Thank you for your interest in Acme. If shortlisted, we may invite "
                       "you for an interview as part of the assessment process."))
        self.assertEqual(rec["current_status"], "Applied")

    def test_unfortunately_volume_does_not_reject(self):
        rec = parser.parse_message(
            self._meta(subject="Thank you for applying to Acme",
                       snippet="Unfortunately we receive a high volume of applications"),
            body_text=("Thank you for applying to Acme. Unfortunately we receive a high "
                       "volume of applications each day, so our team will review yours."))
        self.assertEqual(rec["current_status"], "Applied")

    def test_interview_invitation_detected(self):
        rec = parser.parse_message(
            self._meta(subject="Interview invitation - Data Analyst at Acme",
                       snippet="We would like to invite you to an interview"),
            body_text="We would like to invite you to an interview next Tuesday.")
        self.assertEqual(rec["current_status"], "Interview")

    def test_assessment_detected(self):
        rec = parser.parse_message(
            self._meta(subject="Your Acme online assessment",
                       snippet="Please complete the assessment within 5 days"),
            body_text="Please complete the online assessment within 5 days.")
        self.assertEqual(rec["current_status"], "Assessment / OA")

    def test_rejection_detected(self):
        rec = parser.parse_message(
            self._meta(subject="Update on your application for Data Analyst at Acme",
                       snippet="We regret to inform you that you were not successful"),
            body_text=("We regret to inform you that after careful consideration we have "
                       "decided to move forward with other candidates."))
        self.assertEqual(rec["current_status"], "Rejected")

    def test_offer_detected(self):
        rec = parser.parse_message(
            self._meta(subject="Congratulations from Acme",
                       snippet="We wanted to share some news about your application"))
        self.assertNotEqual(rec["current_status"], "Offer")
        rec = parser.parse_message(
            self._meta(subject="Your offer from Acme",
                       snippet="We are pleased to offer you the position"),
            body_text="We are pleased to offer you the position of Data Analyst.")
        self.assertEqual(rec["current_status"], "Offer")


class ParseMessageTests(MetaTestCase):
    def test_metadata_only_parse(self):
        rec = parser.parse_message(self._meta(snippet="Thank you for applying"))
        self.assertEqual(rec["thread_id"], "T1")
        self.assertEqual(rec["company_name"], "Acme")
        self.assertTrue(rec["is_valid"])
        self.assertTrue(rec["needs_body"])

    def test_body_parse_extracts_url_and_platform(self):
        body = ("Thank you for applying to Acme for the Data Analyst role. "
                "View your application: https://acme.myworkdayjobs.com/en-US/careers/job/Data-Analyst_R123")
        rec = parser.parse_message(self._meta(), body_text=body)
        self.assertIn("myworkdayjobs.com", rec["job_url"])
        self.assertEqual(rec["source_platform"], "Direct ATS")
        self.assertFalse(rec["needs_body"])

    def test_status_detection(self):
        rec = parser.parse_message(
            self._meta(subject="Interview invitation - Data Analyst at Acme",
                       snippet="We would like to schedule an interview"))
        self.assertEqual(rec["current_status"], "Interview")

    def test_rejection_status(self):
        rec = parser.parse_message(
            self._meta(subject="Update on your application for Data Analyst at Acme",
                       snippet="We regret to inform you that you were not successful"))
        self.assertEqual(rec["current_status"], "Rejected")

    def test_role_from_linkedin_slug_when_unknown(self):
        body = ("Hi, your application was sent. "
                "https://www.linkedin.com/jobs/view/quantitative-risk-analyst-at-acme-4012345678")
        rec = parser.parse_message(
            self._meta(subject="Your job application", snippet="application sent"),
            body_text=body)
        self.assertIn("Quantitative Risk Analyst", rec["role_title"])

    def test_fallback_company_and_role(self):
        rec = parser.parse_message(
            self._meta(**{"from": "recruiter@mystery-firm.com",
                          "subject": "A task awaits you",
                          "snippet": "Please complete this assessment"}))
        self.assertEqual(rec["company_name"], "Mystery Firm")

    def test_body_recovers_company_and_role_from_generic_subject(self):
        body = ("Thank you for applying. You have applied for the position of Data Analyst "
                "at Prudential Assurance Berhad. We will review your application.")
        rec = parser.parse_message(
            self._meta(**{"from": "no-reply@myhr.com",
                          "subject": "Your application was successfully submitted",
                          "snippet": "application submitted"}),
            body_text=body)
        self.assertEqual(rec["company_name"], "Prudential Assurance Berhad")
        self.assertIn("Data Analyst", rec["role_title"])

    def test_platform_company_replaced_by_body_employer(self):
        body = ("Thank you for your interest in Tokio Marine Life Insurance Malaysia. "
                "Your application for the Risk Analyst role has been received.")
        rec = parser.parse_message(
            self._meta(**{"from": "jobs@workday.com",
                          "subject": "Application received",
                          "snippet": "application received"}),
            body_text=body)
        self.assertIn("Tokio Marine", rec["company_name"])
        self.assertIn("Risk Analyst", rec["role_title"])

    def test_quoted_reply_does_not_leak_status(self):
        body = ("Thanks for your note.\n\n"
                "On Mon, 1 Sep 2026 at 10:00, HR <hr@acme.com> wrote:\n"
                "> We regret to inform you that you were unsuccessful.")
        rec = parser.parse_message(self._meta(subject="Re: Your application",
                                              snippet="Thanks for your note"),
                                   body_text=body)
        self.assertNotEqual(rec["current_status"], "Rejected")

    def test_sanitize_body_strips_quotes_and_signature(self):
        body = ("The first message body.\n\n"
                "On Tue, 2 Sep 2026 at 09:00, Someone <a@b.com> wrote:\n"
                "> old quoted text\n\n-- \nRegards,\nSignature")
        cleaned = parser.sanitize_body(body)
        self.assertIn("The first message body.", cleaned)
        self.assertNotIn("old quoted text", cleaned)
        self.assertNotIn("Signature", cleaned)

    def test_extract_company_role_helper(self):
        company, role = parser.extract_company_role(
            "You have applied for the position of Business Intelligence Analyst at "
            "AIA Bhd. Good luck!")
        self.assertEqual(company, "AIA Bhd")
        self.assertIn("Business Intelligence Analyst", role)

    def test_company_from_invitation_subject(self):
        rec = parser.parse_message(
            self._meta(**{"from": "Lever <notifications@lever.co>",
                          "subject": "Interview invitation - Quantitative Risk Analyst "
                                     "at Gamma Bank",
                          "snippet": "We would like to invite you to an interview."}),
            body_text="We would like to invite you to an interview. Pick a slot.")
        self.assertIn("Gamma", rec["company_name"])
        self.assertIn("Quantitative Risk Analyst", rec["role_title"])
        self.assertEqual(rec["current_status"], "Interview")

    def test_company_from_invitation_for_company_subject(self):
        rec = parser.parse_message(
            self._meta(**{"from": "HackerRank <support@hackerrank.com>",
                          "subject": "Your assessment invitation for Delta Analytics",
                          "snippet": "Complete the online assessment within 5 days."}),
            body_text="Complete the online assessment within 5 days to proceed.")
        self.assertIn("Delta Analytics", rec["company_name"])
        self.assertEqual(rec["current_status"], "Assessment / OA")


class JobUrlTests(unittest.TestCase):
    def test_prefers_job_link_over_unsubscribe(self):
        body = ("Welcome https://example.com/unsubscribe now. "
                "View job posting: https://jobs.lever.co/acme/abc123")
        url = parser.extract_job_url(body)
        self.assertEqual(url, "https://jobs.lever.co/acme/abc123")

    def test_strips_tracking_params(self):
        body = "Apply here https://acme.com/apply?utm_source=x&gclid=1&job=5"
        url = parser.extract_job_url(body)
        self.assertNotIn("utm_", url)
        self.assertNotIn("gclid", url)

    def test_rejects_image_links(self):
        self.assertEqual(parser.extract_job_url("https://cdn.example.com/logo.png"), "")

    def test_rejects_social_and_profile_links(self):
        body = ("Follow us https://www.linkedin.com/company/acme and "
                "https://www.linkedin.com/in/recruiter — booking: https://calendly.com/acme")
        self.assertEqual(parser.extract_job_url(body), "")


class CategoriserTests(unittest.TestCase):
    def test_job_types(self):
        self.assertEqual(parser.classify_job_type("Data Analyst Internship"), "Internship")
        self.assertEqual(parser.classify_job_type("Graduate Programme 2026"),
                         "Graduate Programme / Trainee")
        self.assertEqual(parser.classify_job_type("Analyst (12 months contract)"),
                         "Contract / Fixed-Term")
        self.assertEqual(parser.classify_job_type("Data Analyst"), "Full-Time")

    def test_seniority(self):
        self.assertEqual(parser.classify_seniority("Data Analyst Intern"), "Internship")
        self.assertEqual(parser.classify_seniority("Senior Risk Analyst"), "Senior / Lead")
        self.assertEqual(parser.classify_seniority("Head of Analytics"), "Manager / VP+")

    def test_industry(self):
        self.assertEqual(parser.classify_industry("Prudential Assurance"), "Insurance")
        self.assertEqual(parser.classify_industry("Maybank Berhad"), "Banking & Finance")
        self.assertEqual(parser.classify_industry("Totally Unknown Co"), "Other")


class HelperTests(unittest.TestCase):
    def test_is_platform_company(self):
        self.assertTrue(parser.is_platform_company("myHR"))
        self.assertTrue(parser.is_platform_company("JobStreet"))
        self.assertTrue(parser.is_platform_company(""))
        self.assertFalse(parser.is_platform_company("Prudential"))

    def test_looks_like_role(self):
        self.assertTrue(parser.looks_like_role("Data Analyst"))
        self.assertFalse(parser.looks_like_role("hi there"))
        self.assertFalse(parser.looks_like_role("a"))


if __name__ == "__main__":
    unittest.main()
