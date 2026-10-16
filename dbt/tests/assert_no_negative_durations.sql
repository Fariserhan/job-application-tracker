-- Durations are days elapsed; a negative value means a date bug upstream.
select application_id, days_to_response, days_to_interview, days_since_applied
from {{ ref('fct_applications') }}
where days_to_response < 0 or days_to_interview < 0 or days_since_applied < 0
