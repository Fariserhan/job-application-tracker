-- Grain: one row per application. The table every metric in the app, the Excel workbook,
-- the DAX measures and the Tableau fields is computed from.
with apps as (
    select * from {{ ref('stg_applications') }}
),

milestones as (
    select * from {{ ref('int_application_milestones') }}
),

status as (
    select * from {{ ref('dim_status') }}
),

platform as (
    select * from {{ ref('dim_platform') }}
)

select
    a.application_id,
    -- keys
    substr(md5(lower(a.company_name)), 1, 16)                         as company_key,
    p.platform_key,
    a.current_status                                                  as status_key,
    substr(md5(lower(a.role_family)), 1, 16)                          as role_family_key,
    substr(md5(lower(a.seniority)), 1, 16)                            as seniority_key,
    substr(md5(lower(a.industry)), 1, 16)                             as industry_key,
    substr(md5(lower(a.job_type)), 1, 16)                             as job_type_key,
    -- role-playing dates (application date is the active relationship in BI tools)
    a.applied_date,
    a.last_activity_date,
    m.first_response_on                                               as first_response_date,
    -- descriptive attributes (denormalised for convenience; dims remain the source of truth)
    a.company_name,
    coalesce(a.role_title, '(role not stated)')                       as role_title,
    a.platform,
    p.channel,
    p.is_direct,
    a.role_family,
    a.seniority,
    a.industry,
    a.job_type,
    a.location,
    a.current_status,
    s.funnel_stage,
    s.is_terminal,
    s.is_active,
    a.origin,
    a.parsed_by,
    a.parse_confidence,
    a.job_url,
    a.gmail_link,
    -- funnel depth (furthest stage ever reached)
    m.max_stage_reached,
    m.is_response,
    m.max_stage_reached >= 2                                          as reached_assessment,
    m.max_stage_reached >= 3                                          as reached_interview,
    m.max_stage_reached >= 4                                          as reached_offer,
    a.current_status = 'Rejected'                                     as is_rejected,
    a.current_status = 'Ghosted'                                      as is_ghosted,
    -- timings (NULL when unknown, never zero-filled)
    case when m.first_response_on >= a.applied_date
         then date_diff('day', a.applied_date, m.first_response_on) end as days_to_response,
    case when m.interview_on >= a.applied_date
         then date_diff('day', a.applied_date, m.interview_on) end       as days_to_interview,
    date_diff('day', a.applied_date, current_date)                    as days_since_applied,
    date_diff('day', coalesce(a.last_activity_date, a.applied_date), current_date)
                                                                      as days_since_activity,
    a.job_description is not null and length(a.job_description) >= 40 as has_job_description,
    m.event_count,
    m.has_low_trust_dates,
    1                                                                 as application_count
from apps a
join milestones m using (application_id)
left join status s on s.status_key = a.current_status
left join platform p on lower(p.platform) = lower(a.platform)
