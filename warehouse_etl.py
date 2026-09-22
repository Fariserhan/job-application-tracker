"""ETL: operational tracker tables -> the star-schema warehouse.

Split out of warehouse.py so the dimensional model (schema + semantics) stays readable.
`warehouse.build_warehouse()` calls `run_etl()` here.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta

from warehouse import (
    WAREHOUSE_DDL,
    _as_list,
    _connect,
    _days_between,
    _event_type,
    _is_direct_platform,
    _norm_date,
    _role_family,
    _seniority,
    _skill_family,
    _status_rank,
    _status_row,
)

STALE_DAYS = 21
NOW = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731


def _ensure_operational(conn):
    """Make sure the operational tables exist before reading them (fresh install)."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS applications (
            thread_id TEXT PRIMARY KEY, company_name TEXT NOT NULL, role_title TEXT NOT NULL,
            source_platform TEXT DEFAULT 'Other', application_date TEXT NOT NULL,
            current_status TEXT NOT NULL, last_updated TEXT NOT NULL,
            job_description_snippet TEXT DEFAULT '', job_url TEXT DEFAULT '',
            gmail_link TEXT DEFAULT '', latest_subject TEXT DEFAULT '',
            is_valid INTEGER NOT NULL DEFAULT 1)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL,
            from_status TEXT, to_status TEXT, note TEXT DEFAULT '', changed_at TEXT)"""
    )


def _table_columns(conn, table: str) -> set:
    try:
        return {r["name"] if isinstance(r, sqlite3.Row) else r[1]
                for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _ensure_operational_columns(conn):
    """Additive migration for the operational tables — never destructive.

    A fresh or partially-migrated database must still be readable by the ETL; missing
    classification columns are added as their documented defaults.
    """
    expected = {
        "job_description_snippet": "TEXT DEFAULT ''",
        "job_url": "TEXT DEFAULT ''",
        "gmail_link": "TEXT DEFAULT ''",
        "latest_subject": "TEXT DEFAULT ''",
        "is_valid": "INTEGER NOT NULL DEFAULT 1",
        "job_type": "TEXT DEFAULT ''",
        "seniority_level": "TEXT DEFAULT ''",
        "industry": "TEXT DEFAULT ''",
        "notes": "TEXT DEFAULT ''",
        "filter_reason": "TEXT DEFAULT ''",
    }
    have = _table_columns(conn, "applications")
    for column, ddl in expected.items():
        if column not in have:
            conn.execute(f"ALTER TABLE applications ADD COLUMN {column} {ddl}")


# ---------------------------------------------------------------- date dimension


def _populate_dim_date(conn, start: date, end: date) -> int:
    rows = []
    day = start
    while day <= end:
        iso = day.isocalendar()
        rows.append((
            day.isoformat(), day.year, (day.month - 1) // 3 + 1, day.month,
            day.strftime("%B"), day.strftime("%Y-%m"),
            (day - timedelta(days=day.weekday())).isoformat(),
            day.strftime("%A"), day.weekday() + 1,
            1 if day.weekday() >= 5 else 0,
        ))
        day += timedelta(days=1)
    conn.executemany(
        "INSERT OR REPLACE INTO dim_date (date_key, year, quarter, month, month_name, "
        "year_month, week_start, day_of_week, day_of_week_n, is_weekend) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


# ---------------------------------------------------------------- dimension loaders


def _get_or_create(conn, table: str, key_col: str, name_col: str, name: str, **extra) -> int | str:
    name = (name or "").strip() or "Unknown"
    row = conn.execute(f"SELECT {key_col} FROM {table} WHERE {name_col} = ?", (name,)).fetchone()
    if row:
        return row[0]
    cols = [name_col] + list(extra)
    placeholders = ", ".join("?" for _ in cols)
    cur = conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
        [name] + list(extra.values()))
    return cur.lastrowid


def _load_application_rows(conn) -> list:
    cols = _table_columns(conn, "applications")
    wanted = ["thread_id", "company_name", "role_title", "source_platform",
              "application_date", "current_status", "last_updated",
              "job_description_snippet", "job_type", "seniority_level", "industry",
              "is_valid"]
    select = [c for c in wanted if c in cols]
    is_valid_filter = "WHERE COALESCE(is_valid, 1) = 1" if "is_valid" in cols else ""
    return conn.execute(
        f"SELECT {', '.join(select)} FROM applications {is_valid_filter}").fetchall()


def _load_events(conn) -> list:
    try:
        return conn.execute(
            "SELECT thread_id, from_status, to_status, note, changed_at "
            "FROM status_history ORDER BY thread_id, id").fetchall()
    except sqlite3.Error:
        return []


# ---------------------------------------------------------------- main ETL


def run_etl(db_path: str | None = None, stale_days: int = STALE_DAYS) -> dict:
    """Rebuild the warehouse from the operational tables. Idempotent.

    Returns counts: {'applications', 'events', 'skills', 'dates', 'companies', ...}.
    """
    conn = _connect(db_path)
    try:
        _ensure_operational(conn)
        _ensure_operational_columns(conn)

        apps = _load_application_rows(conn)
        events = _load_events(conn)

        # --- drop the fact objects before re-running the DDL ---
        # `CREATE TABLE IF NOT EXISTS` cannot restructure a table, so a warehouse built by an
        # older version (e.g. one that still carried fit columns) would keep its stale shape
        # and the load below would fail on it. Dropping the facts + the reporting view and
        # re-creating them from WAREHOUSE_DDL makes an existing warehouse self-healing; the
        # dimension tables are keyed by name and are left alone so their keys stay stable.
        conn.execute("DROP VIEW IF EXISTS vw_application_analysis")
        for table in ("fact_application_skill", "fact_status_event", "fact_application"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.executescript(WAREHOUSE_DDL)

        # --- date range covers applications AND status events ---
        all_dates = [d for d in (_norm_date(a["application_date"]) for a in apps) if d]
        all_dates += [d for d in (_norm_date(a["last_updated"]) for a in apps) if d]
        all_dates += [d for d in (_norm_date(e["changed_at"]) for e in events) if d]
        today = date.today()
        if all_dates:
            start = min(date.fromisoformat(d) for d in all_dates)
            end = max(max(date.fromisoformat(d) for d in all_dates), today)
        else:
            start, end = today - timedelta(days=365), today
        _populate_dim_date(conn, start, end)

        # --- status dimension (complete by construction) ---
        for status in ("Saved / Planning", "Applied", "Assessment / OA", "Interview",
                       "Offer", "Rejected", "Ghosted"):
            stage, order, terminal, positive = _status_row(status)
            conn.execute(
                "INSERT OR REPLACE INTO dim_status (status_key, funnel_stage, stage_order, "
                "is_terminal, is_positive) VALUES (?, ?, ?, ?, ?)",
                (status, stage, order, terminal, positive))

        # --- first employer-side response per thread (drives days_to_response) ---
        # `status_history` cannot be trusted for this: repair/re-sync passes rebuild it, so
        # historical Assessment/Interview events are gone (measured: 412 rows, almost all
        # Applied/Ghosted). The authoritative evidence that a real response happened is the
        # application's OWN state.
        #
        # The response DATE is the crux. Two honest sources, in order of strength:
        #   1. a surviving status_history row that records an advanced stage, or
        #   2. the row's own `last_updated` — after the date-semantics fix in
        #      storage.upsert_application, that timestamp only advances when the thread
        #      genuinely moved, so a Rejected/Ghosted row's last_updated IS the day the
        #      employer's decision landed.
        # When neither gives a date later than the application date, the response date is
        # UNKNOWN and the row is excluded from response-time metrics rather than being
        # recorded as a zero-day response (which would silently fake the statistic).
        first_response = {}
        advanced_ranks = {2, 3, 4}  # Assessment / OA, Interview, Offer
        for e in events:
            to_status = (e["to_status"] or "").strip()
            thread = e["thread_id"]
            changed = _norm_date(e["changed_at"])
            if not changed or not thread:
                continue
            if _status_rank(to_status) in advanced_ranks:
                if thread not in first_response or changed < first_response[thread]:
                    first_response[thread] = changed

        for a in apps:
            thread = a["thread_id"]
            status = (a["current_status"] or "").strip()
            app_date = _norm_date(a["application_date"])
            last_upd = _norm_date(a["last_updated"])
            responded = _status_rank(status) in advanced_ranks or status == "Rejected"
            if not responded or thread in first_response:
                continue
            # A decision landed on this row; last_updated is the only surviving evidence of
            # when. Require it to be strictly later than the application to count as known.
            if last_upd and app_date and last_upd > app_date:
                first_response[thread] = last_upd

        now = NOW()
        event_rows = []
        dim_counts = {"companies": 0, "platforms": 0, "role_families": 0,
                      "seniorities": 0, "industries": 0, "job_types": 0, "skills": 0}

        for a in apps:
            thread = a["thread_id"]
            title = (a["role_title"] or "").strip()
            company = (a["company_name"] or "").strip() or "Unknown"
            platform = (a["source_platform"] or "").strip() or "Other"
            status = (a["current_status"] or "Applied").strip()
            app_date = _norm_date(a["application_date"])
            last_upd = _norm_date(a["last_updated"])

            role_fam = _role_family(title)
            seniority, seniority_rank = _seniority(title)
            industry = (a["industry"] if "industry" in a.keys() else "") or "Unknown"
            job_type = (a["job_type"] if "job_type" in a.keys() else "") or "Unknown"

            company_key = _get_or_create(conn, "dim_company", "company_key",
                                         "company_name", company)
            platform_key = _get_or_create(conn, "dim_platform", "platform_key",
                                          "platform_name", platform,
                                          is_direct=_is_direct_platform(platform))
            role_key = _get_or_create(conn, "dim_role_family", "role_family_key",
                                      "role_family", role_fam)
            sen_key = _get_or_create(conn, "dim_seniority", "seniority_key",
                                     "seniority_name", seniority,
                                     seniority_rank=seniority_rank)
            ind_key = _get_or_create(conn, "dim_industry", "industry_key",
                                     "industry_name", industry)
            jt_key = _get_or_create(conn, "dim_job_type", "job_type_key",
                                    "job_type_name", job_type)

            days_to_response = None
            if thread in first_response and app_date:
                days_to_response = _days_between(app_date, first_response[thread])
            days_waiting = _days_between(app_date, last_upd or date.today().isoformat())

            is_response = 1 if _status_rank(status) in advanced_ranks or status == "Rejected" else 0
            is_interview_plus = 1 if status in ("Interview", "Offer") else 0
            is_rejected = 1 if status == "Rejected" else 0
            is_ghosted = 1 if status == "Ghosted" else 0
            active = status in ("Applied", "Assessment / OA", "Interview")
            is_stale = 1 if (active and days_waiting is not None and days_waiting > stale_days) else 0
            has_jd = 1 if len(str(a["job_description_snippet"] if "job_description_snippet" in a.keys() else "").strip()) >= 40 else 0

            conn.execute(
                """INSERT INTO fact_application (
                    thread_id, company_key, platform_key, status_key, role_family_key,
                    seniority_key, industry_key, job_type_key, application_date_key,
                    last_updated_key, company_name, role_title, source_platform,
                    current_status, application_date, last_updated,
                    days_to_response, days_waiting, is_response, is_interview_plus,
                    is_rejected, is_ghosted, is_stale, has_jd, loaded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?)""",
                (thread, company_key, platform_key, status, role_key, sen_key, ind_key,
                 jt_key, app_date, last_upd, company, title, platform, status, app_date,
                 last_upd, days_to_response, days_waiting, is_response, is_interview_plus,
                 is_rejected, is_ghosted, is_stale, has_jd, now))

        for e in events:
            thread = e["thread_id"]
            to_status = (e["to_status"] or "").strip()
            from_status = (e["from_status"] or "").strip() or None
            changed = _norm_date(e["changed_at"])
            company = conn.execute(
                "SELECT company_name FROM fact_application WHERE thread_id = ?",
                (thread,)).fetchone()
            event_rows.append((
                thread, from_status, to_status,
                _status_rank(from_status) if from_status else None,
                _status_rank(to_status), (e["note"] or ""), changed or "",
                changed, _event_type(e["note"] or "", to_status),
                company["company_name"] if company else "", now))

        if event_rows:
            conn.executemany(
                """INSERT INTO fact_status_event (thread_id, from_status, to_status, stage_from,
                       stage_to, note, changed_at, changed_date_key, event_type, company_name,
                       loaded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                event_rows)

        conn.commit()

        for table, key in (("dim_company", "companies"), ("dim_platform", "platforms"),
                           ("dim_role_family", "role_families"), ("dim_seniority", "seniorities"),
                           ("dim_industry", "industries"), ("dim_job_type", "job_types"),
                           ("dim_skill", "skills")):
            dim_counts[key] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

        return {
            "applications": len(apps),
            "events": len(event_rows),
            "dates": (end - start).days + 1,
            **dim_counts,
        }
    finally:
        conn.close()
