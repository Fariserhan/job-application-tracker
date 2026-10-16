# Tableau guide - job-search dashboard from one CSV

Tableau reads the flat file `job_applications.csv` from the export folder
(`python -m jobtracker export`). That file is the dbt view `rpt_application_analysis`:
one row per application, every fact column plus the calendar attributes of `applied_date`. The
calculated fields are in `tableau/calculations.md` (machine-readable copy:
`tableau/calculations.json`). Each one reproduces a SQL metric from
`jobtracker/warehouse/metrics.py`, and every threshold is generated from
`jobtracker/benchmarks.py`.

Works in Tableau Desktop or Tableau Public (free), version 2020.2 or later.

## 1. Connect

1. **Connect > Text file** > `job_applications.csv`.
2. On the Data Source page, check the types: `applied_date`, `last_activity_date`,
   `first_response_date`, `applied_week` = **Date**; `is_*`, `reached_*`, `has_*`,
   `applied_on_weekend` = **Boolean**; `days_*` = **Number (whole)**.
3. `days_to_response` and `days_to_interview` contain **NULLs** where the date is unknown. Leave
   them as NULLs. `MEDIAN`, `AVG` and `COUNT` skip NULLs, which is the point. Never wrap them in
   `ZN()`, which would invent same-day replies.
4. Rename the data source `Job applications`. Optional: hide `*_key`, `application_count` (still
   used by calculations; hiding keeps it working) and `gmail_link` from the Data pane.

## 2. Parameter and calculated fields

1. **Create Parameter** `Follow-up Days`: Integer, current value 7 (=
   `settings.follow_up_after_days`), range 1-60, step 1. Show the parameter control.
2. Create the 54 calculated fields from `tableau/calculations.md` (**Analysis > Create
   Calculated Field**, paste the name and the formula). Create them in the order listed: later
   fields reference earlier ones. Put them in folders (**Data pane > Group by Folder**)
   matching the headings: Volume, Funnel, Rates, Reliability, Timing, Channels, Cohorts,
   Velocity, Benchmarks.
3. Default formats: every `... %` field **Percentage, 1 decimal**; `(pp)` fields **Number, 1
   decimal, +/- prefix**; day fields **Number, 1 decimal**.

Field types you will meet, and what to say about them in an interview:

| Type | Fields | Note |
|---|---|---|
| Aggregate | Response Rate %, Wilson CI Low/High, Response Band, Sample Confidence, Median Days To Response ... | Ratio of sums, so it is correct at any level of detail. Never an average of row-level rates. |
| Row-level | Needs Follow-up Flag, Days To Response Bucket | Evaluated per application, then aggregated. |
| LOD | Platform Response Rate % (`FIXED [platform]`), Cohort Response Rate % (`FIXED [applied_month]`), Cohort Is Maturing, Company First Applied, Is Repeat Employer, Response Rate vs Overall (pp) (table-scoped `{ SUM(...) }`) | FIXED ignores dimension filters. **Add the date and channel filters to context** (right-click > *Add to Context*) so the LODs respect them. |
| Table calc | Rolling 4-Week Avg Applications (`WINDOW_AVG(..., -3, 0)`), Cumulative Applications (`RUNNING_SUM`), Platform Response Rank (`RANK_DENSE`) | Set **Compute Using** as noted per field. On a week axis, turn on **Show Missing Values** so empty weeks count as zero, as they do in the SQL. |

## 3. Colours and fonts (match the app)

Light editorial palette. Use **Format > Workbook** to set the fonts: titles **Georgia** (the
app's Newsreader), body **Tableau Book / Segoe UI** (the app's Inter). Set worksheet
shading to `#FFFFFF` and dashboard shading to `#F7F5F0`. Use `#E3DED5` hairlines for row and
column dividers and `#191919` for text.

| Role | Hex |
|---|---|
| "You" series, headline KPI | `#1D3FBB` (accent, use sparingly) |
| Benchmarks, context series | `#C9C3B8`, `#A39E94` |
| Positive / negative / caution | `#2E7D4F` / `#C04A32` / `#B7791F` |
| Statuses | Applied `#A39E94`, Assessment / OA `#6B7FD7`, Interview `#1D3FBB`, Offer `#2E7D4F`, Rejected `#C04A32`, Ghosted `#C9C3B8`, Withdrawn `#8A857C` |
| Sample confidence | Adequate `#2E7D4F`, Indicative `#B7791F`, Too small `#C04A32` |

To reuse the palette, add it to `My Tableau Repository/Preferences.tps`:

```xml
<color-palette name="Job search - editorial" type="regular">
  <color>#1D3FBB</color><color>#6B7FD7</color><color>#2E7D4F</color><color>#C04A32</color>
  <color>#B7791F</color><color>#A39E94</color><color>#C9C3B8</color><color>#8A857C</color>
</color-palette>
```

## 4. Worksheets

| Sheet | Build |
|---|---|
| **KPI - BANs** | Measure Names on Columns, Measure Values on Text, filtered to `Total Applications`, `Response Rate %`, `Interview Rate %`, `Offers`, `Active Pipeline`, `Median Days To Response`. Format as big numbers (Georgia 24). Add `Response Rate CI Low %` / `High %` and `Response Band` to Tooltip. |
| **Weekly velocity** | Columns: `applied_week` (exact date, continuous, *Show Missing Values*). Rows: `Total Applications` (bar, `#C9C3B8`) and `Rolling 4-Week Avg Applications` (line, `#1D3FBB`); dual axis, synchronise. Compute using `applied_week`. |
| **Funnel vs benchmark** | Measure Names on Rows filtered to `Interviews per 100`, `Offers per 100`; Measure Values on Columns (bar, `#1D3FBB`). Add the matching `Benchmark ... per 100 (Mid)` as a reference line per pane, or build a second sheet. Label with `... vs Benchmark`. |
| **Platforms with CI** | Rows: `platform` sorted by `Response Rate %` desc. Columns: `Response Rate CI Low %` as a **Gantt bar**, Size = `Response Rate CI Width %` (the interval, colour `#A39E94`), plus `Response Rate %` on a dual axis as a **circle** (`#1D3FBB`), synchronised. Label: `Sample Confidence`. Reference line: `Overall response rate` = average of `Response Rate %` per table. This is the "indicative, not significant" chart: overlapping bars mean no real difference. |
| **Interview vs direct benchmark** | Rows: `channel`. Columns: `Interview Rate %`. Reference line constant from `Direct Interview Benchmark %` (6.87%), label "direct-application benchmark". Colour by sign of `Interview Rate vs Direct Benchmark (pp)` (positive / negative). |
| **Cohort heatmap** | Rows: `applied_month`. Columns: Measure Names (`Response Rate %`, `Interview Rate %`). Square marks, colour sequential `#FFFFFF` -> `#1D3FBB`. Italicise / filter with `Cohort Is Maturing` (young cohorts are floors, not results). |
| **Reply time** | Columns: `Days To Response Bucket` (sort by `MIN([days_to_response])`). Rows: `Responses With Known Reply Date`. Bars `#6B7FD7`. Caption: "Known reply dates only." |
| **Status mix** | Rows: `current_status`. Columns: `Total Applications`. Colour: `current_status` with the status palette. |
| **Follow-up queue** | Rows: `company_name`, `role_title`, `platform`, `current_status`, `last_activity_date`. Text: `days_since_activity`. Filter: `Needs Follow-up Flag` = True. Sort by `days_since_activity` desc. Colour `days_since_activity`: white -> `#B7791F` -> `#C04A32`. Show the `Follow-up Days` parameter control. |

## 5. Dashboard

**Size**: Fixed, 1366 x 900. Background `#F7F5F0`. Outer padding 24, inner padding 12.

1. **Header** (horizontal container): title "Job search pipeline" (Georgia 22, `#191919`) and a
   subtitle with the period and the build date (`#5F5B54`).
2. **Filter row**: `applied_date` (range), `channel`, `role_family`. Apply to all worksheets
   using this data source, and **Add to Context** (the LOD fields depend on it).
3. **Row 1**: KPI - BANs (full width).
4. **Row 2**: Weekly velocity (60%) | Funnel vs benchmark (40%).
5. **Row 3**: Platforms with CI (50%) | Interview vs direct benchmark (50%).
6. **Row 4**: Follow-up queue (60%) | Reply time over Status mix (40%).
7. **Actions**: Dashboard > Actions > Filter: selecting a platform in *Platforms with CI*
   filters the follow-up queue and the weekly chart.
8. **Footnote** text object (`#8A857C`, 9 pt): "Rates carry Wilson 95% intervals; platform
   comparisons are indicative at this sample size. Reply-time measures use known reply dates
   only. Benchmarks: published aggregate figures (response bands Strong 5%+, Good 3%+, Average
   2%+; 6.87% direct-application interview rate)."

## 6. Check

With no filters, the BANs must equal the Excel Dashboard KPI table and the app (same SQL
definitions). A mismatch usually means a boolean imported as a string (`"True"`) or a NULL
turned into zero. Publish to Tableau Public with **Server > Tableau Public > Save to Tableau
Public**. The synthetic demo dataset (`python -m jobtracker demo`) is safe to publish, but
your real data is not.
