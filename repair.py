"""Audit & repair pass over scraped application rows.

The scraper's rules improve over time; this module re-judges the *stored* rows with the
current parser so old mistakes are corrected without waiting for new email:

  * flags false positives (verification/account emails, platform digests, task notices)
  * repairs company / role / status from the stored subject + cached JD text
  * scans filtered-out rows for real applications that were missed and recovers them

Run it from the dashboard (🛠 Maintenance → "Repair & recover") or:

    python repair.py            # dry-run report, writes application_audit.csv
    python repair.py --apply    # backs up first, then applies the repairs
"""
import csv
import re
import sys

import parser
import storage

VERIFY_RE = re.compile(
    r"verify (?:your|the) (?:email|account|candidate|device|details|identity)|"
    r"verification (?:code|email|link|process)|"
    r"confirm your (?:email|account|identity)|confirm your identity|"
    r"activate your account|reset your password|"
    r"one[- ]?time (?:code|password|passcode)|candidate account|new device",
    re.IGNORECASE,
)

DIGEST_RE = re.compile(
    r"new activity in jobs you applied for|activity in jobs you applied for|"
    r"task awaits|pending tasks?|workfeed|"
    r"resume (?:has been|was) approved|reviewing your resume|"
    r"let'?s stay connected|we(?:'re| are) growing|"
    r"have you finished your job applications",
    re.IGNORECASE,
)

_PLATFORM_SENDER_HINTS = (
    "linkedin", "jobstreet", "hiredly", "prosple", "workday", "myhr", "oraclecloud",
)


def is_junk_company(company: str) -> bool:
    company = (company or "").strip()
    if len(company) < 2:
        return True
    return parser.is_junk_company_name(company)


def is_junk_role(role: str, subject: str = "") -> bool:
    role = (role or "").strip()
    if not role or not re.search(r"[A-Za-z]{2}", role):
        return True
    if not parser.looks_like_role(role):
        return True
    if subject and role.lower().strip() == subject.lower().strip():
        return True
    return bool(DIGEST_RE.search(role) or VERIFY_RE.search(role))


_ROLE_KEYWORD_RE = re.compile(
    r"\b(?:analyst|engineer|manager|executive|officer|associate|specialist|intern|"
    r"internship|consultant|director|lead|advisor|administrator|coordinator|developer|"
    r"scientist|architect|trainee|apprentice|technician|clerk|accountant|auditor|"
    r"actuar\w*|underwrit\w*|claims?|pricing|risk|data|research|marketing|finance|"
    r"operations?|support|services?)\b",
    re.IGNORECASE,
)


def _suggest_fields(subject: str, snippet: str):
    company, role = parser.extract_company_role(subject)
    if not role:
        role = parser._extract_role(subject)
    if not role or not company:
        body = parser.sanitize_body(snippet)[:800]
        body_company, body_role = parser.extract_company_role(body)
        # Body prose is untrusted for the employer: require a company marker or 2+ words.
        if not company and body_company and (
                parser._COMPANY_MARKER_RE.search(body_company)
                or len(body_company.split()) >= 2):
            company = body_company
        # Body text is prose — only trust a role candidate that names a function.
        if not role and body_role and _ROLE_KEYWORD_RE.search(body_role):
            role = body_role
    if not role:
        role = parser.role_from_application_snippet(snippet, company or "")
    return company, role


_STATUS_RANK = parser.STATUS_RANK


def judge_row(row: dict) -> dict:
    """Re-judge one stored row and return its verdict + corrected fields."""
    subject = row.get("latest_subject") or ""
    snippet = row.get("job_description_snippet") or ""
    company = (row.get("company_name") or "").strip()
    role = (row.get("role_title") or "").strip()
    status = row.get("current_status") or "Applied"
    text = f"{subject}\n{snippet}"

    flags = []
    if VERIFY_RE.search(text):
        flags.append("verification/account")
    if DIGEST_RE.search(subject):
        flags.append("platform digest/task")

    sug_company, sug_role = _suggest_fields(subject, snippet)
    if not sug_company and not is_junk_company(company):
        sug_company = company
    if not sug_role and not is_junk_role(role, subject):
        sug_role = role

    # Status: adopt the message's evidence unless it would downgrade a terminal state or
    # roll back more than one stage.
    sug_status = parser._detect_status(text) or ""
    if sug_status and sug_status != status:
        if status in ("Offer", "Rejected") and _STATUS_RANK[sug_status] < _STATUS_RANK[status]:
            sug_status = ""
        elif _STATUS_RANK.get(status, 0) - _STATUS_RANK.get(sug_status, 0) > 1:
            sug_status = ""

    valid_now, _reason = parser.classify_message("", subject, snippet)
    if flags:
        verdict = "FALSE POSITIVE"
        reason = f"audit: {flags[0]}"
    elif valid_now:
        verdict = "REAL"
        reason = ""
    else:
        verdict = "REAL (checked)"
        reason = ""

    return {
        "flags": flags,
        "verdict": verdict,
        "valid": not flags,
        "reason": reason,
        "suggested_company": sug_company or "",
        "suggested_role": sug_role or "",
        "suggested_status": sug_status,
    }


def _reclassify(rep: dict, row: dict):
    """Recompute job type / seniority / industry from the (repaired) fields + JD."""
    target_company = rep.get("company", row.get("company_name") or "")
    target_role = rep["role"] if "role" in rep else (row.get("role_title") or "")
    snippet = (row.get("job_description_snippet") or "")[:300]
    subject = row.get("latest_subject") or ""
    targets = {
        "job_type": parser.classify_job_type(f"{target_role} {subject} {snippet}"),
        "seniority_level": parser.classify_seniority(target_role),
        "industry": parser.classify_industry(f"{target_company} {target_role} {snippet}"),
    }
    for field, value in targets.items():
        if value and value != (row.get(field) or ""):
            rep[field] = value


def plan_valid_repair(row: dict) -> dict | None:
    """Repair for a currently-valid row, or None when nothing should change."""
    judged = judge_row(row)
    rep = {"thread_id": row["thread_id"], "note": "audit repair"}

    if not judged["valid"]:
        rep["is_valid"] = False
        rep["reason"] = judged["reason"]
        rep["note"] = f"audit: {judged['reason']}"
        return rep

    company = (row.get("company_name") or "").strip()
    sug_company = judged["suggested_company"]
    if is_junk_company(company):
        target_company = sug_company if (sug_company and not is_junk_company(sug_company)) else "Unknown"
        if target_company != company:
            rep["company"] = target_company

    role = (row.get("role_title") or "").strip()
    sug_role = judged["suggested_role"]
    if is_junk_role(role, row.get("latest_subject") or ""):
        target_role = sug_role if (sug_role and not is_junk_role(sug_role)) else ""
        if target_role != role:
            rep["role"] = target_role

    if judged["suggested_status"] and judged["suggested_status"] != row["current_status"]:
        rep["status"] = judged["suggested_status"]

    _reclassify(rep, row)
    if len(rep) <= 2:  # only thread_id + note -> nothing to change
        return None
    return rep


def plan_recovery(row: dict) -> dict | None:
    """Recovery for a filtered-out row that is actually a real application."""
    subject = row.get("latest_subject") or ""
    snippet = row.get("job_description_snippet") or ""
    if VERIFY_RE.search(f"{subject}\n{snippet}") or DIGEST_RE.search(subject):
        return None
    valid, _reason = parser.classify_message("", subject, snippet)
    if not valid:
        return None
    company, role = _suggest_fields(subject, snippet)
    if not role:
        role = parser._extract_role(subject)
    rep = {
        "thread_id": row["thread_id"],
        "is_valid": True,
        "reason": "audit recovery",
        "note": "audit recovery — looked like a real application",
    }
    if company and not is_junk_company(company):
        rep["company"] = company
    if role and not is_junk_role(role, subject):
        rep["role"] = role
    sug_status = parser._detect_status(f"{subject}\n{snippet}")
    if sug_status and sug_status != row["current_status"]:
        rep["status"] = sug_status
    _reclassify(rep, row)
    return rep


def build_plan(rows: list | None = None):
    rows = rows if rows is not None else storage.get_all_rows()
    repairs = []
    for row in rows:
        rep = plan_valid_repair(row) if row["is_valid"] else plan_recovery(row)
        if rep:
            rep["_row"] = row
            repairs.append(rep)
    return repairs


def write_audit_csv(rows: list, path: str = "application_audit.csv"):
    """Full per-row judgment (valid rows: what is wrong; invalid rows: recovery state)."""
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["thread_id", "currently_valid", "verdict", "flags", "status",
                         "company_stored", "role_stored", "suggested_company",
                         "suggested_role", "suggested_status", "platform", "subject"])
        for row in rows:
            judged = judge_row(row)
            if row["is_valid"]:
                verdict = judged["verdict"]
            else:
                recovery = plan_recovery(row)
                verdict = "MISSED REAL (recover)" if recovery else "noise"
            writer.writerow([
                row["thread_id"], row["is_valid"], verdict, "; ".join(judged["flags"]),
                row["current_status"], row["company_name"], row["role_title"],
                judged["suggested_company"], judged["suggested_role"],
                judged["suggested_status"], row["source_platform"],
                (row.get("latest_subject") or "")[:160],
            ])
    return path


def run(apply: bool = False):
    rows = storage.get_all_rows()
    repairs = build_plan(rows)
    counts = {"rows": 0, "company": 0, "role": 0, "status": 0, "invalidated": 0,
              "recovered": 0, "job_type": 0, "seniority_level": 0, "industry": 0}
    if apply and repairs:
        backup = storage.backup_all()
        counts = storage.apply_repairs(repairs)
        print(f"[repair] backup written to {backup}")
    write_audit_csv(rows)
    if apply:
        print(f"[repair] applied: {counts}")
    else:
        invalidated = sum(1 for r in repairs if r.get("is_valid") is False)
        recovered = sum(1 for r in repairs if r.get("is_valid") is True)
        print(f"[repair] dry-run: {len(repairs)} rows planned "
              f"({invalidated} to invalidate, {recovered} to recover)")
    return repairs, counts


if __name__ == "__main__":
    run(apply="--apply" in sys.argv)
