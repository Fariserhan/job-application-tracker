"""Rule-based categorisers: job type, seniority, industry and role family."""
from __future__ import annotations

import re

from .patterns import (
    DEFAULT_INDUSTRY,
    DEFAULT_JOB_TYPE,
    DEFAULT_ROLE_FAMILY,
    DEFAULT_SENIORITY,
    INDUSTRY_RULES,
    JOB_TYPE_RULES,
    ROLE_FAMILY_RULES,
    SENIORITY_RULES,
)


def _compile(rules):
    return [(label, re.compile(pattern)) for label, pattern in rules if pattern]


_JOB_TYPE = _compile(JOB_TYPE_RULES)
_SENIORITY = _compile(SENIORITY_RULES)
_INDUSTRY = _compile(INDUSTRY_RULES)
_ROLE_FAMILY = _compile(ROLE_FAMILY_RULES)

SENIORITY_RANK = {
    "Internship": 1,
    "Graduate / Entry": 2,
    "Executive / Junior": 3,
    "Analyst / Associate": 4,
    "Senior / Lead": 5,
    "Manager / VP+": 6,
    DEFAULT_SENIORITY: 0,
}


def _first(rules, text: str, default: str) -> str:
    lowered = (text or "").lower()
    for label, pattern in rules:
        if pattern.search(lowered):
            return label
    return default


def classify_job_type(text: str) -> str:
    return _first(_JOB_TYPE, text, DEFAULT_JOB_TYPE)


def classify_seniority(text: str) -> str:
    return _first(_SENIORITY, text, DEFAULT_SENIORITY)


def classify_industry(text: str) -> str:
    return _first(_INDUSTRY, text, DEFAULT_INDUSTRY)


def classify_role_family(title: str) -> str:
    return _first(_ROLE_FAMILY, title, DEFAULT_ROLE_FAMILY)
