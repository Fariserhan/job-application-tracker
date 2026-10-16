-- Anything that reached assessment or beyond, or was rejected, is an employer response.
select application_id
from {{ ref('fct_applications') }}
where (reached_assessment or is_rejected) and not is_response
