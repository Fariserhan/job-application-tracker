-- Status changes, typed and joined to the stage vocabulary. `is_trusted_date` marks events
-- whose date really is the day it happened (from an email or a person) versus dates carried
-- over from the v1 tracker or reconstructed by the migration — those are kept for the
-- audit trail but only used for timing when they are later than the application date.
with events as (
    select * from {{ source('raw', 'status_events') }}
),

stages as (
    select * from {{ ref('status_stages') }}
)

select
    e.event_id,
    e.thread_id                                         as application_id,
    nullif(trim(e.from_status), '')                     as from_status,
    trim(e.to_status)                                   as to_status,
    try_cast(substr(e.occurred_on, 1, 10) as date)      as occurred_on,
    e.source,
    nullif(trim(e.message_id), '')                      as message_id,
    nullif(trim(e.note), '')                            as note,
    s.stage_order                                       as stage_to,
    coalesce(s.is_employer_response, false)
        and e.source <> 'system'                        as is_employer_response,
    e.source in ('gmail', 'manual', 'llm', 'repair', 'system') as is_trusted_date
from events e
left join stages s on s.status = trim(e.to_status)
where try_cast(substr(e.occurred_on, 1, 10) as date) is not null
