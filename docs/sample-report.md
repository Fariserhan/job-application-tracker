# Job search analysis — synthetic demo data

_Generated 16 October 2026 from the analytics warehouse (2026-10-16T16:41:47). Every figure comes from the SQL metric layer; rates carry 95% Wilson intervals._

> **Demo dataset.** These numbers describe the synthetic mailbox that ships with the project, generated to exercise the real pipeline. They are not a real person's job search. Run `python -m jobtracker report` on your own data for a real analysis.

## Headline

- **168 applications** to **28 employers** between 23 Apr 2026 and 15 Oct 2026.
- **25.0% got an employer response** (95% CI 19.1%–32.1%) — **Strong** against the published bands (<2% below average · 2–3% average · 3–5% good · 5%+ strong).
- **13.1% reached an interview** (95% CI 8.8%–19.0%); well-targeted applications are quoted at 5–10%.
- **3 offer(s)**, 27 rejections, 102 ghosted (60.7%), 36 still open.
- Median time to a first reply: **8.5 days** (known reply dates: 42 of 42).
- Last 30 days: 33 applications vs 32 in the 30 before; response rate 12.1% vs 37.5% (recent applications have had less time to get a reply).

## Where applications stop

Stages count the **furthest stage ever reached**, so an application rejected after its interview still counts as an interview.

| Stage | Applications | % of applied | Step conversion % | vs benchmark |
|---|---|---|---|---|
| Applied | 168 | 100.0 | – | – |
| Employer responded | 42 | 25.0 | 25.0 | – |
| Assessment | 31 | 18.5 | 73.8 | – |
| Interview | 22 | 13.1 | 71.0 | Ahead |
| Offer | 3 | 1.8 | 13.6 | Ahead |

The largest drop is into **Employer responded** (126 applications lost at that step).

## Which channels work

| Platform | Apps | Response % | CI low | CI high | Interview % | Sample |
|---|---|---|---|---|---|---|
| LinkedIn | 60 | 10.0 | 4.7 | 20.1 | 6.7 | Adequate |
| JobStreet | 42 | 28.6 | 17.2 | 43.6 | 11.9 | Adequate |
| Direct ATS | 23 | 39.1 | 22.2 | 59.2 | 17.4 | Indicative |
| Hiredly | 14 | 21.4 | 7.6 | 47.6 | 7.1 | Indicative |
| Prosple | 13 | 53.8 | 29.1 | 76.8 | 38.5 | Indicative |
| Indeed | 13 | 23.1 | 8.2 | 50.3 | 15.4 | Indicative |
| Company Site | 2 | 50.0 | 9.5 | 90.5 | 0.0 | Too small |
| Referral | 1 | 100.0 | 20.7 | 100.0 | 100.0 | Too small |

**Prosple** has the highest response rate (53.8%) and **LinkedIn** the lowest (10.0%). Their 95% intervals do not overlap, so the gap is unlikely to be luck.

Applying on employers' own careers sites: 16.0% interview rate vs the 6.87% published benchmark for direct applications (1.95% via job boards).

## How long employers take

Time to first reply is estimated with a **Kaplan–Meier** survival curve: applications still waiting count as *censored* observations rather than being dropped, which would make employers look faster than they are.

- Fewer than half of applications ever got a reply within 176 days.
- First reply: median 8.5 days (IQR 6–13.5 days, n=42)
- Interview: median 13 days (IQR 9–20 days, n=22)

## Monthly cohorts

Recent cohorts are still maturing — their rates are floors, not final.

| Month | Apps | Response % | Interview % | Open |
|---|---|---|---|---|
| 2026-04 | 3 | 33.3 | 33.3 | 1 |
| 2026-05 | 27 | 29.6 | 14.8 | 2 |
| 2026-06 | 25 | 16.0 | 12.0 | 0 |
| 2026-07 | 33 | 18.2 | 3.0 | 1 |
| 2026-08 | 29 | 44.8 | 31.0 | 3 |
| 2026-09 | 29 | 24.1 | 10.3 | 7 |
| 2026-10 | 22 | 13.6 | 4.5 | 22 |

## Role families

| Role family | Apps | Response % | Interview % |
|---|---|---|---|
| Data & Analytics | 97 | 22.7 | 13.4 |
| Actuarial | 40 | 27.5 | 12.5 |
| Risk & Quantitative | 20 | 30.0 | 10.0 |
| Business Analysis | 11 | 27.3 | 18.2 |

## Data quality & caveats

| Check | Passing | Total | Coverage % |
|---|---|---|---|
| Employer identified | 168 | 168 | 100.0 |
| Role title identified | 168 | 168 | 100.0 |
| Job description captured | 165 | 168 | 98.2 |
| Posting link captured | 139 | 168 | 82.7 |
| Reply date known (of replies) | 42 | 42 | 100.0 |
| High-confidence parse | 168 | 168 | 100.0 |
| Event dates from source emails | 168 | 168 | 100.0 |

- One person's search is a small sample: rates are shown with intervals and rankings with sample-size labels for that reason.
- Unknown reply dates are excluded from timing metrics, never treated as zero.
- Benchmarks are aggregate figures from public job-search research (mixed seniority, mostly cold applications) and are context, not targets.
