-- company dimension: one row per distinct value (case-insensitive), keyed by a stable
-- hash so keys survive rebuilds and match the fact table's foreign key.
select
    substr(md5(lower(company_name)), 1, 16) as company_key,
    min(company_name)                       as company_name,
    count(*)                          as applications
from {{ ref('stg_applications') }}
group by 1
