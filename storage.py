import csv
import json
import os
import re
import shutil
import sqlite3
from datetime import datetime

import parser

DB_PATH = "applications.db"
CSV_PATH = "job_tracker.csv"
STATE_PATH = "sync_state.json"

COLUMNS = [
    "thread_id",
    "company_name",
    "role_title",
    "source_platform",
    "application_date",
    "current_status",
    "last_updated",
    "job_description_snippet",
    "job_url",
    "gmail_link",
    "latest_subject",
    "is_valid",
    "fit_score",
    "matched_skills",
    "missing_skills",
    "actionable_improvements",
    "filter_reason",
    "job_type",
    "seniority_level",
    "industry",
    "notes",
    "fit_source",
]

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS applications (
    thread_id TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    role_title TEXT NOT NULL,
    source_platform TEXT DEFAULT 'Other',
    application_date TEXT NOT NULL,
    current_status TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    job_description_snippet TEXT DEFAULT '',
    job_url TEXT DEFAULT '',
    gmail_link TEXT DEFAULT '',
    latest_subject TEXT DEFAULT '',
    is_valid INTEGER NOT NULL DEFAULT 1
)
"""

STATUS_MIGRATIONS = [
    ("UPDATE applications SET current_status = 'Assessment / OA' WHERE current_status = 'Screening/OA'"),
    ("UPDATE applications SET current_status = 'Ghosted' WHERE current_status = 'Ghosted/Inactive'"),
]

FIT_COLUMNS = {
    "fit_score": "REAL",
    "matched_skills": "TEXT DEFAULT ''",
    "missing_skills": "TEXT DEFAULT ''",
    "actionable_improvements": "TEXT DEFAULT ''",
    "filter_reason": "TEXT DEFAULT ''",
    "job_type": "TEXT DEFAULT ''",
    "seniority_level": "TEXT DEFAULT ''",
    "industry": "TEXT DEFAULT ''",
    "fit_source": "TEXT DEFAULT 'rule'",
    "fit_cv_sig": "TEXT DEFAULT ''",
    "ai_classified": "INTEGER DEFAULT 0",
}

NOTE_COLUMNS = {
    "notes": "TEXT DEFAULT ''",
}

CREATE_STATUS_HISTORY = """
CREATE TABLE IF NOT EXISTS status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    note TEXT DEFAULT '',
    changed_at TEXT
)
"""

CREATE_HISTORY_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_status_history_thread "
    "ON status_history (thread_id, id)"
)

CREATE_DISCOVERED = """
CREATE TABLE IF NOT EXISTS discovered_jobs (
    job_key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT DEFAULT '',
    source TEXT DEFAULT '',
    url TEXT DEFAULT '',
    description TEXT DEFAULT '',
    posted_date TEXT DEFAULT '',
    search_role TEXT DEFAULT '',
    fit_score REAL,
    matched_skills TEXT DEFAULT '',
    missing_skills TEXT DEFAULT '',
    discovered_at TEXT
)
"""

DISCOVERY_SOURCES = {
    "LinkedIn": "LinkedIn",
    "JobStreet": "JobStreet",
    "Hiredly": "Hiredly",
    "Remotive": "Other",
}


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 10000")
    except sqlite3.Error:
        pass
    return conn


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log_history(conn, thread_id: str, from_status, to_status, note: str = ""):
    if (from_status or "") == (to_status or "") and not note:
        return
    conn.execute(
        "INSERT INTO status_history (thread_id, from_status, to_status, note, changed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (thread_id, from_status, to_status, note, _now()),
    )


def _migrate(conn):
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(applications)")}

    additions = {
        "source_platform": "TEXT DEFAULT 'Other'",
        "job_description_snippet": "TEXT DEFAULT ''",
        "job_url": "TEXT DEFAULT ''",
        "gmail_link": "TEXT DEFAULT ''",
    }
    for column, definition in additions.items():
        if column not in cols:
            conn.execute(f"ALTER TABLE applications ADD COLUMN {column} {definition}")

    if "is_valid" not in cols:
        if "is_valid_application" in cols:
            conn.execute("ALTER TABLE applications RENAME COLUMN is_valid_application TO is_valid")
        else:
            conn.execute("ALTER TABLE applications ADD COLUMN is_valid INTEGER NOT NULL DEFAULT 1")

    # CV fit-analyzer columns (in-place, additive)
    fit_cols = {row["name"] for row in conn.execute("PRAGMA table_info(applications)")}
    for column, definition in FIT_COLUMNS.items():
        if column not in fit_cols:
            conn.execute(f"ALTER TABLE applications ADD COLUMN {column} {definition}")

    # Per-application free-text notes (additive)
    note_cols = {row["name"] for row in conn.execute("PRAGMA table_info(applications)")}
    for column, definition in NOTE_COLUMNS.items():
        if column not in note_cols:
            conn.execute(f"ALTER TABLE applications ADD COLUMN {column} {definition}")

    for statement in STATUS_MIGRATIONS:
        conn.execute(statement)


def init_db():
    with _connect() as conn:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.Error:
            pass  # WAL is best-effort (unavailable on some network filesystems)
        conn.execute(CREATE_TABLE)
        _migrate(conn)
        conn.execute(CREATE_DISCOVERED)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_messages (
                message_id TEXT PRIMARY KEY,
                thread_id TEXT,
                processed_at TEXT,
                body_text TEXT DEFAULT ''
            )
        """)
        _migrate_processed_messages(conn)
        conn.execute(CREATE_STATUS_HISTORY)
        conn.execute(CREATE_HISTORY_INDEX)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_applications_valid_status "
            "ON applications (is_valid, current_status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_applications_last_updated "
            "ON applications (last_updated)"
        )


def _migrate_processed_messages(conn):
    """Add the body cache column to databases created before deep re-parsing existed.

    Storing the sanitised body means a Force Full Re-sync can re-classify every old email
    with the current filter rules without re-downloading anything from Gmail.
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(processed_messages)")}
    if "body_text" not in cols:
        conn.execute("ALTER TABLE processed_messages ADD COLUMN body_text TEXT DEFAULT ''")


def upsert_application(record: dict) -> str:
    with _connect() as conn:
        existing = conn.execute(
            "SELECT application_date, last_updated, job_url, job_description_snippet, "
            "current_status, is_valid, filter_reason, company_name, role_title, source_platform "
            "FROM applications WHERE thread_id = ?",
            (record["thread_id"],),
        ).fetchone()

        is_valid = 1 if record.get("is_valid", True) else 0

        if existing is None:
            conn.execute(
                "INSERT INTO applications (thread_id, company_name, role_title, source_platform, "
                "application_date, current_status, last_updated, job_description_snippet, "
                "job_url, gmail_link, latest_subject, is_valid, filter_reason, "
                "job_type, seniority_level, industry) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record["thread_id"],
                    record["company_name"],
                    record["role_title"],
                    record.get("source_platform", "Other"),
                    record["application_date"],
                    record["current_status"],
                    record["last_updated"],
                    record.get("job_description_snippet", ""),
                    record.get("job_url", ""),
                    record.get("gmail_link", ""),
                    record["latest_subject"],
                    is_valid,
                    record.get("filter_reason", ""),
                    record.get("job_type", ""),
                    record.get("seniority_level", ""),
                    record.get("industry", ""),
                ),
            )
            return "inserted"

        # --- merge rules: never let an older message roll the thread back ---
        incoming_ts = str(record.get("last_updated") or "")
        existing_ts = str(existing["last_updated"] or "")
        status = record["current_status"]
        if incoming_ts < existing_ts and existing["current_status"]:
            status = existing["current_status"]

        # --- validity: OR across the thread's messages ---
        # A thread is a real application if ANY of its messages is a confirmation/response,
        # so a new email that trips the filter can never erase an application that an
        # earlier (or newly re-parsed) message proved. Explicit user overrides stay sticky
        # in both directions.
        manual = bool((existing["filter_reason"] or "").startswith("manual"))
        if manual:
            merged_valid = existing["is_valid"]
            reason = existing["filter_reason"] or ""
        else:
            merged_valid = 1 if (existing["is_valid"] == 1 or is_valid == 1) else 0
            if is_valid:
                reason = record.get("filter_reason") or existing["filter_reason"] or ""
            elif existing["is_valid"] == 1:
                reason = existing["filter_reason"] or ""
            else:
                reason = record.get("filter_reason") or existing["filter_reason"] or ""
            if not merged_valid and not reason:
                reason = "no positive affirmation"

        # Never replace a real employer / role / platform with a platform placeholder.
        company = record.get("company_name") or existing["company_name"]
        if (parser.is_platform_company(company)
                and not parser.is_platform_company(existing["company_name"])):
            company = existing["company_name"]
        role = record.get("role_title") or existing["role_title"]
        if not parser.looks_like_role(role) and parser.looks_like_role(existing["role_title"]):
            role = existing["role_title"]
        platform = record.get("source_platform", "Other") or existing["source_platform"]
        if platform == "Other" and existing["source_platform"] not in ("", "Other"):
            platform = existing["source_platform"]

        conn.execute(
            "UPDATE applications SET company_name = ?, role_title = ?, source_platform = ?, "
            "current_status = ?, last_updated = MAX(last_updated, ?), latest_subject = ?, "
            "is_valid = ?, "
            "job_type = CASE WHEN ? != '' THEN ? ELSE job_type END, "
            "seniority_level = CASE WHEN ? != '' THEN ? ELSE seniority_level END, "
            "industry = CASE WHEN ? != '' THEN ? ELSE industry END, "
            "filter_reason = ?, "
            "job_url = CASE WHEN job_url = '' THEN ? ELSE job_url END, "
            "job_description_snippet = CASE WHEN job_description_snippet = '' THEN ? ELSE job_description_snippet END, "
            "gmail_link = CASE WHEN gmail_link = '' THEN ? ELSE gmail_link END "
            "WHERE thread_id = ?",
            (
                company,
                role,
                platform,
                status,
                incoming_ts,
                record["latest_subject"],
                merged_valid,
                record.get("job_type", ""), record.get("job_type", ""),
                record.get("seniority_level", ""), record.get("seniority_level", ""),
                record.get("industry", ""), record.get("industry", ""),
                reason,
                record.get("job_url", ""),
                record.get("job_description_snippet", ""),
                record.get("gmail_link", ""),
                record["thread_id"],
            ),
        )
        if status != existing["current_status"]:
            _log_history(conn, record["thread_id"], existing["current_status"],
                         status, "updated from Gmail")
        return "updated"


def get_existing(thread_id: str):
    with _connect() as conn:
        row = conn.execute(
            "SELECT job_url, job_description_snippet FROM applications WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        return dict(row) if row else None


def get_existing_map(thread_ids: list) -> dict:
    """One-shot lookup: thread_id -> {job_url, job_description_snippet, company_name,
    role_title, current_status, is_valid, filter_reason, ai_classified} for known threads
    (used as 'memory' for AI classification and to carry verdicts over skipped bodies)."""
    if not thread_ids:
        return {}
    placeholders = ",".join("?" for _ in thread_ids)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT thread_id, job_url, job_description_snippet, company_name, role_title, "
            f"current_status, is_valid, filter_reason, ai_classified "
            f"FROM applications WHERE thread_id IN ({placeholders})",
            tuple(thread_ids),
        ).fetchall()
    return {r["thread_id"]: dict(r) for r in rows}


def filter_unprocessed(message_ids: list) -> list:
    """Message ids that have never been fully processed (body not yet fetched)."""
    if not message_ids:
        return []
    placeholders = ",".join("?" for _ in message_ids)
    with _connect() as conn:
        known = {
            r[0] for r in conn.execute(
                f"SELECT message_id FROM processed_messages WHERE message_id IN ({placeholders})",
                tuple(message_ids),
            )
        }
    return [mid for mid in message_ids if mid not in known]


def get_processed_bodies(message_ids: list) -> dict:
    """Cached sanitised bodies for already-processed messages -> {message_id: body_text}.

    Lets a deep re-parse re-classify old mail with current rules without touching Gmail.
    Messages whose body was never stored (metadata-only noise, pre-upgrade rows) are absent.
    """
    if not message_ids:
        return {}
    placeholders = ",".join("?" for _ in message_ids)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT message_id, body_text FROM processed_messages "
            f"WHERE message_id IN ({placeholders}) AND body_text != ''",
            tuple(message_ids),
        ).fetchall()
    return {r["message_id"]: r["body_text"] for r in rows}


def mark_messages_processed(pairs: list, bodies: dict | None = None):
    """pairs: [(message_id, thread_id), ...] — optionally persist the fetched bodies.

    The body cache is bounded (8 KB/message) and only updated when a non-empty body is
    supplied, so a failed fetch can never blank a previously stored body.
    """
    if not pairs:
        return
    bodies = bodies or {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO processed_messages (message_id, thread_id, processed_at, body_text) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(message_id) DO UPDATE SET "
            "thread_id = excluded.thread_id, "
            "body_text = CASE WHEN excluded.body_text != '' THEN excluded.body_text "
            "ELSE processed_messages.body_text END",
            [(mid, tid, now, str(bodies.get(mid) or "")[:8000]) for mid, tid in pairs],
        )


def apply_ghosting(stale_days: int = 21) -> int:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT thread_id, current_status FROM applications "
            "WHERE current_status = 'Applied' "
            "AND last_updated < date('now', ?)",
            (f"-{stale_days} day",),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE applications SET current_status = 'Ghosted' "
                "WHERE thread_id = ?",
                (row["thread_id"],),
            )
            _log_history(conn, row["thread_id"], row["current_status"], "Ghosted",
                         f"auto-ghosted — no update for >{stale_days} days")
        return len(rows)


def apply_enrichment(enriched: dict) -> int:
    updated = 0
    with _connect() as conn:
        for thread_id, payload in enriched.items():
            snippet = (payload.get("og_description") or "").strip()
            title = (payload.get("og_title") or "").strip()
            combined = snippet
            if title and snippet:
                combined = f"{title} — {snippet}"
            elif title:
                combined = title
            if not combined:
                continue
            conn.execute(
                "UPDATE applications SET job_description_snippet = ? WHERE thread_id = ?",
                (combined[:600], thread_id),
            )
            updated += 1
    return updated


def export_csv(path: str = None):
    """Export the tracker. Selects the documented columns explicitly so future schema
    additions (fit_source, ai_classified, …) can never break the export."""
    path = path or CSV_PATH
    columns = list(COLUMNS)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(columns)} FROM applications ORDER BY last_updated DESC"
        ).fetchall()
    with open(path, "w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(dict(row) for row in rows)


def backup_to(path: str) -> str:
    """Consistent snapshot of the database via the sqlite backup API."""
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return path


def backup_all(backup_dir: str = "backups") -> str:
    """Backup DB + CSV + sync state + CV profile + AI cache into backups/<timestamp>/."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = os.path.join(backup_dir, stamp)
    os.makedirs(folder, exist_ok=True)
    backup_to(os.path.join(folder, "applications.db"))
    for filename in (CSV_PATH, STATE_PATH, "cv_profile.txt",
                     os.environ.get("AI_CACHE_PATH", "ai_cache.json")):
        if filename and os.path.exists(filename):
            try:
                shutil.copy2(filename, os.path.join(folder, os.path.basename(filename)))
            except OSError:
                pass
    return folder


def get_recent_activity(limit: int = 40) -> list:
    """A unified activity feed: status_history events first, then recent application updates."""
    feed = []
    with _connect() as conn:
        hist = conn.execute(
            "SELECT h.changed_at AS ts, h.thread_id, a.company_name, a.role_title, "
            "h.from_status, h.to_status, h.note AS detail "
            "FROM status_history h JOIN applications a ON a.thread_id = h.thread_id "
            "ORDER BY h.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        updates = conn.execute(
            "SELECT last_updated AS ts, thread_id, company_name, role_title, current_status, "
            "NULL AS from_status, '' AS detail "
            "FROM applications WHERE is_valid = 1 "
            "AND last_updated >= date('now', '-45 day') "
            "ORDER BY last_updated DESC LIMIT ?",
            (limit,),
        ).fetchall()
    for r in hist:
        feed.append(dict(r, kind="status"))
    for r in updates:
        feed.append(dict(r, kind="update"))
    feed.sort(key=lambda r: r.get("ts") or "", reverse=True)
    return feed[:limit]


def reset():
    for path in (DB_PATH, "job_tracker.db", STATE_PATH, CSV_PATH):
        try:
            os.remove(path)
            print(f"[reset] removed {path}")
        except FileNotFoundError:
            pass


def load_state() -> dict:
    """Sync state, tolerating a missing or corrupt file (never blocks a sync)."""
    default = {"last_sync_epoch": None, "history_id": None}
    if not os.path.exists(STATE_PATH):
        return default
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as state_file:
            state = json.load(state_file)
    except (OSError, ValueError):
        return default
    if not isinstance(state, dict):
        return default
    default.update(state)
    return default


def save_state(state: dict):
    """Atomic write so an interrupted sync can never leave a half-written state file."""
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as state_file:
        json.dump(state, state_file, indent=2)
    os.replace(tmp, STATE_PATH)


def _clean_score(value):
    """Coerce a fit score to a float in [0, 100]; None when unusable."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score != score:  # NaN
        return None
    return max(0.0, min(100.0, score))


def _clean_skill_list(value, limit: int = 30) -> list:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    out = []
    for item in value:
        text = str(item).strip()
        if not text or text.lower() in ("nan", "none"):
            continue
        out.append(text[:80])
        if len(out) >= limit:
            break
    return out


def _sanitize_fit_payload(payload: dict) -> dict | None:
    """Normalise an analyzer/AI payload; None when it carries no usable score."""
    if not isinstance(payload, dict):
        return None
    score = _clean_score(payload.get("fit_score"))
    if score is None:
        return None
    return {
        "fit_score": score,
        "matched_skills": _clean_skill_list(payload.get("matched_skills")),
        "missing_skills": _clean_skill_list(payload.get("missing_skills")),
        "actionable_improvements": _clean_skill_list(
            payload.get("actionable_improvements"), limit=6),
    }


def save_fit_results(results: dict, cv_sig: str = "") -> int:
    """results: {thread_id: {fit_score, matched_skills, missing_skills, actionable_improvements}}.

    Marks fit_source='rule' + the CV signature so a later sync can tell whether the stored
    score is still current (and only recompute when the CV changed).
    """
    updated = 0
    with _connect() as conn:
        for thread_id, payload in results.items():
            clean = _sanitize_fit_payload(payload)
            if clean is None:
                continue
            cursor = conn.execute(
                "UPDATE applications SET fit_score = ?, matched_skills = ?, "
                "missing_skills = ?, actionable_improvements = ?, "
                "fit_source = 'rule', fit_cv_sig = ? WHERE thread_id = ?",
                (
                    clean["fit_score"],
                    json.dumps(clean["matched_skills"]),
                    json.dumps(clean["missing_skills"]),
                    json.dumps(clean["actionable_improvements"]),
                    cv_sig,
                    thread_id,
                ),
            )
            if cursor.rowcount > 0:
                updated += 1
    return updated


def get_fit_pending() -> list:
    """Valid rows that have text to analyze but no stored fit score yet."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT thread_id, role_title, job_description_snippet FROM applications "
            "WHERE is_valid = 1 AND (fit_score IS NULL OR fit_score < 0) "
            "AND job_description_snippet != ''"
        ).fetchall()
    return [dict(r) for r in rows]


def update_snippet(thread_id: str, text: str) -> bool:
    """Overwrite the JD snippet (used by the JD backfill scraper)."""
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE applications SET job_description_snippet = ? WHERE thread_id = ?",
            ((text or "")[:2000], thread_id),
        )
        return cursor.rowcount > 0


def set_job_url(thread_id: str, url: str) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE applications SET job_url = ? WHERE thread_id = ?",
            ((url or "")[:500], thread_id),
        )
        return cursor.rowcount > 0


def refine_og_company_role(thread_id: str, company: str, role: str) -> bool:
    """Update company/role from a scraped OG title ('Role - Company - JobStreet')."""
    company = (company or "").strip()
    role = (role or "").strip()
    with _connect() as conn:
        row = conn.execute("SELECT company_name, role_title FROM applications WHERE thread_id = ?",
                           (thread_id,)).fetchone()
        if not row:
            return False
        if company and not re.search(r"jobstreet|linkedin|hiredly|prosple|workday",
                                     company, re.IGNORECASE):
            conn.execute("UPDATE applications SET company_name = ? WHERE thread_id = ?",
                         (company, thread_id))
        if role and len(role) >= 6:
            conn.execute("UPDATE applications SET role_title = ? WHERE thread_id = ?",
                         (role, thread_id))
        return True


def refine_platform_rows() -> int:
    """Repair legacy rows whose company/role is a platform placeholder.

    Re-runs the parser's company/role refinement over stored rows, but only accepts a
    change when it is strictly an improvement: a platform company replaced by a real one,
    or a junk role replaced by a plausible title. Returns the number of rows touched.
    """
    updated = 0
    with _connect() as conn:
        rows = conn.execute(
            "SELECT thread_id, company_name, role_title, latest_subject "
            "FROM applications WHERE is_valid = 1"
        ).fetchall()
        for row in rows:
            old_company = row["company_name"] or ""
            old_role = row["role_title"] or ""
            record = {"company_name": old_company, "role_title": old_role,
                      "latest_subject": row["latest_subject"] or ""}
            try:
                parser.refine_company_role(record)
            except Exception:
                continue
            new_company = (record.get("company_name") or "").strip()
            new_role = (record.get("role_title") or "").strip()
            changed = False
            if (new_company and new_company != old_company
                    and parser.is_platform_company(old_company)
                    and not parser.is_platform_company(new_company)):
                conn.execute("UPDATE applications SET company_name = ? WHERE thread_id = ?",
                             (new_company, row["thread_id"]))
                changed = True
            if (new_role and new_role != old_role
                    and not parser.looks_like_role(old_role)
                    and parser.looks_like_role(new_role)):
                conn.execute("UPDATE applications SET role_title = ? WHERE thread_id = ?",
                             (new_role, row["thread_id"]))
                changed = True
            if changed:
                updated += 1
    return updated


def get_valid_rows_for_fit() -> list:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT thread_id, role_title, job_description_snippet, fit_score, "
            "fit_source, fit_cv_sig FROM applications WHERE is_valid = 1"
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_rows() -> list:
    """Every application row (dicts) for audit/repair passes."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT thread_id, company_name, role_title, source_platform, application_date, "
            "current_status, last_updated, job_description_snippet, job_url, latest_subject, "
            "is_valid, filter_reason, job_type, seniority_level, industry "
            "FROM applications ORDER BY application_date DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def apply_repairs(repairs: list) -> dict:
    """Apply audit repairs: {thread_id, company?, role?, status?, is_valid?, reason?, note?,
    job_type?, seniority_level?, industry?}.

    Field repairs never touch `last_updated` (so historical waiting times stay honest);
    status changes are written to the audit history.
    """
    counts = {"rows": 0, "company": 0, "role": 0, "status": 0, "invalidated": 0,
              "recovered": 0, "job_type": 0, "seniority_level": 0, "industry": 0}
    with _connect() as conn:
        for rep in repairs:
            thread_id = rep.get("thread_id")
            if not thread_id:
                continue
            row = conn.execute(
                "SELECT company_name, role_title, current_status, is_valid, "
                "job_type, seniority_level, industry "
                "FROM applications WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            if not row:
                continue
            sets, values = [], []
            if rep.get("company") and rep["company"] != row["company_name"]:
                sets.append("company_name = ?")
                values.append(rep["company"][:200])
                counts["company"] += 1
            if rep.get("role") is not None and rep["role"] != (row["role_title"] or ""):
                sets.append("role_title = ?")
                values.append(rep["role"][:200])
                counts["role"] += 1
            for field in ("job_type", "seniority_level", "industry"):
                if rep.get(field) and rep[field] != (row[field] or ""):
                    sets.append(f"{field} = ?")
                    values.append(rep[field][:80])
                    counts[field] += 1
            if rep.get("status") and rep["status"] != row["current_status"]:
                sets.append("current_status = ?")
                values.append(rep["status"])
                counts["status"] += 1
                _log_history(conn, thread_id, row["current_status"], rep["status"],
                             rep.get("note") or "audit repair")
            if rep.get("is_valid") is not None:
                new_valid = 1 if rep["is_valid"] else 0
                if new_valid != row["is_valid"]:
                    sets.append("is_valid = ?")
                    values.append(new_valid)
                    sets.append("filter_reason = ?")
                    values.append((rep.get("reason") or (
                        "audit recovery" if new_valid else "audit: noise"))[:200])
                    counts["recovered" if new_valid else "invalidated"] += 1
                    _log_history(conn, thread_id, None, None,
                                 rep.get("note") or (
                                     "audit recovery — looked like a real application"
                                     if new_valid else "audit: marked noise"))
            if sets:
                values.append(thread_id)
                conn.execute(
                    f"UPDATE applications SET {', '.join(sets)} WHERE thread_id = ?", values)
                counts["rows"] += 1
    return counts


def mark_fit_ai(results: dict, cv_sig: str = "") -> int:
    """Persist AI-computed fit scores (fit_source='ai' + CV signature) so they are never
    recomputed until the CV changes."""
    updated = 0
    with _connect() as conn:
        for thread_id, payload in results.items():
            clean = _sanitize_fit_payload(payload)
            if clean is None:
                continue
            cursor = conn.execute(
                "UPDATE applications SET fit_score = ?, matched_skills = ?, missing_skills = ?, "
                "actionable_improvements = ?, fit_source = 'ai', fit_cv_sig = ? "
                "WHERE thread_id = ?",
                (
                    clean["fit_score"],
                    json.dumps(clean["matched_skills"]),
                    json.dumps(clean["missing_skills"]),
                    json.dumps(clean["actionable_improvements"]),
                    cv_sig,
                    thread_id,
                ),
            )
            if cursor.rowcount > 0:
                updated += 1
    return updated


def apply_ai_classification(results: dict) -> int:
    """Apply AI email-classification verdicts (with memory) to application rows.

    results: {thread_id: {is_valid, company, role, status, reason}} — marks ai_classified=1
    so the same email is never re-classified on later syncs. Only known statuses are accepted,
    and a manually recovered row is never pushed back into the noise pile.
    """
    updated = 0
    with _connect() as conn:
        for thread_id, payload in results.items():
            if not isinstance(payload, dict):
                continue
            fields = []
            values = []
            is_valid = payload.get("is_valid")
            if is_valid is not None:
                fields.append(
                    "is_valid = CASE WHEN filter_reason LIKE 'manual%' THEN is_valid ELSE ? END")
                values.append(1 if is_valid else 0)
            company = (payload.get("company") or "").strip()
            if company and not parser.is_platform_company(company):
                fields.append("company_name = ?")
                values.append(company[:200])
            role = (payload.get("role") or "").strip()
            if role and parser.looks_like_role(role):
                fields.append("role_title = ?")
                values.append(role[:200])
            status = (payload.get("status") or "").strip()
            if status in parser.STATUSES:
                fields.append("current_status = ?")
                values.append(status)
            reason = (payload.get("reason") or "").strip()
            if reason:
                fields.append(
                    "filter_reason = CASE WHEN filter_reason LIKE 'manual%' "
                    "THEN filter_reason ELSE ? END")
                values.append(f"ai: {reason[:200]}")
            fields.append("ai_classified = 1")
            values.append(thread_id)
            conn.execute(
                f"UPDATE applications SET {', '.join(fields)} WHERE thread_id = ?", values
            )
            updated += 1
    return updated


def update_status(thread_id: str, status: str, note: str = "") -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    with _connect() as conn:
        row = conn.execute(
            "SELECT current_status FROM applications WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if not row:
            return False
        from_status = row["current_status"]
        cursor = conn.execute(
            "UPDATE applications SET current_status = ?, last_updated = ? WHERE thread_id = ?",
            (status, today, thread_id),
        )
        _log_history(conn, thread_id, from_status, status, note)
        return cursor.rowcount > 0


def mark_applied(thread_id: str) -> str:
    """Move a 'Saved / Planning' row into 'Applied' (records application date = today).

    Returns 'applied', 'updated' or 'not_found'.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    with _connect() as conn:
        row = conn.execute(
            "SELECT current_status, application_date FROM applications WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        if not row:
            return "not_found"
        was_saved = row["current_status"] == "Saved / Planning"
        if was_saved:
            conn.execute(
                "UPDATE applications SET current_status = 'Applied', application_date = ?, "
                "last_updated = ? WHERE thread_id = ?",
                (today, today, thread_id),
            )
            _log_history(conn, thread_id, "Saved / Planning", "Applied",
                         "moved from Saved / Planning — recorded application date")
            return "applied"
        if row["current_status"] != "Applied":
            conn.execute(
                "UPDATE applications SET current_status = 'Applied', last_updated = ? "
                "WHERE thread_id = ?",
                (today, thread_id),
            )
            _log_history(conn, thread_id, row["current_status"], "Applied")
            return "updated"
        return "already"


def update_note(thread_id: str, note: str) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE applications SET notes = ? WHERE thread_id = ?",
            ((note or "").strip()[:2000], thread_id),
        )
        return cursor.rowcount > 0


def update_company_role(thread_id: str, company: str, role: str) -> bool:
    """Manual correction of company/role (for rows no parser rule could repair)."""
    company = (company or "").strip()[:200]
    role = (role or "").strip()[:200]
    if not company or not role:
        return False
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE applications SET company_name = ?, role_title = ? WHERE thread_id = ?",
            (company, role, thread_id),
        )
        return cursor.rowcount > 0


def get_status_history(thread_id: str | None = None) -> list:
    with _connect() as conn:
        if thread_id is not None:
            rows = conn.execute(
                "SELECT id, thread_id, from_status, to_status, note, changed_at "
                "FROM status_history WHERE thread_id = ? ORDER BY id DESC",
                (thread_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, thread_id, from_status, to_status, note, changed_at "
                "FROM status_history ORDER BY id DESC LIMIT 500"
            ).fetchall()
    return [dict(r) for r in rows]


def delete_application(thread_id: str) -> bool:
    with _connect() as conn:
        conn.execute("DELETE FROM status_history WHERE thread_id = ?", (thread_id,))
        cursor = conn.execute(
            "DELETE FROM applications WHERE thread_id = ?", (thread_id,))
        return cursor.rowcount > 0


def get_tracked_discovery_keys() -> set:
    """job_keys currently present in the tracker as manual (discovery) rows."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT thread_id FROM applications WHERE thread_id LIKE 'manual:%'"
        ).fetchall()
    return {r["thread_id"][len("manual:"):] for r in rows}


def save_discovered_fits(results: dict) -> int:
    """results: {job_key: {fit_score, matched_skills, missing_skills}}"""
    updated = 0
    with _connect() as conn:
        for job_key, payload in results.items():
            conn.execute(
                "UPDATE discovered_jobs SET fit_score = ?, matched_skills = ?, "
                "missing_skills = ? WHERE job_key = ?",
                (
                    payload.get("fit_score"),
                    json.dumps(payload.get("matched_skills", [])),
                    json.dumps(payload.get("missing_skills", [])),
                    job_key,
                ),
            )
            updated += 1
    return updated


def set_valid(thread_id: str, is_valid: bool = True, reason: str = "manual recovery") -> bool:
    """Manual override of the noise verdict.

    Both directions are sticky: a recovered row cannot be re-filtered and a row the user
    marked as noise cannot be auto-recovered, until the user changes it again.
    """
    reason = (reason or "").strip()
    if not reason:
        reason = "manual recovery" if is_valid else "manual: marked as noise"
    with _connect() as conn:
        row = conn.execute(
            "SELECT is_valid FROM applications WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if not row:
            return False
        cursor = conn.execute(
            "UPDATE applications SET is_valid = ?, filter_reason = ? WHERE thread_id = ?",
            (1 if is_valid else 0, reason, thread_id),
        )
        if is_valid and not row["is_valid"]:
            _log_history(conn, thread_id, None, None, f"recovered from noise filter — {reason}")
        elif not is_valid and row["is_valid"]:
            _log_history(conn, thread_id, None, None, f"marked as noise — {reason}")
        return cursor.rowcount > 0


def save_discovered(jobs: list) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _connect() as conn:
        conn.execute("DELETE FROM discovered_jobs")
        for job in jobs:
            conn.execute(
                "INSERT OR REPLACE INTO discovered_jobs (job_key, title, company, location, "
                "source, url, description, posted_date, search_role, fit_score, "
                "matched_skills, missing_skills, discovered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job.get("job_key"),
                    job.get("title", ""),
                    job.get("company", ""),
                    job.get("location", ""),
                    job.get("source", ""),
                    job.get("url", ""),
                    job.get("description", "")[:800],
                    job.get("posted_date", ""),
                    job.get("search_role", ""),
                    job.get("fit_score"),
                    json.dumps(job.get("matched_skills", [])),
                    json.dumps(job.get("missing_skills", [])),
                    now,
                ),
            )
        return len(jobs)


def load_discovered() -> list:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM discovered_jobs ORDER BY fit_score DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def clear_discovered():
    with _connect() as conn:
        conn.execute("DELETE FROM discovered_jobs")


def update_discovered_description(job_key: str, description: str, analysis: dict) -> bool:
    """Persist a lazily-fetched JD (and its re-scored fit) into the discovery cache."""
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE discovered_jobs SET description = ?, fit_score = ?, "
            "matched_skills = ?, missing_skills = ? WHERE job_key = ?",
            (
                (description or "")[:2000],
                analysis.get("fit_score"),
                json.dumps(analysis.get("matched_skills", [])),
                json.dumps(analysis.get("missing_skills", [])),
                job_key,
            ),
        )
        return cursor.rowcount > 0


def add_manual_application(job: dict) -> str:
    """Move a discovered job into applications.db as Saved / Planning."""
    source = DISCOVERY_SOURCES.get(job.get("source", ""), "Other")
    today = datetime.now().strftime("%Y-%m-%d")
    thread_id = f"manual:{job.get('job_key')}"
    record = {
        "thread_id": thread_id,
        "company_name": job.get("company", "Unknown"),
        "role_title": job.get("title", "Unknown"),
        "source_platform": source,
        "application_date": today,
        "current_status": "Saved / Planning",
        "last_updated": today,
        "job_description_snippet": (job.get("description") or "")[:600],
        "job_url": job.get("url", ""),
        "gmail_link": "",
        "latest_subject": f"Added from {job.get('source', 'discovery')}: {job.get('title', '')}",
        "is_valid": 1,
    }
    with _connect() as conn:
        existing = conn.execute(
            "SELECT thread_id FROM applications WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if existing:
            return "exists"
        conn.execute(
            "INSERT INTO applications (thread_id, company_name, role_title, source_platform, "
            "application_date, current_status, last_updated, job_description_snippet, "
            "job_url, gmail_link, latest_subject, is_valid) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record["thread_id"], record["company_name"], record["role_title"],
                record["source_platform"], record["application_date"], record["current_status"],
                record["last_updated"], record["job_description_snippet"], record["job_url"],
                record["gmail_link"], record["latest_subject"], 1,
            ),
        )
        # carry over the fit analysis computed during discovery
        conn.execute(
            "UPDATE applications SET fit_score = ?, matched_skills = ?, missing_skills = ?, "
            "actionable_improvements = ? WHERE thread_id = ?",
            (
                job.get("fit_score"),
                json.dumps(job.get("matched_skills", [])),
                json.dumps(job.get("missing_skills", [])),
                "[]",
                thread_id,
            ),
        )
    return "added"
