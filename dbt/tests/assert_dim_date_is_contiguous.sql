-- The date table must have no gaps (time intelligence in Power BI depends on it).
select count(*) as missing_days
from {{ ref('dim_date') }}
having count(*) <> date_diff('day', min(date_day), max(date_day)) + 1
