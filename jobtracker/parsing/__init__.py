"""Deterministic, offline email parser (with an optional, budget-capped Claude fallback)."""
from .categorize import (
    classify_industry,
    classify_job_type,
    classify_role_family,
    classify_seniority,
)
from .classify import Verdict, classify_message
from .extract import (
    extract_company_role,
    extract_job_url,
    is_junk_company_name,
    is_platform_company,
    looks_like_role,
)
from .pipeline import ParsedMessage, finalize, message_date, parse_message
from .status import detect_status
from .text import sanitize_body

__all__ = [
    "ParsedMessage",
    "Verdict",
    "classify_industry",
    "classify_job_type",
    "classify_message",
    "classify_role_family",
    "classify_seniority",
    "detect_status",
    "extract_company_role",
    "extract_job_url",
    "finalize",
    "is_junk_company_name",
    "is_platform_company",
    "looks_like_role",
    "message_date",
    "parse_message",
    "sanitize_body",
]
