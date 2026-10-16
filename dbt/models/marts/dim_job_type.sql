-- job type dimension: one row per distinct value (case-insensitive), keyed by a stable
-- hash so keys survive rebuilds and match the fact table's foreign key.
select
    substr(md5(lower(job_type)), 1, 16) as job_type_key,
    min(job_type)                       as job_type,
    count(*)                          as applications
from {{ ref('stg_applications') }}
group by 1
