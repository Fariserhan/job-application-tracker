# Power BI guide - build the 3-page job-search report

This guide turns the exported star schema into a three-page Power BI report: **Overview**,
**Channels & funnel**, and **Timing & follow-ups**. It takes about an hour. Every number on
the finished report matches the app and the Excel workbook, because every measure is a
translation of one SQL definition in `jobtracker/warehouse/metrics.py`.

Everything you need is in the export folder (`python -m jobtracker export`, written to
`<data dir>/exports/` by default):

| File | Used in step |
|---|---|
| `star_schema/*.csv` (10 tables) | 1 Import |
| `powerbi/model.json` | 2 Relationships, 4 Column hygiene (it lists every relationship, type, hidden column and sort-by column) |
| `powerbi/measures.dax` | 5 Measures (62 measures, one DAX query) |
| `powerbi/theme.json` (copy in `docs/bi/powerbi-theme.json`) | 6 Theme |

---

## 1. Import the star schema

1. **File > Options and settings > Options > Current file > Data load**: untick
   **Auto date/time**. `dim_date` is the calendar; hidden auto-date tables would compete with it.
2. **Home > Get data > Text/CSV**, and import each file in `star_schema/` (repeat 10 times or
   use *Get data > Folder* and *Combine* per file). Keep the query names as the file names:
   `fct_applications`, `fct_status_events`, `dim_date`, `dim_company`, `dim_platform`,
   `dim_status`, `dim_role_family`, `dim_seniority`, `dim_industry`, `dim_job_type`.
3. Click **Transform data** and check the column types (Power Query detects most of them):
   - `*_date`, `date_day`, `week_start`, `occurred_on`: **Date**
   - `is_*`, `reached_*`, `has_*`: **True/False**
   - `days_*`, `max_stage_reached`, `event_count`, `application_count`, `stage_order`,
     `sort_order`, `date_key`: **Whole number**; `parse_confidence`: **Decimal number**
   - **Never** use *Replace values* on `days_to_response` or `days_to_interview`. A blank cell
     means the reply date is unknown. A zero would assert that the employer replied the same day,
     which would drag every median down.
4. **Close & Apply**.

## 2. Model view: relationships

Open **Model view** and delete any relationships Power BI auto-detected. Then create these.
All are **Many-to-one (\*:1)** with **Single** cross-filter direction, from the fact (many
side) to the dimension (one side):

| From (many) | To (one) | Active? |
|---|---|---|
| `fct_applications[applied_date]` | `dim_date[date_day]` | **Yes**, the date every time-intelligence measure uses |
| `fct_applications[first_response_date]` | `dim_date[date_day]` | **No** (role-playing date, dotted line) |
| `fct_applications[last_activity_date]` | `dim_date[date_day]` | **No** (role-playing date, dotted line) |
| `fct_applications[company_key]` | `dim_company[company_key]` | Yes |
| `fct_applications[platform_key]` | `dim_platform[platform_key]` | Yes |
| `fct_applications[status_key]` | `dim_status[status_key]` | Yes |
| `fct_applications[role_family_key]` | `dim_role_family[role_family_key]` | Yes |
| `fct_applications[seniority_key]` | `dim_seniority[seniority_key]` | Yes |
| `fct_applications[industry_key]` | `dim_industry[industry_key]` | Yes |
| `fct_applications[job_type_key]` | `dim_job_type[job_type_key]` | Yes |
| `fct_status_events[application_id]` | `fct_applications[application_id]` | Yes |

Why the two inactive date relationships: a fact can only have one active path to a dimension.
`applied_date` drives the report, and the measures `[Responses (by Reply Date)]` and
`[Applications Touched (by Activity Date)]` switch on the other two inside `CALCULATE` with
`USERELATIONSHIP`. `fct_status_events` deliberately has **no** relationship to `dim_date`,
because it would create an ambiguous second path (dim_date -> fct_applications -> events).
`[Status Changes (by Event Date)]` applies the visible dates with `TREATAS` instead.

To reproduce the layout from the screenshot-friendly *diagram* view, arrange `fct_applications`
in the centre, the seven dimensions around it, `dim_date` on the left and `fct_status_events` on
the right.

## 3. Mark the date table

Select `dim_date` > **Table tools > Mark as date table** > date column **`date_day`**. Power BI
validates that it is unique and contiguous (the dbt model guarantees it: one row per day from
the first application, or 30 days ago, to the build date).

## 4. Column hygiene (two minutes, big difference)

- **Hide** surrogate keys and the denormalised copies on the fact so report authors slice by the
  dimensions: every column listed with `"isHidden": true` in `model.json` (all `*_key`
  columns, `application_count`, and on `fct_applications` the text copies `company_name`,
  `platform`, `channel`, `role_family`, `seniority`, `industry`, `job_type`, `current_status`).
- **Sort by column**: `dim_date[month_name]` by `month`, `dim_date[day_of_week]` by
  `day_of_week_num`, `dim_status[status]` by `sort_order`, `dim_status[funnel_stage]` by
  `stage_order`.
- **Summarization = Don't summarize** for every numeric column except `application_count`. In
  particular `days_to_response` must only be aggregated through the median measures.

## 5. Add the measures

1. Open **DAX query view** (left rail), paste the whole of `powerbi/measures.dax`, and press
   **Run**. The first result grid shows the headline numbers. Check them against the Excel
   workbook's Dashboard KPI table. The second grid is the per-platform table.
2. Click **Update model with changes** (the banner above the editor). All 62 measures are added
   to `fct_applications`.
3. Optional, recommended: create a measures-only table (**Enter data** > one empty column > name
   it `_Measures`), select all measures in the Data pane and set **Home table = _Measures**, then
   hide the empty column. The table jumps to the top of the field list.
4. **Display folders**: in Model view, Ctrl-click the measures of one folder and type the folder in
   **Properties > Display folder**. Folders and format strings are in the comment above every
   measure: `1 Volume`, `2 Funnel`, `3 Reliability`, `4 Timing`, `5 Channels`, `6 Benchmarks`.
   Rates use `0.0%`, counts `#,0`, day measures `0.0`, gaps in percentage points `+0.0;-0.0;0.0`.
5. **Follow-up threshold as a slider (what-if parameter)**: **Modeling > New parameter > Numeric
   range**, name `Follow-up Days`, min 1, max 60, increment 1, default 7, *Add slicer to this
   page* ticked. Then edit the measure `[Follow-up Threshold (days)]` to:

   ```dax
   Follow-up Threshold (days) = SELECTEDVALUE ( 'Follow-up Days'[Follow-up Days], 7 )
   ```

   `[Needs Follow-up]` reads the threshold, so the slider now drives it. With the fixed value
   (the default in `measures.dax`) it matches `settings.follow_up_after_days` and the app.

### The measures, by folder

| Folder | Measures |
|---|---|
| 1 Volume | Total Applications, Companies, Active Pipeline, Direct Channel Share %, Applications Last 30 Days, Applications Prev 30 Days, Applications Change 30d, Applications Change 30d %, Cumulative Applications, Rolling 4-Week Avg Applications |
| 2 Funnel | Responses, Assessments, Interviews, Offers, Rejections, Ghosted, Response / Interview / Offer / Rejection / Ghost Rate %, Assessment / Interview / Offer Step Conversion %, Responses / Interviews / Offers per 100, Responses / Interviews / Offers vs Benchmark |
| 3 Reliability | Response Rate CI Low % / High %, Interview Rate CI Low % / High % (Wilson score intervals written in DAX), Sample Confidence, Response Band |
| 4 Timing | Median Days To Response, Average Days To Response, Responses With Known Reply Date, Median Days To Interview, Follow-up Threshold (days), Needs Follow-up, Responses (by Reply Date), Applications Touched (by Activity Date), Status Changes (by Event Date), Last 30d / Prev 30d Response Rate % |
| 5 Channels | Platform Response Rank, Overall Response Rate %, Response Rate vs Overall (pp), Interview Rate vs Direct Benchmark (pp) |
| 6 Benchmarks | Direct / Job Board Interview Benchmark %, Benchmark Interviews / Offers per 100 (Low, High, Mid) |

Patterns worth pointing out in an interview:

- **Blank-aware median**: `MEDIANX ( FILTER ( fct_applications, NOT ISBLANK ( fct_applications[days_to_response] ) ), ... )`.
  Unknown reply dates are excluded, never treated as zero.
- **Wilson interval in DAX**: variables for n, p and z (1.96), then the closed-form bounds clamped
  to [0, 1]. These are honest error bars for small samples, where the normal approximation
  breaks down.
- **Role-playing dates**: `USERELATIONSHIP` inside `CALCULATE`.
- **Time intelligence**: `DATESINPERIOD` on the marked date table (last 30 days vs the 30
  before), running totals with `REMOVEFILTERS ( dim_date )`, and a rolling 4-week average that
  shortens its window at the start of the series exactly like the SQL `ROWS BETWEEN 3 PRECEDING`.
- **Ranking**: `RANKX` over `FILTER ( ALL ( dim_platform[platform] ), [Total Applications] > 0 )`
  with `DENSE` ties.
- **Generated thresholds**: the `Response Band` SWITCH and every benchmark constant are written
  by `jobtracker/bi/dax.py` from `jobtracker/benchmarks.py`, so the bands can never drift from the
  app.

## 6. Theme

**View > Themes > Browse for themes** > `powerbi/theme.json`. The theme matches the app's light
editorial look:

| Role | Colour |
|---|---|
| Page (paper) | `#F7F5F0` |
| Visual surface | `#FFFFFF`, hairline border `#E3DED5` |
| Text (ink) / secondary / notes | `#191919` / `#5F5B54` / `#8A857C` |
| Accent, for "your" series and headline KPIs, used sparingly | `#1D3FBB` (soft tint `#DCE3FA`) |
| Positive / negative / caution | `#2E7D4F` / `#C04A32` / `#B7791F` |
| Context and benchmark series | `#C9C3B8` / `#A39E94` |

Status colours (set them per category on any status visual): Applied `#A39E94`,
Assessment / OA `#6B7FD7`, Interview `#1D3FBB`, Offer `#2E7D4F`, Rejected `#C04A32`,
Ghosted `#C9C3B8`, Withdrawn `#8A857C`.

Fonts: the theme uses **Georgia** for titles and KPI callouts (the app's Newsreader) and
**Segoe UI** for body text (the app's Inter). Keep visual titles left-aligned, sentence case,
and turn off visual borders where two visuals share a rule.

Canvas: **Format page > Canvas settings > 16:9, 1280 x 720**. Use a 24 px outer margin and
16 px gutters. Every page has the same header: a text box with the page title in Georgia 20
and a one-line subtitle in `#5F5B54` saying what the page answers.

## 7. Build the pages

**Slicers on every page** (sync them with **View > Sync slicers**): `dim_date[date_day]`
(style *Between*), `dim_platform[channel]` (dropdown), `dim_role_family[role_family]`
(dropdown). Put them in a single row under the page title.

### Page 1 - Overview: "How is the search going?"

| Visual | Field wells | Format |
|---|---|---|
| **Card (new)** x 6, one row | Values: `Total Applications`, `Response Rate %`, `Interview Rate %`, `Offers`, `Active Pipeline`, `Median Days To Response` | Callout values in the accent for the first two only, ink for the rest. Reference labels on the Response Rate card: `Response Rate CI Low %`, `Response Rate CI High %` (label "95% CI"). On Median Days: `Responses With Known Reply Date` (label "known reply dates"). |
| **Card** | `Response Band` | Subtitle: "vs published bands: Strong 5%+, Good 3%+, Average 2%+" |
| **Line and clustered column chart** "Weekly applications" | X-axis: `dim_date[week_start]` (continuous off, type categorical). Column y: `Total Applications`. Line y: `Rolling 4-Week Avg Applications` | Columns `#C9C3B8` (context), line `#1D3FBB` 2 px. |
| **Card** "Momentum" | `Applications Last 30 Days`, reference label `Applications Change 30d` | Conditional colour on the reference label: positive `#2E7D4F`, negative `#C04A32`. |
| **Stacked bar** "Where every application stands" | Y-axis: `dim_status[status]`. X-axis: `Total Applications` | Per-category status colours (above), data labels on. |

### Page 2 - Channels & funnel: "Where do replies come from?"

| Visual | Field wells | Format |
|---|---|---|
| **Funnel** "Application funnel" | Values (no category): `Total Applications`, `Responses`, `Assessments`, `Interviews`, `Offers` | Single colour `#1D3FBB`; data labels "Data value, percent of first". |
| **Clustered column** "Per 100 applications vs benchmark" | See the small helper table below. X-axis: `Funnel Stages[Stage]`. Y-axis: `Stage per 100`, `Stage Benchmark (Mid)` | You `#1D3FBB`, benchmark `#C9C3B8`. Tooltips: `Stage vs Benchmark`. |
| **Clustered bar** "Response rate by platform" | Y-axis: `dim_platform[platform]`. X-axis: `Response Rate %` | Bars `#1D3FBB`. **Analytics > Error bars** on: upper `Response Rate CI High %`, lower `Response Rate CI Low %`, bar style, colour `#5F5B54`. **Analytics > X-axis constant line** value fx = `Overall Response Rate %`, dashed `#A39E94`, label "overall". |
| **Table** "Platform scorecard" | `dim_platform[platform]`, `dim_platform[channel]`, `Total Applications`, `Response Rate %`, `Response Rate CI Low %`, `Response Rate CI High %`, `Interview Rate %`, `Interview Rate vs Direct Benchmark (pp)`, `Platform Response Rank`, `Sample Confidence` | Conditional formatting: `Response Rate %` data bars `#DCE3FA`; `Interview Rate vs Direct Benchmark (pp)` font colour rules (< 0 `#C04A32`, >= 0 `#2E7D4F`); `Sample Confidence` font colour by rules on text: Adequate `#2E7D4F`, Indicative `#B7791F`, Too small `#C04A32`. |
| **Clustered column** "Interview rate by channel vs 6.87% direct benchmark" | X-axis: `dim_platform[channel]`. Y-axis: `Interview Rate %` | **Analytics > Y-axis constant line** fx = `Direct Interview Benchmark %`, label "direct-application benchmark". |
| **Text box** (footnote) | "Platform comparisons are indicative: at these sample sizes the 95% intervals overlap. Read the Sample Confidence column before ranking." | `#8A857C`, 9 pt. |

The helper table for the per-100 chart is report-only, so it is not in the warehouse.
**Modeling > New table**:

```dax
Funnel Stages =
DATATABLE (
    "Stage", STRING, "Order", INTEGER,
    { { "Employer responded", 1 }, { "Assessment", 2 }, { "Interview", 3 }, { "Offer", 4 } }
)
```

Sort `Stage` by `Order`, leave it unrelated to the model, and add three measures:

```dax
Stage per 100 =
SWITCH (
    SELECTEDVALUE ( 'Funnel Stages'[Stage] ),
    "Employer responded", [Responses per 100],
    "Assessment", DIVIDE ( [Assessments], [Total Applications] ) * 100,
    "Interview", [Interviews per 100],
    "Offer", [Offers per 100]
)

Stage Benchmark (Mid) =
SWITCH (
    SELECTEDVALUE ( 'Funnel Stages'[Stage] ),
    "Interview", [Benchmark Interviews per 100 (Mid)],
    "Offer", [Benchmark Offers per 100 (Mid)]
)

Stage vs Benchmark =
SWITCH (
    SELECTEDVALUE ( 'Funnel Stages'[Stage] ),
    "Interview", [Interviews vs Benchmark],
    "Offer", [Offers vs Benchmark]
)
```

(The assessment stage has no published benchmark, so its benchmark bar is intentionally blank.)

### Page 3 - Timing & follow-ups: "What needs doing this week?"

| Visual | Field wells | Format |
|---|---|---|
| **Slicer** | `'Follow-up Days'[Follow-up Days]` (the what-if parameter from step 5) | Single value slider. |
| **Card** x 3 | `Needs Follow-up`, `Median Days To Response`, `Median Days To Interview` | `Needs Follow-up` in the accent. |
| **Clustered column** "Replies by the date they arrived" | X-axis: `dim_date[week_start]`. Y-axis: `Responses (by Reply Date)` | `#1D3FBB`. Subtitle: "inactive relationship activated with USERELATIONSHIP". |
| **Line chart** "Cumulative applications" | X-axis: `dim_date[date_day]`. Y-axis: `Cumulative Applications` | `#1D3FBB`; secondary line `Status Changes (by Event Date)` optional in `#A39E94`. |
| **Column chart** "How long replies take" | X-axis: `fct_applications[days_to_response]` grouped into **bins of 7** (right-click the column > New group > Bin size 7; blanks are excluded automatically). Y-axis: `Responses With Known Reply Date` | `#6B7FD7`. Footnote: "Known reply dates only." |
| **Table** "Follow-up queue" | `dim_company[company_name]`, `fct_applications[role_title]`, `dim_platform[platform]`, `dim_status[status]`, `fct_applications[last_activity_date]`, `fct_applications[days_since_activity]`, `fct_applications[gmail_link]` | Visual-level filter: `Needs Follow-up` **is 1**. Sort by `days_since_activity` descending. `gmail_link`: **Column tools > Data category > Web URL** and *URL icon* on. Background colour scale on `days_since_activity`: `#FFFFFF` -> `#B7791F` (at the threshold) -> `#C04A32` (21 days). |

### Report tooltip (all pages)

Add a page, **Format page > Page information > Allow use as tooltip**, canvas *Tooltip*. Add
cards for `Total Applications`, `Response Rate %`, `Response Rate CI Low %`,
`Response Rate CI High %`, `Sample Confidence` and `Median Days To Response`. On the platform,
channel and status visuals, set **Format > General > Tooltips > Type: Report page** and select
this page. Every hover then shows the interval and the sample label, so no rate is ever shown
without its uncertainty.

## 8. Check and screenshot

- With no slicers applied, the Overview cards must equal the Excel workbook's Dashboard KPI
  table and the app's KPI strip. They come from the same SQL, so any difference means a
  relationship or a column type is wrong (usually a boolean imported as text).
- Turn on **View > Page view > Fit to page**, collapse the Filters pane, and use
  **File > Export > Export to PDF** or a 1280 x 720 screenshot of each page.
- For a CV or portfolio, the model diagram (step 2) and the DAX query view with
  `measures.dax` are worth a screenshot each: they show the star schema and the measure layer,
  not just the charts.
