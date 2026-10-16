-- Per application: how far it got and when each milestone happened.
--
-- The funnel is built on the FURTHEST stage ever reached, not on the current status: an
-- application rejected after its interview still counts as an interview. The furthest
-- stage is the max over the event log and the current status (rows whose history predates
-- the event log still contribute their current stage).
--
-- First response = the first employer-side event (assessment, interview, offer or
-- rejection). Its date is only used when it is trustworthy: dated by an email / a person,
-- or (for migrated v1 history) strictly after the application date. Otherwise it stays
-- NULL and is excluded from timing metrics; it is never zero-filled.
with apps as (
    select * from {{ ref('stg_applications') }}
),

events as (
    select * from {{ ref('stg_status_events') }}
),

stages as (
    select * from {{ ref('status_stages') }}
),

event_rollup as (
    select
        e.application_id,
        max(e.stage_to)                                                         as max_event_stage,
        min(e.occurred_on) filter (where e.is_employer_response
            and (e.is_trusted_date or e.occurred_on > a.applied_date))          as first_response_on,
        min(e.occurred_on) filter (where e.to_status = 'Assessment / OA')       as assessment_on,
        min(e.occurred_on) filter (where e.to_status = 'Interview')             as interview_on,
        min(e.occurred_on) filter (where e.to_status = 'Offer')                 as offer_on,
        max(e.occurred_on) filter (where e.to_status in ('Rejected', 'Ghosted', 'Withdrawn'))
                                                                                as closed_on,
        bool_or(e.is_employer_response)                                         as any_employer_response,
        count(*)                                                                as event_count,
        bool_or(not e.is_trusted_date)                                          as has_low_trust_dates
    from events e
    join apps a using (application_id)
    group by e.application_id
)

select
    a.application_id,
    greatest(coalesce(r.max_event_stage, 0), coalesce(s.stage_order, 1), 1)    as max_stage_reached,
    -- an employer response is any assessment / interview / offer / rejection, in the log
    -- or in the current status
    coalesce(r.any_employer_response, false) or coalesce(s.is_employer_response, false)
                                                                               as is_response,
    r.first_response_on,
    r.assessment_on,
    r.interview_on,
    r.offer_on,
    r.closed_on,
    coalesce(r.event_count, 0)                                                 as event_count,
    coalesce(r.has_low_trust_dates, false)                                     as has_low_trust_dates
from apps a
left join event_rollup r using (application_id)
left join stages s on s.status = a.current_status
