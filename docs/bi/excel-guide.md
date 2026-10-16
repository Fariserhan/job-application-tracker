# Excel guide - `job_search_analytics.xlsx`

`python -m jobtracker export` writes the workbook to `<data dir>/exports/`
(`jobtracker.bi.build_workbook(settings)` returns the same file as bytes). It holds two kinds of numbers, and each sheet's subtitle says
which kind it shows:

- **Live formulas**: the Dashboard reads the `tblApplications` Excel Table with
  `COUNTIFS`, `SUMPRODUCT`, `MEDIAN`, `AVERAGEIFS` and `INDEX/MATCH`, plus the thresholds on
  the Benchmarks sheet. Paste new rows into the table and every tile recalculates.
- **SQL snapshots**: the report sheets hold the output of the SQL metric layer
  (`jobtracker/warehouse/metrics.py`) as values. Window functions, Kaplan-Meier survival and
  Wilson intervals are computed once, in DuckDB, and never re-derived in the spreadsheet.

Only Excel 2007-era functions are used: no `LET`, `FILTER`, `XLOOKUP` or dynamic arrays. The
file therefore calculates the same in Excel 2010+, Microsoft 365 and LibreOffice, and the test
suite relies on that (see *How it is verified*).

## Sheets

| Sheet | What it shows | Numbers |
|---|---|---|
| **Cover** | Title, as-of date, hyperlinked contents, the notes to read before quoting a number, how to refresh | - |
| **Dashboard** | 8 KPI tiles, funnel per 100 vs benchmark, response rate by platform, weekly velocity with 4-week rolling average, and the KPI calculation table that feeds the tiles | Live |
| **Applications** | `tblApplications`: one row per application, 45 columns with frozen header and first three columns, autofilter, status colours, a colour scale on `days_since_activity` (white -> caution at the follow-up threshold -> red at the ghost window), data bars on reply time and age, and links to the posting and the email | Data |
| **Funnel** | Stages on the furthest stage reached, `LAG` step conversion, lost at each step, published per-100 benchmark range, Ahead / On par / Behind | SQL |
| **Platforms** | Applications, responses, interviews, offers, response and interview rate with **Wilson 95% CI**, gap to the 6.87% direct benchmark, share, dense rank, **sample confidence**; column chart with CI error bars | SQL (+ 2 helper formulas for the error bars) |
| **Weekly** | Applications / responses / interviews per ISO week, 4-week rolling average, running total, with a cumulative chart | SQL |
| **Cohorts** | Month-of-application cohorts with outcomes and rates; young cohorts in grey italics (`is_maturing`) | SQL |
| **Reply time** | Kaplan-Meier time-to-first-reply (still-waiting applications are censored, not dropped) with chart, reply-time buckets, median / quartile days to each milestone | SQL |
| **Follow-ups** | Open applications quiet for the follow-up threshold or longer, oldest first, with a suggested action and links | SQL |
| **Segments** | Role family, industry, seniority, job type with Wilson intervals | SQL |
| **Data quality** | Coverage of every attribute the analysis depends on (coverage = live `passing / total` formula with data bars) and the current status mix | SQL + formulas |
| **Data dictionary** | *One metric, four tools*: each metric's definition with its SQL, Excel, DAX and Tableau expression side by side. Then every `tblApplications` column with its type and meaning | Reference |
| **Benchmarks** | The `Benchmarks` table (published figures, sample-size cut-offs, Wilson z, follow-up and ghost windows) and the `ResponseBands` table | Inputs |

## Formulas on the Dashboard

The tiles only reference **defined names**. Each name points at a cell in the KPI calculation
table (Dashboard, columns K-N), and that table holds the real formulas. Column M names the SQL
column each formula reproduces. Highlights:

```excel
Applications          =COUNTA(tblApplications[application_id])
Employer responses    =COUNTIFS(tblApplications[is_response],TRUE)
Response rate         =IF(kpi_applications>0,kpi_responses/kpi_applications,"")
95% CI low (Wilson)   =IF(kpi_applications>0,MAX(0,MIN(1,(kpi_response_rate+WilsonZ^2/(2*kpi_applications)
                        -WilsonZ*SQRT(kpi_response_rate*(1-kpi_response_rate)/kpi_applications
                        +WilsonZ^2/(4*kpi_applications^2)))/(1+WilsonZ^2/kpi_applications))),"")
Needs follow-up       =COUNTIFS(tblApplications[is_active],TRUE,tblApplications[days_since_activity],">="&FollowUpDays)
Companies (distinct)  =IFERROR(SUMPRODUCT((tblApplications[company_name]<>"")
                        /COUNTIF(tblApplications[company_name],tblApplications[company_name]&"")),0)
Median days to reply  =IF(COUNT(tblApplications[days_to_response])>0,MEDIAN(tblApplications[days_to_response]),"")
Average days to reply =IFERROR(AVERAGEIFS(tblApplications[days_to_response],tblApplications[days_to_response],"<>"),"")
Response band         =IF(kpi_applications>0,INDEX(ResponseBands[band],
                        MATCH(ROUND(kpi_response_rate*100,1),ResponseBands[lower_pct],1)),"No data")
Last 30 days          =COUNTIFS(tblApplications[days_since_applied],"<30")
```

Why these are correct:

- **Unknown reply dates are blank cells, never 0.** `MEDIAN`, `AVERAGEIFS` and `COUNT` skip
  blanks, so the timing KPIs use known dates only. The tile says how many dates are known.
- **Response band** uses an approximate `MATCH` (match type 1) over `ResponseBands[lower_pct]`,
  which is sorted ascending. It returns the highest lower bound at or below the rate, so the
  thresholds live in one table instead of a nested `IF`. The rate is rounded to one decimal
  first, exactly as the SQL rounds before banding.
- **Wilson intervals** use the named cell `WilsonZ` (1.96). They are honest error bars for a
  small sample, unlike `p +/- 1.96*SQRT(p(1-p)/n)`, which can go below 0% at small n.
- **The platform block** below the KPI table repeats the response rate and Wilson interval per
  platform with `COUNTIFS(tblApplications[platform],K..,...)`, so the "indicative only" message
  can be checked by hand.

Defined names: `kpi_*` (one per KPI), and on the Benchmarks sheet `DirectInterviewBenchmark`,
`JobBoardInterviewBenchmark`, `BenchRespLo/Hi`, `BenchIntLo/Hi`, `BenchOfferLo/Hi`,
`SampleAdequate`, `SampleIndicative`, `WilsonZ`, `FollowUpDays`, `GhostAfterDays`
(**Formulas > Name Manager** lists them all).

## Refreshing with new data

- **Full refresh (recommended)**: run `python -m jobtracker sync` (or `build`), then
  `python -m jobtracker export`. Every sheet is rebuilt, the snapshot sheets included.
- **Quick refresh in Excel**: open `job_applications.csv`, copy the rows, and paste them over the
  data rows of `tblApplications`. The table resizes, and the Dashboard tiles and charts
  recalculate (press F9 if calculation is manual). Paste only the columns the table has, in its
  order: the CSV also carries the `*_key` columns, which the workbook leaves out. The report
  sheets keep showing their export date until the next full refresh.
- **New platform**: the live platform block lists the platforms that existed at export. Add a
  row by copying the last row of the block down and typing the platform name in column K.
- **Follow-up window**: change `FollowUpDays` on the Benchmarks sheet (the blue input cell on the
  blue tint). `Needs follow-up` and the Active-pipeline tile follow immediately. Change
  `settings.follow_up_after_days` in the app to make it permanent.
- **Benchmarks** are generated from `jobtracker/benchmarks.py`. Editing them in the workbook is
  fine for a what-if, but change the Python module to change them everywhere (app, DAX,
  Tableau, workbook).

## Styling conventions

The workbook follows the app's light editorial look: paper `#F7F5F0` dashboard canvas, white
tiles with hairline `#E3DED5` rules, ink `#191919` text, Georgia headings, Calibri body text. One
accent, `#1D3FBB`, is used sparingly: the headline KPI values, "your" series in charts, links.
Benchmarks and context series are warm greys (`#C9C3B8`, `#A39E94`). Status colours: Applied
`#A39E94`, Assessment / OA `#6B7FD7`, Interview `#1D3FBB`, Offer `#2E7D4F`, Rejected `#C04A32`,
Ghosted `#C9C3B8`, Withdrawn `#8A857C`. Every sheet prints on A4 (the Cover portrait, the rest landscape), fitted to the page
width, with the sheet name and as-of date in the header and page numbers in the footer.

## How it is verified

`tests/test_bi.py` builds the synthetic demo dataset and then:

1. Builds the workbook, recalculates it in headless LibreOffice
   (`soffice --convert-to xlsx`), and reads every Dashboard KPI and the live platform block
   back. Each value must equal `Metrics.kpis()`, `period_compare` and `platform_performance`
   from the SQL layer (rates compared at the SQL's one-decimal rounding). Any `#VALUE!`,
   `#NAME?` or other error anywhere in the workbook fails the test.
2. Checks that `tblApplications` has exactly as many rows as `fct_applications`, that unknown reply
   times stay blank, that the `ResponseBands` / `Benchmarks` tables equal
   `jobtracker/benchmarks.py`, and that no dynamic-array-only function is used.

To run the same check by hand: `python -m jobtracker.bi.verify --data-dir <data dir>`.
