"""Behavioural tests for the deterministic email parser (``jobtracker.parsing``).

The first half is a faithful port of the v1 suite (``tests/test_parser.py`` at 9da4efb,
48 tests) onto the modular API: every behavioural assertion is kept, only the call sites
changed (``parser._clean_company_name`` -> ``extract.clean_company_name``,
``record['current_status']`` -> ``record.status`` …). The second half covers behaviour the
rebuild added: confidence / ambiguity flags, sentence-run company captures, notification
subjects that must never become role titles, honest message dates and role families.

Everything here is offline and deterministic — no network, no LLM.
"""
from __future__ import annotations

import unittest

from jobtracker.parsing import (
    classify_industry,
    classify_job_type,
    classify_message,
    classify_role_family,
    classify_seniority,
    detect_status,
    extract_company_role,
    extract_job_url,
    is_platform_company,
    looks_like_role,
    message_date,
    parse_message,
    sanitize_body,
)
from jobtracker.parsing.classify import NO_AFFIRMATION, Verdict
from jobtracker.parsing.extract import (
    clean_company_name,
    clean_role_name,
    company_from_display_name,
    company_from_sender_domain,
    company_role_from_card,
    is_decorated,
    role_from_subject_segments,
)
from jobtracker.parsing.status import outcome_sentences


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


# ============================================================== legacy port (v1 suite)


class ClassifyMessageTests(unittest.TestCase):
    def test_application_confirmation_is_valid(self):
        valid, reason = classify_message(
            "jobs@linkedin.com", "Thank you for applying to Acme for Data Analyst")
        self.assertTrue(valid)
        self.assertEqual(reason, "affirmed")

    def test_application_received_is_valid(self):
        valid, _ = classify_message(
            "no-reply@myworkday.com", "Application Received - Data Analyst at Acme")
        self.assertTrue(valid)

    def test_job_alert_is_noise(self):
        valid, reason = classify_message(
            "jobalert@linkedin.com", "Job Alert: 10 new Data Analyst jobs")
        self.assertFalse(valid)
        self.assertIn("blacklist", reason)

    def test_digest_is_noise(self):
        valid, _ = classify_message(
            "digest@jobstreet.com", "Your weekly digest of recommended jobs")
        self.assertFalse(valid)

    def test_rejection_is_valid(self):
        valid, _ = classify_message(
            "hr@acme.com", "We regret to inform you that you were unsuccessful")
        self.assertTrue(valid)

    def test_offer_is_valid(self):
        valid, _ = classify_message(
            "hr@acme.com", "Congratulations! We are pleased to offer you the role")
        self.assertTrue(valid)

    def test_non_job_commercial_is_vetoed(self):
        valid, _ = classify_message(
            "info@shop.com",
            "Your application has been received: claim your trade-in discount now")
        self.assertFalse(valid)

    def test_platform_loose_tier(self):
        valid, reason = classify_message(
            "careers@myworkday.com", "Assistant Manager, BI at DFI Retail Group")
        self.assertTrue(valid)
        self.assertIn("loose", reason)

    def test_subscription_thanks_is_not_valid(self):
        valid, _ = classify_message(
            "hello@saas.com", "Thank you for subscribing to our newsletter")
        self.assertFalse(valid)

    def test_purchase_thanks_is_not_valid(self):
        valid, _ = classify_message("shop@store.com", "Thank you for your purchase")
        self.assertFalse(valid)

    def test_job_board_marketing_subjects_are_noise(self):
        for subject in ("Jobs picked for you at Acme",
                        "New jobs for you in Kuala Lumpur",
                        "Meet the team hiring now",
                        "Virtual career fair this week"):
            valid, _ = classify_message("digest@jobstreet.com", subject)
            self.assertFalse(valid, subject)

    def test_verification_emails_are_noise(self):
        for subject in ("Please verify your email address for your account",
                        "Verify your candidate account",
                        "Confirm your identity for job PROTEGE Apprentice - 32927",
                        "Faris, please verify your new device"):
            valid, _ = classify_message("careers@workday.com", subject)
            self.assertFalse(valid, subject)

    def test_platform_digests_and_task_notices_are_noise(self):
        for subject in ("FARIS ERHAN, new activity in jobs you applied for",
                        "PwC Application: You have pending tasks",
                        "A Task Awaits You: Questions for External Candidates",
                        "Your Resume Has Been Approved!"):
            valid, _ = classify_message("no-reply@myworkday.com", subject)
            self.assertFalse(valid, subject)

    def test_acknowledge_receipt_is_valid(self):
        valid, _ = classify_message(
            "", "Your recent application for Data Analyst",
            "We would like to acknowledge receipt of your application for the role.")
        self.assertTrue(valid)


class ExtractionQualityTests(MetaTestCase):
    def test_numeric_roles_rejected(self):
        self.assertFalse(looks_like_role("4460583743"))
        self.assertTrue(looks_like_role("MCTF 2026"))

    def test_role_subject_cleanup(self):
        self.assertEqual(
            clean_role_name("Thank you for applying Renewal Business Pricing, Analyst"),
            "Renewal Business Pricing, Analyst")
        self.assertEqual(
            clean_role_name("Your Job Application Has Been Received - Hong Leong Bank"), "")
        self.assertEqual(
            clean_role_name("Thanks for applying to RHB Bank"), "Thanks for applying to RHB Bank")

    def test_company_prose_cleanup(self):
        self.assertEqual(clean_company_name("ITA Asia Limited. Unfortunately"), "ITA Asia Limited")
        self.assertEqual(clean_company_name("us"), "")
        self.assertEqual(clean_company_name("this"), "")
        self.assertEqual(clean_company_name("CryoCord Sdn Bhd."), "CryoCord Sdn Bhd")

    def test_sent_confirmation_has_no_numeric_role(self):
        rec = parse_message(
            self._meta(**{"from": "LinkedIn <jobs-noreply@linkedin.com>",
                          "subject": "Faris, your application was sent to TNG Digital"}))
        self.assertEqual(rec.role_title, "")
        self.assertIn("TNG", rec.company_name)

    def test_hiredly_housekeeping_is_invalid(self):
        for subject in ("Your Resume Has Been Approved!", "We Are Reviewing Your Resume"):
            rec = parse_message(
                self._meta(**{"from": "Ashley from Hiredly <ashley@hiredly.com>",
                              "subject": subject}))
            self.assertFalse(rec.is_valid, subject)

    def test_thank_you_for_applying_role_extracted(self):
        rec = parse_message(
            self._meta(**{"from": "AIA <careers@aia.com>",
                          "subject": "Thank you for applying Renewal Business Pricing, Analyst"}))
        self.assertEqual(rec.role_title, "Renewal Business Pricing, Analyst")


class StatusPrecisionTests(MetaTestCase):
    def test_confirmation_is_applied_not_interview(self):
        rec = parse_message(
            self._meta(subject="Your application to Acme",
                       snippet="If shortlisted, we will invite you for an interview"),
            body_text=("Thank you for your interest in Acme. If shortlisted, we may invite "
                       "you for an interview as part of the assessment process."))
        self.assertEqual(rec.status, "Applied")

    def test_unfortunately_volume_does_not_reject(self):
        rec = parse_message(
            self._meta(subject="Thank you for applying to Acme",
                       snippet="Unfortunately we receive a high volume of applications"),
            body_text=("Thank you for applying to Acme. Unfortunately we receive a high "
                       "volume of applications each day, so our team will review yours."))
        self.assertEqual(rec.status, "Applied")

    def test_interview_invitation_detected(self):
        rec = parse_message(
            self._meta(subject="Interview invitation - Data Analyst at Acme",
                       snippet="We would like to invite you to an interview"),
            body_text="We would like to invite you to an interview next Tuesday.")
        self.assertEqual(rec.status, "Interview")

    def test_assessment_detected(self):
        rec = parse_message(
            self._meta(subject="Your Acme online assessment",
                       snippet="Please complete the assessment within 5 days"),
            body_text="Please complete the online assessment within 5 days.")
        self.assertEqual(rec.status, "Assessment / OA")

    def test_rejection_detected(self):
        rec = parse_message(
            self._meta(subject="Update on your application for Data Analyst at Acme",
                       snippet="We regret to inform you that you were not successful"),
            body_text=("We regret to inform you that after careful consideration we have "
                       "decided to move forward with other candidates."))
        self.assertEqual(rec.status, "Rejected")

    def test_offer_detected(self):
        rec = parse_message(
            self._meta(subject="Congratulations from Acme",
                       snippet="We wanted to share some news about your application"))
        self.assertNotEqual(rec.status, "Offer")
        rec = parse_message(
            self._meta(subject="Your offer from Acme",
                       snippet="We are pleased to offer you the position"),
            body_text="We are pleased to offer you the position of Data Analyst.")
        self.assertEqual(rec.status, "Offer")


class ParseMessageTests(MetaTestCase):
    def test_metadata_only_parse(self):
        rec = parse_message(self._meta(snippet="Thank you for applying"))
        self.assertEqual(rec.thread_id, "T1")
        self.assertEqual(rec.company_name, "Acme")
        self.assertTrue(rec.is_valid)
        self.assertTrue(rec.needs_body)

    def test_body_parse_extracts_url_and_platform(self):
        body = ("Thank you for applying to Acme for the Data Analyst role. "
                "View your application: "
                "https://acme.myworkdayjobs.com/en-US/careers/job/Data-Analyst_R123")
        rec = parse_message(self._meta(), body_text=body)
        self.assertIn("myworkdayjobs.com", rec.job_url)
        self.assertEqual(rec.source_platform, "Direct ATS")
        self.assertFalse(rec.needs_body)

    def test_status_detection(self):
        rec = parse_message(
            self._meta(subject="Interview invitation - Data Analyst at Acme",
                       snippet="We would like to schedule an interview"))
        self.assertEqual(rec.status, "Interview")

    def test_rejection_status(self):
        rec = parse_message(
            self._meta(subject="Update on your application for Data Analyst at Acme",
                       snippet="We regret to inform you that you were not successful"))
        self.assertEqual(rec.status, "Rejected")

    def test_role_from_linkedin_slug_when_unknown(self):
        body = ("Hi, your application was sent. "
                "https://www.linkedin.com/jobs/view/quantitative-risk-analyst-at-acme-4012345678")
        rec = parse_message(
            self._meta(subject="Your job application", snippet="application sent"),
            body_text=body)
        self.assertIn("Quantitative Risk Analyst", rec.role_title)

    def test_fallback_company_and_role(self):
        rec = parse_message(
            self._meta(**{"from": "recruiter@mystery-firm.com",
                          "subject": "A task awaits you",
                          "snippet": "Please complete this assessment"}))
        self.assertEqual(rec.company_name, "Mystery Firm")

    def test_body_recovers_company_and_role_from_generic_subject(self):
        body = ("Thank you for applying. You have applied for the position of Data Analyst "
                "at Prudential Assurance Berhad. We will review your application.")
        rec = parse_message(
            self._meta(**{"from": "no-reply@myhr.com",
                          "subject": "Your application was successfully submitted",
                          "snippet": "application submitted"}),
            body_text=body)
        self.assertEqual(rec.company_name, "Prudential Assurance Berhad")
        self.assertIn("Data Analyst", rec.role_title)

    def test_platform_company_replaced_by_body_employer(self):
        body = ("Thank you for your interest in Tokio Marine Life Insurance Malaysia. "
                "Your application for the Risk Analyst role has been received.")
        rec = parse_message(
            self._meta(**{"from": "jobs@workday.com",
                          "subject": "Application received",
                          "snippet": "application received"}),
            body_text=body)
        self.assertIn("Tokio Marine", rec.company_name)
        self.assertIn("Risk Analyst", rec.role_title)

    def test_quoted_reply_does_not_leak_status(self):
        body = ("Thanks for your note.\n\n"
                "On Mon, 1 Sep 2026 at 10:00, HR <hr@acme.com> wrote:\n"
                "> We regret to inform you that you were unsuccessful.")
        rec = parse_message(self._meta(subject="Re: Your application",
                                       snippet="Thanks for your note"),
                            body_text=body)
        self.assertNotEqual(rec.status, "Rejected")

    def test_sanitize_body_strips_quotes_and_signature(self):
        body = ("The first message body.\n\n"
                "On Tue, 2 Sep 2026 at 09:00, Someone <a@b.com> wrote:\n"
                "> old quoted text\n\n-- \nRegards,\nSignature")
        cleaned = sanitize_body(body)
        self.assertIn("The first message body.", cleaned)
        self.assertNotIn("old quoted text", cleaned)
        self.assertNotIn("Signature", cleaned)

    def test_extract_company_role_helper(self):
        company, role = extract_company_role(
            "You have applied for the position of Business Intelligence Analyst at "
            "AIA Bhd. Good luck!")
        self.assertEqual(company, "AIA Bhd")
        self.assertIn("Business Intelligence Analyst", role)

    def test_company_from_invitation_subject(self):
        rec = parse_message(
            self._meta(**{"from": "Lever <notifications@lever.co>",
                          "subject": "Interview invitation - Quantitative Risk Analyst "
                                     "at Gamma Bank",
                          "snippet": "We would like to invite you to an interview."}),
            body_text="We would like to invite you to an interview. Pick a slot.")
        self.assertIn("Gamma", rec.company_name)
        self.assertIn("Quantitative Risk Analyst", rec.role_title)
        self.assertEqual(rec.status, "Interview")

    def test_company_from_invitation_for_company_subject(self):
        rec = parse_message(
            self._meta(**{"from": "HackerRank <support@hackerrank.com>",
                          "subject": "Your assessment invitation for Delta Analytics",
                          "snippet": "Complete the online assessment within 5 days."}),
            body_text="Complete the online assessment within 5 days to proceed.")
        self.assertIn("Delta Analytics", rec.company_name)
        self.assertEqual(rec.status, "Assessment / OA")


class JobUrlTests(unittest.TestCase):
    def test_prefers_job_link_over_unsubscribe(self):
        body = ("Welcome https://example.com/unsubscribe now. "
                "View job posting: https://jobs.lever.co/acme/abc123")
        self.assertEqual(extract_job_url(body), "https://jobs.lever.co/acme/abc123")

    def test_strips_tracking_params(self):
        url = extract_job_url("Apply here https://acme.com/apply?utm_source=x&gclid=1&job=5")
        self.assertNotIn("utm_", url)
        self.assertNotIn("gclid", url)

    def test_rejects_image_links(self):
        self.assertEqual(extract_job_url("https://cdn.example.com/logo.png"), "")

    def test_rejects_social_and_profile_links(self):
        body = ("Follow us https://www.linkedin.com/company/acme and "
                "https://www.linkedin.com/in/recruiter — booking: https://calendly.com/acme")
        self.assertEqual(extract_job_url(body), "")


class CategoriserTests(unittest.TestCase):
    def test_job_types(self):
        self.assertEqual(classify_job_type("Data Analyst Internship"), "Internship")
        self.assertEqual(classify_job_type("Graduate Programme 2026"),
                         "Graduate Programme / Trainee")
        self.assertEqual(classify_job_type("Analyst (12 months contract)"),
                         "Contract / Fixed-Term")
        self.assertEqual(classify_job_type("Data Analyst"), "Full-Time")

    def test_seniority(self):
        self.assertEqual(classify_seniority("Data Analyst Intern"), "Internship")
        self.assertEqual(classify_seniority("Senior Risk Analyst"), "Senior / Lead")
        self.assertEqual(classify_seniority("Head of Analytics"), "Manager / VP+")

    def test_industry(self):
        self.assertEqual(classify_industry("Prudential Assurance"), "Insurance")
        self.assertEqual(classify_industry("Maybank Berhad"), "Banking & Finance")
        self.assertEqual(classify_industry("Totally Unknown Co"), "Other")


class HelperTests(unittest.TestCase):
    def test_is_platform_company(self):
        self.assertTrue(is_platform_company("myHR"))
        self.assertTrue(is_platform_company("JobStreet"))
        self.assertTrue(is_platform_company(""))
        self.assertFalse(is_platform_company("Prudential"))

    def test_looks_like_role(self):
        self.assertTrue(looks_like_role("Data Analyst"))
        self.assertFalse(looks_like_role("hi there"))
        self.assertFalse(looks_like_role("a"))


# ============================================================== behaviour added in v2


class ConfidenceAndAmbiguityTests(MetaTestCase):
    def test_verdict_is_iterable_and_carries_confidence(self):
        verdict = classify_message("hr@acme.com", "Application received - Data Analyst")
        self.assertIsInstance(verdict, Verdict)
        valid, reason = verdict
        self.assertTrue(valid)
        self.assertEqual(reason, "affirmed")
        self.assertGreaterEqual(verdict.confidence, 0.9)
        self.assertFalse(verdict.ambiguous)

    def test_blacklist_is_confident_noise(self):
        verdict = classify_message("jobalerts-noreply@linkedin.com", "Job Alert: 25 new jobs")
        self.assertFalse(verdict.is_valid)
        self.assertGreaterEqual(verdict.confidence, 0.95)

    def test_loose_platform_tier_is_ambiguous(self):
        verdict = classify_message("careers@myworkday.com",
                                   "Assistant Manager, BI at DFI Retail Group")
        self.assertTrue(verdict.is_valid)
        self.assertTrue(verdict.ambiguous)

    def test_platform_near_miss_is_ambiguous_but_other_senders_are_not(self):
        near = classify_message("no-reply@hiredly.com", "Something happened")
        far = classify_message("friend@gmail.com", "Lunch on Friday?")
        self.assertFalse(near.is_valid)
        self.assertTrue(near.ambiguous)
        self.assertFalse(far.is_valid)
        self.assertFalse(far.ambiguous)

    def test_parsed_message_ambiguous_when_employer_missing(self):
        rec = parse_message(self._meta(**{
            "from": "LinkedIn <jobs-noreply@linkedin.com>",
            "subject": "Your application was sent", "snippet": "application was sent"}))
        self.assertTrue(rec.is_valid)
        self.assertEqual(rec.company_name, "Unknown")
        self.assertTrue(rec.ambiguous)

    def test_complete_confirmation_is_not_ambiguous(self):
        rec = parse_message(self._meta(),
                            body_text="Thank you for applying to Acme for the Data Analyst role.")
        self.assertTrue(rec.is_valid)
        self.assertEqual(rec.company_name, "Acme")
        self.assertEqual(rec.role_title, "Data Analyst")
        self.assertFalse(rec.ambiguous)


class SentenceRunCompanyTests(MetaTestCase):
    def test_company_capture_stops_at_next_sentence(self):
        self.assertEqual(
            clean_company_name("Acme Sdn Bhd. The employer will be in touch"), "Acme Sdn Bhd")
        self.assertEqual(clean_company_name("Acme Sdn Bhd. We wish you luck"), "Acme Sdn Bhd")

    def test_jobstreet_body_company_does_not_run_on(self):
        rec = parse_message(
            self._meta(**{"from": "JobStreet <noreply@s.jobstreet.com>",
                          "subject": "Your application was successfully submitted",
                          "snippet": "application submitted"}),
            body_text=("Hi there, your application for Pricing Analyst was successfully "
                       "submitted to Acme Sdn Bhd. The employer will be in touch if your "
                       "application is shortlisted."))
        self.assertEqual(rec.company_name, "Acme Sdn Bhd")
        self.assertEqual(rec.role_title, "Pricing Analyst")


class NotificationSubjectRoleTests(MetaTestCase):
    def test_linkedin_sent_subject_is_not_a_role(self):
        for subject in ("Aina, your application was sent to Bayu Analytics",
                        "Daniel, your application was sent to Cahaya Bank"):
            self.assertFalse(looks_like_role(subject), subject)
            rec = parse_message(self._meta(**{
                "from": "LinkedIn <jobs-noreply@linkedin.com>", "subject": subject}))
            self.assertEqual(rec.role_title, "", subject)
            self.assertNotEqual(rec.company_name, "Unknown")

    def test_linkedin_body_supplies_role(self):
        rec = parse_message(
            self._meta(**{"from": "LinkedIn <jobs-noreply@linkedin.com>",
                          "subject": "Aina, your application was sent to Bayu Analytics"}),
            body_text=("Your application was sent to Bayu Analytics Data Analyst Bayu "
                       "Analytics Kuala Lumpur, Federal Territory of Kuala Lumpur, Malaysia "
                       "(On-site) Applied on May 02, 2026."))
        self.assertEqual(rec.company_name, "Bayu Analytics")
        self.assertEqual(rec.role_title, "Data Analyst")

    def test_status_sentences_are_not_roles(self):
        for text in ("Your application was successfully submitted",
                     "Application was sent", "your job application"):
            self.assertFalse(looks_like_role(text), text)


class MessageDateTests(unittest.TestCase):
    def test_internal_date_wins(self):
        # 2025-01-01 12:00 UTC — the same calendar day in every timezone from UTC-11 to UTC+11.
        self.assertEqual(message_date({"internal_date": 1735732800000}), "2025-01-01")

    def test_date_header_fallback(self):
        self.assertEqual(
            message_date({"internal_date": 0, "date_header": "Tue, 03 Mar 2026 09:00:00 +0000"}),
            "2026-03-03")

    def test_never_defaults_to_today(self):
        self.assertEqual(message_date({}), "")
        self.assertEqual(message_date({"internal_date": None, "date_header": "garbage"}), "")
        rec = parse_message({"thread_id": "T9", "subject": "Application received",
                             "from": "hr@acme.com"})
        self.assertEqual(rec.message_date, "")
        self.assertIn("no message date", rec.notes)


class RoleFamilyTests(unittest.TestCase):
    def test_role_families(self):
        cases = {
            "Actuarial Data Analyst": "Actuarial",  # first match wins
            "Pricing Analyst": "Actuarial",
            "Reserving Analyst": "Actuarial",
            "Credit Risk Analyst": "Risk & Quantitative",
            "Business Intelligence Analyst": "Data & Analytics",
            "Data Analyst": "Data & Analytics",
            "Business Analyst": "Business Analysis",
            "Claims Executive": "Insurance Operations",
            "Financial Analyst": "Finance & Accounting",
            "Brand Marketing Executive": "Marketing & Product",
            "Barista": "Other",
        }
        for title, family in cases.items():
            self.assertEqual(classify_role_family(title), family, title)

    def test_parse_sets_role_family(self):
        rec = parse_message(make_meta(subject="Thank you for applying to Acme for Pricing Analyst"))
        self.assertEqual(rec.role_title, "Pricing Analyst")
        self.assertEqual(rec.role_family, "Actuarial")


class NearMissConfidenceTests(unittest.TestCase):
    """Mail that reached the parser passed the Gmail job-mail search, so a no-affirmation
    verdict on recruiting-flavoured mail must be low-confidence (flagged for review), while
    genuinely unrelated mail stays a confident drop."""

    def test_personal_recruiter_next_steps_is_ambiguous(self):
        verdict = classify_message("Jane Tan <jane@orchidlabs.io>", "Next steps",
                                   "Let's find a time to chat.")
        self.assertFalse(verdict.is_valid)
        self.assertEqual(verdict.reason, NO_AFFIRMATION)
        self.assertLessEqual(verdict.confidence, 0.6)
        self.assertTrue(verdict.ambiguous)

    def test_personal_sender_at_company_domain_is_ambiguous(self):
        verdict = classify_message("Daniel Lim <daniel.lim@acme.io>", "Thursday", "See you then")
        self.assertTrue(verdict.ambiguous)

    def test_recruiting_vocabulary_is_ambiguous(self):
        verdict = classify_message("info@acme.io", "About the position",
                                   "We have a question about your CV.")
        self.assertFalse(verdict.is_valid)
        self.assertTrue(verdict.ambiguous)

    def test_unrelated_mail_stays_confident(self):
        for sender, subject, snippet in (
                ("friend@gmail.com", "Lunch on Friday?", "Mamak at 1?"),
                ("Hafiz <hafiz.personal@gmail.com>", "Photos", "From the trip"),
                ("noreply@shop.my", "Your parcel is on the way", "Tracking number 123")):
            verdict = classify_message(sender, subject, snippet)
            self.assertFalse(verdict.is_valid, subject)
            self.assertGreaterEqual(verdict.confidence, 0.85, subject)
            self.assertFalse(verdict.ambiguous, subject)

    def test_ambiguity_reaches_parsed_message(self):
        rec = parse_message(make_meta(**{"from": "Jane Tan <jane@orchidlabs.io>",
                                         "subject": "Next steps",
                                         "snippet": "Let's find a time to chat."}))
        self.assertTrue(rec.ambiguous)


class GenericSubjectRoleTests(unittest.TestCase):
    def test_generic_subjects_never_become_roles(self):
        for subject in ("Next steps", "Following up", "Quick question", "Re: Next steps",
                        "Great news!", "Re: Following up", "Checking in"):
            rec = parse_message(make_meta(**{"from": "Jane Tan <jane@orchidlabs.io>",
                                             "subject": subject,
                                             "snippet": "Let's find a time to chat."}))
            self.assertEqual(rec.role_title, "", subject)
            self.assertEqual(role_from_subject_segments(subject), "", subject)
            self.assertTrue(is_decorated(subject), subject)

    def test_decorated_subjects_yield_the_title(self):
        cases = {
            "Next steps - Data Analyst, Risk": "Data Analyst, Risk",
            "Re: Second round - Portfolio Risk Analyst": "Portfolio Risk Analyst",
            "Cendana Wealth - Update on Job: Investment Risk Analyst": "Investment Risk Analyst",
            "IFRS 17 Reporting Analyst - Application Outcome": "IFRS 17 Reporting Analyst",
            "Panggilan Temuduga / Interview Invitation - Reserving Analyst": "Reserving Analyst",
            "Invitation: Final interview - Pricing Analyst (Faris Erhan) @ Thu Oct 22, 2026 2pm":
                "Pricing Analyst",
        }
        for subject, role in cases.items():
            self.assertEqual(role_from_subject_segments(subject), role, subject)

    def test_requisition_ids_are_stripped(self):
        self.assertEqual(clean_role_name("Investment Risk Analyst (2600123)"),
                         "Investment Risk Analyst")
        self.assertEqual(clean_role_name("IFRS 17 Reporting Analyst (Job Req 10234)"),
                         "IFRS 17 Reporting Analyst")


class StatusRuleTests(MetaTestCase):
    def test_conditional_outcomes_are_not_outcomes(self):
        for text in (
                "We acknowledge receipt of your application. If you do not hear from us within "
                "four weeks, please consider your application unsuccessful.",
                "Thank you for applying. Due to the high volume of applications, unfortunately "
                "we are unable to respond to each applicant individually.",
                "Your application has been received. If you are shortlisted for an interview, "
                "we will contact you within two weeks."):
            self.assertEqual(detect_status(text), "Applied", text)

    def test_conditional_sentences_are_dropped_but_others_kept(self):
        kept = outcome_sentences("we regret to inform you. if shortlisted we will call you.")
        self.assertIn("regret", kept)
        self.assertNotIn("shortlisted", kept)

    def test_rejection_phrasings(self):
        for text in ("we've decided not to move forward at this time",
                     "we have decided to pursue other applicants",
                     "we've decided to proceed with other applicants for this role",
                     "we have decided to offer the role to another candidate",
                     "the employer has decided not to progress your application",
                     "The position has now been filled",
                     "we won't be progressing your application to the final round",
                     "Status: Not suitable",
                     "permohonan anda tidak berjaya"):
            self.assertEqual(detect_status(text), "Rejected", text)

    def test_congratulations_alone_is_not_an_offer(self):
        for text in ("Congratulations Faris! Your application for the position of Analyst has "
                     "been successfully submitted.",
                     "Congratulations, you have been shortlisted for an interview for the role."):
            self.assertNotEqual(detect_status(text), "Offer", text)
        self.assertEqual(detect_status("the team would like to offer you the Data Analyst role"),
                         "Offer")

    def test_one_way_video_interview_is_an_assessment(self):
        self.assertEqual(
            detect_status("The next step is a recorded on-demand video interview on HireVue."),
            "Assessment / OA")
        self.assertEqual(detect_status("Please join a video interview with the hiring manager."),
                         "Interview")

    def test_assessment_centre_is_not_an_interview(self):
        self.assertEqual(
            detect_status("We would like to invite you to our Assessment Centre on 4 November."),
            "Assessment / OA")


class NonJobApplicationTests(unittest.TestCase):
    def test_non_job_applications_are_noise(self):
        for sender, subject, snippet in (
                ("alerts@bank.my", "Your credit card application has been received",
                 "Thank you for applying for the Platinum Credit Card."),
                ("pgadmissions@uni.edu.my", "Application received: Master of Data Science",
                 "We have received your application to the programme."),
                ("pgadmissions@uni.edu.my", "Offer of Admission",
                 "We are pleased to offer you a place in the programme."),
                ("promo@shop.my", "We've extended our offer", "Shop now - offer ends Sunday."),
                ("promo@shop.my", "Congratulations!", "We're pleased to offer you RM20 off."),
                ("noreply@insurer.my", "Your motor insurance application is complete",
                 "Your application has been received. Policy number: X1."),
                ("noreply@sapsf.com", "Welcome", "Thank you for your interest in careers at Acme. "
                 "Your candidate profile has been created.")):
            self.assertFalse(classify_message(sender, subject, snippet).is_valid, subject)


class JobMailIsNeverVetoedTests(unittest.TestCase):
    """The non-job vetoes must not catch banking / insurance *jobs* — the owner's field."""

    def test_finance_and_insurance_job_mail_stays_valid(self):
        for subject, snippet in (
                ("Thank you for applying - Credit Card Risk Analyst",
                 "We have received your application for the Credit Card Risk Analyst position."),
                ("Application received: Motor Insurance Pricing Analyst",
                 "Your application for Motor Insurance Pricing Analyst has been received."),
                ("Application received: Loan Operations Executive",
                 "Thank you for your application for the Loan Application Processing role."),
                ("Thank you for applying!",
                 "Your application has been received. Not the right fit? Join our talent "
                 "community.")):
            self.assertTrue(classify_message("careers@acmebank.com.my", subject, snippet).is_valid,
                            subject)


class ExtractionRuleTests(unittest.TestCase):
    def test_person_display_names_are_not_employers(self):
        self.assertEqual(company_from_display_name("Nur Aisyah Rahman <aisyah.rahman@acme.com>"),
                         "")
        self.assertEqual(company_from_display_name("Jane Tan <jtan@acme.com>"), "")
        self.assertEqual(company_from_display_name("Cendana Wealth <cendanawealth@taleo.net>"),
                         "Cendana Wealth")
        self.assertEqual(company_from_display_name("Unit Sumber Manusia <hr@acme.com.my>"), "")

    def test_company_verified_by_sender_domain(self):
        text = "Thank you for applying for the role at Tasik Actuarial Consulting. Regards"
        self.assertEqual(company_from_sender_domain("careers@tasikactuarial.com", text),
                         "Tasik Actuarial Consulting")
        self.assertEqual(company_from_sender_domain("hr@mahkotagi.com.my",
                                                    "Mahkota General Insurance Berhad"),
                         "Mahkota General Insurance Berhad")
        self.assertEqual(company_from_sender_domain("jobs-noreply@linkedin.com", text), "")
        self.assertEqual(company_from_sender_domain("careers@other.com", text), "")

    def test_card_layout(self):
        body = ("Application submitted\nBusiness Data Analyst\nHalia Foods Bhd - Shah Alam\n"
                "The following items were sent to Halia Foods Bhd.")
        self.assertEqual(company_role_from_card(body), ("Halia Foods Bhd", "Business Data Analyst"))

    def test_company_run_ons_are_cut(self):
        self.assertEqual(clean_company_name("Sinar Logistics in Shah Alam, Selangor"),
                         "Sinar Logistics")
        self.assertEqual(clean_company_name("Halia Foods Bhd has"), "Halia Foods Bhd")
        self.assertEqual(clean_company_name("Capital Modelling Analyst position at Andaman Re"),
                         "Andaman Re")
        self.assertEqual(clean_company_name("Pantai Timur Bank for the Credit Risk Analyst role"),
                         "Pantai Timur Bank")


class StatusRankTests(unittest.TestCase):
    def test_rejection_outranks_thank_you(self):
        self.assertEqual(
            detect_status("Thank you for applying. Unfortunately you were not selected."),
            "Rejected")

    def test_no_status_evidence_is_empty(self):
        self.assertEqual(detect_status("Lunch on Friday?"), "")


if __name__ == "__main__":
    unittest.main()
