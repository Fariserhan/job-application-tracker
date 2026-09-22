"""Analytics warehouse over the job-application tracker (SQL-first).

This module is the analytical core the dashboard reads from. Instead of computing every
number ad-hoc in pandas, it:

  1. ensures a real dimensional model (star schema) exists inside applications.db,
  2. ETLs the operational `applications` + `status_history` rows into it,
  3. answers the tracker's business questions with SQL that actually uses CTEs, window
     functions and conditional aggregation.

The star schema is built from the real tracker data — one candidate's applications —
so it is deliberately sized to what is genuinely there rather than padded to look big:

    fact_application          grain = one application (thread)
    fact_status_event         grain = one status transition
    fact_application_skill    grain = application x skill (bridge; no longer populated)
    dim_date                  calendar, one row per day (marked as the date table)
    dim_company               employer
    dim_platform              source platform
    dim_status                pipeline status + its funnel stage + whether it is terminal
    dim_role_family           role classification derived from the title
    dim_seniority             seniority tier
    dim_industry              industry
    dim_job_type              employment type
    dim_skill                 skill / tool name + the family it belongs to

Everything is additive and idempotent: `build_warehouse()` can be re-run at any time and
rebuilds the model from the current operational tables. The operational tables are never
modified by this module.

Usage:
    from warehouse import build_warehouse, connect
    build_warehouse()                       # ETL (safe to re-run)
    with connect() as con:
        df = pd.read_sql_query(QUERIES["funnel_conversion"], con)

The dashboard calls `run_all()` for its KPI/chart data.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import date, datetime, timedelta

DB_PATH = "applications.db"

# ---------------------------------------------------------------- funnel semantics

FUNNEL_STAGES = ["Applied", "Assessment / OA", "Interview", "Offer"]
STATUS_STAGE = {
    "Saved / Planning": 0,
    "Applied": 1,
    "Assessment / OA": 2,
    "Interview": 3,
    "Offer": 4,
    "Rejected": -1,
    "Ghosted": -1,
}
# A status is "terminal" when the pipeline stops there (either a win or a loss).
TERMINAL_STATUSES = {"Offer", "Rejected", "Ghosted"}

ROLE_FAMILIES = [
    ("Actuarial", r"actuari\w+|\bpricing\b|reserving|valuation|experience analys\w*"),
    ("Risk & Quantitative", r"\brisk\b|quant\w*|\bcapital\b|solvency|market risk|credit risk"),
    ("Data & Analytics", r"data (?:analyst|scientis\w+|engineer\w*|enablement)|\banalytics\b|"
                         r"insight\w*|business intelligence|\bbi\b|statistical analyst|"
                         r"data scientist"),
    ("Business Analysis", r"business analyst|business analysis|product owner|project manage\w*|"
                          r"consultant"),
    ("Insurance Operations", r"claims|underwrit\w+|reinsur\w+|broking|takaful|insurance|"
                             r"portfolio services|renewal"),
    ("Marketing & Product", r"marketing|product (?:strategy|development|manage\w*)|brand|"
                            r"growth|campaign"),
    ("Other", r"."),
]

SKILL_FAMILIES = [
    ("Database & Query", r"\bsql\b|database|warehous|\betl\b|query"),
    ("BI & Visualisation", r"power\s?bi|tableau|qlik|looker|\bpbix\b|\bdax\b|dashboard|"
                           r"visualis|visualiz|business intelligence"),
    ("Programming", r"python|pandas|numpy|scikit|\br\b|javascript|\bjava\b|c\+\+|vba|macro"),
    ("Statistics & Modelling", r"glm|regression|statistic|probability|bayesian|forecast|"
                               r"time series|prophet|arima|monte carlo|machine learning|"
                               r"scikit|modelling|modeling|predictive"),
    ("Actuarial & Insurance", r"ifrs|mfrs|reserv|solvency|actuarial|prophet|vpms|pricing|"
                              r"claims|underwrit|takaful|insurance|premium|loss ratio"),
    ("Spreadsheet", r"excel|spreadsheet|vlookup|pivot|vba"),
    ("Risk & Regulation", r"risk|basel|complian\w+|governance|audit|regulat\w+"),
    ("Cloud & Engineering", r"\baws\b|azure|\bgcp\b|docker|kubernetes|airflow|spark|git\b|"
                            r"databricks"),
    ("Communication & Business", r"stakeholder|presentation|communicat\w+|reporting|"
                                 r"business partnering|analytical thinking"),
]

# ---------------------------------------------------------------- DDL


def _connect(db_path: str | None = None):
    conn = sqlite3.connect(db_path or DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA foreign_keys = ON")
    except sqlite3.Error:
        pass
    return conn


def connect(db_path: str | None = None):
    """Public connection factory — callers use `with connect() as con:`."""
    return _connect(db_path)


WAREHOUSE_DDL = """
-- ============================ dimensions ============================

CREATE TABLE IF NOT EXISTS dim_date (
    date_key       TEXT PRIMARY KEY,          -- YYYY-MM-DD, the marked date table
    year           INTEGER NOT NULL,
    quarter        INTEGER NOT NULL,
    month          INTEGER NOT NULL,
    month_name     TEXT    NOT NULL,
    year_month     TEXT    NOT NULL,          -- YYYY-MM
    week_start     TEXT    NOT NULL,          -- Monday of that ISO week
    day_of_week    TEXT    NOT NULL,
    day_of_week_n  INTEGER NOT NULL,          -- 1 = Monday
    is_weekend     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_company (
    company_key    INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name   TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS dim_platform (
    platform_key   INTEGER PRIMARY KEY AUTOINCREMENT,
    platform_name  TEXT NOT NULL UNIQUE,
    is_direct      INTEGER NOT NULL DEFAULT 0   -- 1 = employer ATS, 0 = job board
);

CREATE TABLE IF NOT EXISTS dim_status (
    status_key     TEXT PRIMARY KEY,
    funnel_stage   TEXT    NOT NULL,            -- Applied / Assessment / Interview / Offer / Outside
    stage_order    INTEGER NOT NULL,            -- 0 = pre-application, -1 = terminal/no funnel
    is_terminal    INTEGER NOT NULL,            -- 1 = pipeline stops here
    is_positive    INTEGER NOT NULL             -- 1 = a win (Offer)
);

CREATE TABLE IF NOT EXISTS dim_role_family (
    role_family_key INTEGER PRIMARY KEY AUTOINCREMENT,
    role_family     TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS dim_seniority (
    seniority_key  INTEGER PRIMARY KEY AUTOINCREMENT,
    seniority_name TEXT NOT NULL UNIQUE,
    seniority_rank INTEGER NOT NULL             -- 1 = most junior
);

CREATE TABLE IF NOT EXISTS dim_industry (
    industry_key   INTEGER PRIMARY KEY AUTOINCREMENT,
    industry_name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS dim_job_type (
    job_type_key   INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type_name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS dim_skill (
    skill_key      INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name     TEXT NOT NULL UNIQUE,
    skill_family   TEXT NOT NULL DEFAULT 'Other'
);

-- ============================ facts ============================

-- Grain: one application (one Gmail thread / one tracked role).
CREATE TABLE IF NOT EXISTS fact_application (
    application_key     INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id           TEXT NOT NULL UNIQUE,
    company_key         INTEGER NOT NULL REFERENCES dim_company(company_key),
    platform_key        INTEGER NOT NULL REFERENCES dim_platform(platform_key),
    status_key          TEXT    NOT NULL REFERENCES dim_status(status_key),
    role_family_key     INTEGER NOT NULL REFERENCES dim_role_family(role_family_key),
    seniority_key       INTEGER NOT NULL REFERENCES dim_seniority(seniority_key),
    industry_key        INTEGER NOT NULL REFERENCES dim_industry(industry_key),
    job_type_key        INTEGER NOT NULL REFERENCES dim_job_type(job_type_key),
    application_date_key TEXT   REFERENCES dim_date(date_key),
    last_updated_key    TEXT    REFERENCES dim_date(date_key),
    company_name        TEXT NOT NULL,
    role_title          TEXT NOT NULL,
    source_platform     TEXT NOT NULL,
    current_status      TEXT NOT NULL,
    application_date    TEXT,
    last_updated        TEXT,
    days_to_response    INTEGER,       -- application -> first employer-side status change
    days_waiting        INTEGER,       -- application -> last_updated
    is_response         INTEGER NOT NULL DEFAULT 0,  -- employer replied at all
    is_interview_plus   INTEGER NOT NULL DEFAULT 0,  -- reached interview or offer
    is_rejected         INTEGER NOT NULL DEFAULT 0,
    is_ghosted          INTEGER NOT NULL DEFAULT 0,  -- status Ghosted
    is_stale            INTEGER NOT NULL DEFAULT 0,  -- no update for STALE_DAYS while active
    has_jd              INTEGER NOT NULL DEFAULT 0,
    matched_skill_count INTEGER NOT NULL DEFAULT 0,
    missing_skill_count INTEGER NOT NULL DEFAULT 0,
    loaded_at           TEXT NOT NULL
);

-- Grain: one status transition for one application.
CREATE TABLE IF NOT EXISTS fact_status_event (
    event_key      INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id      TEXT NOT NULL,
    from_status    TEXT,
    to_status      TEXT NOT NULL,
    stage_from     INTEGER,
    stage_to       INTEGER,
    note           TEXT DEFAULT '',
    changed_at     TEXT NOT NULL,
    changed_date_key TEXT REFERENCES dim_date(date_key),
    event_type     TEXT NOT NULL DEFAULT 'status_change',  -- status_change | sync | manual | system
    company_name   TEXT NOT NULL DEFAULT '',
    loaded_at      TEXT NOT NULL
);

-- Grain: one application x one skill (bridge, carries the requirement type).
CREATE TABLE IF NOT EXISTS fact_application_skill (
    application_skill_key INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id        TEXT NOT NULL,
    skill_key        INTEGER NOT NULL REFERENCES dim_skill(skill_key),
    skill_name       TEXT NOT NULL,
    requirement_type TEXT NOT NULL,   -- matched | missing | improvement
    is_matched       INTEGER NOT NULL DEFAULT 0,
    loaded_at        TEXT NOT NULL
);

-- ============================ indexes ============================

CREATE INDEX IF NOT EXISTS ix_fact_app_date      ON fact_application (application_date_key);
CREATE INDEX IF NOT EXISTS ix_fact_app_status    ON fact_application (status_key);
CREATE INDEX IF NOT EXISTS ix_fact_app_company   ON fact_application (company_key);
CREATE INDEX IF NOT EXISTS ix_fact_app_platform  ON fact_application (platform_key);
CREATE INDEX IF NOT EXISTS ix_fact_app_rolefam   ON fact_application (role_family_key);
CREATE INDEX IF NOT EXISTS ix_fact_ev_thread     ON fact_status_event (thread_id, changed_at);
CREATE INDEX IF NOT EXISTS ix_fact_ev_date       ON fact_status_event (changed_date_key);
CREATE INDEX IF NOT EXISTS ix_fact_skill_thread  ON fact_application_skill (thread_id);
CREATE INDEX IF NOT EXISTS ix_fact_skill_skill   ON fact_application_skill (skill_key);

-- ============================ reporting view ============================

-- A denormalised convenience view so the dashboard (and anyone importing this into
-- Power BI / Tableau as a single flat extract) can read one tidy table.
CREATE VIEW IF NOT EXISTS vw_application_analysis AS
SELECT
    f.thread_id,
    f.company_name,
    f.role_title,
    rf.role_family,
    s.seniority_name,
    i.industry_name,
    jt.job_type_name,
    f.source_platform,
    p.is_direct                                                        AS is_direct_employer,
    f.current_status,
    st.funnel_stage,
    st.is_terminal,
    f.application_date,
    d.year                                                             AS applied_year,
    d.year_month                                                       AS applied_month,
    d.week_start                                                       AS applied_week,
    f.last_updated,
    f.days_waiting,
    f.days_to_response,
    f.is_response,
    f.is_interview_plus,
    f.is_rejected,
    f.is_ghosted,
    f.is_stale,
    f.has_jd,
    f.matched_skill_count,
    f.missing_skill_count
FROM fact_application f
JOIN dim_role_family rf  ON rf.role_family_key = f.role_family_key
JOIN dim_seniority   s   ON s.seniority_key    = f.seniority_key
JOIN dim_industry    i   ON i.industry_key     = f.industry_key
JOIN dim_job_type    jt  ON jt.job_type_key    = f.job_type_key
JOIN dim_platform    p   ON p.platform_key     = f.platform_key
JOIN dim_status      st  ON st.status_key      = f.status_key
LEFT JOIN dim_date   d   ON d.date_key         = f.application_date_key;
"""

# ---------------------------------------------------------------- helpers


def _norm_date(value) -> str | None:
    """Coerce a stored date value to YYYY-MM-DD, or None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("nan", "none", "nat"):
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:19], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    return m.group(0) if m else None


def _as_list(value) -> list:
    """Skills/improvements are stored as JSON arrays; tolerate plain text too."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if not text or text.lower() in ("nan", "none"):
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(v).strip() for v in parsed if str(v).strip()]
        if isinstance(parsed, str) and parsed.strip():
            return [parsed.strip()]
    except (ValueError, TypeError):
        pass
    return [p.strip() for p in re.split(r"[;,|]", text) if p.strip()]


def _role_family(title: str) -> str:
    t = (title or "").lower()
    for name, pattern in ROLE_FAMILIES:
        if re.search(pattern, t):
            return name
    return "Other"


def _seniority(title: str) -> tuple[str, int]:
    t = (title or "").lower()
    tiers = [
        ("Internship", 1, r"intern\b|industrial train\w+|industrial attach\w+"),
        ("Graduate / Entry", 2, r"graduate|entry[- ]level|trainee|fresh|protege|"
                               r"management train\w+|\bmctf\b|\bpbtb\b"),
        ("Junior / Executive", 3, r"\bjunior\b|\bexecutive\b|officer|assistant|advisor|agent"),
        ("Analyst / Associate", 4, r"analyst|associate"),
        ("Senior / Lead", 5, r"\bsenior\b|\bsr\.?\b|\blead\b|specialist"),
        ("Manager / VP+", 6, r"manager|\bavp\b|\bvp\d*\b|vice president|director|head of|chief"),
    ]
    for name, rank, pattern in tiers:
        if re.search(pattern, t):
            return name, rank
    if not t.strip():
        return "Unknown", 0
    return "Analyst / Associate", 4


def _skill_family(skill: str) -> str:
    s = (skill or "").lower()
    for name, pattern in SKILL_FAMILIES:
        if re.search(pattern, s):
            return name
    return "Other"


def _is_direct_platform(platform: str) -> int:
    p = (platform or "").lower()
    return 1 if ("direct" in p or "ats" in p or "workday" in p or "oracle" in p) else 0


def _status_row(status: str) -> tuple[str, int, int, int]:
    """(funnel_stage, stage_order, is_terminal, is_positive)"""
    s = status or "Applied"
    if s in TERMINAL_STATUSES:
        return s, -1, 1, 1 if s == "Offer" else 0
    if s in FUNNEL_STAGES[1:]:
        return s, FUNNEL_STAGES.index(s) + 1, 0, 0
    if s == "Applied":
        return "Applied", 1, 0, 0
    return "Outside", 0, 0, 0


def _days_between(start, end) -> int | None:
    a, b = _norm_date(start), _norm_date(end)
    if not a or not b:
        return None
    delta = (date.fromisoformat(b) - date.fromisoformat(a)).days
    return delta if delta >= 0 else None


def _status_rank(status: str) -> int:
    """Higher = further along the (positive) pipeline; -1 = left the pipeline."""
    return STATUS_STAGE.get(status or "", 0)


def _event_type(note: str, to_status: str) -> str:
    n = (note or "").lower()
    if "auto-ghost" in n or "ghost" in n:
        return "system"
    if "manual" in n or "recover" in n or "noise" in n:
        return "manual"
    if n:
        return "status_change"
    return "sync"


# ---------------------------------------------------------------- public API


def build_warehouse(db_path: str | None = None, stale_days: int = 21) -> dict:
    """ETL the operational tables into the star schema. Idempotent and additive.

    Re-running rebuilds the fact tables from the current operational data; the dimension
    tables are keyed by name and reused, so keys stay stable across rebuilds.

    Returns the row counts from the load.
    """
    from warehouse_etl import run_etl

    return run_etl(db_path=db_path, stale_days=stale_days)


def ensure_warehouse(db_path: str | None = None) -> dict:
    """Create the star schema without loading it (safe on an empty database)."""
    conn = _connect(db_path)
    try:
        conn.executescript(WAREHOUSE_DDL)
        conn.commit()
    finally:
        conn.close()
    return {"created": True}
