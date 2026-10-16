-- Marked date table (Power BI / Tableau): one row per day from the first application to
-- today, so time intelligence never has gaps.
with bounds as (
    select
        least(coalesce(min(applied_date), current_date), current_date - 30) as start_date,
        current_date                                                       as end_date
    from {{ ref('stg_applications') }}
),

days as (
    select cast(range as date) as date_day
    from bounds, range(bounds.start_date, bounds.end_date + interval 1 day, interval 1 day)
)

select
    date_day,
    cast(strftime(date_day, '%Y%m%d') as integer)       as date_key,
    year(date_day)                                     as year,
    quarter(date_day)                                  as quarter,
    month(date_day)                                    as month,
    strftime(date_day, '%B')                           as month_name,
    strftime(date_day, '%Y-%m')                        as year_month,
    cast(date_trunc('week', date_day) as date)         as week_start,
    isodow(date_day)                                   as day_of_week_num,
    strftime(date_day, '%A')                           as day_of_week,
    isodow(date_day) >= 6                              as is_weekend,
    date_diff('day', date_day, current_date)           as days_ago
from days
