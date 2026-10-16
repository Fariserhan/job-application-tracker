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

from ..domain import APPLIED, DETECTION_RANK
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
    company_from_sender,
    detect_platform,
    extract_company_role,
    extract_job_url,
    is_platform_company,
    looks_like_role,
    parse_subject,
    refine_company_role,
    role_from_application_snippet,
    role_from_subject,
    role_from_url_slug,
)
from .status import detect_status
from .text import sanitize_body


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


def parse_message(meta: dict, body_text: str = "") -> ParsedMessage:
    subject = meta.get("subject") or "(no subject)"
    sender = meta.get("from", "")
    snippet = meta.get("snippet", "")

    subj_company, subj_role = parse_subject(subject)
    verdict = classify_message(sender, subject, snippet)
    record = ParsedMessage(
        message_id=meta.get("id", ""),
        thread_id=meta["thread_id"],
        message_date=message_date(meta),
        company_name=subj_company or company_from_sender(sender, subject),
        role_title=subj_role or role_from_subject(subject),
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
        body_company, body_role = extract_company_role(sanitized)
        if body_company and not is_platform_company(body_company) and (
                not record.company_name or is_platform_company(record.company_name)):
            record.company_name = body_company
        if is_platform_company(record.company_name):
            # Platform senders often name the employer only in the subject
            # ("…, your application was sent to Acme").
            subject_company, _ = extract_company_role(subject)
            if subject_company:
                record.company_name = subject_company
        if not body_role:
            known = record.company_name if not is_platform_company(record.company_name) else ""
            body_role = role_from_application_snippet(sanitized, known or body_company)
        if body_role and (not record.role_title or not looks_like_role(record.role_title)
                          or record.role_title.lower() == subject.lower()):
            record.role_title = body_role
        # Body evidence is stronger than the subject, but never downgrades it.
        body_status = detect_status(sanitized)
        if body_status and DETECTION_RANK[body_status] >= DETECTION_RANK.get(record.detected_status, -1):
            record.detected_status = body_status
    else:
        missing_fields = not (record.company_name and record.role_title and record.detected_status)
        second_chance = record.filter_reason == NO_AFFIRMATION
        record.needs_body = record.is_valid or missing_fields or second_chance

    finalize(record, snippet)
    return record


def finalize(record: ParsedMessage, snippet: str = "") -> ParsedMessage:
    """Quality gate + categorisation. Idempotent; also run after an LLM correction."""
    if not record.company_name:
        record.company_name = company_fallback(record.sender)
    if not record.role_title:
        # Only fall back to the subject when it actually reads like a job title.
        record.role_title = record.subject if looks_like_role(record.subject) else ""
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
    record.role_title = role if looks_like_role(role) else ""
    if not record.message_date:
        record.notes.append("no message date")
    cat_text = f"{record.role_title} {record.subject} {snippet}"
    record.job_type = classify_job_type(cat_text)
    record.seniority_level = classify_seniority(record.role_title)
    record.industry = classify_industry(f"{record.company_name} {record.role_title}")
    record.role_family = classify_role_family(record.role_title)
    return record
