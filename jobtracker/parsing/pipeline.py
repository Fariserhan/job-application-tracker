"""parse_message: one Gmail message -> one structured application record.

Two passes, so Gmail quota is only spent where it matters:

* metadata pass (subject, sender, snippet) decides whether the body is worth fetching —
  hard-blacklisted mail never costs a body download;
* body pass re-classifies with the full first-message text, finds the posting link and
  recovers the employer / role that generic subjects hide.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from ..domain import APPLIED, ASSESSMENT, DETECTION_RANK
from .categorize import (
    classify_industry,
    classify_job_type,
    classify_role_family,
    classify_seniority,
)
from .classify import NO_AFFIRMATION, classify_message
from .extract import (
    clean_company_name,
    clean_role_name,
    company_fallback,
    company_from_bracket,
    company_from_display_name,
    company_from_domain,
    company_from_sender_domain,
    company_role_from_card,
    detect_platform,
    extract_company_role,
    extract_job_url,
    is_decorated,
    is_platform_company,
    looks_like_role,
    parse_subject,
    plausible_company,
    refine_company_role,
    role_from_application_snippet,
    role_from_subject,
    role_from_subject_segments,
    role_from_url_slug,
    strip_platform_suffix,
)
from .patterns import ASSESSMENT_VENDOR_SENDERS
from .status import detect_status
from .text import first_message, sanitize_body


@dataclass
class ParsedMessage:
    message_id: str
    thread_id: str
    message_date: str  # YYYY-MM-DD; '' when Gmail gave no usable date
    company_name: str
    role_title: str
    source_platform: str
    detected_status: str  # what this email evidences ('' = nothing beyond "it's job mail")
    is_valid: bool
    filter_reason: str
    confidence: float
    job_url: str = ""
    body_excerpt: str = ""
    gmail_link: str = ""
    subject: str = ""
    sender: str = ""
    job_type: str = ""
    seniority_level: str = ""
    industry: str = ""
    role_family: str = ""
    needs_body: bool = False
    parsed_by: str = "rules"  # rules | llm
    notes: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return self.detected_status or APPLIED

    @property
    def ambiguous(self) -> bool:
        """Worth a second opinion: low-confidence verdict, or a valid row with no employer/role."""
        if self.confidence < 0.75:
            return True
        return self.is_valid and (self.company_name == "Unknown" or not self.role_title)

    def to_dict(self) -> dict:
        return asdict(self)


def message_date(meta: dict) -> str:
    """The email's own local date, or '' (never 'today' — that would fake history)."""
    ts = meta.get("internal_date") or 0
    if ts:
        return datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d")
    try:
        return parsedate_to_datetime(meta.get("date_header", "")).strftime("%Y-%m-%d")
    except (TypeError, ValueError, IndexError):
        return ""


def _today() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%d")


def _good_company(name: str) -> bool:
    return bool(name) and not is_platform_company(name) and plausible_company(name)


def _good_role(role: str) -> bool:
    return bool(role) and looks_like_role(role) and not is_decorated(role)


def resolve_company(sender: str, subject: str, body: str = "", card_company: str = "",
                    body_company: str = "") -> str:
    """The employer, from the most to the least trustworthy source:

    1. a name in the email that the sender's own domain spells ('tasikactuarial.com' ->
       'Tasik Actuarial Consulting')
    2. an explicit subject template ("Thank you for applying to <Company> for …")
    3. a job-board confirmation card
    4. subject phrasing ("<Company>: Next steps", "… at <Company>")
    5. the sender's display name (never a person's name or an HR department)
    6. body phrasing ("… position at <Company>")
    7. the sender's domain

    A later candidate that is the same name written in full ('Jati Holdings' ->
    'Jati Holdings Berhad') upgrades the winner.
    """
    subj_company, _ = parse_subject(subject)
    subject_text_company, _ = extract_company_role(subject)
    candidates = [
        company_from_sender_domain(sender, f"{subject}\n{first_message(body)}" if body else subject),
        subj_company,
        card_company,
        subject_text_company,
        company_from_bracket(subject) or strip_platform_suffix(company_from_display_name(sender)),
        body_company,
        company_from_domain(sender),
    ]
    good = [clean_company_name(c) for c in candidates if c]
    good = [c for c in good if _good_company(c)]
    if not good:
        return ""
    chosen = good[0]
    for other in good[1:]:
        if (other.lower().startswith(chosen.lower() + " ")
                and len(other.split()) - len(chosen.split()) <= 3):
            chosen = other
    return chosen


def resolve_role(subject: str, company: str, card_role: str = "", body_role: str = "") -> str:
    subj_role = parse_subject(subject)[1]
    _, subject_text_role = extract_company_role(subject)
    for candidate in (subj_role, card_role, subject_text_role, role_from_subject(subject),
                      role_from_subject_segments(subject, company), body_role):
        candidate = clean_role_name(candidate or "")
        if _good_role(candidate) and candidate.lower() != (company or "").lower():
            return candidate
    return ""


def parse_message(meta: dict, body_text: str = "") -> ParsedMessage:
    subject = meta.get("subject") or "(no subject)"
    sender = meta.get("from", "")
    snippet = meta.get("snippet", "")

    verdict = classify_message(sender, subject, snippet)
    company = resolve_company(sender, subject)
    record = ParsedMessage(
        message_id=meta.get("id", ""),
        thread_id=meta["thread_id"],
        message_date=message_date(meta),
        company_name=company,
        role_title=resolve_role(subject, company),
        source_platform=detect_platform(sender),
        detected_status=detect_status(f"{subject} {snippet}"),
        is_valid=verdict.is_valid,
        filter_reason=verdict.reason,
        confidence=verdict.confidence,
        gmail_link=meta.get("gmail_link", ""),
        subject=subject,
        sender=sender,
    )

    if body_text:
        sanitized = sanitize_body(body_text)
        record.job_url = extract_job_url(body_text)
        record.body_excerpt = sanitized[:600]
        body_verdict = classify_message(sender, subject, sanitized)
        record.is_valid = body_verdict.is_valid
        record.filter_reason = body_verdict.reason
        record.confidence = body_verdict.confidence
        if record.source_platform == "Other":
            record.source_platform = detect_platform(sender, record.job_url)
        # Generic subjects ("Your application was successfully submitted") hide the employer;
        # the body usually names it. The sanitised text has no URLs, so a link can never be
        # captured as a company name.
        card_company, card_role = company_role_from_card(body_text)
        body_company, body_role = extract_company_role(sanitized)
        record.company_name = resolve_company(sender, subject, body_text, card_company,
                                              body_company)
        if not body_role:
            body_role = role_from_application_snippet(
                sanitized, record.company_name or body_company)
        record.role_title = resolve_role(subject, record.company_name, card_role, body_role)
        # Body evidence is stronger than the subject, but never downgrades it.
        body_status = detect_status(sanitized)
        if body_status and DETECTION_RANK[body_status] >= DETECTION_RANK.get(record.detected_status, -1):
            record.detected_status = body_status
    else:
        missing_fields = not (record.company_name and record.role_title and record.detected_status)
        second_chance = record.filter_reason == NO_AFFIRMATION
        record.needs_body = record.is_valid or missing_fields or second_chance

    # Assessment vendors (HackerRank, SHL, …) only write about tests: a reminder or an
    # extension from one is an assessment-stage email even without "assessment" wording.
    if (record.is_valid and DETECTION_RANK.get(record.detected_status, 0) < DETECTION_RANK[ASSESSMENT]
            and any(v in sender.lower() for v in ASSESSMENT_VENDOR_SENDERS)):
        record.detected_status = ASSESSMENT

    finalize(record, snippet)
    return record


def finalize(record: ParsedMessage, snippet: str = "") -> ParsedMessage:
    """Quality gate + categorisation. Idempotent; also run after an LLM correction."""
    if not record.company_name:
        record.company_name = company_fallback(record.sender)
    if not record.role_title:
        # Only fall back to the subject when it is a bare job title — never "Next steps",
        # "Following up" or a notification sentence.
        subject = record.subject
        record.role_title = subject if _good_role(subject) and len(subject) <= 80 else ""
    record.company_name, record.role_title = refine_company_role(
        record.company_name, record.role_title, record.subject)
    if not looks_like_role(record.role_title) and record.job_url:
        record.role_title = role_from_url_slug(record.job_url) or record.role_title
    # Never store a pronoun / platform placeholder as the employer, or a status phrase /
    # job ID as the role.
    company = clean_company_name(record.company_name)
    if not company or is_platform_company(company):
        fallback = clean_company_name(company_fallback(record.sender))
        company = fallback if (fallback and not is_platform_company(fallback)) else "Unknown"
    record.company_name = company
    role = clean_role_name(record.role_title)
    record.role_title = role if looks_like_role(role) and not is_decorated(role) else ""
    if not record.message_date:
        record.notes.append("no message date")
    cat_text = f"{record.role_title} {record.subject} {snippet}"
    record.job_type = classify_job_type(cat_text)
    record.seniority_level = classify_seniority(record.role_title)
    record.industry = classify_industry(f"{record.company_name} {record.role_title}")
    record.role_family = classify_role_family(record.role_title)
    return record
