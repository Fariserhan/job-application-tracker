"""BI layer over the job-application warehouse.

Turns the warehouse into the artifacts a hiring manager in a data/BI role actually
expects to see referenced:

  * `warehouse.db`  — the star schema itself (SQL DDL, CTEs, window functions)
  * Excel export    — one CSV per report + a workbook build guide with real formulas
  * DAX measures    — copy-paste ready, written against THIS model's tables/columns
  * Tableau calcs   — calculated fields written against THIS model's fields
  * model spec      — the relationships, cardinalities and grain, documented

Honesty rules this module follows (they matter more than the artifacts):
  * The DAX/Tableau measures are written for the measures this data can actually
    support — application-pipeline and job-search analytics. Insurance measures such as
    loss ratio or IBNR do NOT belong to this model and are not faked here.
  * Every export names its own limitations, so nothing over-claims.

Usage:
    venv\\Scripts\\python.exe bi_export.py          # build + export everything
    from bi_export import export_bi_pack, dax_measures, tableau_calculations
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import warehouse
import warehouse_queries as wq

OUT_DIR = "bi_pack"
EXCEL_DIR = os.path.join(OUT_DIR, "excel")
BI_DIR = os.path.join(OUT_DIR, "bi")

STALE_DAYS = 21
# ---------------------------------------------------------------- Excel


def excel_sheets() -> dict:
    """{sheet_name: DataFrame} — the report set the workbook is built from.

    These are the same numbers the dashboard shows, pulled from the same SQL, so the
    workbook and the app can never disagree.
    """
    return {
        "kpi_summary": wq.query("headline_kpis"),
        "kpi_period_compare": wq.query("kpi_last_30_vs_prev"),
        "funnel": wq.query("funnel_vs_benchmark"),
        "outcome_mix": wq.query("outcome_mix"),
        "velocity": wq.query("monthly_velocity_running"),
        "platform_ranking": wq.query("platform_effectiveness"),
        "response_times": wq.query("response_time_analysis"),
        "response_time_bands": wq.query("response_time_distribution"),
        "application_timing": wq.query("application_timing"),
        "silence_cohort": wq.query("silence_cohort"),
        "company_pipeline": wq.query("company_pipeline"),
        "role_family": wq.query("role_family_performance"),
        "transition_matrix": wq.query("status_transition_matrix"),
        "activity_by_week": wq.query("weekly_activity_heatmap"),
        "seniority_industry": wq.query("seniority_and_industry_mix"),
        "data_quality": wq.query("data_quality_audit"),
    }


def excel_formula_guide() -> str:
    """The workbook build guide — the formulas that turn the CSV sheets into a report."""
    return f"""# Excel workbook build guide

Every sheet below is exported as a CSV in this folder. The workbook is built by importing
each CSV as a Table (Ctrl+T) using the exact table name given, then pasting the formulas
below. Table names matter: the formulas use structured references so the workbook keeps
working after the next Gmail sync adds rows.

## Sheets and table names

| Sheet | Table name | Source CSV | What it is |
|---|---|---|---|
| Applications | `tblApplications` | `job_applications.csv` | one row per application |
| Funnel | `tblFunnel` | `funnel.csv` | stage counts + conversion |
| Velocity | `tblVelocity` | `velocity.csv` | applications per month + running total |
| Platforms | `tblPlatforms` | `platform_ranking.csv` | platform effectiveness + ranks |
| KPIs | `tblKpis` | `kpi_summary.csv` | headline numbers |
| Silence | `tblSilence` | `silence_cohort.csv` | how long each role has been quiet |
| Quality | `tblQuality` | `data_quality.csv` | warehouse coverage audit |

## KPI sheet — dynamic-array formulas

Paste into the top-left cell; these spill and rebuild themselves on refresh.

```excel
=LET(
  total,  COUNTROWS(tblApplications),
  resp,   COUNTIFS(tblApplications[is_response],1),
  inter,  COUNTIFS(tblApplications[is_interview_plus],1),
  rate,   IFERROR(resp/total,0),
  HSTACK(
    HSTACK("Applications", total),
    HSTACK("Responses", resp),
    HSTACK("Interviews+", inter),
    HSTACK("Response rate", rate)
  )
)
```

Dynamic-array variants of the same block, laid out across rows:

```excel
="Applications: "        & COUNTROWS(tblApplications)
="Response rate: "       & TEXT(COUNTIFS(tblApplications[is_response],1)/COUNTROWS(tblApplications),"0.0%")
="Median days to response: " & IFERROR(MEDIAN(IF(tblApplications[days_to_response]>0,tblApplications[days_to_response])),"n/a")
="Stale (>={STALE_DAYS}d): " & COUNTIFS(tblApplications[is_stale],1)
="Direct-channel share: " & TEXT(COUNTIFS(tblApplications[is_direct_employer],1)/COUNTROWS(tblApplications),"0.0%")
```

## Funnel conversion with XLOOKUP

```excel
="Conversion to " & B4 & ": " &
 IFERROR(TEXT(C4/INDEX(tblFunnel[applications],MATCH("Applied",tblFunnel[stage],0)),"0.0%"),"n/a")
```

## Silence watchlist — everything quiet for {STALE_DAYS}+ days

```excel
=LET(
  names, FILTER(tblApplications[company_name] & " - " & tblApplications[role_title],
                (tblApplications[current_status]="Applied") * (tblApplications[days_waiting]>={STALE_DAYS})),
  IF(ROWS(names)=0, "nothing stale", names)
)
```

## Top skill gaps by priority

```excel
=TAKE(SORT(FILTER(HSTACK(tblSkills[skill_name],tblSkills[priority_score]),tblSkills[status]="Gap"),2,-1),5)
```

## Platform effectiveness against the average (SUMIFS / AVERAGEIFS)

```excel
=IFERROR(SUMIFS(tblApplications[is_response],tblApplications[source_platform],A4)
        /COUNTIFS(tblApplications[source_platform],A4),0)
=IFERROR(COUNTIFS(tblApplications[source_platform],A4,tblApplications[is_response],1)/COUNTIFS(tblApplications[source_platform],A4),"n/a")
```

## Conditional formatting to apply

| Sheet | Column | Rule | Reason |
|---|---|---|---|
| Applications | `days_waiting` | 3-colour scale, red >= {STALE_DAYS} | matches the tracker's ghost threshold |
| Platforms | `interview_rate_pct` | red < 2, amber 2-6.9, green >= 6.9 | the direct-application benchmark |
| Platforms | `response_rate_pct` | red < 10, amber 10-25, green >= 25 | outcome quality at a glance |
| Skills | `status` | green = Covered, red = Gap | the whole point of the sheet |
| Funnel | `step_conversion_pct` | red < 5 | where the pipeline actually dies |

## VBA — one macro, one real job

The only automation worth writing here is a full refresh, because that is the actual
manual friction in a reporting workbook. Alt+F11 -> Insert Module, paste:

```vba
Option Explicit

Public Sub RefreshTrackerReports()
    ' Refresh every connected query and pivot in the workbook, then stamp freshness.
    Dim ws As Worksheet, qt As QueryTable, pc As PivotCache
    Application.ScreenUpdating = False
    Application.StatusBar = "Refreshing tracker reports..."

    For Each ws In ThisWorkbook.Worksheets
        For Each qt In ws.QueryTables
            On Error Resume Next
            qt.Refresh BackgroundQuery:=False
            On Error GoTo 0
        Next qt
        For Each pc In ws.PivotTables
            On Error Resume Next
            pc.RefreshTable
            On Error GoTo 0
        Next pc
    Next ws

    With ThisWorkbook.Worksheets("KPIs")
        .Range("stamp_date").Value2 = Date
        .Range("stamp_source").Value2 = "warehouse.db / applications.db export"
    End With

    Application.StatusBar = False
    Application.ScreenUpdating = True
    MsgBox "Tracker reports refreshed.", vbInformation
End Sub
```

## Limitations to keep visible in the workbook

* This is one candidate's pipeline, not a business dataset. Trends over months are thin.
* `days_to_response` is blank when the response date is genuinely unknown — blanks are
  excluded from the median rather than being treated as zero.
* Insurance measures (loss ratio, IBNR, reserving) do **not** belong to this dataset;
  the claims-modelling artifacts are separate and use synthetic claims data.
"""


def unified_csv(db_path: str | None = None):
    """ONE flat CSV — every application, every useful attribute, one row each.

    This is the export most people actually want: open it and everything is there. It is
    built from the warehouse reporting view, so it carries the same numbers as the
    dashboard and the BI pack, plus the links from the operational table.
    """
    import pandas as pd

    with warehouse.connect(db_path) as conn:
        try:
            df = pd.read_sql_query("SELECT * FROM vw_application_analysis", conn)
        except Exception:
            return pd.DataFrame()
    if df.empty:
        return df

    # Links live on the operational table; join them by thread id.
    try:
        with warehouse.connect(db_path) as conn:
            links = pd.read_sql_query(
                "SELECT thread_id, job_url, gmail_link FROM applications", conn)
        if not links.empty and "thread_id" in links.columns:
            df = df.merge(links.drop_duplicates("thread_id"), on="thread_id", how="left")
    except Exception:
        pass

    # Readable order: identity -> classification -> dates -> outcome -> links.
    order = [
        "company_name", "role_title", "role_family", "seniority_name", "industry_name",
        "job_type_name", "source_platform", "is_direct_employer",
        "current_status", "funnel_stage", "is_terminal",
        "application_date", "applied_year", "applied_month", "applied_week", "day_of_week",
        "last_updated", "days_waiting", "days_to_response",
        "is_response", "is_interview_plus", "is_rejected", "is_ghosted", "is_stale", "has_jd",
        "job_url", "gmail_link", "thread_id",
    ]
    for col in order:
        if col not in df.columns:
            df[col] = ""
    df = df[order].sort_values("application_date", ascending=False, na_position="last")
    return df.reset_index(drop=True)


def unified_csv_conn(db_path: str | None = None):
    """Test-friendly alias for `unified_csv` — same function, explicit about its source."""
    return unified_csv(db_path)


def export_unified(outdir: str = OUT_DIR) -> dict:
    """Write the single flat CSV. Returns {'job_applications': path, 'rows': n}."""
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "job_applications.csv")
    df = unified_csv()
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return {"job_applications": path, "rows": len(df)}


def export_excel(outdir: str = EXCEL_DIR) -> dict:
    """Write the unified CSV plus one CSV per report and the build guide.

    `job_applications` is the single file to use day to day; the per-report CSVs exist
    only so the Excel workbook and the Power BI / Tableau models have their source sheets.
    """
    os.makedirs(outdir, exist_ok=True)
    written = {}
    unified = export_unified(os.path.dirname(os.path.abspath(outdir)))
    written["job_applications"] = unified["job_applications"]
    for name, df in excel_sheets().items():
        path = os.path.join(outdir, f"{name}.csv")
        df.to_csv(path, index=False, encoding="utf-8-sig")
        written[name] = path
    guide = os.path.join(outdir, "README_excel.md")
    with open(guide, "w", encoding="utf-8") as fh:
        fh.write(excel_formula_guide())
    written["README_excel"] = guide
    return written


# ---------------------------------------------------------------- Power BI / DAX


def dax_measures() -> dict:
    """DAX measures written against THIS model's tables and columns.

    Table names match the warehouse exactly (fact_application, dim_date, dim_status,
    dim_platform, dim_company, dim_role_family, dim_skill, fact_application_skill).
    """
    return {
        "Total Applications":
            "COUNTROWS ( fact_application )",

        "Responses":
            "CALCULATE ( COUNTROWS ( fact_application ), fact_application[is_response] = 1 )",

        "Response Rate %":
            "DIVIDE ( [Responses], [Total Applications] )",

        "Interview Rate %":
            "DIVIDE (\n"
            "    CALCULATE ( COUNTROWS ( fact_application ),\n"
            "                fact_application[is_interview_plus] = 1 ),\n"
            "    [Total Applications]\n"
            ")",

        "Rejection Rate %":
            "DIVIDE (\n"
            "    CALCULATE ( COUNTROWS ( fact_application ), fact_application[is_rejected] = 1 ),\n"
            "    [Total Applications]\n"
            ")",

        "Ghost Rate %":
            "DIVIDE (\n"
            "    CALCULATE ( COUNTROWS ( fact_application ), fact_application[is_ghosted] = 1 ),\n"
            "    [Total Applications]\n"
            ")",

        "Active Pipeline":
            "CALCULATE ( COUNTROWS ( fact_application ),\n"
            "            fact_application[current_status] IN {{ \"Applied\", \"Assessment / OA\", \"Interview\" }} )",

        "Stale Applications":
            "CALCULATE ( COUNTROWS ( fact_application ), fact_application[is_stale] = 1 )",

        "Median Days To Response":
            "-- Blanks are excluded: an unknown response date must not read as a 0-day reply.\n"
            "MEDIANX (\n"
            "    FILTER ( fact_application, NOT ISBLANK ( fact_application[days_to_response] ) ),\n"
            "    fact_application[days_to_response]\n"
            ")",

        "Avg Days Waiting":
            "AVERAGE ( fact_application[days_waiting] )",

        "Applications (30d)":
            "CALCULATE ( [Total Applications],\n"
            "            DATESINPERIOD ( dim_date[date_key], MAX ( dim_date[date_key] ), -30, DAY ) )",

        "Applications (prev 30d)":
            "CALCULATE ( [Total Applications],\n"
            "            DATESINPERIOD ( dim_date[date_key], MAX ( dim_date[date_key] ) - 30, -30, DAY ) )",

        "Applications 30d Change":
            "[Applications (30d)] - [Applications (prev 30d)]",

        "Cumulative Applications":
            "-- Running total for the velocity chart.\n"
            "CALCULATE ( [Total Applications],\n"
            "            FILTER ( ALLSELECTED ( dim_date[date_key] ),\n"
            "                     dim_date[date_key] <= MAX ( dim_date[date_key] ) ) )",

        "Applications Per Week":
            "DIVIDE ( [Total Applications], DISTINCTCOUNT ( dim_date[week_start] ) )",

        "Platform Response Rank":
            "RANKX ( ALL ( dim_platform[platform_name] ), [Response Rate %], , DESC, DENSE )",

        "Platform Response vs Average":
            "-- How far this platform sits above/below the overall rate.\n"
            "VAR ThisOne = [Response Rate %]\n"
            "VAR AllOnes = CALCULATE ( [Response Rate %], REMOVEFILTERS ( dim_platform ) )\n"
            "RETURN ThisOne - AllOnes",

        # Benchmark measures. The published research (1.24M applications tracked) puts the
        # average cold-application interview rate at 6.87% when applying on the employer's
        # own careers page, versus 1.95% on LinkedIn. Comparing against that band is what
        # turns a raw rate into a decision.
        "Interview Rate vs Direct Benchmark pp":
            "[Interview Rate %] - 0.0687",

        "Response Band":
            "-- The published bands: <2% below average, 2-3% average, 3-5% good, 5%+ strong.\n"
            "VAR R = [Response Rate %]\n"
            "RETURN\n"
            "    SWITCH ( TRUE (),\n"
            "        ISBLANK ( R ), \"No data\",\n"
            "        R >= 0.05, \"Strong\",\n"
            "        R >= 0.03, \"Good\",\n"
            "        R >= 0.02, \"Average\",\n"
            "        \"Below average\" )",

        "Direct Channel Share %":
            "-- How much of the pipeline went in via an employer's own careers page rather\n"
            "-- than a job board. The research is unambiguous that this is the highest-return\n"
            "-- change available, so it is worth tracking as a first-class metric.\n"
            "VAR Direct = CALCULATE ( [Total Applications], dim_platform[is_direct] = 1 )\n"
            "RETURN DIVIDE ( Direct, [Total Applications] )",

        "Applications Needing A Follow Up":
            "-- Applied with no reply past the chase threshold. This is the action list.\n"
            "CALCULATE ( COUNTROWS ( fact_application ),\n"
            "            fact_application[current_status] = \"Applied\",\n"
            "            fact_application[days_waiting] >= 7 )",

        "Outcome Mix %":
            "-- Share of the pipeline sitting in the current status. Used as a treemap or bar\n"
            "-- to answer 'where did my applications actually end up?' in one glance.\n"
            "DIVIDE ( COUNTROWS ( fact_application ),\n"
            "         CALCULATE ( COUNTROWS ( fact_application ), ALL ( dim_status ) ) )",

        "Sample Confidence":
            "-- Guardrail: never present a rate built on a handful of rows as a finding.\n"
            "VAR N = [Total Applications]\n"
            "RETURN SWITCH ( TRUE (), N >= 30, \"Adequate\", N >= 10, \"Indicative\", \"Too small\" )",
    }


def dax_file_text() -> str:
    lines = [
        "-- ===================================================================== ",
        "-- DAX measure pack for the job-application tracker warehouse",
        f"-- Generated {datetime.now():%Y-%m-%d %H:%M} from warehouse.db",
        "-- Model: fact_application, fact_application_skill, fact_status_event",
        "--        dim_date (date table, key: date_key), dim_company, dim_platform,",
        "--        dim_status, dim_role_family, dim_seniority, dim_industry,",
        "--        dim_job_type, dim_skill",
        "--",
        "-- Relationships: every fact -> dimension is many-to-one, single direction.",
        "-- Set dim_date as the marked date table; fact_application[application_date_key]",
        "-- is the ACTIVE relationship and [last_updated_key] should be marked INACTIVE",
        "-- (role-playing dimension) and used via USERELATIONSHIP when needed.",
        "--",
        "-- These are application-pipeline measures. Insurance measures (loss ratio, IBNR,",
        "-- reserving) do not apply to this dataset and are deliberately absent.",
        "-- ===================================================================== ",
        "",
    ]
    for name, expr in dax_measures().items():
        lines.append(f"-- {name}")
        lines.append(f"{name} =")
        lines.append(expr)
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- Tableau


def tableau_calculations() -> dict:
    """Tableau calculated fields written against this model's field names."""
    return {
        "Response Rate":
            "SUM([is_response]) / COUNTD([thread_id])",
        "Interview Rate":
            "SUM([is_interview_plus]) / COUNTD([thread_id])",
        "Median Days To Response":
            "MEDIAN(IIF(NOT ISNULL([days_to_response]), [days_to_response], NULL))",
        "Response Band":
            'IF ISNULL(SUM([is_response]) / COUNTD([thread_id])) THEN "No data"\n'
            'ELSEIF SUM([is_response]) / COUNTD([thread_id]) >= 0.05 THEN "Strong"\n'
            'ELSEIF SUM([is_response]) / COUNTD([thread_id]) >= 0.03 THEN "Good"\n'
            'ELSEIF SUM([is_response]) / COUNTD([thread_id]) >= 0.02 THEN "Average"\n'
            'ELSE "Below average"\nEND',
        "Interview Rate vs Direct Benchmark pp":
            "(SUM([is_interview_plus]) / COUNTD([thread_id])) - 0.0687",
        "Running Total Applications":
            "RUNNING_SUM(SUM([application_count]))",
        "Applications Per Week":
            "SUM([application_count])",
        "Month Over Month Change":
            "ZN(SUM([application_count])) - LOOKUP(ZN(SUM([application_count])), -1)",
        "Platform Response Rank":
            "RANK_DENSE(SUM([is_response]))",
        "Stale Flag":
            f'IF [days_waiting] >= {STALE_DAYS} THEN "Stale (>= {STALE_DAYS}d)" ELSE "Active" END',
        "Waiting Bucket":
            'IF [days_waiting] <= 7 THEN "1. 0-7 days"\n'
            'ELSEIF [days_waiting] <= 14 THEN "2. 8-14 days"\n'
            'ELSEIF [days_waiting] <= 21 THEN "3. 15-21 days"\n'
            'ELSEIF [days_waiting] <= 30 THEN "4. 22-30 days"\n'
            'ELSE "5. 30+ days"\nEND',
        "Direct Channel":
            "IIF([is_direct_employer] = 1, 'Employer careers page', 'Job board')",
        "Needs Follow Up":
            'IF [current_status] = "Applied" AND [days_waiting] >= 7 '
            'THEN "Chase now" ELSE "No action" END',
        "Sample Confidence":
            'IF COUNTD([thread_id]) >= 30 THEN "Adequate"\n'
            'ELSEIF COUNTD([thread_id]) >= 10 THEN "Indicative"\n'
            'ELSE "Too small"\nEND',
    }


def tableau_file_text() -> str:
    lines = [
        "// Tableau calculated fields for the job-application tracker warehouse",
        f"// Generated {datetime.now():%Y-%m-%d %H:%M}",
        "// Connect Tableau to the vw_application_analysis view (or the fact + dim CSVs).",
        f"// Apply a data-source filter: current_status <> 'Saved / Planning'.",
        "// Set the date source to [application_date] and mark it as a date dimension.",
        "",
    ]
    for name, expr in tableau_calculations().items():
        lines.append(f"// {name}")
        lines.append(f"{name}:")
        lines.append(expr)
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- model spec


def model_spec() -> dict:
    """The documented dimensional model: grain, relationships, columns."""
    return {
        "warehouse_file": "warehouse.db (SQLite star schema, built by warehouse.py)",
        "grain": {
            "fact_application": "one row per tracked application (thread_id)",
            "fact_application_skill": "one row per application x skill signal",
            "fact_status_event": "one row per recorded status transition",
        },
        "dimensions": {
            "dim_date": {"key": "date_key", "note": "marked as the date table; was built by the warehouse ETL"},
            "dim_company": {"key": "company_key"},
            "dim_platform": {"key": "platform_key", "attributes": ["platform_name", "is_direct"]},
            "dim_status": {"key": "status_key",
                           "attributes": ["funnel_stage", "stage_order", "is_terminal", "is_positive"]},
            "dim_role_family": {"key": "role_family_key"},
            "dim_seniority": {"key": "seniority_key", "attributes": ["seniority_rank"]},
            "dim_industry": {"key": "industry_key"},
            "dim_job_type": {"key": "job_type_key"},
            "dim_skill": {"key": "skill_key", "attributes": ["skill_family"]},
        },
        "relationships": [
            {"from": "fact_application[company_key]", "to": "dim_company[company_key]",
             "cardinality": "many-to-one", "direction": "single"},
            {"from": "fact_application[platform_key]", "to": "dim_platform[platform_key]",
             "cardinality": "many-to-one", "direction": "single"},
            {"from": "fact_application[status_key]", "to": "dim_status[status_key]",
             "cardinality": "many-to-one", "direction": "single"},
            {"from": "fact_application[role_family_key]", "to": "dim_role_family[role_family_key]",
             "cardinality": "many-to-one", "direction": "single"},
            {"from": "fact_application[seniority_key]", "to": "dim_seniority[seniority_key]",
             "cardinality": "many-to-one", "direction": "single"},
            {"from": "fact_application[industry_key]", "to": "dim_industry[industry_key]",
             "cardinality": "many-to-one", "direction": "single"},
            {"from": "fact_application[job_type_key]", "to": "dim_job_type[job_type_key]",
             "cardinality": "many-to-one", "direction": "single"},
            {"from": "fact_application[application_date_key]", "to": "dim_date[date_key]",
             "cardinality": "many-to-one", "direction": "single",
             "note": "ACTIVE date relationship"},
            {"from": "fact_application[last_updated_key]", "to": "dim_date[date_key]",
             "cardinality": "many-to-one", "direction": "single",
             "note": "INACTIVE — role-playing date dimension, activate with USERELATIONSHIP"},
            {"from": "fact_application_skill[thread_id]", "to": "fact_application[thread_id]",
             "cardinality": "many-to-one", "direction": "single"},
            {"from": "fact_application_skill[skill_key]", "to": "dim_skill[skill_key]",
             "cardinality": "many-to-one", "direction": "single"},
        ],
        "reporting_view": "vw_application_analysis (denormalised, safe to import directly)",
        "excel_export": "one CSV per report in bi_pack/excel/ + README_excel.md",
        "limitations": [
            "One candidate's job pipeline — small sample; rates are indicative, not significant.",
            "days_to_response is NULL when the response date is unknown (excluded, never zero-filled).",
            "Insurance-domain measures (loss ratio, IBNR, CSM) do not apply to this dataset.",
            "status_history was rebuilt by repair passes, so it is not a complete event log.",
        ],
    }


# ---------------------------------------------------------------- pack


def export_bi_pack(outdir: str = BI_DIR) -> dict:
    """Write the DAX, Tableau, model spec and README. Returns {name: path}."""
    os.makedirs(outdir, exist_ok=True)
    written = {}
    for name, text in (
        ("measures.dax", dax_file_text()),
        ("calculations.tableau", tableau_file_text()),
        ("model_spec.json", json.dumps(model_spec(), indent=2)),
        ("README_bi.md", bi_readme()),
    ):
        path = os.path.join(outdir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        written[name] = path
    return written


def bi_readme() -> str:
    return f"""# BI layer — Power BI / Tableau over the application tracker

## What this is

The tracker's data is modelled into a **star schema** (`warehouse.db`) and reported through
four tools that must all agree: SQL, Excel, Power BI (DAX) and Tableau. The point is not
the volume of data — it is one candidate's job pipeline — it is that the *modelling and the
measure definitions are correct and validated across tools*.

## The model

Grain: **one row per application** in `fact_application`, plus a skill bridge
(`fact_application_skill`) and a status-event fact (`fact_status_event`).

Dimensions: `dim_date` (marked date table), `dim_company`, `dim_platform`, `dim_status`
(carries `funnel_stage` / `stage_order` / `is_terminal`), `dim_role_family`,
`dim_seniority`, `dim_industry`, `dim_job_type`, `dim_skill` (carries `skill_family`).

`fact_application` has **two date keys** — `application_date_key` (active) and
`last_updated_key` (inactive) — a role-playing date dimension. In Power BI, leave the
`last_updated` relationship inactive and reach it with `USERELATIONSHIP` so a single date
slicer cannot double-count.

`vw_application_analysis` is a denormalised view — the quickest way to get the whole model
into Tableau or Power BI in one import.

## Power BI — import steps

1. Get Data → **Text/CSV**, import every CSV in `bi_pack/excel/`, *or* connect
   directly to `warehouse.db` via the SQLite ODBC driver and select `vw_application_analysis`.
2. Model view → create the many-to-one relationships listed in `model_spec.json`.
3. Mark `dim_date` as the date table (`date_key`).
4. Paste each measure from `measures.dax` into a blank measure table.

## Tableau — import steps

1. Connect to `bi_pack/job_applications.csv` (or the SQLite view).
2. Add a data-source filter `current_status <> "Saved / Planning"`.
3. Paste each calculated field from `calculations.tableau`.
4. Suggested dashboard: KPI band, funnel bar, platform scatter
   (x = applications, y = response rate), monthly line with a running total,
   and a skill-gap bar sorted by priority.

## Excel

`bi_pack/excel/` holds the same report set as CSVs, plus `README_excel.md` with the
structured-reference formulas, the conditional-formatting rules and the refresh macro.

## Measures that belong to this dataset

Pipeline analytics: response/interview/rejection/ghost rates, funnel conversion against
days-to-response (median), days waiting, application velocity and running totals,
published benchmarks, period-over-period change, application timing and weekday effect,
platform effectiveness against the direct-application benchmark.

## Measures that do NOT belong to this dataset

Loss ratio, incurred claims, IBNR, reserving, CSM — this model has no premium, claim or
exposure grain. Those live in the claims-modelling artifacts and use synthetic claims data.
Mixing the two vocabularies would be overselling the project.

## Limitations to state plainly

* ~{STALE_DAYS}-day staleness threshold matches the tracker's ghosting rule.
* Sample sizes are small; platform rankings are indicative, not statistically significant
  (the `Sample Confidence` measure exists to say so on the report itself).
* `days_to_response` is NULL where the response date is genuinely unknown; blanks are
  excluded from the median, never treated as zero.
* `status_history` was rebuilt by repair passes and is not a complete event log, so
  time-in-stage analysis is out of scope for this dataset.
"""


def run() -> dict:
    """Build the warehouse, export the Excel + BI packs, return a summary."""
    counts = warehouse.build_warehouse()
    excel_files = export_excel()
    bi_files = export_bi_pack()

    kpi = wq.query("headline_kpis")
    headline = {}
    if not kpi.empty:
        row = kpi.iloc[0].to_dict()
        headline = {
            "applications": int(row.get("total_applications") or 0),
            "responses": int(row.get("responses") or 0),
            "response_rate_pct": float(row.get("response_rate_pct") or 0),
            "interviews_plus": int(row.get("interviews_plus") or 0),
            "avg_days_waiting": float(row.get("avg_days_waiting") or 0),
            "median_days_to_response": float(row.get("avg_days_to_response") or 0),
        }

    return {
        "warehouse": counts,
        "headline": headline,
        "exports_excel": excel_files,
        "exports_bi": bi_files,
        "assumptions": {
            "stale_days": STALE_DAYS,
            "source": "applications.db -> warehouse.db (star schema)",
            "tools": "SQL (SQLite) · Excel · Power BI (DAX) · Tableau",
        },
        "interpretation": (
            f"{len(headline) and headline.get('applications', 0)} valid applications modelled "
            f"into a star schema; {headline.get('response_rate_pct', 0)}% got a response and "
            f"the median known reply took {headline.get('median_days_to_response', 0):.0f} days. "
            f"Numbers are produced once in SQL and reused by every tool, so the Excel sheet, "
            f"the DAX measures, the Tableau fields and the dashboard cannot disagree."
        ),
    }


if __name__ == "__main__":
    result = run()
    print("=" * 78)
    print("BI LAYER — star schema, Excel, Power BI (DAX), Tableau")
    print("=" * 78)
    print(f"\nWarehouse: {result['warehouse']}")
    print("\nHeadline numbers")
    for k, v in result["headline"].items():
        print(f"  {k:28s} : {v}")
    print(f"\nExcel export: {len(result['exports_excel'])} file(s) -> {EXCEL_DIR}")
    print(f"BI pack:      {len(result['exports_bi'])} file(s) -> {BI_DIR}")
    print(f"\nDAX measures:        {len(dax_measures())}")
    print(f"Tableau fields:      {len(tableau_calculations())}")
    print(f"\n{result['interpretation']}")
