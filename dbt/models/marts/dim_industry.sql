-- industry dimension: one row per distinct value (case-insensitive), keyed by a stable
-- hash so keys survive rebuilds and match the fact table's foreign key.
select
    substr(md5(lower(industry)), 1, 16) as industry_key,
    min(industry)                       as industry,
    count(*)                          as applications
from {{ ref('stg_applications') }}
group by 1
