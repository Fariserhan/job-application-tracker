-- Every email the sync has inspected, with the parser's verdict. Feeds the parser-quality
-- and noise-filter reporting, never the application counts.
select
    message_id,
    thread_id                                          as application_id,
    try_cast(nullif(message_date, '') as date)         as message_date,
    nullif(subject, '')                                as subject,
    nullif(sender, '')                                 as sender,
    coalesce(try_cast(is_valid as integer), 0) = 1     as is_application_mail,
    nullif(filter_reason, '')                          as filter_reason,
    nullif(detected_status, '')                        as detected_status,
    try_cast(confidence as double)                     as confidence,
    coalesce(nullif(parsed_by, ''), 'rules')           as parsed_by
from {{ source('raw', 'processed_messages') }}
