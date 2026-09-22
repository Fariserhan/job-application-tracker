"""Analytical SQL over the warehouse — the queries the dashboard reads from.

Every query here is written against the star schema in `warehouse.py` and uses real SQL
technique: CTEs, window functions (SUM OVER, ROW_NUMBER, RANK, LAG), conditional
aggregation and date-table joins — no pandas aggregation standing in for SQL.

Consumers:
  * `dashboard.py` — pulls KPI/chart frames via `run_all()` / `query(name)`
  * `bi_export.py`  — writes these same results as the Power BI / Tableau data source
  * Power BI / Tableau — the DAX measures and calculated fields in `bi_export.py` are
    written against exactly these result shapes.

All queries are read-only and safe to run on an empty warehouse (they return no rows).
"""
from __future__ import annotations

from warehouse import connect

QUERIES: dict[str, str] = {

    # ------------------------------------------------------------------ KPIs
    "headline_kpis": """
    -- Single-row KPI strip. Conditional aggregation over the fact table; no joins needed
    -- beyond the status dimension, so it stays fast even as the fact grows.
    --
    -- `is_response` counts any employer reply that is not a form rejection — the metric the
    -- industry benchmarks are quoted against (2-3% average, 3-5% good, 5%+ strong).
    SELECT
        COUNT(*)                                                          AS total_applications,
        SUM(is_response)                                                  AS responses,
        SUM(is_interview_plus)                                            AS interviews_plus,
        SUM(CASE WHEN current_status = 'Offer' THEN 1 ELSE 0 END)          AS offers,
        SUM(is_rejected)                                                  AS rejections,
        SUM(is_ghosted)                                                   AS ghosted,
        SUM(is_stale)                                                     AS stale,
        SUM(CASE WHEN st.is_terminal = 0 THEN 1 ELSE 0 END)                AS active_pipeline,
        ROUND(100.0 * SUM(is_response)      / NULLIF(COUNT(*), 0), 1)      AS response_rate_pct,
        ROUND(100.0 * SUM(is_interview_plus) / NULLIF(COUNT(*), 0), 1)     AS interview_rate_pct,
        ROUND(100.0 * SUM(is_rejected)      / NULLIF(COUNT(*), 0), 1)      AS rejection_rate_pct,
        ROUND(AVG(days_to_response), 1)                                   AS avg_days_to_response,
        ROUND(AVG(days_waiting), 1)                                       AS avg_days_waiting,
        -- How the response rate reads against the published benchmark bands.
        CASE
            WHEN COUNT(*) = 0 THEN 'No data'
            WHEN 100.0 * SUM(is_response) / NULLIF(COUNT(*), 0) >= 5.0 THEN 'Strong'
            WHEN 100.0 * SUM(is_response) / NULLIF(COUNT(*), 0) >= 3.0 THEN 'Good'
            WHEN 100.0 * SUM(is_response) / NULLIF(COUNT(*), 0) >= 2.0 THEN 'Average'
            ELSE 'Below average'
        END                                                               AS response_band
    FROM fact_application f
    JOIN dim_status st ON st.status_key = f.status_key
    """,

    "kpi_last_30_vs_prev": """
    -- Period-over-period volume: the last 30 days against the 30 before it.
    WITH bounds AS (
        SELECT DATE('now')                AS today,
               DATE('now', '-30 days')    AS d30,
               DATE('now', '-60 days')    AS d60
    )
    SELECT
        SUM(CASE WHEN f.application_date >= b.d30 THEN 1 ELSE 0 END)                  AS last_30,
        SUM(CASE WHEN f.application_date >= b.d60
                  AND f.application_date <  b.d30 THEN 1 ELSE 0 END)                  AS prev_30,
        SUM(CASE WHEN f.application_date >= b.d30 THEN 1 ELSE 0 END)
          - SUM(CASE WHEN f.application_date >= b.d60
                      AND f.application_date <  b.d30 THEN 1 ELSE 0 END)              AS change,
        -- response rate in each window, so the delta is measured on quality not just volume
        ROUND(100.0 * SUM(CASE WHEN f.application_date >= b.d30 THEN f.is_response END)
              / NULLIF(SUM(CASE WHEN f.application_date >= b.d30 THEN 1 END), 0), 1)   AS last_30_response_pct,
        ROUND(100.0 * SUM(CASE WHEN f.application_date >= b.d60
                                 AND f.application_date <  b.d30 THEN f.is_response END)
              / NULLIF(SUM(CASE WHEN f.application_date >= b.d60
                                  AND f.application_date <  b.d30 THEN 1 END), 0), 1)  AS prev_30_response_pct
    FROM fact_application f
    CROSS JOIN bounds b
    """,

    # ------------------------------------------------------------------ funnel
    "funnel_conversion": """
    -- Stage-to-stage conversion over the CURRENT status of each application.
    --
    -- The tracker stores a single current status per thread, not a full event log, so a
    -- stage is counted by mapping the status back to its funnel position. Terminal
    -- outcomes (Rejected / Ghosted) left the positive pipeline, but they DID progress past
    -- "Applied" simply by responding, so they are counted at the Applied stage only and
    -- excluded from deeper stages — which is why `dropped_at_stage` is meaningful.
    WITH staged AS (
        SELECT
            f.thread_id,
            CASE
                WHEN f.current_status IN ('Rejected', 'Ghosted') THEN 1
                WHEN f.current_status = 'Offer'                  THEN 4
                WHEN f.current_status = 'Interview'              THEN 3
                WHEN f.current_status = 'Assessment / OA'        THEN 2
                ELSE 1
            END AS reached_stage
        FROM fact_application f
    ),
    counts AS (
        SELECT
            SUM(CASE WHEN reached_stage >= 1 THEN 1 ELSE 0 END) AS applied,
            SUM(CASE WHEN reached_stage >= 2 THEN 1 ELSE 0 END) AS assessment,
            SUM(CASE WHEN reached_stage >= 3 THEN 1 ELSE 0 END) AS interview,
            SUM(CASE WHEN reached_stage >= 4 THEN 1 ELSE 0 END) AS offer
        FROM staged
    ),
    unpivoted AS (
        SELECT 'Applied'            AS stage, 1 AS stage_order, applied    AS applications FROM counts
        UNION ALL SELECT 'Assessment / OA', 2, assessment FROM counts
        UNION ALL SELECT 'Interview',       3, interview  FROM counts
        UNION ALL SELECT 'Offer',           4, offer      FROM counts
    )
    SELECT
        stage,
        stage_order,
        applications,
        LAG(applications) OVER (ORDER BY stage_order)                          AS previous_stage,
        ROUND(100.0 * applications / NULLIF(LAG(applications)
              OVER (ORDER BY stage_order), 0), 1)                              AS step_conversion_pct,
        ROUND(100.0 * applications / NULLIF(MAX(applications)
              OVER (), 0), 1)                                                  AS pct_of_applied,
        LAG(applications) OVER (ORDER BY stage_order) - applications           AS dropped_at_stage
    FROM unpivoted
    ORDER BY stage_order
    """,

    # ------------------------------------------------------------------ velocity
    "monthly_velocity_running": """
    -- Applications per month with a running (cumulative) total and a month-over-month
    -- delta. Window functions over the date dimension; the running total uses the frame
    -- clause so it accumulates in calendar order.
    WITH monthly AS (
        SELECT
            d.year_month                                      AS year_month,
            MIN(d.date_key)                                   AS month_start,
            COUNT(*)                                          AS applications,
            SUM(f.is_response)                                AS responses,
            SUM(f.is_interview_plus)                          AS interviews,
            SUM(CASE WHEN f.current_status = 'Offer' THEN 1 ELSE 0 END) AS offers
        FROM fact_application f
        JOIN dim_date d ON d.date_key = f.application_date_key
        GROUP BY d.year_month
    )
    SELECT
        year_month,
        month_start,
        applications,
        responses,
        interviews,
        offers,
        ROUND(100.0 * responses  / NULLIF(applications, 0), 1)                    AS response_rate_pct,
        ROUND(100.0 * interviews / NULLIF(applications, 0), 1)                    AS interview_rate_pct,
        SUM(applications) OVER (ORDER BY year_month
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)                     AS running_total,
        applications - LAG(applications) OVER (ORDER BY year_month)               AS mom_change,
        ROUND(100.0 * (applications - LAG(applications) OVER (ORDER BY year_month))
              / NULLIF(LAG(applications) OVER (ORDER BY year_month), 0), 1)       AS mom_change_pct,
        ROUND(AVG(applications) OVER (ORDER BY year_month
            ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 1)                         AS rolling_3m_avg
    FROM monthly
    ORDER BY year_month
    """,

    # ------------------------------------------------------------------ platform
    "platform_effectiveness": """
    -- Which platform actually produces responses, with each platform ranked by both
    -- volume and effectiveness.
    --
    -- `vs_direct_benchmark_pp` compares each platform's interview rate to the published
    -- direct-careers-page benchmark (6.87%). The industry research is unambiguous that
    -- applying at the source beats job boards by a wide margin, so this column is the
    -- single most actionable number on the dashboard: a large negative value means effort
    -- should move to the employer's own careers page.
    SELECT
        p.platform_name,
        p.is_direct                                                      AS is_direct_employer,
        COUNT(*)                                                         AS applications,
        SUM(f.is_response)                                               AS responses,
        SUM(f.is_interview_plus)                                         AS interviews,
        SUM(CASE WHEN f.current_status = 'Offer' THEN 1 ELSE 0 END)       AS offers,
        SUM(f.is_ghosted)                                                AS ghosted,
        ROUND(100.0 * SUM(f.is_response)      / NULLIF(COUNT(*), 0), 1)   AS response_rate_pct,
        ROUND(100.0 * SUM(f.is_interview_plus) / NULLIF(COUNT(*), 0), 1)  AS interview_rate_pct,
        ROUND(AVG(f.days_to_response), 1)                                AS avg_days_to_response,
        -- Gap to the 6.87% direct-application interview benchmark, in percentage points.
        ROUND(100.0 * SUM(f.is_interview_plus) / NULLIF(COUNT(*), 0) - 6.87, 2)
                                                                         AS vs_direct_benchmark_pp,
        DENSE_RANK() OVER (ORDER BY COUNT(*) DESC)                       AS volume_rank,
        DENSE_RANK() OVER (ORDER BY 1.0 * SUM(f.is_response) / NULLIF(COUNT(*), 0) DESC)
                                                                         AS effectiveness_rank,
        ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)               AS share_of_applications_pct
    FROM fact_application f
    JOIN dim_platform p ON p.platform_key = f.platform_key
    GROUP BY p.platform_name, p.is_direct
    ORDER BY applications DESC
    """,

    "funnel_vs_benchmark": """
    -- The funnel measured against the published 2026 benchmark: from 100 applications the
    -- industry expectation is 2-3 responses, 5-10 first-round interviews and 0-1 offer.
    -- Each row carries the benchmark midpoint so the tracker shows where it beats or lags
    -- the market rather than an isolated number.
    WITH reached AS (
        SELECT
            SUM(CASE WHEN st.stage_order >= 1 OR st.stage_order = -1 THEN 1 ELSE 0 END)
                                                                              AS applied,
            SUM(CASE WHEN f.is_response = 1 THEN 1 ELSE 0 END)                AS responded,
            SUM(CASE WHEN f.is_interview_plus = 1 THEN 1 ELSE 0 END)          AS interviewed,
            SUM(CASE WHEN f.current_status = 'Offer' THEN 1 ELSE 0 END)       AS offered
        FROM fact_application f
        JOIN dim_status st ON st.status_key = f.status_key
    ),
    staged AS (
        SELECT 'Applied'          AS stage, 1 AS stage_order, applied    AS applications,
               NULL                 AS benchmark_pct FROM reached
        UNION ALL SELECT 'Employer responded', 2, responded,   2.5  FROM reached
        UNION ALL SELECT 'Interview',          3, interviewed, 7.5  FROM reached
        UNION ALL SELECT 'Offer',              4, offered,     1.0  FROM reached
    )
    SELECT
        stage,
        stage_order,
        applications,
        benchmark_pct,
        -- your rate as a % of the applications made, and the plain difference to benchmark
        ROUND(100.0 * applications / NULLIF(MAX(applications) OVER (), 0), 2) AS your_rate_pct,
        ROUND(100.0 * applications / NULLIF(MAX(applications) OVER (), 0) - benchmark_pct, 2)
                                                                              AS vs_benchmark_pp,
        CASE
            WHEN benchmark_pct IS NULL THEN NULL
            WHEN 100.0 * applications / NULLIF(MAX(applications) OVER (), 0) >= benchmark_pct
                THEN 'Ahead'
            ELSE 'Behind'
        END                                                                    AS verdict
    FROM staged
    ORDER BY stage_order
    """,

    # ------------------------------------------------------------------ response time
    "response_time_analysis": """
    -- Days-to-response distribution by platform and job type, with percentile-style
    -- medians derived in SQL. Only rows that actually got a response are counted, so the
    -- median is not diluted by silent applications.
    SELECT
        p.platform_name,
        jt.job_type_name,
        COUNT(*)                                                   AS responses,
        MIN(f.days_to_response)                                    AS fastest_days,
        ROUND(AVG(f.days_to_response), 1)                          AS avg_days,
        MAX(f.days_to_response)                                    AS slowest_days,
        SUM(CASE WHEN f.days_to_response <= 7  THEN 1 ELSE 0 END)   AS within_7d,
        SUM(CASE WHEN f.days_to_response <= 14 THEN 1 ELSE 0 END)   AS within_14d,
        SUM(CASE WHEN f.days_to_response <= 30 THEN 1 ELSE 0 END)   AS within_30d,
        ROUND(100.0 * SUM(CASE WHEN f.days_to_response <= 14 THEN 1 ELSE 0 END)
              / NULLIF(COUNT(*), 0), 1)                             AS responded_within_14d_pct
    FROM fact_application f
    JOIN dim_platform p  ON p.platform_key  = f.platform_key
    JOIN dim_job_type jt ON jt.job_type_key = f.job_type_key
    WHERE f.days_to_response IS NOT NULL
    GROUP BY p.platform_name, jt.job_type_name
    ORDER BY responses DESC, avg_days
    """,

    "silence_cohort": """
    -- Everything still waiting, bucketed by how long it has been silent. This is the
    -- follow-up queue's data source; the bucket boundaries live in SQL so the app cannot
    -- drift from the report.
    SELECT
        CASE
            WHEN f.days_waiting <= 7  THEN '1. 0-7 days'
            WHEN f.days_waiting <= 14 THEN '2. 8-14 days'
            WHEN f.days_waiting <= 21 THEN '3. 15-21 days'
            WHEN f.days_waiting <= 30 THEN '4. 22-30 days'
            ELSE '5. 30+ days'
        END                                                        AS waiting_bucket,
        COUNT(*)                                                   AS applications,
        SUM(f.is_stale)                                            AS flagged_stale,
        ROUND(AVG(f.days_waiting), 1)                              AS avg_days_waiting,
        GROUP_CONCAT(f.company_name, ' | ')                        AS companies
    FROM fact_application f
    JOIN dim_status st ON st.status_key = f.status_key
    WHERE st.is_terminal = 0 AND f.current_status IN ('Applied', 'Assessment / OA')
    GROUP BY waiting_bucket
    ORDER BY waiting_bucket
    """,

    # ------------------------------------------------------------------ skills
    "skill_gap_frequency": """
    -- Skill demand vs ownership. For every skill the pipeline has asked about, how many
    -- applications demanded it, how often it was already matched, and the resulting net
    -- gap. The ownership ratio drives the priority score.
    WITH per_skill AS (
        SELECT
            s.skill_name,
            s.skill_family,
            COUNT(DISTINCT CASE WHEN a.requirement_type IN ('matched', 'missing')
                                THEN a.thread_id END)                       AS applications_demanding,
            COUNT(DISTINCT CASE WHEN a.requirement_type = 'matched'
                                THEN a.thread_id END)                       AS applications_matched,
            COUNT(DISTINCT CASE WHEN a.requirement_type = 'missing'
                                THEN a.thread_id END)                       AS applications_missing,
            COUNT(DISTINCT CASE WHEN a.requirement_type = 'improvement'
                                THEN a.thread_id END)                       AS flagged_for_action
        FROM fact_application_skill a
        JOIN dim_skill s ON s.skill_key = a.skill_key
        GROUP BY s.skill_name, s.skill_family
    ),
    total AS (SELECT COUNT(*) AS applications FROM fact_application)
    SELECT
        skill_name,
        skill_family,
        applications_demanding,
        applications_matched,
        applications_missing,
        flagged_for_action,
        ROUND(100.0 * applications_demanding / NULLIF((SELECT applications FROM total), 0), 1)
                                                                        AS pct_of_pipeline,
        ROUND(100.0 * applications_missing
              / NULLIF(applications_demanding, 0), 1)                   AS missing_rate_pct,
        -- Priority: demand weighted up by how often the skill is still missing.
        ROUND(applications_demanding + 2.0 * applications_missing, 1)    AS priority_score,
        RANK() OVER (ORDER BY applications_demanding + 2.0 * applications_missing DESC)
                                                                        AS priority_rank,
        CASE WHEN applications_missing > 0 THEN 'Gap' ELSE 'Covered' END AS status
    FROM per_skill
    WHERE applications_demanding > 0
    ORDER BY priority_score DESC
    """,

    "skill_gap_by_role_family": """
    -- The same gap data sliced by the role family it came from, so it is clear WHICH kind
    -- of job is asking for the skill (an actuarial JD and a data-analyst JD want
    -- different things). Conditional aggregation over the bridge table.
    SELECT
        rf.role_family,
        s.skill_name,
        s.skill_family,
        COUNT(DISTINCT a.thread_id)                                        AS applications,
        SUM(CASE WHEN a.requirement_type = 'missing' THEN 1 ELSE 0 END)     AS missing_count,
        ROUND(100.0 * SUM(CASE WHEN a.requirement_type = 'missing' THEN 1 ELSE 0 END)
              / NULLIF(COUNT(*), 0), 1)                                    AS missing_share_pct
    FROM fact_application_skill a
    JOIN dim_skill s        ON s.skill_key       = a.skill_key
    JOIN fact_application f ON f.thread_id       = a.thread_id
    JOIN dim_role_family rf ON rf.role_family_key = f.role_family_key
    WHERE a.requirement_type IN ('matched', 'missing')
    GROUP BY rf.role_family, s.skill_name, s.skill_family
    HAVING COUNT(DISTINCT a.thread_id) >= 1
    ORDER BY rf.role_family, missing_count DESC, applications DESC
    """,

    # ------------------------------------------------------------------ timing
    "application_timing": """
    -- When you apply, and whether it matters. The published research is specific that
    -- applications made in the first 24-48 hours of a posting perform 2-3x better, and
    -- that weekday timing shows a real (if smaller) effect.
    SELECT
        d.day_of_week,
        d.day_of_week_n,
        COUNT(*)                                                          AS applications,
        SUM(f.is_response)                                                AS responses,
        SUM(f.is_interview_plus)                                          AS interviews,
        ROUND(100.0 * SUM(f.is_response) / NULLIF(COUNT(*), 0), 1)         AS response_rate_pct,
        ROUND(100.0 * SUM(f.is_interview_plus) / NULLIF(COUNT(*), 0), 1)   AS interview_rate_pct,
        -- rank the weekdays by response rate so the best day is obvious
        DENSE_RANK() OVER (ORDER BY 1.0 * SUM(f.is_response) / NULLIF(COUNT(*), 0) DESC)
                                                                          AS effectiveness_rank
    FROM fact_application f
    JOIN dim_date d ON d.date_key = f.application_date_key
    GROUP BY d.day_of_week, d.day_of_week_n
    ORDER BY d.day_of_week_n
    """,

    "response_time_distribution": """
    -- How long employers take to answer, bucketed. Only rows with a KNOWN response date are
    -- included — an unknown date is never treated as a same-day reply.
    SELECT
        CASE
            WHEN f.days_to_response IS NULL       THEN '0. Unknown'
            WHEN f.days_to_response <= 7          THEN '1. Within a week'
            WHEN f.days_to_response <= 14         THEN '2. 1-2 weeks'
            WHEN f.days_to_response <= 30         THEN '3. 2-4 weeks'
            WHEN f.days_to_response <= 60         THEN '4. 1-2 months'
            ELSE '5. Over 2 months'
        END                                                               AS response_bucket,
        COUNT(*)                                                          AS responses,
        ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)                AS share_pct,
        ROUND(AVG(f.days_to_response), 1)                                 AS avg_days
    FROM fact_application f
    WHERE f.is_response = 1
    GROUP BY response_bucket
    ORDER BY response_bucket
    """,

    "outcome_mix": """
    -- The end state of every application, ordered by how far it got. This is the one-line
    -- answer to "where did my 100+ applications actually end up?".
    SELECT
        f.current_status                                                  AS outcome,
        COUNT(*)                                                          AS applications,
        ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)                AS share_pct,
        ROUND(AVG(f.days_waiting), 1)                                     AS avg_days_elapsed,
        SUM(CASE WHEN st.is_terminal = 0 THEN 1 ELSE 0 END)               AS still_open
    FROM fact_application f
    JOIN dim_status st ON st.status_key = f.status_key
    GROUP BY f.current_status
    ORDER BY COUNT(*) DESC
    """,

    # ------------------------------------------------------------------ company / role
    "company_pipeline": """
    -- Employer-level view with the concentration risk made explicit: how many of the
    -- applications went to this one employer and what came back.
    SELECT
        c.company_name,
        COUNT(*)                                                          AS applications,
        COUNT(DISTINCT f.role_title)                                      AS distinct_roles,
        SUM(f.is_response)                                                AS responses,
        SUM(f.is_interview_plus)                                          AS interviews,
        SUM(CASE WHEN f.current_status = 'Offer' THEN 1 ELSE 0 END)        AS offers,
        SUM(f.is_rejected)                                                AS rejections,
        SUM(f.is_ghosted)                                                 AS ghosted,
        SUM(CASE WHEN st.is_terminal = 0 THEN 1 ELSE 0 END)               AS still_open,
        ROUND(100.0 * SUM(f.is_response) / NULLIF(COUNT(*), 0), 1)         AS response_rate_pct,
        MAX(f.application_date)                                           AS last_applied,
        ROUND((JULIANDAY('now') - JULIANDAY(MAX(f.application_date))), 0)  AS days_since_last,
        CASE WHEN COUNT(*) > 1 THEN 1 ELSE 0 END                          AS repeat_employer
    FROM fact_application f
    JOIN dim_company c ON c.company_key = f.company_key
    JOIN dim_status  st ON st.status_key = f.status_key
    GROUP BY c.company_name
    ORDER BY applications DESC, responses DESC
    """,

    "role_family_performance": """
    -- Which role families convert best, on real outcomes rather than on an assumed notion of
    -- "quality of application".
    SELECT
        rf.role_family,
        COUNT(*)                                                          AS applications,
        SUM(f.is_response)                                                AS responses,
        SUM(f.is_interview_plus)                                          AS interviews,
        ROUND(100.0 * SUM(f.is_response) / NULLIF(COUNT(*), 0), 1)         AS response_rate_pct,
        ROUND(100.0 * SUM(f.is_interview_plus) / NULLIF(COUNT(*), 0), 1)   AS interview_rate_pct,
        ROUND(AVG(f.days_to_response), 1)                                 AS avg_days_to_response
    FROM fact_application f
    JOIN dim_role_family rf ON rf.role_family_key = f.role_family_key
    GROUP BY rf.role_family
    ORDER BY applications DESC
    """,

    # ------------------------------------------------------------------ status events
    "status_transition_matrix": """
    -- The transition matrix: which status moves to which. A self-join-free pivot built
    -- from the event fact, which is what makes the funnel auditable rather than inferred
    -- from the current status alone.
    SELECT
        COALESCE(e.from_status, '(new)')                                   AS from_status,
        e.to_status,
        COUNT(*)                                                           AS transitions,
        ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY e.from_status), 1)
                                                                           AS pct_of_from_status,
        ROUND(AVG(JULIANDAY(e.changed_date_key) - JULIANDAY(f.application_date)), 1)
                                                                           AS avg_days_from_application
    FROM fact_status_event e
    JOIN fact_application f ON f.thread_id = e.thread_id
    WHERE e.from_status IS NOT NULL AND e.from_status <> e.to_status
    GROUP BY e.from_status, e.to_status
    ORDER BY transitions DESC
    """,

    "recent_activity": """
    -- Latest status movements with the elapsed time since the previous event on the same
    -- application — LAG over the partitioned event stream.
    SELECT
        e.changed_at,
        e.company_name,
        f.role_title,
        e.from_status,
        e.to_status,
        e.event_type,
        e.note,
        JULIANDAY(e.changed_at)
          - JULIANDAY(LAG(e.changed_at) OVER (PARTITION BY e.thread_id
                                              ORDER BY e.changed_at))      AS days_since_previous_event
    FROM fact_status_event e
    JOIN fact_application f ON f.thread_id = e.thread_id
    ORDER BY e.changed_at DESC
    LIMIT 60
    """,

    "weekly_activity_heatmap": """
    -- Day-of-week x week activity grid (application volume), the classic heatmap source.
    SELECT
        d.week_start,
        d.day_of_week,
        d.day_of_week_n,
        COUNT(f.thread_id)                                                 AS applications,
        SUM(f.is_response)                                                 AS responses
    FROM dim_date d
    LEFT JOIN fact_application f ON f.application_date_key = d.date_key
    GROUP BY d.week_start, d.day_of_week, d.day_of_week_n
    ORDER BY d.week_start, d.day_of_week_n
    """,

    "seniority_and_industry_mix": """
    -- Composition of the pipeline by seniority tier and industry, with the response rate
    -- per cell so the mix can be judged on outcomes not just counts.
    SELECT
        se.seniority_name,
        se.seniority_rank,
        i.industry_name,
        COUNT(*)                                                          AS applications,
        SUM(f.is_response)                                                AS responses,
        ROUND(100.0 * SUM(f.is_response) / NULLIF(COUNT(*), 0), 1)         AS response_rate_pct
    FROM fact_application f
    JOIN dim_seniority se ON se.seniority_key = f.seniority_key
    JOIN dim_industry  i  ON i.industry_key   = f.industry_key
    GROUP BY se.seniority_name, se.seniority_rank, i.industry_name
    ORDER BY se.seniority_rank, applications DESC
    """,

    "data_quality_audit": """
    -- Warehouse audit: which rows are missing the attributes the model depends on. This
    -- is the query that keeps the report honest about its own coverage.
    SELECT 'applications'                  AS entity,
           COUNT(*)                        AS rows,
           SUM(CASE WHEN application_date_key IS NULL THEN 1 ELSE 0 END) AS missing_date,
           SUM(CASE WHEN has_jd = 0 THEN 1 ELSE 0 END)                   AS missing_jd,
           SUM(CASE WHEN company_name IN ('Unknown', '') THEN 1 ELSE 0 END) AS missing_company,
           SUM(CASE WHEN role_title = '' THEN 1 ELSE 0 END)              AS missing_role
    FROM fact_application
    UNION ALL
    SELECT 'status_events', COUNT(*), NULL, NULL, NULL, NULL FROM fact_status_event
    UNION ALL
    SELECT 'skill_links', COUNT(*), NULL, NULL, NULL, NULL FROM fact_application_skill
    """,
}


def query(name: str, db_path: str | None = None, **params):
    """Run one named query and return a pandas DataFrame (empty on a cold warehouse)."""
    import pandas as pd

    sql = QUERIES.get(name)
    if sql is None:
        raise KeyError(f"unknown query '{name}'; known: {', '.join(sorted(QUERIES))}")
    with connect(db_path) as conn:
        try:
            return pd.read_sql_query(sql, conn, params=params or None)
        except Exception:
            # A brand-new database has no warehouse tables yet — report empty, never crash.
            return pd.DataFrame()


def run_all(db_path: str | None = None) -> dict:
    """Run every query once. Returns {name: DataFrame}."""
    return {name: query(name, db_path) for name in QUERIES}


def sql_for(name: str) -> str:
    """Expose the SQL text (the dashboard shows it so the queries are inspectable)."""
    return QUERIES.get(name, "")
