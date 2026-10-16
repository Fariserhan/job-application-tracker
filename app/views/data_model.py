"""Data & model: under the hood: architecture, real dbt lineage, star schema, data tests,
coverage, the parser's audit trail, the metric SQL itself, and the BI pack."""
from __future__ import annotations

import json

import streamlit as st

from app.ui import charts, data, lineage
from app.ui import components as ui
from jobtracker.warehouse.metrics import SQL, UNFILTERED, Metrics

manifest = data.manifest()
graph = lineage.discover()
tests = manifest.get("tests", {}) or {}
failing = sum(1 for v in tests.values() if v)
counts = manifest.get("counts", {}) or {}

ui.lede("Data & model", [
    ("Every number is computed once, in SQL", "em"),
    (f": {len(graph.models)} dbt models, {len(tests)} data tests, "
     f"{'all passing' if not failing else f'{failing} failing'}.", ""),
], "For the technically curious. How a Gmail message becomes a number in this app: a deterministic parser, an "
   "event-sourced store, dbt models over a DuckDB star schema, and one metric layer that the "
   "app, the Excel workbook, Power BI and Tableau all read from, so the four can never drift.")

ui.flow([
    ("Gmail", "Read-only API; batched, quota-aware fetches with an adaptive pacer."),
    ("Parser", "Deterministic rules filter noise and extract employer, role and status. "
               "Optional Claude review only for ambiguous mail."),
    ("Store", "SQLite. One row per application; every status change is an append-only event."),
    ("dbt models", f"{len(graph.models)} models and {len(tests)} singular data tests over "
                   "the raw extract."),
    ("Star schema", "DuckDB: fct_applications, fct_status_events and their dimensions."),
    ("Four surfaces", "This app, the Excel workbook, Power BI (DAX) and Tableau: one "
                      "metric definition each."),
])

# ---------------------------------------------------------------- lineage
with ui.section(1, "Lineage",
                f"{len(graph.sources)} raw tables become {len(graph.models)} models",
                "Read live from the dbt project by the same parser that executes it, so this "
                "diagram is the pipeline. Grey: raw sources and seeds · outlined: staging and "
                "dimensions · solid blue: facts."):
    st.graphviz_chart(lineage.lineage_dot(graph), width="stretch")
    ui.source(f"dbt/models · {len(graph.seeds)} seeds · built "
              f"{ui.esc(str(manifest.get('built_at', '-')))} in {manifest.get('seconds', '-')} s.")

# ---------------------------------------------------------------- schema + tests
with ui.section(2, "Model & tests",
                "A star schema, checked on every build",
                "One fact row per application, joined to conformed dimensions; status history "
                "lives in its own event fact. Each build runs the data tests before it replaces "
                "the live warehouse."):
    left, right = st.columns([7, 5], gap="large")
    with left:
        st.graphviz_chart(lineage.star_dot(data.columns()), width="stretch")
        ui.source("Grain: fct_applications = one application · fct_status_events = one status "
                  "change. Keys in bold.")
    with right:
        ui.label("Data tests (failing rows)")
        ui.test_results(tests, {
            "assert_dim_date_is_contiguous": "The date dimension has no gaps.",
            "assert_funnel_is_monotonic": "Each funnel stage holds no more than the one before.",
            "assert_no_negative_durations": "No reply arrives before its application.",
            "assert_response_implies_response_flag": "Every dated reply is flagged as a response.",
            "assert_unknown_response_dates_stay_null": "Unknown reply dates stay NULL, never zero.",
        })
        ui.label("Row counts")
        ui.compare_table([(name, f"{int(n):,}", "", "", "") for name, n in counts.items()],
                         head=("Table", "Rows", "", ""))

# ---------------------------------------------------------------- quality + parser
quality = data.metric("data_quality")
parser = data.unfiltered("parser_quality")
inspected = int(parser["messages"].sum()) if not parser.empty else 0
kept = int(parser.loc[parser["is_application_mail"], "messages"].sum()) if not parser.empty else 0
with ui.section(3, "Quality",
                f"{inspected:,} emails inspected; {kept:,} kept as application mail",
                "The report audits itself: how complete each attribute the analysis depends on "
                "is, and how the noise filter judged every email it saw."):
    left, right = st.columns([5, 7], gap="large")
    with left:
        ui.label("Coverage of the filtered applications")
        ui.meters((str(r.check_name), int(r.passing), int(r.total))
                  for r in quality.itertuples(index=False))
    with right:
        ui.label("Noise-filter verdicts (blue = kept)")
        charts.show(charts.parser_verdicts(parser), key="h-parser")
        ui.source("mart_parser_quality · blacklist tiers are hard rules; 'noise' is a scored "
                  "verdict; 'affirmed' means the email proves an application.")

# ---------------------------------------------------------------- metric SQL
NAMES = [n for n in SQL if n != "segment_performance"] + ["segment_performance", *UNFILTERED]
with ui.section(4, "Metric layer",
                "Read the SQL behind any number",
                "Each metric is one statement over the marts, composed with the same filtered "
                "base CTE: CTEs, window functions, Wilson intervals and Kaplan–Meier in plain "
                "SQL. Pick one to see it and its live result under the current filters."):
    pick = st.selectbox("Metric", NAMES, index=NAMES.index("funnel"), key="sql_pick")
    left, right = st.columns([7, 5], gap="large")
    with left:
        st.code(Metrics.sql(pick, segment="role_family"), language="sql", height=420)
    with right:
        ui.label("Result")
        result = (data.metric(pick, segment="role_family") if pick not in UNFILTERED
                  else data.unfiltered(pick))
        st.dataframe(result, hide_index=True, height=420)

# ---------------------------------------------------------------- BI pack
with ui.section(5, "BI pack",
                "The same numbers in Excel, Power BI and Tableau",
                "Generated from the same metric definitions and benchmarks, with the same "
                "palette, so the workbook, the DAX measures and the Tableau fields always "
                "agree with this page."):
    try:
        from jobtracker import bi
    except Exception as exc:  # the pack is optional for the app
        st.info(f"The BI pack is not available in this build ({type(exc).__name__}).")
    else:
        s = data.settings()
        cols = st.columns(3, gap="large")
        with cols[0]:
            ui.subhead("Excel workbook", "KPI sheet, pivots and formulas that recalculate.")
            try:
                book = data.workbook()
            except Exception as exc:
                st.warning(f"Workbook unavailable: {type(exc).__name__}: {exc}")
            else:
                st.download_button("Download .xlsx", book, file_name="job-search-analytics.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument."
                                        "spreadsheetml.sheet", icon=":material/download:")
        with cols[1]:
            ui.subhead("Power BI", "DAX measures, model spec and a matching report theme.")
            measures = bi.dax_measures(s.follow_up_after_days)
            dax_text = "\n\n".join(f"{name} =\n{expr}" for name, expr in measures.items())
            st.download_button("Download measures.dax", dax_text, file_name="measures.dax",
                               icon=":material/download:")
            st.download_button("Download theme.json", json.dumps(bi.powerbi_theme(), indent=2),
                               file_name="jobtracker-theme.json", icon=":material/download:",
                               type="tertiary")
        with cols[2]:
            ui.subhead("Tableau", "Calculated fields against the flat reporting view.")
            calcs = bi.tableau_calculations()
            calc_text = "\n\n".join(f"// {name}\n{expr}" for name, expr in calcs.items())
            st.download_button("Download calculations", calc_text,
                               file_name="calculations.tableau.txt", icon=":material/download:")
        with st.expander(f"Preview the {len(measures)} DAX measures"):
            st.code(dax_text, language="sql", height=360)
        with st.expander(f"Preview the {len(calcs)} Tableau calculations"):
            st.code(calc_text, language="sql", height=360)
