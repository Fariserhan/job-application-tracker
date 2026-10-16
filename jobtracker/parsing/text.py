"""Text normalisation for email bodies and links."""
from __future__ import annotations

import html as html_lib
import re

from .patterns import (
    QUOTE_SPLIT_PATTERNS,
    SIGNATURE_MARKERS,
    SIGNATURE_SEPARATOR,
    TRACKING_PARAMS_RE,
)

_QUOTE_SPLIT = [re.compile(p) for p in QUOTE_SPLIT_PATTERNS]
_SIGNATURE = [re.compile(m, re.IGNORECASE | re.MULTILINE)
              for m in [SIGNATURE_SEPARATOR, *SIGNATURE_MARKERS]]
_URL = re.compile(r"https?://\S+")

MAX_BODY_WORDS = 400


def strip_tracking_params(url: str) -> str:
    cleaned = TRACKING_PARAMS_RE.sub("", url)
    # If the *first* query param was the tracking one, the next '&' must become '?'
    # ('.../job?utm_source=x&job=5' -> '.../job&job=5' -> '.../job?job=5').
    if "?" in url and "?" not in cleaned:
        cleaned = cleaned.replace("&", "?", 1)
    return cleaned.rstrip("?&")


def first_message(body_text: str) -> str:
    """The new message only, line structure kept: quoted replies, signatures and URLs cut.

    Quoted previous messages leak old statuses into classification (a follow-up quoting an
    old rejection would read as Rejected). Cutting at the first quote marker keeps the
    verdict about the new message.
    """
    text = body_text or ""
    for pattern in _QUOTE_SPLIT:
        match = pattern.search(text)
        if match and match.start() > 0:
            text = text[: match.start()]
    for marker in _SIGNATURE:
        match = marker.search(text)
        if match and match.start() > 0:
            text = text[: match.start()]
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))
    text = html_lib.unescape(text)
    return _URL.sub(" ", text)


def sanitize_body(body_text: str) -> str:
    """First-message text collapsed to one line of at most MAX_BODY_WORDS words."""
    return " ".join(first_message(body_text).split()[:MAX_BODY_WORDS])


def strip_html(raw_html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw_html or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()
