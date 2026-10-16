"""Operational schema and versioned migrations (SQLite, ``PRAGMA user_version``).

v1 databases (the original tracker) are upgraded in place, additively, after a backup.
Nothing a user typed (notes, manual overrides) is ever dropped.

Tables
------
applications       one row per application (Gmail thread or manual entry): current state
status_events      append-only log of every status change, dated by the EMAIL that caused
                   it (not by when the sync ran); the source of funnel depth and timings
processed_messages one row per Gmail message seen: sanitised body cache + parse verdict
llm_calls          cache + cost ledger for the optional Claude fallback
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime

SCHEMA_VERSION = 2

APPLICATION_COLUMNS = {
    "thread_id": "TEXT PRIMARY KEY",
    "company_name": "TEXT NOT NULL",
    "role_title": "TEXT NOT NULL DEFAULT ''",
    "source_platform": "TEXT NOT NULL DEFAULT 'Other'",
    "application_date": "TEXT NOT NULL",
    "current_status": "TEXT NOT NULL",
    "last_updated": "TEXT NOT NULL",
    "job_description_snippet": "TEXT DEFAULT ''",
    "job_url": "TEXT DEFAULT ''",
    "gmail_link": "TEXT DEFAULT ''",
    "latest_subject": "TEXT DEFAULT ''",
    "is_valid": "INTEGER NOT NULL DEFAULT 1",
    "filter_reason": "TEXT DEFAULT ''",
    "job_type": "TEXT DEFAULT ''",
    "seniority_level": "TEXT DEFAULT ''",
    "industry": "TEXT DEFAULT ''",
    "notes": "TEXT DEFAULT ''",
    # v2
    "role_family": "TEXT DEFAULT ''",
    "location": "TEXT DEFAULT ''",
    "salary_text": "TEXT DEFAULT ''",
    "employment_type": "TEXT DEFAULT ''",
    "posted_date": "TEXT DEFAULT ''",
    "parse_confidence": "REAL",
    "parsed_by": "TEXT DEFAULT 'rules'",
    "origin": "TEXT DEFAULT 'gmail'",  # gmail | manual | demo
    "created_at": "TEXT",
    "updated_at": "TEXT",
}

CREATE_APPLICATIONS = "CREATE TABLE IF NOT EXISTS applications (\n    " + ",\n    ".join(
    f"{name} {ddl}" for name, ddl in APPLICATION_COLUMNS.items()) + "\n)"

CREATE_STATUS_EVENTS = """
CREATE TABLE IF NOT EXISTS status_events (
    event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id    TEXT NOT NULL,
    from_status  TEXT,
    to_status    TEXT NOT NULL,
    occurred_on  TEXT NOT NULL,          -- YYYY-MM-DD the change happened (email date)
    recorded_at  TEXT NOT NULL,          -- when the tracker wrote the row
    source       TEXT NOT NULL,          -- gmail | manual | system | repair | legacy | inferred | llm
    message_id   TEXT DEFAULT '',
    note         TEXT DEFAULT ''
)"""

CREATE_PROCESSED = """
CREATE TABLE IF NOT EXISTS processed_messages (
    message_id   TEXT PRIMARY KEY,
    thread_id    TEXT,
    processed_at TEXT,
    body_text    TEXT DEFAULT ''
)"""

PROCESSED_COLUMNS = {
    "message_date": "TEXT DEFAULT ''",
    "subject": "TEXT DEFAULT ''",
    "sender": "TEXT DEFAULT ''",
    "is_valid": "INTEGER",
    "filter_reason": "TEXT DEFAULT ''",
    "detected_status": "TEXT DEFAULT ''",
    "confidence": "REAL",
    "parsed_by": "TEXT DEFAULT 'rules'",
}

CREATE_LLM_CALLS = """
CREATE TABLE IF NOT EXISTS llm_calls (
    message_id    TEXT PRIMARY KEY,
    model         TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd      REAL DEFAULT 0,
    result_json   TEXT DEFAULT '',
    error         TEXT DEFAULT ''
)"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_app_valid_status ON applications (is_valid, current_status)",
    "CREATE INDEX IF NOT EXISTS ix_app_last_updated ON applications (last_updated)",
    "CREATE INDEX IF NOT EXISTS ix_events_thread ON status_events (thread_id, occurred_on, event_id)",
    "CREATE INDEX IF NOT EXISTS ix_processed_thread ON processed_messages (thread_id)",
    "CREATE INDEX IF NOT EXISTS ix_llm_created ON llm_calls (created_at)",
]

STATUS_ALIASES = [
    ("Screening/OA", "Assessment / OA"),
    ("Ghosted/Inactive", "Ghosted"),
    ("Response", "Assessment / OA"),
]


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,)).fetchone() is not None


def _add_missing(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    have = _columns(conn, table)
    if table == "applications" and "is_valid" not in have and "is_valid_application" in have:
        conn.execute("ALTER TABLE applications RENAME COLUMN is_valid_application TO is_valid")
        have = _columns(conn, table)
    for name, ddl in columns.items():
        if name in have or "PRIMARY KEY" in ddl:
            continue
        # SQLite cannot ADD a NOT NULL column without a default; relax it for migrations.
        safe = ddl.replace("NOT NULL ", "") if "DEFAULT" not in ddl else ddl
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {safe}")


# ---------------------------------------------------------------------------- migrations


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    """v1 -> v2: event log, richer columns, role family, legacy status names."""
    from ..parsing.categorize import classify_role_family

    _add_missing(conn, "applications", APPLICATION_COLUMNS)
    _add_missing(conn, "processed_messages", PROCESSED_COLUMNS)
    for old, new in STATUS_ALIASES:
        conn.execute("UPDATE applications SET current_status=? WHERE current_status=?", (new, old))

    stamp = now()
    conn.execute("UPDATE applications SET created_at = COALESCE(created_at, ?), "
                 "updated_at = COALESCE(updated_at, ?)", (stamp, stamp))
    rows = conn.execute("SELECT thread_id, role_title FROM applications "
                        "WHERE COALESCE(role_family, '') = ''").fetchall()
    conn.executemany("UPDATE applications SET role_family=? WHERE thread_id=?",
                     [(classify_role_family(r[1] or ""), r[0]) for r in rows])
    conn.execute("UPDATE applications SET origin='manual' WHERE thread_id LIKE 'manual:%'")

    # Carry the v1 audit trail over. Its timestamps were sync times, so it is labelled
    # 'legacy' and the metric layer treats its dates as low-trust.
    if _table_exists(conn, "status_history"):
        conn.execute(
            """INSERT INTO status_events (thread_id, from_status, to_status, occurred_on,
                                          recorded_at, source, note)
               SELECT thread_id, NULLIF(from_status, ''), to_status,
                      substr(COALESCE(changed_at, ?), 1, 10), COALESCE(changed_at, ?),
                      'legacy', COALESCE(note, '')
               FROM status_history
               WHERE COALESCE(to_status, '') <> ''
               ORDER BY id""", (stamp, stamp))
    # Every application gets its opening event; advanced rows with no surviving evidence
    # of how they got there get one inferred event dated at their last activity.
    conn.execute(
        """INSERT INTO status_events (thread_id, from_status, to_status, occurred_on,
                                      recorded_at, source, note)
           SELECT a.thread_id, NULL, 'Applied', substr(a.application_date, 1, 10), ?,
                  'inferred', 'opening event reconstructed during v2 migration'
           FROM applications a
           WHERE a.current_status <> 'Saved / Planning'
             AND NOT EXISTS (SELECT 1 FROM status_events e
                             WHERE e.thread_id = a.thread_id AND e.from_status IS NULL)""",
        (stamp,))
    conn.execute(
        """INSERT INTO status_events (thread_id, from_status, to_status, occurred_on,
                                      recorded_at, source, note)
           SELECT a.thread_id, 'Applied', a.current_status, substr(a.last_updated, 1, 10), ?,
                  'inferred', 'current status reconstructed during v2 migration'
           FROM applications a
           WHERE a.current_status NOT IN ('Applied', 'Saved / Planning')
             AND NOT EXISTS (SELECT 1 FROM status_events e
                             WHERE e.thread_id = a.thread_id
                               AND e.to_status = a.current_status)""",
        (stamp,))
    # The removed job-discovery feature left an (almost always empty) table behind.
    if (_table_exists(conn, "discovered_jobs")
            and conn.execute("SELECT COUNT(*) FROM discovered_jobs").fetchone()[0] == 0):
        conn.execute("DROP TABLE discovered_jobs")


MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {2: _migrate_to_v2}


def current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def needs_migration(conn: sqlite3.Connection) -> bool:
    return _table_exists(conn, "applications") and current_version(conn) < SCHEMA_VERSION


def ensure_schema(conn: sqlite3.Connection) -> int:
    """Create / upgrade the schema. Returns the version the database ended on."""
    fresh = not _table_exists(conn, "applications")
    conn.execute(CREATE_APPLICATIONS)
    conn.execute(CREATE_STATUS_EVENTS)
    conn.execute(CREATE_PROCESSED)
    conn.execute(CREATE_LLM_CALLS)
    _add_missing(conn, "processed_messages", PROCESSED_COLUMNS)
    if fresh:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    else:
        version = current_version(conn)
        for target in sorted(MIGRATIONS):
            if version < target:
                MIGRATIONS[target](conn)
                conn.execute(f"PRAGMA user_version = {target}")
                version = target
    for ddl in INDEXES:
        conn.execute(ddl)
    return current_version(conn)
