-- One row per real application: noise rows the parser filtered out are excluded here, once,
-- so no downstream model can accidentally count them. Text is trimmed and typed; dates are
-- cast to DATE; empty strings become NULL so "unknown" is never confused with a value.
with source as (
    select * from {{ source('raw', 'applications') }}
),

cleaned as (
    select
        thread_id                                                    as application_id,
        coalesce(nullif(trim(company_name), ''), 'Unknown')          as company_name,
        nullif(trim(role_title), '')                                 as role_title,
        coalesce(nullif(trim(source_platform), ''), 'Other')         as platform,
        try_cast(substr(application_date, 1, 10) as date)            as applied_date,
        case
            when trim(current_status) in ('Saved / Planning', 'Applied', 'Assessment / OA',
                                          'Interview', 'Offer', 'Rejected', 'Ghosted',
                                          'Withdrawn') then trim(current_status)
            when trim(current_status) in ('Screening/OA', 'Response') then 'Assessment / OA'
            when trim(current_status) = 'Ghosted/Inactive' then 'Ghosted'
            else 'Applied'  -- any unrecognised legacy value is treated as still open
        end                                                          as current_status,
        try_cast(substr(last_updated, 1, 10) as date)                as last_activity_date,
        nullif(trim(job_description_snippet), '')                    as job_description,
        nullif(trim(job_url), '')                                    as job_url,
        nullif(trim(gmail_link), '')                                 as gmail_link,
        nullif(trim(latest_subject), '')                             as latest_subject,
        coalesce(nullif(trim(job_type), ''), 'Full-Time')            as job_type,
        coalesce(nullif(trim(seniority_level), ''), 'Not Specified') as seniority,
        coalesce(nullif(trim(industry), ''), 'Other')                as industry,
        coalesce(nullif(trim(role_family), ''), 'Other')             as role_family,
        nullif(trim(location), '')                                   as location,
        nullif(trim(salary_text), '')                                as salary_text,
        nullif(trim(notes), '')                                      as notes,
        coalesce(nullif(trim(origin), ''), 'gmail')                  as origin,
        coalesce(nullif(trim(parsed_by), ''), 'rules')               as parsed_by,
        try_cast(parse_confidence as double)                         as parse_confidence
    from source
    where coalesce(try_cast(is_valid as integer), 1) = 1
)

select *
from cleaned
where current_status <> 'Saved / Planning'
  and applied_date is not null
