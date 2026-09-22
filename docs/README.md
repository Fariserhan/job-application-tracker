# Full documentation

Back to the [project overview](../README.md).

A personal job-application tracker: sync Gmail, filter out the noise, track every
application automatically, and read the search through a set of visualisations benchmarked
against published hiring data.

```bash
launch_dashboard.bat        # or: streamlit run dashboard.py
```

## How it works

1. Open the dashboard and click **Sync Gmail now**.
2. It pulls your application email, filters marketing noise, extracts company/role/status,
   and updates the tracker's status from the most recent message.
3. Every report and chart refreshes from the same warehouse the sync just rebuilt.

There is no CV matching, no scoring, and no job-board scraping — it is one tracker with
automatic visualisations.

## Measured against real hiring data

The dashboard does not just show raw numbers; it places them against published benchmarks
so each one reads as good, average, or a problem:

| Metric | Benchmark used |
|---|---|
| Response rate | <2% below average · 2–3% average · 3–5% good · 5%+ strong |
| Interview rate | 5–10% for well-targeted applications |
| Direct vs job board | 6.87% interview rate applying on a company's careers page vs 1.95% on LinkedIn (1.24M applications tracked) |
| Funnel from 100 applications | 2–3 responses → 5–10 interviews → 0–1 offer |

## The dashboard

**Overview** — a benchmarked KPI strip (response rate with its band, interview rate, still
active, waiting on a reply, median reply time, last-30-days volume), insights, the follow-up
action queue, and recent activity.

**Tracker** — every application as an interactive table: company, role, platform, type,
status, dates, waiting days, Gmail link, plus a detail popup with the job description, status
override, notes and full status history. Filters live in the sidebar.

**Reports** — four tabs, each a different question:
- **Funnel & pace** — where applications stop, against the published funnel benchmark; plus
  monthly velocity with a running total
- **Where it works** — platform effectiveness with the direct-application benchmark line,
  employer repeats, role families
- **Waiting & follow-up** — how long you have been waiting, reply-time distribution, the
  status transition audit trail, application timing by weekday
- **Export & model** — one-file CSV download, data-quality audit, and the warehouse model

## Architecture

| Module | Responsibility |
|---|---|
| `auth.py` | OAuth 2.0 flow, token caching |
| `gmail_fetcher.py` | Gmail search, batched fetches, body extraction |
| `parser.py` | Deterministic extraction + strict anti-noise filter |
| `enricher.py` | OpenGraph job-posting scraper, SSRF-guarded |
| `storage.py` | SQLite operational store, audit trail, backups |
| `warehouse.py` | Star-schema DDL and build entry point |
| `warehouse_etl.py` | ETL from the operational tables into the star schema |
| `warehouse_queries.py` | The SQL library — CTEs, window functions |
| `bi_export.py` | Excel / Power BI (DAX) / Tableau pack |
| `dashboard.py` | The Streamlit app |
| `tests/` | Hermetic unit tests incl. warehouse invariants |

## The SQL / BI layer

The tracker's data is modelled into a star schema — `fact_application` (one row per
application, with a role-playing date key), `fact_status_event`, and nine dimensions — and
every number is computed **once, in SQL**, so the dashboard, the Excel export, the DAX
measures and the Tableau fields cannot disagree.

The analytical work genuinely happens in SQL: `LAG` for stage conversion, `SUM() OVER` for
running totals, `DENSE_RANK` for platform ranking, CTEs and conditional aggregation
throughout.

`bi_export.py` writes the BI pack into `bi_pack/`: one unified CSV, the per-report CSVs,
`measures.dax` (23 measures), `calculations.tableau` (14 calculated fields) and
`model_spec.json`.

## Data-integrity guarantees

- **Status never goes backwards on an older email** — syncs process oldest → newest.
- **`last_updated` only advances on a real change**, so waiting and reply-time metrics mean
  something instead of being structurally zero.
- **An unknown reply date is never zero-filled** — those rows are excluded, not counted as
  same-day replies.
- **Manually recovered rows stay recovered**, and both directions are logged.
- **The warehouse is additive and idempotent** — rebuilding never touches the operational
  tables, and a failed rebuild cannot fail a completed sync.

## Testing

```bash
venv\Scripts\python.exe -m unittest discover -s tests
```

131 hermetic tests: parser, storage invariants, sync engine, repair planner, Gmail
extraction, SSRF guard, the warehouse star-schema invariants (every fact joins every
dimension, the funnel is monotonic, an unknown reply date is never zero-filled), the BI
pack, and a headless dashboard smoke test.

## Setup

1. `pip install -r requirements.txt`
2. Google Cloud: enable the Gmail API, create a Desktop-app OAuth client, save as
   `credentials.json` (scope: `gmail.readonly`). The first sync opens a browser once.

## Limitations (stated deliberately)

- One person's pipeline: ~110 applications over a few weeks. Trends over time are thin, and
  platform comparisons are indicative rather than statistically significant.
- Funnel stages beyond screening are nearly empty in the data (1 interview, 0 offers). The
  dashboard shows that rather than dressing it up.
- Insurance-domain measures (loss ratio, IBNR, reserving) do not apply to this dataset — it
  has no premium or claim grain — and are deliberately absent from the BI pack.
