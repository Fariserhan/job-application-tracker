-- Honesty rule: no response means no response date. A days_to_response value on an
-- application with no employer response would be a fabricated statistic.
select application_id
from {{ ref('fct_applications') }}
where not is_response and days_to_response is not null
