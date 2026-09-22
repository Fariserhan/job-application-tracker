# Job Application Suite - agent guide

Gmail sync -> parse/classify -> SQL star-schema warehouse -> one Streamlit tracker with
automatic visualisations + a BI pack (Excel / Power BI DAX / Tableau).

## Removed features — do not reintroduce
- **Job search / discovery** (removed 2026-09-12): no `discovery.py`, no market scanner,
  no `scan_market`. The tracker does not search job boards.
- **CV matching / fit scoring** (removed 2026-09-12): no `matcher.py`, no `cv_profile.txt`,
  no `fit_score` anywhere. `tests/test_dashboard_smoke.py` asserts these stay gone.

## Commands
- Tests: `venv\Scripts\python.exe -m unittest discover -s tests`
- Sync: `main.py`  |  Dashboard: `streamlit run dashboard.py` (or `launch_dashboard.bat`)
- Analytics warehouse: `warehouse.py` (DDL + `build_warehouse()`), `warehouse_etl.py`
  (ETL), `warehouse_queries.py` (the SQL library — CTEs, window functions)
- BI pack: `bi_export.py` -> `bi_pack/job_applications.csv`, `bi_pack/excel/*.csv`,
  `bi_pack/bi/measures.dax`, `bi_pack/bi/calculations.tableau`, `bi_pack/bi/model_spec.json`
- Date repair: `backfill_dates.py` (dry run; `--apply` backs up first)
- Theme: `.streamlit/config.toml` (financial-dashboard preset). Do NOT inject custom CSS.

## Analytics engine (why it exists)
Faris applies for actuarial / insurance-pricing / reserving / data & BI analyst roles in
Malaysia. Those JDs screen for SQL, Excel, Power BI, Tableau and star-schema modelling. So
the tracker's own data is modelled into a real star schema and every number is computed
**once, in SQL**, then reused by the dashboard, the Excel export, the DAX measures and the
Tableau fields. Keep it that way: never recompute an analytic in pandas when a query
already exists, and never let the four surfaces drift.

## Benchmarks are load-bearing
The dashboard compares itself to published hiring data (`warehouse_queries.py`):
response rate bands (<2% / 2-3% / 3-5% / 5%+), the 6.87% direct-application interview
benchmark, and the 100-application funnel (2-3 responses -> 5-10 interviews -> 0-1 offer).
If you change a band, update the DAX/Tableau equivalents in `bi_export.py` too.

Honesty rules — do not break them:
- Insurance measures (loss ratio, IBNR, CSM) do NOT apply to this dataset (no premium or
  claim grain). They must not appear in `bi_export.dax_measures()`.
- `days_to_response` is NULL when the reply date is unknown. Never zero-fill it.
- Platform comparisons are indicative, not statistically significant at this sample size.

## Never read or commit (secrets / personal data)
`credentials.json`, `token.json`, `applications.db`, `job_tracker.csv`,
`sync_state.json`, `.streamlit/secrets.toml`, `backups/`, `*.pdf`.

## Conventions
- The parser is fully deterministic and offline — no LLM/AI layer exists any more.
  Do not reintroduce a network dependency into parsing.
- Python 3.12 in `venv/`; no new deps without asking.
- Warehouse/BI/dashboard code is stdlib + pandas/numpy + plotly only, deterministic.
- Hermetic `unittest` per module (network mocked). Add a test for non-trivial changes.
- Structural code search: prefer `ast-grep`, scoped paths (skip `venv/`).
