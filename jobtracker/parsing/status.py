"""Status detection: which pipeline event does this email announce?"""
from __future__ import annotations

import re

from ..domain import DETECTION_RANK
from .patterns import CONDITIONAL_SENTENCE_PATTERNS, STATUS_KEYWORDS

_STATUS = [(status, [re.compile(p) for p in patterns]) for status, patterns in STATUS_KEYWORDS]
_CONDITIONAL = [re.compile(p) for p in CONDITIONAL_SENTENCE_PATTERNS]
# Sentence boundary: ., ! or ? followed by whitespace — but not the abbreviation dots in
# company names ("Sdn. Bhd.", "Co. Ltd").
_SENTENCE_SPLIT = re.compile(r"(?<!\bsdn\.)(?<!\bco\.)(?<!\bpte\.)(?<!\binc\.)(?<=[.!?])\s+")
# A recorded / on-demand "video interview" (HireVue and friends) has no interviewer: it is
# an assessment step, and must not be read as a live interview.
_ONE_WAY_VIDEO = re.compile(
    r"\b(?:on[- ]demand|one[- ]way|pre[- ]?recorded|recorded|asynchronous|digital) "
    r"video interviews?\b")


def outcome_sentences(text: str) -> str:
    """The text minus sentences about a *hypothetical* outcome.

    "If you do not hear from us within 4 weeks, please consider your application
    unsuccessful" is a confirmation, not a rejection; "only shortlisted candidates will be
    contacted for an interview" is not an interview invitation.
    """
    sentences = _SENTENCE_SPLIT.split(text)
    return " ".join(s for s in sentences if not any(p.search(s) for p in _CONDITIONAL))


def detect_status(text: str) -> str:
    """The strongest status the text evidences, or '' when none.

    When several match, the highest-ranked wins (Rejected > Offer > Interview > Assessment >
    Applied): a rejection that thanks you for applying is a rejection.
    """
    lowered = _ONE_WAY_VIDEO.sub("video assessment", outcome_sentences((text or "").lower()))
    detected = [status for status, patterns in _STATUS if any(p.search(lowered) for p in patterns)]
    if not detected:
        return ""
    return max(detected, key=lambda s: DETECTION_RANK[s])


def stronger(a: str, b: str) -> str:
    """The higher-ranked of two detected statuses ('' counts as nothing)."""
    if not a:
        return b
    if not b:
        return a
    return a if DETECTION_RANK.get(a, 0) >= DETECTION_RANK.get(b, 0) else b
