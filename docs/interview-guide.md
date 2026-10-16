# Talking about this project in interviews

A crib sheet for data analyst / BI / actuarial analyst interviews. Each story is framed as
situation → what I did → result, with the trade-off I'd expect a follow-up question on.

## The 30-second version

> "I was applying to a lot of analyst roles and wanted to manage the search like a data
> problem. I built a pipeline that reads my job-application emails from Gmail, classifies
> them with a rule engine I evaluated at a measured F1 against a labelled test set, models
> them into a star schema with dbt on DuckDB, and serves one SQL metric layer to a
> Streamlit app, an Excel workbook and Power BI / Tableau measures. It showed me which
> channels actually get replies — with confidence intervals, because one person's sample is
> small — and how long employers really take to respond, using survival analysis."

## Stories

**1. Modelling — "one metric, defined once".**
Every number is a SQL statement over the marts; the app, the Excel formulas, the DAX
measures and the Tableau fields are generated from the same definitions and a test checks
each against the SQL result. *Follow-up to expect:* why not compute in pandas? → drift
between surfaces is the classic BI failure; this makes it impossible by construction.

**2. Finding and fixing a metric bug.**
The first version counted the funnel on *current* status, so an application rejected after
an interview counted as "Applied" — Assessment → Interview read 100% and real interviews
disappeared. I rebuilt status as an append-only event log dated by the source email and
moved the funnel to the furthest stage ever reached. *Trade-off:* migrated history only had
sync-time dates, so those events are kept but flagged low-trust and excluded from timing.

**3. Statistics that respect a small sample.**
Platform response rates carry Wilson 95% intervals and sample-size labels; the report only
calls a gap a finding when the intervals don't overlap. Time to reply uses Kaplan–Meier
survival so applications still waiting are censored, not dropped — dropping them makes
employers look faster than they are. *Expect:* "why Wilson not normal approximation?" →
the normal interval breaks at small n and near 0%/100% (it can go negative).

**4. Data quality as a feature.**
Unknown reply dates stay NULL (a dbt test enforces it), the warehouse audits its own
coverage (employer identified, reply date known…), and 50 dbt tests run in CI.

**5. Measuring a classifier instead of trusting it.**
The email parser is a deterministic rule engine with a labelled evaluation corpus —
precision / recall / F1 and a status confusion matrix — and CI fails if accuracy drops.
Ambiguous cases can optionally go to Claude Haiku: rules first, one cached call per email,
hard monthly budget, ~US$0.0001 per email. *Expect:* "why not just use an LLM?" → cost,
privacy (clear-cut mail never leaves the machine), determinism and testability.

**6. Engineering hygiene.**
Versioned schema migrations with automatic backups, atomic warehouse swaps, an SSRF-guarded
scraper (resolves DNS and re-validates every redirect), quota-aware batched Gmail fetching,
hermetic tests on Linux and Windows, typed and linted code.

## Numbers to quote (fill in from your own report)

Run `python -m jobtracker report` on your real data and take the headline numbers from
`data/exports/job-search-report.md`: applications sent, response rate (with CI), interview
rate, median days to first reply, best channel. Quote your own results — never the demo's.

## Skills map (for the CV bullet points)

| JD keyword | Where it shows |
|---|---|
| SQL (CTEs, window functions) | `jobtracker/warehouse/metrics.py`, `dbt/models/` |
| Data modelling / star schema | `dbt/models/marts/`, `docs/architecture.md` |
| dbt, data testing | `dbt/`, 50 tests, CI `dbt build` |
| Excel (formulas, tables, charts) | `python -m jobtracker export` → workbook |
| Power BI (DAX, data model) | `docs/bi/power-bi-guide.md`, `measures.dax` |
| Tableau | `docs/bi/tableau-guide.md` |
| Python (pandas, testing) | `jobtracker/`, `tests/` |
| Statistics | Wilson intervals, Kaplan–Meier, cohort analysis |
| Data quality / governance | NULL rules, data tests, self-audit, migrations with backups |

### Example CV bullets

- Built an end-to-end job-search analytics pipeline (Python, SQL, dbt, DuckDB, Streamlit)
  that ingests Gmail, classifies emails with a rule engine evaluated at *F1 = X* on a
  labelled set, and models the data into a tested star schema (50 dbt data tests in CI).
- Defined every KPI once in SQL and delivered it consistently to a web app, an Excel
  workbook and Power BI / Tableau measures; applied Wilson intervals and Kaplan–Meier
  survival analysis to avoid over-reading a small sample.
