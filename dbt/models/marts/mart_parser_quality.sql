-- Noise filter & parser audit: how many inspected emails were judged real application mail,
-- by which tier, with what confidence — and how many needed the optional Claude fallback.
select
    coalesce(filter_reason, '(none)')                               as filter_reason,
    is_application_mail,
    parsed_by,
    count(*)                                                        as messages,
    round(avg(confidence), 3)                                       as avg_confidence,
    count(*) filter (where confidence < 0.75)                       as low_confidence_messages
from {{ ref('stg_messages') }}
group by all
