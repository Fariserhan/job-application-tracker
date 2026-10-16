"""Status detection: which pipeline event does this email announce?"""
from __future__ import annotations

import re

from ..domain import DETECTION_RANK
from .patterns import STATUS_KEYWORDS

_STATUS = [(status, [re.compile(p) for p in patterns]) for status, patterns in STATUS_KEYWORDS]


def detect_status(text: str) -> str:
    """The strongest status the text evidences, or '' when none.

    When several match, the highest-ranked wins (Rejected > Offer > Interview > Assessment >
    Applied): a rejection that thanks you for applying is a rejection.
    """
    lowered = (text or "").lower()
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
