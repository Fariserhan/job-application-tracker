# Setup

## 1. Install (Windows)

```bat
py -3.12 -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
```

macOS / Linux: `python3 -m venv venv && venv/bin/pip install -r requirements.txt`.

Try it without Gmail first. The demo builds a synthetic inbox and runs it through the real
pipeline:

```bat
venv\Scripts\python.exe -m jobtracker demo
set JOBTRACKER_DATA_DIR=demo_data
venv\Scripts\python.exe -m streamlit run app\streamlit_app.py
```

## 2. Connect Gmail (read-only)

1. Google Cloud Console → create a project → **APIs & Services → Library** → enable the
   **Gmail API**.
2. **OAuth consent screen** → External → add yourself as a test user. Scope:
   `https://www.googleapis.com/auth/gmail.readonly` (the tracker never sends, labels or
   deletes mail).
3. **Credentials → Create credentials → OAuth client ID → Desktop app** → download the
   JSON and save it as `data\credentials.json`.
4. Run the first sync. A browser window asks you to approve access once; the token is cached
   in `data\token.json` and refreshed automatically.

```bat
venv\Scripts\python.exe -m jobtracker sync
```

Or click **Sync Gmail** in the app (`launch_app.bat`).

## 3. Upgrading from v1 / getting updates

**Windows, one step:** open the project folder, click the address bar, type `cmd`, press
Enter, then paste this line and press Enter:

```bat
git pull origin master && update.bat
```

From then on, just double-click **`update.bat`** whenever you want the latest version. It
downloads it, rebuilds the Python environment if it is too old, installs the requirements,
syncs Gmail and opens the app. Your data is untouched. The first run re-reads the whole
mailbox once so every email is judged by the current parser (a few minutes); later runs only
fetch new mail.

Data needs nothing. If `applications.db` is still in the project root, the app keeps using the
root as its data directory. On first start the schema is migrated in place to v2, after an
automatic backup in `backups\<timestamp>-pre-v2-migration\`. Your notes, manual
corrections and status history are preserved. To move to the new layout, move
`applications.db`, `sync_state.json`, `credentials.json` and `token.json` into `data\`.

After migrating, run **Settings → Full re-parse** once (or `jobtracker sync --full`) so every
email is re-judged by the current rules and the status-event log is rebuilt with real email
dates.

### Sync automatically (optional)

Double-click **`auto_sync.bat`** once. Windows then reads your new job emails every morning
at 9:00 and each time you sign in, with no window popping up, so the tracker is already up
to date when you open it. Each run adds one line to `sync.log` in your data folder. Run
`auto_sync.bat off` to stop it. If Google ever asks you to sign in again, the background run
skips (it never opens a browser) until you click **Sync Gmail** in the app once.

## 4. Optional: Claude fallback for ambiguous emails

Off by default. When on, only emails the rules mark as ambiguous are sent (subject, sender
and ~1,500 trimmed characters), each at most once, under a hard monthly budget.

```bat
venv\Scripts\python.exe -m pip install anthropic
set ANTHROPIC_API_KEY=sk-ant-...
```

Then enable it in **Settings** (model defaults to `claude-haiku-5-5`, budget to US$1/month),
or set `JOBTRACKER_LLM=1`. Spend is tracked per call in the `llm_calls` table and shown on the
Settings page.

## 5. Configuration

Settings live in `data\settings.json` (edited from the app). Environment variables override:

| Variable | Default | Meaning |
|---|---|---|
| `JOBTRACKER_DATA_DIR` | `data` | where the database, warehouse, backups and exports live |
| `JOBTRACKER_GHOST_AFTER_DAYS` | 21 | silence before an Applied row becomes Ghosted |
| `JOBTRACKER_FOLLOW_UP_AFTER_DAYS` | 7 | silence before a row enters the follow-up queue |
| `GMAIL_AFTER` | 2026/01/01 | ignore mail before this date |
| `GMAIL_MAX_RESULTS` | 2000 | cap per sync |
| `JOBTRACKER_LLM` | off | enable the Claude fallback |
| `JOBTRACKER_LLM_MODEL` | claude-haiku-5-5 | fallback model |
| `JOBTRACKER_LLM_BUDGET_USD` | 1.00 | monthly spend cap |
| `JOBTRACKER_ENRICH` | on | read job-posting pages for title / description / location |
| `JOBTRACKER_DEMO` | off | force demo mode in the app |

## 6. Deploy the public demo (Streamlit Community Cloud)

1. Push the repository to GitHub (public).
2. [share.streamlit.io](https://share.streamlit.io) → **Create app** → pick the repo, branch
   `master`, main file `app/streamlit_app.py`.
3. Advanced settings → environment variable `JOBTRACKER_DEMO=1` (the app also falls back to
   demo mode automatically when it finds no database).

The hosted app only ever sees the synthetic demo data; your real database stays on your PC.
Put the URL in the README badge and on your CV.

## 7. Publish the dbt docs (lineage site)

Repository **Settings → Pages → Source: GitHub Actions**. The `Publish dbt docs` workflow
then deploys the dbt documentation site (model lineage, column docs, tests) on every push to
`master`.
