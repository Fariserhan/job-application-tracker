-- seniority dimension: one row per distinct value (case-insensitive), keyed by a stable
-- hash so keys survive rebuilds and match the fact table's foreign key.
select
    substr(md5(lower(seniority)), 1, 16) as seniority_key,
    min(seniority)                       as seniority,
    count(*)                          as applications
from {{ ref('stg_applications') }}
group by 1
