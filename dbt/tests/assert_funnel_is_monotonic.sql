-- A funnel can only narrow: every stage must hold no more applications than the one before.
with counts as (
    select
        count(*)                                  as applied,
        count(*) filter (where reached_assessment) as assessment,
        count(*) filter (where reached_interview)  as interview,
        count(*) filter (where reached_offer)      as offer
    from {{ ref('fct_applications') }}
)
select * from counts
where assessment > applied or interview > assessment or offer > interview
