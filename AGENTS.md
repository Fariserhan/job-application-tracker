# Job Search Analytics - agent guide

Gmail -> deterministic parser (optional, budget-capped Claude fallback) -> SQLite operational
store with an append-only status-event log -> dbt models on DuckDB (star schema) -> one SQL
metric layer -> Streamlit app + findings report + Excel / Power BI / Tableau pack.

## Removed features (do not reintroduce)
- **Job search / discovery** (removed 2026-09-12): no `discovery.py`, no market scanner,
  no `scan_market`. The tracker does not search job boards.
- **CV matching / fit scoring** (removed 2026-09-12): no `matcher.py`, no `cv_profile.txt`,
  no `fit_score` anywhere. `tests/test_app_smoke.py` asserts these stay gone.

## Layout
- `jobtracker/`: the package. `parsing/` (rules, classifier, extraction, `llm.py` fallback,
  `evaluate.py`), `store/` (SQLite schema + migrations + `Repository`), `gmail/`, `sync.py`,
  `enrich.py`, `repair.py`, `warehouse/` (load, dbt-subset `runner.py`, `metrics.py`),
  `bi/` (Excel / DAX / Tableau), `report.py`, `demo/` (synthetic mailbox), `cli.py`.
- `dbt/`: the warehouse models (staging -> intermediate -> marts), seeds, data tests.
- `app/`: the Streamlit app (`app/streamlit_app.py`), `app/assets/style.css`.
- `tests/`: hermetic pytest suite; `tests/fixtures/email_corpus.jsonl` is the labelled
  parser evaluation corpus.
- Data lives in `data/` (or `JOBTRACKER_DATA_DIR`); a v1 checkout with `applications.db`
  in the project root keeps working until the files are moved.

## Commands (Windows: `venv\Scripts\python.exe`, elsewhere `python`)
- Tests: `python -m pytest` · Lint: `ruff check .` · Types: `mypy`
- App: `streamlit run app/streamlit_app.py` (or `launch_app.bat`)
- CLI: `python -m jobtracker sync|build|demo|show|check|repair|reparse|export|report|eval|backup`
- dbt (dev/CI only): `cd dbt && dbt build --profiles-dir .` with `JOBTRACKER_WAREHOUSE` set
- Parser gate: `python -m jobtracker eval` (CI fails below the F1 / status thresholds)

## Analytics engine (why it exists)
Faris applies for actuarial / insurance-pricing / reserving / data & BI analyst roles in
Malaysia. Those JDs screen for SQL, Excel, Power BI, Tableau and star-schema modelling. So
the tracker's own data is modelled into a real star schema and every number is computed
**once, in SQL** (`jobtracker/warehouse/metrics.py` over the dbt marts), then reused by the
app, the report, the Excel workbook, the DAX measures and the Tableau fields. Keep it that
way: never recompute an analytic in pandas when a metric query exists, and never let the
surfaces drift. The built-in runner executes only `ref` / `source` / `config` Jinja, so keep
dbt models inside that subset (CI runs real `dbt build` on the same files).

## Benchmarks have one source
`jobtracker/benchmarks.py` is the single source: response-rate bands (<2% / 2-3% / 3-5% /
5%+), the 6.87% direct-application interview benchmark (1.95% job boards), the
100-application funnel (5-10 interviews -> 0-1 offer; no "any reply" range, because v1's
2-3 was fewer than the interviews, so it was dropped) and the sample-size
thresholds. The SQL, DAX, Tableau and Excel layers are generated from it. Change a band
there and nowhere else.

Data rules (do not break them):
- Insurance measures (loss ratio, IBNR, CSM) do NOT apply to this dataset (no premium or
  claim grain). They must not appear in the BI pack.
- `days_to_response` is NULL when the reply date is unknown. Never zero-fill it.
- Funnel stages use the furthest stage ever reached (`max_stage_reached`), not the
  current status.
- Platform comparisons are indicative: rates carry Wilson 95% intervals and sample-size
  labels; time-to-reply uses Kaplan–Meier (waiting applications are censored, not dropped).
- The demo dataset is synthetic and must always be labelled as such.

## Never read or commit (secrets / personal data)
`credentials.json`, `token.json`, `applications.db`, `warehouse.duckdb`, `data/`,
`job_tracker.csv`, `sync_state.json`, `settings.json`, `.streamlit/secrets.toml`,
`backups/`, `exports/`, `*.pdf`.

## Conventions
- The parser is deterministic and offline. The Claude fallback (`parsing/llm.py`) is off
  by default, only sees mail the rules mark ambiguous, caches every answer by message id,
  and stops at the monthly budget. Never make it required.
- Status changes go through `Repository` so they land in `status_events` dated by the
  source email; never UPDATE `current_status` directly.
- Python 3.11+; no new runtime deps without asking (`requirements.txt`).
- UI: theme in `.streamlit/config.toml`; custom styling only in the single scoped
  `app/assets/style.css` design system (tokens + components). No inline CSS elsewhere.
  The stylesheet must not contain `<` (Streamlit's HTML sanitiser drops the whole block;
  `load_css()` raises). Colours are pinned in `app/ui/theme.py` and mirrored in
  `jobtracker/bi/style.py`. Change both together. Motion respects `prefers-reduced-motion`.
- Hermetic pytest per module (network mocked, data in temp dirs). Add a test for
  non-trivial changes. Structural code search: prefer `ast-grep`, scoped paths.
