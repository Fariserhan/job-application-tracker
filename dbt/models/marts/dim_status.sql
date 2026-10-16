-- Status dimension straight from the status_stages seed: funnel stage, order, flags.
select
    status                    as status_key,
    status,
    funnel_stage,
    stage_order,
    is_terminal,
    is_active,
    is_employer_response,
    sort_order
from {{ ref('status_stages') }}
