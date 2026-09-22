# Job Application Tracker

A personal job-application tracker: sync Gmail, filter out the noise, track every
application automatically, and read the search through visualisations benchmarked against
published hiring data.

```bash
launch_dashboard.bat        # or: streamlit run dashboard.py
```

Full documentation: **[docs/README.md](docs/README.md)**

## Layout

```
dashboard.py            the Streamlit app (run this)
main.py                 the sync engine (Gmail -> parse -> warehouse)

storage.py              SQLite operational store
parser.py               email parsing + the anti-noise filter
gmail_fetcher.py        Gmail API search and body fetching
auth.py                 OAuth flow and token caching
enricher.py             job-posting scraper (OpenGraph, SSRF-guarded)
repair.py               audit and repair pass over stored rows

warehouse.py            star-schema DDL + build entry point
warehouse_etl.py        ETL from the operational tables into the star schema
warehouse_queries.py    the SQL library (CTEs, window functions)
bi_export.py            Excel / Power BI (DAX) / Tableau pack

backfill_dates.py       one-off repair for last_updated semantics

tests/                  hermetic unit tests
docs/                   documentation
.streamlit/config.toml  theme + server config
```

Data files (created on first run, never committed):
`applications.db`, `job_tracker.csv`, `sync_state.json`, `token.json`, `credentials.json`,
`backups/`, `bi_pack/`.

## Quick start

1. `pip install -r requirements.txt`
2. Double-click `launch_dashboard.bat`
3. Click **Sync Gmail now**

## Tests

```bash
run_tests.bat
# or: venv\Scripts\python.exe -m unittest discover -s tests
```
