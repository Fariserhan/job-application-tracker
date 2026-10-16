"""The semantic metric layer: every number the app, the Excel workbook and the BI pack show.

Each metric is one SQL statement over the dbt marts, computed once in DuckDB — the app
never re-derives an analytic in pandas. Every statement starts from a ``base`` CTE (the
filtered ``fct_applications``), so sidebar filters apply uniformly to every chart.

Statistical honesty is built in rather than footnoted:

* rates carry **Wilson score 95% intervals** (well-behaved at small n and near 0%/100%)
* time-to-reply uses **Kaplan–Meier survival**: applications still waiting are *censored*
  observations, not silently dropped (dropping them makes employers look faster than they are)
* unknown durations stay NULL and are excluded, never zero-filled
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .. import benchmarks as bm

Z = 1.96  # 95% two-sided


def wilson(x: str, n: str, bound: str) -> str:
    """SQL expression: Wilson score interval bound (in percent) for x successes out of n."""
    sign = "-" if bound == "lo" else "+"
    p = f"(1.0 * ({x}) / NULLIF({n}, 0))"
    z2 = Z * Z
    return (f"ROUND(100 * GREATEST(0, LEAST(1, (({p} + {z2}/(2.0*({n}))) {sign} {Z} * "
            f"SQRT({p}*(1-{p})/({n}) + {z2}/(4.0*({n})*({n})))) / (1 + {z2}/({n})))), 1)")


def rate(x: str, n: str) -> str:
    return f"ROUND(100.0 * ({x}) / NULLIF({n}, 0), 1)"


# ---------------------------------------------------------------------------- filters

SEGMENT_COLUMNS = {
    "platform": "platform",
    "channel": "channel",
    "role_family": "role_family",
    "industry": "industry",
    "seniority": "seniority",
    "job_type": "job_type",
    "company": "company_name",
}


@dataclass
class Filters:
    date_from: date | None = None
    date_to: date | None = None
    platforms: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    role_families: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    job_types: list[str] = field(default_factory=list)
    search: str = ""

    def clause(self) -> tuple[str, dict]:
        parts: list[str] = ["TRUE"]
        params: dict[str, object] = {}
        if self.date_from:
            parts.append("applied_date >= $f_from")
            params["f_from"] = self.date_from
        if self.date_to:
            parts.append("applied_date <= $f_to")
            params["f_to"] = self.date_to
        for attr, column in (("platforms", "platform"), ("statuses", "current_status"),
                             ("role_families", "role_family"), ("industries", "industry"),
                             ("job_types", "job_type")):
            values = getattr(self, attr)
            if values:
                parts.append(f"list_contains($f_{attr}, {column})")
                params[f"f_{attr}"] = list(values)
        if self.search.strip():
            parts.append("(company_name ILIKE $f_search OR role_title ILIKE $f_search "
                         "OR coalesce(location, '') ILIKE $f_search)")
            params["f_search"] = f"%{self.search.strip()}%"
        return " AND ".join(parts), params

    @property
    def active(self) -> bool:
        return self.clause()[0] != "TRUE"


BASE = "WITH base AS (SELECT * FROM fct_applications WHERE {where})\n"

# ---------------------------------------------------------------------------- the SQL

SQL: dict[str, str] = {}

SQL["kpis"] = f"""
-- Headline KPIs. Response = any employer reply (assessment, interview, offer or rejection);
-- interview rate uses the furthest stage reached, so a later rejection does not erase it.
SELECT
    COUNT(*)                                                     AS applications,
    COUNT(*) FILTER (WHERE is_response)                          AS responses,
    COUNT(*) FILTER (WHERE reached_assessment)                   AS assessments,
    COUNT(*) FILTER (WHERE reached_interview)                    AS interviews,
    COUNT(*) FILTER (WHERE reached_offer)                        AS offers,
    COUNT(*) FILTER (WHERE is_rejected)                          AS rejections,
    COUNT(*) FILTER (WHERE is_ghosted)                           AS ghosted,
    COUNT(*) FILTER (WHERE is_active)                            AS active,
    COUNT(*) FILTER (WHERE is_active AND days_since_activity >= $follow_up_days)
                                                                 AS needs_follow_up,
    COUNT(DISTINCT company_name)                                 AS companies,
    {rate("COUNT(*) FILTER (WHERE is_response)", "COUNT(*)")}         AS response_rate_pct,
    {wilson("COUNT(*) FILTER (WHERE is_response)", "COUNT(*)", "lo")} AS response_rate_lo,
    {wilson("COUNT(*) FILTER (WHERE is_response)", "COUNT(*)", "hi")} AS response_rate_hi,
    {rate("COUNT(*) FILTER (WHERE reached_interview)", "COUNT(*)")}   AS interview_rate_pct,
    {wilson("COUNT(*) FILTER (WHERE reached_interview)", "COUNT(*)", "lo")} AS interview_rate_lo,
    {wilson("COUNT(*) FILTER (WHERE reached_interview)", "COUNT(*)", "hi")} AS interview_rate_hi,
    {rate("COUNT(*) FILTER (WHERE reached_offer)", "COUNT(*)")}       AS offer_rate_pct,
    {rate("COUNT(*) FILTER (WHERE is_rejected)", "COUNT(*)")}         AS rejection_rate_pct,
    {rate("COUNT(*) FILTER (WHERE is_ghosted)", "COUNT(*)")}          AS ghost_rate_pct,
    MEDIAN(days_to_response)                                     AS median_days_to_response,
    ROUND(AVG(days_to_response), 1)                              AS avg_days_to_response,
    COUNT(days_to_response)                                      AS responses_with_known_date,
    MEDIAN(days_to_interview)                                    AS median_days_to_interview,
    {rate("COUNT(*) FILTER (WHERE is_direct)", "COUNT(*)")}           AS direct_share_pct,
    {bm.response_band_sql(rate("COUNT(*) FILTER (WHERE is_response)", "COUNT(*)"))} AS response_band,
    MIN(applied_date)                                            AS first_applied,
    MAX(applied_date)                                            AS last_applied
FROM base
"""

SQL["period_compare"] = """
-- The last 30 days against the 30 before: volume AND quality, so a busier month that
-- converts worse is visible as such.
WITH windows AS (
    SELECT
        CASE WHEN applied_date > current_date - 30 THEN 'last_30'
             WHEN applied_date > current_date - 60 THEN 'prev_30' END AS period,
        is_response, reached_interview
    FROM base
)
SELECT
    COUNT(*) FILTER (WHERE period = 'last_30')                                    AS last_30,
    COUNT(*) FILTER (WHERE period = 'prev_30')                                    AS prev_30,
    COUNT(*) FILTER (WHERE period = 'last_30') - COUNT(*) FILTER (WHERE period = 'prev_30')
                                                                                  AS change,
    ROUND(100.0 * COUNT(*) FILTER (WHERE period = 'last_30' AND is_response)
          / NULLIF(COUNT(*) FILTER (WHERE period = 'last_30'), 0), 1)            AS last_30_response_pct,
    ROUND(100.0 * COUNT(*) FILTER (WHERE period = 'prev_30' AND is_response)
          / NULLIF(COUNT(*) FILTER (WHERE period = 'prev_30'), 0), 1)            AS prev_30_response_pct
FROM windows
"""

_resp_lo, _resp_hi = bm.FUNNEL_PER_100["Employer responded"]
_int_lo, _int_hi = bm.FUNNEL_PER_100["Interview"]
_off_lo, _off_hi = bm.FUNNEL_PER_100["Offer"]
SQL["funnel"] = f"""
-- The application funnel on the furthest stage reached, with stage-to-stage conversion
-- (LAG) and the published "per 100 applications" benchmark next to every stage.
WITH stages AS (
    SELECT 1 AS stage_order, 'Applied' AS stage, COUNT(*) AS applications,
           NULL::DOUBLE AS bench_lo, NULL::DOUBLE AS bench_hi FROM base
    UNION ALL SELECT 2, 'Employer responded', COUNT(*) FILTER (WHERE is_response),
           {_resp_lo}, {_resp_hi} FROM base
    UNION ALL SELECT 3, 'Assessment', COUNT(*) FILTER (WHERE reached_assessment),
           NULL, NULL FROM base
    UNION ALL SELECT 4, 'Interview', COUNT(*) FILTER (WHERE reached_interview),
           {_int_lo}, {_int_hi} FROM base
    UNION ALL SELECT 5, 'Offer', COUNT(*) FILTER (WHERE reached_offer),
           {_off_lo}, {_off_hi} FROM base
)
SELECT
    stage_order,
    stage,
    applications,
    ROUND(100.0 * applications / NULLIF(FIRST_VALUE(applications) OVER w, 0), 1) AS pct_of_applied,
    ROUND(100.0 * applications / NULLIF(LAG(applications) OVER w, 0), 1)         AS step_conversion_pct,
    LAG(applications) OVER w - applications                                      AS lost_at_step,
    bench_lo                                                                     AS benchmark_per_100_lo,
    bench_hi                                                                     AS benchmark_per_100_hi,
    CASE
        WHEN bench_lo IS NULL THEN NULL
        WHEN 100.0 * applications / NULLIF(FIRST_VALUE(applications) OVER w, 0) > bench_hi THEN 'Ahead'
        WHEN 100.0 * applications / NULLIF(FIRST_VALUE(applications) OVER w, 0) >= bench_lo THEN 'On par'
        ELSE 'Behind'
    END                                                                          AS vs_benchmark
FROM stages
WINDOW w AS (ORDER BY stage_order)
ORDER BY stage_order
"""

SQL["sankey"] = """
-- Where every application went, as source -> target flows for a Sankey diagram.
WITH flows AS (
    SELECT 'Applied' AS source, 'Assessment' AS target FROM base WHERE reached_assessment
    UNION ALL SELECT 'Applied', 'Interview' FROM base WHERE reached_interview AND NOT reached_assessment
    UNION ALL SELECT 'Applied', 'Rejected' FROM base WHERE max_stage_reached = 1 AND is_rejected
    UNION ALL SELECT 'Applied', 'Ghosted' FROM base WHERE max_stage_reached = 1 AND is_ghosted
    UNION ALL SELECT 'Applied', 'Awaiting reply' FROM base WHERE max_stage_reached = 1 AND is_active
    UNION ALL SELECT 'Applied', 'Withdrawn' FROM base WHERE max_stage_reached = 1 AND current_status = 'Withdrawn'
    UNION ALL SELECT 'Assessment', 'Interview' FROM base WHERE reached_assessment AND reached_interview
    UNION ALL SELECT 'Assessment', 'Rejected' FROM base WHERE max_stage_reached = 2 AND is_rejected
    UNION ALL SELECT 'Assessment', 'In progress' FROM base WHERE max_stage_reached = 2 AND is_active
    UNION ALL SELECT 'Assessment', 'Ghosted' FROM base WHERE max_stage_reached = 2 AND (is_ghosted OR current_status = 'Withdrawn')
    UNION ALL SELECT 'Interview', 'Offer' FROM base WHERE reached_offer
    UNION ALL SELECT 'Interview', 'Rejected' FROM base WHERE max_stage_reached = 3 AND is_rejected
    UNION ALL SELECT 'Interview', 'In progress' FROM base WHERE max_stage_reached = 3 AND is_active
    UNION ALL SELECT 'Interview', 'Ghosted' FROM base WHERE max_stage_reached = 3 AND (is_ghosted OR current_status = 'Withdrawn')
)
SELECT source, target, COUNT(*) AS applications
FROM flows
GROUP BY source, target
ORDER BY source, applications DESC
"""

SQL["weekly_velocity"] = """
-- Applications per ISO week with outcomes, a 4-week rolling average and a running total.
WITH weeks AS (
    SELECT week_start
    FROM dim_date
    WHERE date_day BETWEEN (SELECT MIN(applied_date) FROM base) AND current_date
    GROUP BY week_start
),
counts AS (
    SELECT CAST(date_trunc('week', applied_date) AS DATE) AS week_start,
           COUNT(*) AS applications,
           COUNT(*) FILTER (WHERE is_response) AS responses,
           COUNT(*) FILTER (WHERE reached_interview) AS interviews
    FROM base GROUP BY 1
)
SELECT
    w.week_start,
    COALESCE(c.applications, 0)                                              AS applications,
    COALESCE(c.responses, 0)                                                 AS responses,
    COALESCE(c.interviews, 0)                                                AS interviews,
    ROUND(AVG(COALESCE(c.applications, 0)) OVER (ORDER BY w.week_start
          ROWS BETWEEN 3 PRECEDING AND CURRENT ROW), 1)                      AS rolling_4w_avg,
    SUM(COALESCE(c.applications, 0)) OVER (ORDER BY w.week_start)            AS cumulative
FROM weeks w
LEFT JOIN counts c USING (week_start)
ORDER BY w.week_start
"""

SQL["monthly_cohorts"] = f"""
-- Cohort view: applications grouped by the month they were sent, and how each cohort
-- has played out so far. Recent cohorts are still maturing — read their rates as floors.
SELECT
    strftime(applied_date, '%Y-%m')                                  AS cohort_month,
    COUNT(*)                                                         AS applications,
    COUNT(*) FILTER (WHERE is_response)                              AS responses,
    COUNT(*) FILTER (WHERE reached_interview)                        AS interviews,
    COUNT(*) FILTER (WHERE reached_offer)                            AS offers,
    COUNT(*) FILTER (WHERE is_rejected)                              AS rejected,
    COUNT(*) FILTER (WHERE is_ghosted)                               AS ghosted,
    COUNT(*) FILTER (WHERE is_active)                                AS still_open,
    {rate("COUNT(*) FILTER (WHERE is_response)", "COUNT(*)")}             AS response_rate_pct,
    {rate("COUNT(*) FILTER (WHERE reached_interview)", "COUNT(*)")}       AS interview_rate_pct,
    MAX(days_since_applied) < 30                                     AS is_maturing
FROM base
GROUP BY 1
ORDER BY 1
"""

SQL["platform_performance"] = f"""
-- Which channel actually produces replies — with 95% Wilson intervals, so a platform with
-- six applications cannot "win" on luck — and the gap to the 6.87% direct-application
-- interview benchmark.
SELECT
    platform,
    ANY_VALUE(channel)                                                      AS channel,
    BOOL_OR(is_direct)                                                      AS is_direct,
    COUNT(*)                                                                AS applications,
    COUNT(*) FILTER (WHERE is_response)                                     AS responses,
    COUNT(*) FILTER (WHERE reached_interview)                               AS interviews,
    COUNT(*) FILTER (WHERE reached_offer)                                   AS offers,
    {rate("COUNT(*) FILTER (WHERE is_response)", "COUNT(*)")}                    AS response_rate_pct,
    {wilson("COUNT(*) FILTER (WHERE is_response)", "COUNT(*)", "lo")}            AS response_rate_lo,
    {wilson("COUNT(*) FILTER (WHERE is_response)", "COUNT(*)", "hi")}            AS response_rate_hi,
    {rate("COUNT(*) FILTER (WHERE reached_interview)", "COUNT(*)")}              AS interview_rate_pct,
    ROUND(100.0 * COUNT(*) FILTER (WHERE reached_interview) / COUNT(*) - {bm.DIRECT_INTERVIEW_RATE_PCT}, 1)
                                                                            AS vs_direct_benchmark_pp,
    MEDIAN(days_to_response)                                                AS median_days_to_response,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)                      AS share_pct,
    DENSE_RANK() OVER (ORDER BY 1.0 * COUNT(*) FILTER (WHERE is_response) / COUNT(*) DESC)
                                                                            AS response_rank,
    CASE WHEN COUNT(*) >= {bm.SAMPLE_ADEQUATE} THEN 'Adequate'
         WHEN COUNT(*) >= {bm.SAMPLE_INDICATIVE} THEN 'Indicative'
         ELSE 'Too small' END                                               AS sample_confidence
FROM base
GROUP BY platform
ORDER BY applications DESC
"""

SQL["segment_performance"] = f"""
-- Generic segment breakdown (role family, industry, seniority, job type, channel, company).
SELECT
    {{segment}}                                                             AS segment,
    COUNT(*)                                                                AS applications,
    COUNT(*) FILTER (WHERE is_response)                                     AS responses,
    COUNT(*) FILTER (WHERE reached_interview)                               AS interviews,
    COUNT(*) FILTER (WHERE reached_offer)                                   AS offers,
    {rate("COUNT(*) FILTER (WHERE is_response)", "COUNT(*)")}                    AS response_rate_pct,
    {wilson("COUNT(*) FILTER (WHERE is_response)", "COUNT(*)", "lo")}            AS response_rate_lo,
    {wilson("COUNT(*) FILTER (WHERE is_response)", "COUNT(*)", "hi")}            AS response_rate_hi,
    {rate("COUNT(*) FILTER (WHERE reached_interview)", "COUNT(*)")}              AS interview_rate_pct,
    MEDIAN(days_to_response)                                                AS median_days_to_response
FROM base
GROUP BY 1
ORDER BY applications DESC, segment
"""

SQL["response_survival"] = """
-- Kaplan–Meier estimate of time-to-first-reply. Applications still waiting are right-
-- censored at their current age instead of being dropped, so the curve is not biased
-- toward fast responders. Replies with an unknown date are excluded (cannot be placed).
WITH obs AS (
    SELECT
        CASE WHEN is_response THEN days_to_response ELSE days_since_applied END AS t,
        is_response                                                             AS event
    FROM base
    WHERE NOT (is_response AND days_to_response IS NULL)
),
grouped AS (
    SELECT t,
           COUNT(*) FILTER (WHERE event)     AS responses,
           COUNT(*) FILTER (WHERE NOT event) AS censored,
           COUNT(*)                          AS n_t
    FROM obs GROUP BY t
),
risk AS (
    SELECT *, SUM(n_t) OVER (ORDER BY t DESC) AS at_risk FROM grouped
),
km AS (
    SELECT *,
           EXP(SUM(LN(GREATEST(1 - 1.0 * responses / at_risk, 1e-12))) OVER (ORDER BY t))
               AS survival
    FROM risk
)
SELECT
    t                                        AS day,
    at_risk,
    responses,
    censored,
    ROUND(100 * survival, 2)                 AS still_waiting_pct,
    ROUND(100 * (1 - survival), 2)           AS replied_pct
FROM km
ORDER BY t
"""

SQL["response_time_buckets"] = """
-- How long employers take to reply, bucketed (known reply dates only).
SELECT
    CASE
        WHEN days_to_response <= 3  THEN '0-3 days'
        WHEN days_to_response <= 7  THEN '4-7 days'
        WHEN days_to_response <= 14 THEN '1-2 weeks'
        WHEN days_to_response <= 30 THEN '2-4 weeks'
        ELSE '1 month+'
    END                                                             AS bucket,
    MIN(days_to_response)                                           AS sort_key,
    COUNT(*)                                                        AS responses,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)              AS share_pct,
    COUNT(*) FILTER (WHERE is_rejected)                             AS rejections,
    COUNT(*) FILTER (WHERE reached_assessment)                      AS progressed
FROM base
WHERE days_to_response IS NOT NULL
GROUP BY 1
ORDER BY sort_key
"""

SQL["weekday_performance"] = f"""
-- Does the day you apply matter? Volume and reply rate by weekday (indicative only).
SELECT
    isodow(applied_date)                                            AS weekday_num,
    strftime(applied_date, '%a')                                    AS weekday,
    COUNT(*)                                                        AS applications,
    COUNT(*) FILTER (WHERE is_response)                             AS responses,
    {rate("COUNT(*) FILTER (WHERE is_response)", "COUNT(*)")}            AS response_rate_pct
FROM base
GROUP BY 1, 2
ORDER BY 1
"""

SQL["activity_calendar"] = """
-- GitHub-style activity grid: one cell per day for the last 26 weeks.
SELECT
    d.date_day,
    d.week_start,
    d.day_of_week_num,
    COUNT(b.application_id)                                         AS applications
FROM dim_date d
LEFT JOIN base b ON b.applied_date = d.date_day
WHERE d.date_day > current_date - 182
GROUP BY ALL
ORDER BY d.date_day
"""

SQL["status_mix"] = """
-- Where every application currently stands.
SELECT
    b.current_status                                                AS status,
    ANY_VALUE(s.sort_order)                                         AS sort_order,
    COUNT(*)                                                        AS applications,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)              AS share_pct,
    ROUND(AVG(b.days_since_activity), 1)                            AS avg_days_since_activity
FROM base b
LEFT JOIN dim_status s ON s.status_key = b.current_status
GROUP BY 1
ORDER BY sort_order
"""

SQL["stage_timing"] = """
-- Typical elapsed time to each milestone (medians; known dates only).
SELECT 'First reply' AS milestone, 1 AS sort_order,
       COUNT(days_to_response) AS applications,
       MEDIAN(days_to_response) AS median_days,
       QUANTILE_CONT(days_to_response, 0.25) AS p25_days,
       QUANTILE_CONT(days_to_response, 0.75) AS p75_days
FROM base
UNION ALL
SELECT 'Interview', 2, COUNT(days_to_interview), MEDIAN(days_to_interview),
       QUANTILE_CONT(days_to_interview, 0.25), QUANTILE_CONT(days_to_interview, 0.75)
FROM base
ORDER BY sort_order
"""

SQL["company_leaderboard"] = f"""
-- Employers: repeat applications, outcomes and recency.
SELECT
    company_name,
    ANY_VALUE(industry)                                             AS industry,
    COUNT(*)                                                        AS applications,
    COUNT(DISTINCT role_title)                                      AS distinct_roles,
    COUNT(*) FILTER (WHERE is_response)                             AS responses,
    COUNT(*) FILTER (WHERE reached_interview)                       AS interviews,
    COUNT(*) FILTER (WHERE reached_offer)                           AS offers,
    COUNT(*) FILTER (WHERE is_active)                               AS open,
    {rate("COUNT(*) FILTER (WHERE is_response)", "COUNT(*)")}            AS response_rate_pct,
    MAX(applied_date)                                               AS last_applied
FROM base
GROUP BY company_name
ORDER BY applications DESC, interviews DESC, company_name
"""

SQL["follow_up_queue"] = """
-- The action list: open applications that have gone quiet, oldest silence first.
SELECT
    application_id,
    company_name,
    role_title,
    platform,
    current_status,
    applied_date,
    last_activity_date,
    days_since_activity,
    gmail_link,
    job_url,
    CASE
        WHEN current_status = 'Interview'        THEN 'Thank-you / status check'
        WHEN current_status = 'Assessment / OA'  THEN 'Confirm the assessment was received'
        WHEN days_since_activity >= $ghost_after_days - 3 THEN 'Final nudge'
        ELSE 'Polite follow-up'
    END                                                             AS suggested_action
FROM base
WHERE is_active AND days_since_activity >= $follow_up_days
ORDER BY days_since_activity DESC
"""

SQL["transitions"] = """
-- Status transition matrix from the event log (who moved where, and how fast).
SELECT
    COALESCE(e.from_status, '(new)')                                AS from_status,
    e.to_status,
    COUNT(*)                                                        AS transitions,
    ROUND(AVG(e.days_since_applied), 1)                             AS avg_days_since_applied
FROM fct_status_events e
JOIN base b USING (application_id)
WHERE e.from_status IS DISTINCT FROM e.to_status
GROUP BY 1, 2
ORDER BY transitions DESC
"""

SQL["recent_activity"] = """
-- The latest status movements, newest first.
SELECT e.occurred_on, e.company_name, e.role_title, e.from_status, e.to_status, e.source,
       e.note, e.application_id
FROM fct_status_events e
JOIN base b USING (application_id)
ORDER BY e.occurred_on DESC, e.event_id DESC
LIMIT 40
"""

SQL["data_quality"] = """
-- The report audits itself: coverage of every attribute the analysis depends on.
SELECT 'Employer identified' AS check_name,
       COUNT(*) FILTER (WHERE company_name <> 'Unknown') AS passing, COUNT(*) AS total FROM base
UNION ALL SELECT 'Role title identified',
       COUNT(*) FILTER (WHERE role_title <> '(role not stated)'), COUNT(*) FROM base
UNION ALL SELECT 'Job description captured',
       COUNT(*) FILTER (WHERE has_job_description), COUNT(*) FROM base
UNION ALL SELECT 'Posting link captured',
       COUNT(*) FILTER (WHERE job_url IS NOT NULL), COUNT(*) FROM base
UNION ALL SELECT 'Reply date known (of replies)',
       COUNT(days_to_response), COUNT(*) FILTER (WHERE is_response) FROM base
UNION ALL SELECT 'High-confidence parse',
       COUNT(*) FILTER (WHERE COALESCE(parse_confidence, 1) >= 0.75 OR origin = 'manual'),
       COUNT(*) FROM base
UNION ALL SELECT 'Event dates from source emails',
       COUNT(*) FILTER (WHERE NOT has_low_trust_dates), COUNT(*) FROM base
"""

SQL["applications"] = """
-- Row-level application list for the tracker table.
SELECT application_id, company_name, role_title, platform, channel, role_family, seniority,
       industry, job_type, location, current_status, funnel_stage, max_stage_reached,
       applied_date, last_activity_date, first_response_date, days_to_response,
       days_since_activity, is_response, reached_interview, is_active, origin, parsed_by,
       parse_confidence, job_url, gmail_link
FROM base
ORDER BY applied_date DESC, company_name
"""

# Queries that read the whole warehouse, not the filtered base.
UNFILTERED = {
    "parser_quality": """
-- How the noise filter judged every inspected email.
SELECT * FROM mart_parser_quality ORDER BY messages DESC
""",
    "filter_options": """
SELECT 'platform' AS dim, platform AS value, COUNT(*) AS n FROM fct_applications GROUP BY 2
UNION ALL SELECT 'status', current_status, COUNT(*) FROM fct_applications GROUP BY 2
UNION ALL SELECT 'role_family', role_family, COUNT(*) FROM fct_applications GROUP BY 2
UNION ALL SELECT 'industry', industry, COUNT(*) FROM fct_applications GROUP BY 2
UNION ALL SELECT 'job_type', job_type, COUNT(*) FROM fct_applications GROUP BY 2
ORDER BY dim, n DESC
""",
    "date_bounds": "SELECT MIN(applied_date) AS first, MAX(applied_date) AS last FROM fct_applications",
}

_PARAM = re.compile(r"\$([a-z_]+)")


def compose(base_cte: str, body: str) -> str:
    """Prefix ``body`` with the base CTE, merging a leading WITH into one CTE list."""
    lines = body.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        if stripped.upper().startswith("WITH "):
            lines[i] = line.replace(stripped[:4], ",", 1)
        break
    return base_cte + "\n".join(lines)


class Metrics:
    """Runs named metric queries against an open DuckDB connection."""

    def __init__(self, con, settings=None, filters: Filters | None = None):
        self.con = con
        self.filters = filters or Filters()
        self.defaults = {
            "follow_up_days": getattr(settings, "follow_up_after_days", 7),
            "ghost_after_days": getattr(settings, "ghost_after_days", 21),
        }

    @staticmethod
    def sql(name: str, segment: str = "platform") -> str:
        if name in UNFILTERED:
            return UNFILTERED[name].strip()
        body = SQL[name]
        if name == "segment_performance":
            body = body.replace("{segment}", SEGMENT_COLUMNS[segment])
        return body.strip()

    def query(self, name: str, segment: str = "platform", **params) -> pd.DataFrame:
        if name in UNFILTERED:
            statement, values = UNFILTERED[name], dict[str, object]()
        else:
            if name == "segment_performance" and segment not in SEGMENT_COLUMNS:
                raise ValueError(f"unknown segment {segment!r}")
            where, values = self.filters.clause()
            statement = compose(BASE.format(where=where), self.sql(name, segment))
        merged = {**self.defaults, **values, **params}
        used = set(_PARAM.findall(statement))
        return self.con.execute(statement, {k: v for k, v in merged.items() if k in used}).df()

    def kpis(self) -> dict:
        frame = self.query("kpis")
        return {} if frame.empty else frame.iloc[0].to_dict()

    def all(self) -> dict[str, pd.DataFrame]:
        out = {name: self.query(name) for name in SQL if name != "segment_performance"}
        for seg in ("role_family", "industry", "seniority", "job_type", "channel"):
            out[f"segment_{seg}"] = self.query("segment_performance", segment=seg)
        out["parser_quality"] = self.query("parser_quality")
        return out
