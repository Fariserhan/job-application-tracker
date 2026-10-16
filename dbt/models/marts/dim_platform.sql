-- Platform dimension, enriched from the platform_channels seed (job board vs employer site).
with platforms as (
    select min(platform) as platform
    from {{ ref('stg_applications') }}
    group by lower(platform)
)

select
    substr(md5(lower(p.platform)), 1, 16)            as platform_key,
    p.platform,
    coalesce(c.channel, 'Other')                     as channel,
    coalesce(c.is_direct, false)                     as is_direct
from platforms p
left join {{ ref('platform_channels') }} c on lower(c.platform) = lower(p.platform)
