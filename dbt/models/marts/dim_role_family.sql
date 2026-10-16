-- role family dimension: one row per distinct value (case-insensitive), keyed by a stable
-- hash so keys survive rebuilds and match the fact table's foreign key.
select
    substr(md5(lower(role_family)), 1, 16) as role_family_key,
    min(role_family)                       as role_family,
    count(*)                          as applications
from {{ ref('stg_applications') }}
group by 1
