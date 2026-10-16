{{ config(materialized='view') }}
-- Flat, denormalised reporting view: the fastest way to get the whole model into Tableau,
-- Power BI or Excel in a single import.
select
    f.*,
    d.year            as applied_year,
    d.quarter         as applied_quarter,
    d.year_month      as applied_month,
    d.week_start      as applied_week,
    d.day_of_week     as applied_weekday,
    d.day_of_week_num as applied_weekday_num,
    d.is_weekend      as applied_on_weekend
from {{ ref('fct_applications') }} f
left join {{ ref('dim_date') }} d on d.date_day = f.applied_date
