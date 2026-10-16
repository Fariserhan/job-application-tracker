"""The anti-noise filter: is this email a real application event?

Strict, ordered tiers (first decisive tier wins):

1. hard blacklist on subject + sender (job alerts, digests, account housekeeping)
2. strong affirmation (explicit "application received/submitted"): wins over marketing
   tails, vetoed only by hard non-job commerce (refunds, trade-ins, accommodation)
3. weak affirmation (interview / assessment / rejection / offer wording): vetoed by any
   career-advice or digest signal
4. loose platform tier: a known ATS / job-board sender whose subject is a bare job title

Each verdict carries a confidence so the optional LLM fallback is only spent on the
genuinely ambiguous cases (tiers 3-4 and the near-misses), never on clear-cut mail.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .extract import looks_like_person, split_sender
from .patterns import (
    AFFIRMATION_PATTERNS,
    ASSESSMENT_VENDOR_SENDERS,
    ASSESSMENT_WORD_RE,
    BLACKLIST_SENDER_HINTS,
    DOMAIN_RE,
    GARBAGE_KEYWORDS,
    GARBAGE_SENDER_DOMAINS,
    HARD_GARBAGE,
    LOOSE_PLATFORM_SENDERS,
    LOOSE_SUBJECT_RE,
    SPONSOR_BLACKLIST,
    STRONG_AFFIRMATION_PATTERNS,
)

_BLACKLIST = [(p, re.compile(p)) for p in SPONSOR_BLACKLIST]
_STRONG = [re.compile(p) for p in STRONG_AFFIRMATION_PATTERNS]
_WEAK = [re.compile(p) for p in AFFIRMATION_PATTERNS]
_GARBAGE = [re.compile(p) for p in GARBAGE_KEYWORDS]
_HARD = [re.compile(p) for p in HARD_GARBAGE]
_BOARD_NOISE = [re.compile(p) for p in (
    r"alert", r"recommend", r"suggest", r"\bdigest\b",
    r"\bhiring\b", r"matching", r"notify", r"weekly", r"daily",
    r"follow\b", r"meet\b", r"puzzle", r"network", r"connect",
    r"new jobs?", r"more jobs?", r"top picks?", r"still accepting",
)]
_JOB_BOARD_DOMAINS = {
    "linkedin", "indeed", "glassdoor", "ziprecruiter", "monster", "careerbuilder", "jobcase",
    "naukri", "shine", "jobstreet", "hiredly", "prosple", "wobb", "workday", "myworkday",
    "myhr", "seek", "jobsdb", "foundit",
}

# Words that mark mail as recruiting-flavoured even when no affirmation rule fires.
_RECRUITING_VOCAB = re.compile(
    r"\b(?:interview\w*|chat|call|role|position|opportunit(?:y|ies)|candidat\w+|resume|cv|"
    r"hiring|recruit\w*|next steps?|shortlist\w*|vacanc(?:y|ies)|application|applied|"
    r"assessment|talent|headhunt\w*)\b")
FREEMAIL_DOMAINS = {"gmail", "googlemail", "yahoo", "hotmail", "outlook", "live", "icloud",
                    "me", "proton", "protonmail", "aol", "gmx", "ymail", "msn"}

# Reasons, so every verdict is auditable and the UI can explain itself.
AFFIRMED = "affirmed"
LOOSE = "platform application notice (loose)"
NOISY = "affirmed-but-noisy"
NO_AFFIRMATION = "no positive affirmation"
CAREER_NOISE = "noise (career-advice/digest)"
NON_JOB = "noise (non-job correspondence)"


@dataclass(frozen=True)
class Verdict:
    is_valid: bool
    reason: str
    confidence: float  # 0..1: how sure the rules are about `is_valid`

    @property
    def ambiguous(self) -> bool:
        return self.confidence < 0.75

    def __iter__(self):  # allows `valid, reason = classify_message(...)`
        yield self.is_valid
        yield self.reason


def is_personal_sender(sender: str) -> bool:
    """A named person writing from a company (non-freemail) domain. This is how recruiters
    and hiring managers write once a process is under way."""
    display, local, domain = split_sender(sender)
    if not domain or domain.split(".")[0] in FREEMAIL_DOMAINS or is_platform_sender(sender):
        return False
    return looks_like_person(display, local)


def is_platform_sender(sender: str) -> bool:
    s = (sender or "").lower()
    return any(p in s for p in LOOSE_PLATFORM_SENDERS)


def is_garbage(sender: str, subject: str, snippet: str = "") -> bool:
    text = f"{subject} {snippet}".lower()
    if any(p.search(text) for p in _GARBAGE):
        return True
    domain_match = DOMAIN_RE.search(sender or "")
    if domain_match:
        bare = domain_match.group(1).lower().split(".")[0]
        if bare in GARBAGE_SENDER_DOMAINS:
            return True
        if bare in _JOB_BOARD_DOMAINS and any(p.search(text) for p in _BOARD_NOISE):
            return True
    return False


def classify_message(sender: str, subject: str, snippet: str = "") -> Verdict:
    subj_sender = f"{sender} {subject}".lower()
    for raw, pattern in _BLACKLIST:
        if pattern.search(subj_sender):
            return Verdict(False, f"blacklist ({raw})", 0.97)
    for hint in BLACKLIST_SENDER_HINTS:
        if hint in subj_sender:
            return Verdict(False, f"blacklist-sender ({hint})", 0.95)

    text = f"{subject} {snippet}".lower()
    garbage = is_garbage(sender, subject, snippet)
    hard = any(p.search(text) for p in _HARD)

    # Strong explicit confirmations win even when the body has a marketing tail
    # (JobStreet / Workday confirmations append "jobs you may like" sections).
    if any(p.search(text) for p in _STRONG):
        if hard:
            return Verdict(False, NON_JOB, 0.8)
        return Verdict(True, AFFIRMED, 0.95)

    if any(p.search(text) for p in _WEAK):
        if hard:
            return Verdict(False, NON_JOB, 0.8)
        if not garbage:
            return Verdict(True, AFFIRMED, 0.85)
        return Verdict(False, NOISY, 0.55)

    # An assessment vendor writing about a test (invitations, reminders, extensions).
    if (not garbage and not hard and any(v in sender.lower() for v in ASSESSMENT_VENDOR_SENDERS)
            and ASSESSMENT_WORD_RE.search(text)):
        return Verdict(True, AFFIRMED, 0.8)

    # Loose tier: an ATS / job-platform sender whose subject is a bare job title
    # ("Assistant Manager, BI at DFI Retail Group") or mentions the application.
    if not garbage and is_platform_sender(sender) and LOOSE_SUBJECT_RE.search(text):
        return Verdict(True, LOOSE, 0.65)

    if garbage:
        return Verdict(False, CAREER_NOISE, 0.85)
    # Nothing matched. Everything reaching the parser already passed the Gmail job-mail
    # search, so a miss that still smells of recruiting (a platform sender, recruiting
    # vocabulary, or a person writing from a company domain, e.g. "Jane Tan <jane@acme.io>":
    # "Next steps") is a near-miss worth a second look rather than a confident drop.
    if is_platform_sender(sender) or _RECRUITING_VOCAB.search(text) or is_personal_sender(sender):
        return Verdict(False, NO_AFFIRMATION, 0.55)
    return Verdict(False, NO_AFFIRMATION, 0.85)
