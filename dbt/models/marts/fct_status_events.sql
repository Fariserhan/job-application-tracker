-- Grain: one row per status change. Feeds the transition matrix, the activity feed and
-- the Sankey flow.
select
    e.event_id,
    e.application_id,
    e.from_status,
    e.to_status,
    e.occurred_on,
    e.source,
    e.note,
    e.is_employer_response,
    e.is_trusted_date,
    f.company_name,
    f.role_title,
    f.platform,
    f.applied_date,
    date_diff('day', f.applied_date, e.occurred_on)  as days_since_applied
from {{ ref('stg_status_events') }} e
join {{ ref('fct_applications') }} f using (application_id)
