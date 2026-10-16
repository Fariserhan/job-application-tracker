"""Domain vocabulary shared by every layer: statuses, funnel stages, platforms.

The pipeline is modelled as an ordered set of *stages*. An application's current status
can move backwards out of the positive pipeline (Rejected / Ghosted), so the furthest stage
it ever reached is tracked separately from where it ended up. That way the funnel
stays accurate: a rejection after an interview still counts as an interview.
"""
from __future__ import annotations

SAVED = "Saved / Planning"
APPLIED = "Applied"
ASSESSMENT = "Assessment / OA"
INTERVIEW = "Interview"
OFFER = "Offer"
REJECTED = "Rejected"
GHOSTED = "Ghosted"
WITHDRAWN = "Withdrawn"

STATUSES = [SAVED, APPLIED, ASSESSMENT, INTERVIEW, OFFER, REJECTED, GHOSTED, WITHDRAWN]

# Positive-pipeline depth. Terminal outcomes carry no depth of their own.
STAGE_ORDER = {SAVED: 0, APPLIED: 1, ASSESSMENT: 2, INTERVIEW: 3, OFFER: 4}
TERMINAL = {OFFER, REJECTED, GHOSTED, WITHDRAWN}
ACTIVE = {APPLIED, ASSESSMENT, INTERVIEW}
# Statuses that prove the employer replied (an outcome or a step forward).
EMPLOYER_RESPONSE = {ASSESSMENT, INTERVIEW, OFFER, REJECTED}

# How strongly an email's evidence ranks when several statuses are detected in one message.
DETECTION_RANK = {SAVED: 0, APPLIED: 1, ASSESSMENT: 2, INTERVIEW: 3, OFFER: 4, REJECTED: 5,
                  GHOSTED: -1, WITHDRAWN: -1}

LEGACY_STATUS_ALIASES = {
    "Screening/OA": ASSESSMENT,
    "Ghosted/Inactive": GHOSTED,
    "Response": ASSESSMENT,
}

PLATFORMS = ["LinkedIn", "JobStreet", "Hiredly", "Prosple", "Indeed", "Direct ATS", "Other"]
DIRECT_PLATFORMS = {"Direct ATS", "Company Site"}

EVENT_SOURCES = ("gmail", "manual", "system", "repair", "legacy", "inferred", "llm")


def normalise_status(status: str | None) -> str:
    s = (status or "").strip()
    s = LEGACY_STATUS_ALIASES.get(s, s)
    return s if s in STATUSES else APPLIED


def stage_of(status: str | None) -> int:
    return STAGE_ORDER.get(normalise_status(status), 0)


def is_terminal(status: str | None) -> bool:
    return normalise_status(status) in TERMINAL
