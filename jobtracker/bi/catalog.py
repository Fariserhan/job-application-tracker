"""The data dictionary and the star-schema model spec — written once, used by every surface.

``COLUMNS`` describes every column the warehouse exposes to BI tools. The Excel data
dictionary, ``powerbi/model.json`` and the README are generated from it, and
``tests/test_bi.py`` asserts it matches the live DuckDB catalogue column-for-column, so the
documentation cannot drift from the dbt models.
"""
from __future__ import annotations

# (column, type, description). Types are the Power BI / Tableau logical types.
_FACT_COLUMNS: list[tuple[str, str, str]] = [
    ("application_id", "text", "Natural key: the Gmail thread id (or a manual id). One row per application."),
    ("company_key", "text", "FK to dim_company (stable md5 hash of the lower-cased name)."),
    ("platform_key", "text", "FK to dim_platform."),
    ("status_key", "text", "FK to dim_status (the current status label)."),
    ("role_family_key", "text", "FK to dim_role_family."),
    ("seniority_key", "text", "FK to dim_seniority."),
    ("industry_key", "text", "FK to dim_industry."),
    ("job_type_key", "text", "FK to dim_job_type."),
    ("applied_date", "date", "Date the application was sent. ACTIVE relationship to dim_date."),
    ("last_activity_date", "date", "Date of the latest status change. Role-playing date (inactive relationship)."),
    ("first_response_date", "date", "Date of the first employer reply, when trustworthy; blank when unknown. Role-playing date (inactive relationship)."),
    ("company_name", "text", "Employer name as parsed from the email."),
    ("role_title", "text", "Role title; '(role not stated)' when the email did not name it."),
    ("platform", "text", "Where the application was made (LinkedIn, JobStreet, Direct ATS, ...)."),
    ("channel", "text", "Platform channel from the platform_channels seed (Job board, Employer careers site, Referral, ...)."),
    ("is_direct", "boolean", "TRUE when applied directly with the employer (careers site / ATS / referral)."),
    ("role_family", "text", "Role family (Data & Analytics, Actuarial, Risk & Quantitative, ...)."),
    ("seniority", "text", "Seniority level inferred from the title."),
    ("industry", "text", "Employer industry."),
    ("job_type", "text", "Full-Time, Internship, Contract, ..."),
    ("location", "text", "Job location when stated; blank otherwise."),
    ("current_status", "text", "Current pipeline status (Applied, Assessment / OA, Interview, Offer, Rejected, Ghosted, Withdrawn)."),
    ("funnel_stage", "text", "Funnel stage of the current status (Applied, Assessment, Interview, Offer, Closed)."),
    ("is_terminal", "boolean", "TRUE when the current status is final (Offer, Rejected, Ghosted, Withdrawn)."),
    ("is_active", "boolean", "TRUE while the application is still open (Applied, Assessment / OA, Interview)."),
    ("origin", "text", "How the row entered the tracker: gmail or manual."),
    ("parsed_by", "text", "Which parser produced the row (deterministic rules by default)."),
    ("parse_confidence", "decimal", "Parser confidence 0-1 for the classification."),
    ("job_url", "text", "Link to the job posting when captured."),
    ("gmail_link", "text", "Link to the source email thread."),
    ("max_stage_reached", "integer", "Furthest funnel stage ever reached: 1 Applied, 2 Assessment, 3 Interview, 4 Offer."),
    ("is_response", "boolean", "TRUE when the employer replied at all (assessment, interview, offer or rejection)."),
    ("reached_assessment", "boolean", "TRUE when the application reached an assessment or further (furthest stage, so a later rejection still counts)."),
    ("reached_interview", "boolean", "TRUE when the application reached an interview or further (furthest stage)."),
    ("reached_offer", "boolean", "TRUE when an offer was received."),
    ("is_rejected", "boolean", "TRUE when the current status is Rejected."),
    ("is_ghosted", "boolean", "TRUE when the current status is Ghosted (no reply within the ghost window)."),
    ("days_to_response", "integer", "Days from application to first employer reply. BLANK when the reply date is unknown - never zero-filled."),
    ("days_to_interview", "integer", "Days from application to the first interview; blank when unknown."),
    ("days_since_applied", "integer", "Age of the application in days at warehouse build time."),
    ("days_since_activity", "integer", "Days since the last status change (or since applying) at warehouse build time."),
    ("has_job_description", "boolean", "TRUE when a job-description snippet of 40+ characters was captured."),
    ("event_count", "integer", "Number of status events logged for the application."),
    ("has_low_trust_dates", "boolean", "TRUE when any event date was inferred rather than taken from a source email."),
    ("application_count", "integer", "Always 1. Sum it to count applications in tools that prefer SUM over COUNT."),
]

_CALENDAR_COLUMNS: list[tuple[str, str, str]] = [
    ("applied_year", "integer", "Calendar year of applied_date (from dim_date)."),
    ("applied_quarter", "integer", "Calendar quarter of applied_date."),
    ("applied_month", "text", "Year-month of applied_date (YYYY-MM): the cohort key."),
    ("applied_week", "date", "Monday of the ISO week of applied_date."),
    ("applied_weekday", "text", "Weekday name of applied_date."),
    ("applied_weekday_num", "integer", "ISO weekday number (1 = Monday)."),
    ("applied_on_weekend", "boolean", "TRUE when applied on a Saturday or Sunday."),
]


def _dim(key: str, name: str, label: str) -> list[tuple[str, str, str]]:
    return [
        (key, "text", f"Surrogate key (md5 of the lower-cased {label})."),
        (name, "text", f"{label.capitalize()} label (case variants folded together)."),
        ("applications", "integer", "Applications with this value at build time (informational; use the measures)."),
    ]


TABLES: dict[str, dict] = {
    "fct_applications": {
        "kind": "fact",
        "grain": "One row per job application.",
        "key": "application_id",
        "columns": _FACT_COLUMNS,
    },
    "fct_status_events": {
        "kind": "fact",
        "grain": "One row per status change, dated by the email that caused it.",
        "key": "event_id",
        "columns": [
            ("event_id", "text", "Natural key of the status event."),
            ("application_id", "text", "FK to fct_applications."),
            ("from_status", "text", "Status before the change; blank for the first event."),
            ("to_status", "text", "Status after the change."),
            ("occurred_on", "date", "Date the change happened (from the source email when trusted)."),
            ("source", "text", "What produced the event (email, manual edit, ghost rule, ...)."),
            ("note", "text", "Free-text note attached to the event."),
            ("is_employer_response", "boolean", "TRUE when the new status is an employer reply."),
            ("is_trusted_date", "boolean", "TRUE when occurred_on comes from a source email or a person."),
            ("company_name", "text", "Employer (denormalised from the application)."),
            ("role_title", "text", "Role title (denormalised)."),
            ("platform", "text", "Platform (denormalised)."),
            ("applied_date", "date", "Application date (denormalised)."),
            ("days_since_applied", "integer", "Days from application to this event."),
        ],
    },
    "dim_date": {
        "kind": "date",
        "grain": "One row per calendar day, contiguous from the first application (or 30 days ago) to today.",
        "key": "date_day",
        "columns": [
            ("date_day", "date", "The day. Key of the marked date table."),
            ("date_key", "integer", "YYYYMMDD integer key."),
            ("year", "integer", "Calendar year."),
            ("quarter", "integer", "Calendar quarter."),
            ("month", "integer", "Month number (sort key for month_name)."),
            ("month_name", "text", "Month name."),
            ("year_month", "text", "YYYY-MM."),
            ("week_start", "date", "Monday of the ISO week."),
            ("day_of_week_num", "integer", "ISO weekday number (1 = Monday; sort key for day_of_week)."),
            ("day_of_week", "text", "Weekday name."),
            ("is_weekend", "boolean", "TRUE on Saturday and Sunday."),
            ("days_ago", "integer", "Days before the warehouse build date."),
        ],
    },
    "dim_company": {"kind": "dimension", "grain": "One row per employer.", "key": "company_key",
                    "columns": _dim("company_key", "company_name", "company name")},
    "dim_platform": {
        "kind": "dimension",
        "grain": "One row per application platform.",
        "key": "platform_key",
        "columns": [
            ("platform_key", "text", "Surrogate key (md5 of the lower-cased platform)."),
            ("platform", "text", "Platform name."),
            ("channel", "text", "Channel group from the platform_channels seed."),
            ("is_direct", "boolean", "TRUE for employer careers sites, ATS portals and referrals."),
        ],
    },
    "dim_status": {
        "kind": "dimension",
        "grain": "One row per pipeline status (from the status_stages seed).",
        "key": "status_key",
        "columns": [
            ("status_key", "text", "Key = the status label."),
            ("status", "text", "Status label."),
            ("funnel_stage", "text", "Funnel stage the status belongs to."),
            ("stage_order", "integer", "Funnel depth: 1 Applied .. 4 Offer, 0 for closed / pre-application."),
            ("is_terminal", "boolean", "TRUE for final statuses."),
            ("is_active", "boolean", "TRUE for open statuses."),
            ("is_employer_response", "boolean", "TRUE when reaching this status implies an employer reply."),
            ("sort_order", "integer", "Display order (sort-by column for status)."),
        ],
    },
    "dim_role_family": {"kind": "dimension", "grain": "One row per role family.", "key": "role_family_key",
                        "columns": _dim("role_family_key", "role_family", "role family")},
    "dim_seniority": {"kind": "dimension", "grain": "One row per seniority level.", "key": "seniority_key",
                      "columns": _dim("seniority_key", "seniority", "seniority level")},
    "dim_industry": {"kind": "dimension", "grain": "One row per industry.", "key": "industry_key",
                     "columns": _dim("industry_key", "industry", "industry")},
    "dim_job_type": {"kind": "dimension", "grain": "One row per job type.", "key": "job_type_key",
                     "columns": _dim("job_type_key", "job_type", "job type")},
}

# The flat reporting view = every fact column + the calendar attributes of applied_date.
FLAT_VIEW = "rpt_application_analysis"
FLAT_COLUMNS: list[tuple[str, str, str]] = _FACT_COLUMNS + _CALENDAR_COLUMNS

# Relationships: (from_table, from_column, to_table, to_column, active, note)
RELATIONSHIPS: list[tuple[str, str, str, str, bool, str]] = [
    ("fct_applications", "applied_date", "dim_date", "date_day", True,
     "Active date relationship: every time-intelligence measure slices by application date."),
    ("fct_applications", "first_response_date", "dim_date", "date_day", False,
     "Role-playing date. Activate inside a measure with USERELATIONSHIP (e.g. [Responses (by Reply Date)])."),
    ("fct_applications", "last_activity_date", "dim_date", "date_day", False,
     "Role-playing date. Activate with USERELATIONSHIP (e.g. [Applications Touched (by Activity Date)])."),
    ("fct_applications", "company_key", "dim_company", "company_key", True, ""),
    ("fct_applications", "platform_key", "dim_platform", "platform_key", True, ""),
    ("fct_applications", "status_key", "dim_status", "status_key", True, ""),
    ("fct_applications", "role_family_key", "dim_role_family", "role_family_key", True, ""),
    ("fct_applications", "seniority_key", "dim_seniority", "seniority_key", True, ""),
    ("fct_applications", "industry_key", "dim_industry", "industry_key", True, ""),
    ("fct_applications", "job_type_key", "dim_job_type", "job_type_key", True, ""),
    ("fct_status_events", "application_id", "fct_applications", "application_id", True,
     "Events filter through their application, so every dimension slicer also filters events. "
     "No direct dim_date relationship (it would create an ambiguous path); "
     "[Status Changes (by Event Date)] uses TREATAS instead."),
]

# Power BI hygiene: hide keys and helper columns from report view; set sort-by columns.
HIDDEN_COLUMNS = {
    "fct_applications": ["company_key", "platform_key", "status_key", "role_family_key",
                         "seniority_key", "industry_key", "job_type_key", "application_count",
                         "company_name", "platform", "channel", "role_family", "seniority",
                         "industry", "job_type", "current_status"],
    "fct_status_events": ["application_id", "company_name", "role_title", "platform", "applied_date"],
    "dim_company": ["company_key", "applications"],
    "dim_platform": ["platform_key"],
    "dim_status": ["status_key"],
    "dim_role_family": ["role_family_key", "applications"],
    "dim_seniority": ["seniority_key", "applications"],
    "dim_industry": ["industry_key", "applications"],
    "dim_job_type": ["job_type_key", "applications"],
    "dim_date": ["date_key"],
}
SORT_BY = {
    ("dim_date", "month_name"): "month",
    ("dim_date", "day_of_week"): "day_of_week_num",
    ("dim_status", "status"): "sort_order",
    ("dim_status", "funnel_stage"): "stage_order",
}

FORMAT_STRINGS = {"date": "yyyy-mm-dd", "integer": "0", "decimal": "0.00"}


def column_names(table: str) -> list[str]:
    if table == FLAT_VIEW:
        return [c[0] for c in FLAT_COLUMNS]
    return [c[0] for c in TABLES[table]["columns"]]


def description(column: str, table: str = FLAT_VIEW) -> str:
    source = FLAT_COLUMNS if table == FLAT_VIEW else TABLES[table]["columns"]
    for name, _kind, text in source:
        if name == column:
            return text
    return ""


def column_type(column: str, table: str = FLAT_VIEW) -> str:
    source = FLAT_COLUMNS if table == FLAT_VIEW else TABLES[table]["columns"]
    for name, kind, _text in source:
        if name == column:
            return kind
    return "text"
