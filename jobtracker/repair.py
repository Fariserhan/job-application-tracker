"""Audit & repair: re-judge stored rows with the *current* rules.

Parser rules improve over time; this corrects old mistakes without waiting for new email:

* flags false positives that slipped through (account verification, platform digests)
* repairs junk employer / role names from the stored subject and job text
* recovers real applications that an older rule filtered out as noise

Dry run by default; ``apply=True`` backs the database up first. Manual verdicts are never
touched, and repairs never move ``last_updated`` (waiting times stay honest).
"""
from __future__ import annotations

import re

from .config import Settings
from .parsing import classify_message, detect_status, is_junk_company_name, looks_like_role
from .parsing.categorize import classify_industry, classify_job_type, classify_seniority
from .parsing.extract import COMPANY_MARKER_RE, extract_company_role, role_from_subject
from .store import Repository

VERIFY_RE = re.compile(
    r"verify (?:your|the) (?:email|account|candidate|device|details|identity)|"
    r"verification (?:code|email|link|process)|confirm your (?:email|account|identity)|"
    r"activate your account|reset your password|one[- ]?time (?:code|password|passcode)|"
    r"candidate account|new device", re.IGNORECASE)
DIGEST_RE = re.compile(
    r"new activity in jobs you applied for|task awaits|pending tasks?|workfeed|"
    r"resume (?:has been|was) approved|reviewing your resume|let'?s stay connected|"
    r"we(?:'re| are) growing|have you finished your job applications", re.IGNORECASE)
ROLE_KEYWORD_RE = re.compile(
    r"\b(?:analyst|engineer|manager|executive|officer|associate|specialist|intern(?:ship)?|"
    r"consultant|director|lead|advisor|administrator|coordinator|developer|scientist|"
    r"architect|trainee|apprentice|accountant|auditor|actuar\w*|underwrit\w*|pricing|risk|"
    r"data|research|marketing|finance)\b", re.IGNORECASE)


def _junk_role(role: str, subject: str = "") -> bool:
    role = (role or "").strip()
    if not role or not looks_like_role(role):
        return True
    if subject and role.lower() == subject.lower().strip():
        return True
    return bool(DIGEST_RE.search(role) or VERIFY_RE.search(role))


def _suggest(subject: str, text: str) -> tuple[str, str]:
    company, role = extract_company_role(subject)
    role = role or role_from_subject(subject)
    if not company or not role:
        body_company, body_role = extract_company_role(text[:800])
        if not company and body_company and (COMPANY_MARKER_RE.search(body_company)
                                             or len(body_company.split()) >= 2):
            company = body_company
        if not role and body_role and ROLE_KEYWORD_RE.search(body_role):
            role = body_role
    return company, role


def plan_row(row: dict) -> dict | None:
    """The repair for one stored row, or None when nothing should change."""
    if (row.get("filter_reason") or "").startswith("manual") or row.get("origin") == "manual":
        return None
    subject = row.get("latest_subject") or ""
    text = row.get("job_description_snippet") or ""
    combined = f"{subject}\n{text}"
    company_now = (row.get("company_name") or "").strip()
    role_now = (row.get("role_title") or "").strip()
    fix: dict = {"thread_id": row["thread_id"]}

    if row["is_valid"]:
        if VERIFY_RE.search(combined) or DIGEST_RE.search(subject):
            fix.update(is_valid=False, reason="repair: account / digest notice")
            return fix
    else:
        if VERIFY_RE.search(combined) or DIGEST_RE.search(subject):
            return None
        verdict = classify_message("", subject, text)
        if not verdict.is_valid:
            return None
        fix.update(is_valid=True, reason="repair: recovered application")

    sug_company, sug_role = _suggest(subject, text)
    if is_junk_company_name(company_now) and sug_company and not is_junk_company_name(sug_company):
        fix["company_name"] = sug_company
    if _junk_role(role_now, subject) and sug_role and not _junk_role(sug_role):
        fix["role_title"] = sug_role
    status = detect_status(combined)
    if not row["is_valid"] and status and status != row.get("current_status"):
        fix["status"] = status
    role = fix.get("role_title", role_now)
    company = fix.get("company_name", company_now)
    for field, value in (("job_type", classify_job_type(f"{role} {subject}")),
                         ("seniority_level", classify_seniority(role)),
                         ("industry", classify_industry(f"{company} {role}"))):
        if value and value != (row.get(field) or ""):
            fix[field] = value
    return fix if len(fix) > 1 else None


def run(settings: Settings, apply: bool = False) -> tuple[list[dict], dict]:
    repo = Repository(settings)
    repo.init()
    plan = [p for p in (plan_row(r) for r in repo.applications()) if p]
    counts = {"rows": 0, "recovered": 0, "invalidated": 0, "company": 0, "role": 0, "status": 0}
    if not apply or not plan:
        counts["rows"] = len(plan)
        return plan, counts
    repo.backup("pre-repair")
    for fix in plan:
        tid = fix["thread_id"]
        if "is_valid" in fix:
            # Repair verdicts are not 'manual', so a later email can still prove otherwise.
            with repo.connect() as conn:
                conn.execute("UPDATE applications SET is_valid=?, filter_reason=? WHERE thread_id=?",
                             (1 if fix["is_valid"] else 0, fix["reason"], tid))
            counts["recovered" if fix["is_valid"] else "invalidated"] += 1
        fields = {k: fix[k] for k in ("company_name", "role_title", "job_type",
                                      "seniority_level", "industry") if k in fix}
        if fields:
            repo.update_fields(tid, **fields)
            counts["company"] += "company_name" in fields
            counts["role"] += "role_title" in fields
        if "status" in fix:
            repo.set_status(tid, fix["status"], note="audit repair", source="repair",
                            occurred_on=(repo.get(tid) or {}).get("last_updated"))
            counts["status"] += 1
        counts["rows"] += 1
    return plan, counts
