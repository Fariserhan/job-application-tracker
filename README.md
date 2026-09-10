# Job Application Suite (Malaysia / Regional)

All-in-one Streamlit web suite for job applications: **Gmail sync, strict anti-noise
filtering, CV Fit Analyzer, and Live Malaysian Job Market Discovery** — daily operation
is 100% browser-based, zero terminal commands required.

```bash
streamlit run dashboard.py
```

That is the only command you ever need. Everything else (Gmail sync, parsing,
enrichment, fit scoring, market scans) runs from the sidebar UI.

## Architecture

| Module | Responsibility |
|---|---|
| `auth.py` | OAuth 2.0 flow, token caching/refresh (`token.json`) — paths resolved next to the code, corrupt tokens self-heal, atomic token writes |
| `gmail_fetcher.py` | Gmail search, `new_batch_http_request(50)` batch fetches, thread deep links, full-body extraction that merges **all** text parts and appends HTML `href`s (so job links hidden inside buttons reach the parser) |
| `parser.py` | Deterministic extraction + **strict 2-stage filter**: sponsor blacklist → positive-affirmation gate |
| `enricher.py` | ThreadPoolExecutor(16), hard 4s timeout per URL, OpenGraph **+ schema.org JSON-LD JobPosting** scraper (title/company/description/salary/location) — attribute-order independent, SSRF-guarded, login walls degrade gracefully |
| `matcher.py` | **CV Fit Analyzer** — fit score 0–100%, matched/missing skills, actionable resume improvements (18+ in-demand skill families incl. Git, Airflow, Docker/K8s, Bloomberg, SAP, warehousing); precompiled lexicons |
| `discovery.py` | **Live Market Discovery** — keyless public feeds: LinkedIn guest API (fixed card parsing → real job URLs), JobStreet v5 JSON, Hiredly (best-effort), Remotive; thread-local HTTP sessions |
| `storage.py` | SQLite (WAL + busy-timeout, in-place additive migration), fit-score persistence with CV signatures, discovered-jobs cache, CSV export, **one-click backups**, `status_history` audit + activity feed |
| `ai.py` | **Free, zero-cost AI analysis** — local Ollama models (or a free-tier OpenAI-compatible endpoint). Disk-cached, defensive (falls back to deterministic logic), never breaks the app |
| `main.py` | Callable sync engine (`run()`/`scan_market()`) + optional CLI flags (`--backup`, `--check`) |
| `dashboard.py` | The web suite: one-click sync, 2 tabs, KPIs, gap analysis, status override |
| `tests/` | Hermetic unit tests + a headless `AppTest` dashboard smoke test (`run_tests.bat`) |

## Data-integrity guarantees

- **Status never goes backwards on an older email** — sync processes messages oldest → newest,
  and `upsert_application` only advances a thread's status when the incoming email is at least
  as recent as the stored row.
- **Manually recovered rows stay recovered** — once you tick *Recover* in the noise table, later
  filtered emails can't push the row back into the noise pile (the `manual recovery` marker wins),
  and recoveries are logged to the activity feed.
- **Already-processed bodies are not re-judged from subject alone** — incremental/full re-syncs
  that skip a stored body reuse the stored validity verdict instead of invalidating the row from
  a metadata-only parse.
- **Real employers are never overwritten by platform names** — a later email from *JobStreet /
  MyWorkday / myHR / LinkedIn* can't replace the actual company or a good role title.
- **Fit scores carry a CV signature** (`fit_cv_sig`) — AI scores are only recomputed when the CV
  actually changes, never on every sync.
- **Atomic writes** for `sync_state.json`, `token.json` and `cv_profile.txt`; a corrupt state or
  token file self-heals instead of blocking the app.
- **CSV export can never fail a sync** — the export selects documented columns and a locked CSV
  (e.g. open in Excel) is reported instead of crashing after the sync work is done.

## In-dashboard one-click sync (sidebar)

1. **[▶ Sync Gmail Now]** — runs the full pipeline with live `st.status()`/`st.progress()`:
   1. Querying Gmail API (count found)
   2. Batch fetching metadata (50/batch)
   3. Filtering noise & parsing applications
   4. Scraping job links concurrently (ThreadPoolExecutor)
   5. Running CV fit evaluations
   6. Done in X.Xs → `st.cache_data.clear()` + `st.rerun()`
2. **[ ] Force Full Re-sync** — ignore the incremental watermark, re-process the mailbox
   (use after changing the CV profile or filter rules).
3. **[ ] Show Filtered Out Noise / Spam** (default OFF) — inspect exactly why each row
   was dropped (`filter_reason` column).

## CV Fit Analyzer (`matcher.py`)

- **Curated CV** (`cv_profile.txt`): Faris Erhan — BSc (Hons) Mathematics & Statistics
  (Warwick), Python/Pandas/NumPy/Scikit-learn, R GLMs, SQL, Advanced Excel/VBA,
  financial mathematics & probability, SOA Exam P candidate. Edit or upload it in the
  sidebar drawer; skill ownership is driven by what the CV actually mentions, and saves
  are atomic.
- **Five-pillar fit score** (0–100%): skills coverage 40% · title alignment 20% ·
  qualification match 15% · seniority fit 15% · location 10%.
- **Seniority caps**: fresh-grad profile → VP/Manager roles capped at 55%, Senior/Lead
  at 70%, and "X+ years" asks capped at 60–72% — a skill-perfect VP posting can no
  longer score 98%.
- Per application: `fit_score`, `matched_skills`, `missing_skills`
  (Power BI, SAS, Tableau, Prophet, IFRS 17 …), 2–3 `actionable_improvements`.
  All persisted to SQLite with `fit_source` (`rule`/`ai`) and `fit_cv_sig`.

## Sync speed

- **Adaptive rate limiter** — pacing derives from Gmail's 250 units/s budget, decays
  ~5% per clean request, backs off instantly on 403/429 (replaces the old fixed 1.5s
  pacing + minute-long exponential retry loops).
- **Parallel batch lanes** (default 3, `GMAIL_PARALLEL_BATCHES` to tune) — Gmail
  executes batch items serially server-side, so concurrent lanes on independent HTTP
  connections cut wall time ~3x; SSL/connection faults auto-retry and fall back to
  sequential.
- **Processed-message cache** (`processed_messages` table) — bodies are fetched once ever and
  cached locally (8 KB each), so even *full* re-syncs skip all previously fetched bodies while
  still being able to deep re-parse them offline.
- Measured on this machine: full 500-message re-sync **580s → ~200s**, daily
  incremental sync **~6s**.

## Strict anti-sponsored / anti-noise filter (`parser.py`)

A message is `is_valid = 1` **only** if it passes both stages:

1. **Blacklist (immediate drop)** — subject/sender matches: *sponsored, job alert,
   jobs you may like, recommended for you, daily/weekly digest, top picks for you,
   similar jobs, join our talent network, new opportunities matching, quora, apply to,
   invitation to apply, jobs picked for you, new jobs for you, career fair, webinar,
   salary report* and **account housekeeping** (*verify your email/account/candidate,
   confirm your identity, activate your account, one-time code, new device*) plus
   **platform digests / portal task notices** (*new activity in jobs you applied for,
   a task awaits you, pending tasks, workfeed*) — a task is not an application.
2. **Positive affirmation** — must confirm an action taken (thank you for applying,
   application received/submitted/sent, *application update for* (JobStreet), *candidate
   home*, resume approved, pending assessment tasks …) or a direct recruitment response
   (invitation to interview, online assessment, regret to inform, not moving forward,
   unsuccessful, not shortlisted, "unlikely your application will progress further",
   contextual "offer").
   - The generic *"thanks for …"* pattern is **application-specific**: promotional
     "Thank you for subscribing / for your purchase" is not affirmed.
3. **Status precision** — interview/assessment/rejection detection needs definite wording:
   hypotheticals ("if shortlisted we may invite you for an interview") do **not** advance a
   confirmation past *Applied*; *"unfortunately"* only counts as a rejection with explicit
   rejection context; bare *"assessment"* no longer marks OA. Assessment vendors
   (HackerRank, Codility, HireVue, TestGorilla, SHL …) and rejection close-outs
   (*no longer under consideration*, *position filled*, *keep on file*) are recognised.
4. **Rejection templates** — JobStreet/ATS phrases like *"unlikely your application will
   progress further"* and *"was not successful"* classify as **Rejected**, not Applied.
5. **Manual correction both ways** — the noise table's *Recover* checkbox promotes a false
   negative back into the tracker, and the detail pop-up's *🚫 Mark as noise* removes a
   false positive. Both are **sticky**: Gmail re-syncs cannot flip a manual verdict back,
   and both directions are logged to the activity feed.

## Email ingestion accuracy (recall & precision)

The Gmail pipeline is built so that real application mail is not missed and noise is not
counted as an application:

- **Three-signal search that cannot silently truncate** — Gmail is searched for
  (1) application sender domains (job boards + ~50 ATS/assessment platforms),
  (2) application subject wording, and (3) full-text body phrases such as *"thank you for
  applying"* / *"we regret to inform"*. Senders and text run as **two complementary
  queries whose results are unioned**, so no single query hits Gmail's search-length limit
  and neither signal can crowd the other out. Spam is included (`in:anywhere -in:trash`)
  because confirmations are occasionally misfiled there; the filter still removes noise.
  The scan cap is 2000 messages (`GMAIL_MAX_RESULTS`) and the UI warns if it is reached.
- **Watermark overlap** — syncs re-query two days behind the last watermark, so a transient
  metadata/body fetch failure can never fall permanently below the sync horizon.
- **Local body cache + deep re-parse** — every fetched body is stored (8 KB cap) in
  `processed_messages`. *Force Full Re-sync* re-parses all cached emails with the current
  rules **without re-downloading**, re-fetches any legacy body that has no cache yet, and
  re-evaluates metadata-only noise rows too (except hard blacklist). Improving the filter
  therefore recovers old false negatives on the next full re-sync.
- **Thread-level validity (OR)** — a thread counts as an application if *any* of its
  messages is a confirmation/response, so a later marketing-looking message can never erase
  a real application; conversely a newly recognised confirmation recovers an old miss.
- **Quoted replies are stripped** — classification only reads the new message, so an old
  quoted "we regret to inform…" cannot leak into a follow-up's verdict.
- **Body-derived company/role** — when the subject is generic ("Your application was
  successfully submitted"), the body is parsed for *"position of X at Y"*, *"thank you for
  your interest in Y"*, etc., and platform names (Workday, myHR, JobStreet, Lever, …) are
  replaced with the real employer; implausible captures (role words without company markers)
  are rejected.
- **Thread-aware AI classification** — when AI classification is enabled, each **message**
  is classified individually (unique ids) and verdicts are merged per thread (newest wins,
  validity OR-ed), instead of collapsing a thread's messages into one confusing request.
- **AI fit scores are truly cached** — deterministic fallback only applies to rows the AI
  was asked about, so a re-sync can no longer overwrite persisted AI scores with rule
  scores.
- **Stored-data repair & missed-application audit** — *🔧 Repair & recover (audit)* in
  🛠 Maintenance (or `python repair.py --apply`) re-judges every stored row with the current
  rules: marks verification/digest false positives as noise, repairs company/role names from
  the stored subject/JD, re-derives job type / seniority / industry, and recovers real
  applications that were filtered out (for example "we would like to acknowledge receipt of
  your application"). It backs up first, is idempotent (re-running plans 0 changes), and
  writes a per-row `application_audit.csv`.

## 🤖 Free AI analysis (zero cost)

Instead of the deterministic insight rules, the dashboard can generate **AI analysis and
follow-up emails** — using only **free models**, so it costs nothing.

- **Provider picker (sidebar → 🤖 Free AI Analysis):** choose from curated free providers and
  paste a free API key (kept in the session only, never written to disk):
  - **Google Gemini (free)** — default; the most stable free tier (key: aistudio.google.com/apikey).
    Default model `gemini-3.6-flash`; the model dropdown auto-fetches the models actually
    available on your account, so retired model names can't silently break it
  - **Groq (free)** — fastest, tighter rate limits · **OpenRouter (free)** — many `:free` models
  - **Cerebras (free)** · **Ollama (local)** — fully private, no key · **Auto** — uses `OPENAI_*`
    env vars or a local Ollama
- **What it powers** (every section is **on-demand via a 🤖 Generate button** — nothing runs
  automatically, so you always stay inside free-tier rate limits; results are disk-cached):
  - 🧠 **Insights & Recommendations** — AI narrative from your real numbers
  - ✍️ **Follow-up emails** — tailored drafts per company/role/status (batched, cached)
  - 🧬 **CV Gap Report** — AI-prioritized action plan · **fit-score explanations** in the
    detail pop-up ("why this score, how to improve")
  - 🌐 **Live-market read** · 🏢 **employer strategy** · 📜 **activity momentum summary**
  - Each falls back to the deterministic version automatically if AI is off, unreachable, or
    returns nothing usable.
- **Guarantees:** all scores/numbers are still computed in Python (AI only writes prose);
  responses are **disk-cached** (`ai_cache.json`) so re-renders and restarts cost nothing;
  a short connect timeout (2s) + 20s generation timeout (`AI_TIMEOUT` to tune, capped at 60s)
  means AI can never hang or break the app; if no key/model is configured it silently uses the
  deterministic logic. Free tiers are rate-limited, which caching absorbs. `AI_DISABLE=1`
  forces deterministic mode.

- **AI fit scores (sync)** — during sync/backfill the CV fit score is computed by the AI in
  batches and persisted with `fit_source='ai'` + a `fit_cv_sig` fingerprint, so it is only
  **recomputed when the CV actually changes** (deterministic scoring stays as the offline
  fallback and for any AI failures). AI verdicts are sanitised (scores clamped to 0–100,
  malformed lists dropped) before they touch the DB.
- **AI email classification (sync)** — during sync the AI decides whether each email is a real
  application and advances its status/company/role using **thread memory** (the row's current
  state), recovering applications the deterministic filter missed. Verdicts are stored with
  `ai_classified=1`, so incremental syncs never re-run them (a full re-sync re-checks, mostly
  via disk cache).
- **Gmail window** — syncs only pull mail from `2026/01/01` onwards (override with `GMAIL_AFTER`)
  to cut volume.

## Tab 1 — My Applications

- KPI cards: Total Valid · Interview Rate · Rejection Rate · Auto-Ghosted (>21d stale) · Avg CV Fit ·
  Active Pipelines · Response Rate · Median Response · Last 30 Days (with delta vs the previous 30)
- **📬 Follow-up queue** — Applied-but-quiet roles and ghosted threads, each with a
  one-click status action (Ghost / re-open / Reject) and a **copy-paste follow-up email draft**
  (Gmail thread + posting links inline).
- **📜 Recent activity** — a live timeline of every status change (manual overrides, Gmail-driven
  updates, auto-ghosting) plus recent application updates.
- **🏢 Employers applied-to-multiple-times** — awareness + consolidation view.
- **🗂 Saved / Planning manager** — roles added from Live Matches are kept out of the KPIs until
  you actually apply; **Mark Applied** stamps today as the application date and moves them into
  the funnel (a **Remove** action deletes them cleanly).
- Per-application **🗒 notes** + **📜 status history** timeline + **↩ Undo last status change** in
  the detail pop-up (persisted in `applications.notes` / `status_history`).
- Pipeline funnel (Applied → Assessment/OA → Interview → Offer) with stage→stage conversion %,
  Application velocity (weekly/monthly, stacked by status)
- Platform donut (LinkedIn / JobStreet / Hiredly / Prosple / Direct ATS)
- Interactive table: Company, Role, Platform, Status, Date, Fit Score (progress bar), **Waiting
  (days — now for every row)**, Last Updated, Gmail deep link (`https://mail.google.com/mail/u/0/#all/{thread_id}`)
- **👁 View buttons** (paginated) under the table open each row's detail pop-up — JD, **CV Gap
  Analysis** (green matched tags / red missing tags / actionable improvements), an optional
  **🤖 AI "why this score & how to improve"**, manual status override (saves to SQLite instantly),
  notes/history, and a **← Back** button to return to the tracker
- One-click CSV export of the filtered view
- Sidebar: **full-text search** across company/role/subject/JD, a **Minimum CV Fit %** slider,
  **🧬 Re-score all** (re-fit every application + cached market role after a CV edit — no re-sync
  needed), **👻 Ghost stale** (persists the auto-ghost rule into the DB, logged to history),
  tunable **rules** (ghost-after / follow-up-after days), and **💾 Backup data** (snapshot DB +
  CSV + state into `backups/<timestamp>/`).
- CSV exports: filtered tracker, **CV Gap Report**, and **live-match results**.

## Tab 2 — Live Job Matches (`discovery.py`)

- Keyless public scans: LinkedIn guest search + JobStreet Malaysia v5 JSON API + Hiredly
  (best-effort; endpoint may be geo-blocked) + Remotive (remote fallback), all through
  ThreadPoolExecutor(16) with 4s timeouts.
- Search targets: Actuarial Analyst, Risk Analyst, Data Analyst, BI Analyst —
  filtered to Malaysia / Kuala Lumpur / Selangor.
- Ranked by Fit Score with color badges (green ≥75%, amber 50–74%, red <50%), showing
  Company, Title, Source, Fit, Top Missing Skill, `[Apply on Platform]` direct link and
  `[＋ Add to My Tracker]` (lands in the tracker as *Saved / Planning*). Roles already in your
  tracker show **✓ Already in tracker** instead.
- Results cached in the `discovered_jobs` SQLite table → instant re-filtering.

## Output schema (`applications.db`, additive in-place migration)

| Column | Notes |
|---|---|
| `thread_id` | Primary key (`manual:<hash>` for tracked discovered jobs) |
| `company_name` / `role_title` | Regex first, LLM fallback |
| `source_platform` | LinkedIn, JobStreet, Hiredly, Prosple, Direct ATS, Other |
| `application_date` / `last_updated` | Preserved / advanced on thread updates |
| `current_status` | Saved / Planning, Applied, Assessment / OA, Interview, Offer, Rejected, Ghosted (auto >21d) |
| `job_description_snippet` | OpenGraph description; email-body fallback |
| `job_url` / `gmail_link` | Posting URL / Gmail thread deep link |
| `is_valid` / `filter_reason` | Strict-filter verdict + why it was dropped |
| `fit_score` / `matched_skills` / `missing_skills` / `actionable_improvements` | CV Fit Analyzer results |
| `job_type` / `seniority_level` / `industry` | Auto-classified: Full-Time, Internship, Graduate Programme / Trainee (PROTEGE), Contract / Fixed-Term · seniority tier · Insurance / Banking / Consulting / Tech … |
| `notes` | Per-application free-text notes (referrals, follow-up log) — editable in the detail pop-up & Saved pipeline |
| `fit_source` / `fit_cv_sig` | Where the fit score came from (`rule`/`ai`) + the CV fingerprint it was computed against |
| `ai_classified` | 1 once the AI classified this thread's email (never re-run on incremental syncs) |

Side tables: `discovered_jobs` (live market cache), `processed_messages`
(each message's sanitised body is cached here so bodies are fetched once ever and deep
re-parses are offline), and `status_history` (full audit trail of every status change —
manual overrides, Gmail-driven updates, auto-ghosting, manual recoveries and manual
noise-marking, each with timestamp + note).

## JD enrichment for tracked jobs

Two one-click enrichment tools sit above the Interactive Tracker:

- **🧲 Scrape missing JDs** — OG-scrapes known posting links (16 workers, 4s timeouts).
  Broken/logo links are skipped.
- **🔎 Look up JDs & fix broken links** — searches live listings per unique role (LinkedIn
  guest + JobStreet v5), fuzzy-matches each tracked job by company name and role tokens, pulls
  the real job description for roles whose confirmation emails carried no link, **and replaces
  dead posting links with the live posting**. JobStreet "application received" emails store a
  short-lived tracking URL (`url.jobstreet.com/ss/...`) that silently redirects to a JobStreet
  logo image instead of the job page — the parser now refuses to store those, the UI hides any
  already-stored ones, and this button either repoints them to the live posting (strict
  company+role match, never a different company) or clears them so the tracker never offers a
  dead logo link again.

Enriched JDs power the row detail panel (**📄 View JD & Fit Breakdown**): full
description, extracted requirement lines, links, and the live 5-pillar gap analysis.

## CV Gap Report

The **🧬 CV Gap Report** exhaustively cross-references your curated CV against every
applied job (titles + scraped JDs) and the live-market cache: per-skill demand
frequency, live-market demand, missing counts, CV ownership, and a priority ranking
that tells you exactly which skill to build next.

## Categorisation

Every application is auto-classified on three axes (sidebar filters + charts):

- **Job Type** — Full-Time · Internship · Graduate Programme / Trainee (incl. PROTEGE)
- **Seniority** — Internship · Graduate / Entry · Executive / Junior · Analyst /
  Associate · Senior / Lead · Manager / VP+
- **Industry** — Insurance · Banking & Finance · Consulting & Advisory · Actuarial &
  Risk Analytics · Tech & Digital · Energy & Utilities · Government / GLC · FMCG /
  Manufacturing / Retail · Recruitment / Staffing

## Optional CLI (kept for automation)

```bash
python main.py --sync       # incremental sync + export
python main.py --full       # force full re-sync
python main.py --reset      # wipe DB/state/CSV + full re-sync
python main.py --market     # run the live market scan and print top matches
python main.py --show       # print tracker summary
python main.py --backup     # snapshot DB + CSV + state + CV + AI cache into backups/
python main.py --check      # print a data-quality report (no network)
```

## Open on iPad / Phone (free)

**Same Wi-Fi (easiest):**
1. Double-click **`launch_ipad.bat`** (or run `streamlit run dashboard.py` — the config
   already binds to `0.0.0.0`).
2. On the iPad, open Safari and go to the URL printed in the dashboard sidebar under
   **📱 Open on iPad / Phone** (e.g. `http://192.168.1.x:8501`) — or scan the QR code shown there.
3. First launch only: allow Streamlit through the **Windows Firewall** prompt
   (check "Private networks").

**Away from home (free, no signup):** run
`cloudflared tunnel --url http://localhost:8501`
([download cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/))
and open the printed `https://<random>.trycloudflare.com` URL from anywhere. The
dashboard's sync buttons work from the iPad exactly like on the PC.

## Security

- **Set a password before exposing the dashboard.** The app binds to `0.0.0.0` and has no
  login by default, so a LAN or Cloudflare-tunnel URL is readable by anyone who has it.
  Set `DASH_PASSWORD=your-passphrase` in the environment and restart Streamlit — the app
  then refuses to render anything until the password is entered (constant-time compare).
- **Secrets and personal data are git-ignored** (`.gitignore` covers `credentials.json`,
  `token.json`, `applications.db*`, `job_tracker.csv`, `cv_profile.txt`, `ai_cache.json`,
  `backups/`, …). Never commit them.
- **Fetched job pages are SSRF-guarded** — `enricher.fetch_page` refuses loopback, private
  and link-local hosts, caps the download size, and only follows http(s).
- **Email text is HTML-escaped before rendering** — company names, role titles, subjects and
  JD snippets come from untrusted email content; the dashboard escapes them before any
  `unsafe_allow_html` sink and refuses non-http(s) URLs in links/buttons.
- **AI keys stay in the session only** — never written to disk (sanitised before use).

## Testing

Tests are hermetic (temporary DB / CV / AI cache — no personal data) and need no extra
dependencies:

```bash
python -m unittest discover -s tests -v    # or double-click run_tests.bat
```

They cover the parser (`tests/test_parser.py`), CV fit analyzer (`test_matcher.py`), SQLite
storage invariants incl. the status-regression / manual-recovery / OR-validity / body-cache
rules (`test_storage.py`), the sync engine's AI-fit caching and thread-verdict merge
(`test_main.py`), the audit/repair planner (`test_repair.py`), the AI layer (`test_ai.py`),
enrichment + SSRF guard (`test_enricher.py`), provider parsers (`test_discovery.py`), Gmail
body extraction + search coverage (`test_gmail_fetcher.py`), JD matching (`test_jd_lookup.py`),
and a **headless Streamlit AppTest smoke test** of the whole dashboard
(`test_dashboard_smoke.py`).

## Setup (first time only)

1. `pip install -r requirements.txt`
2. Google Cloud: enable **Gmail API**, create a **Desktop app** OAuth client, save as
   `credentials.json` (scopes: `gmail.readonly`). The first sync opens a browser once
   and caches `token.json`.
3. Optional: `OPENAI_API_KEY` enables the LLM extraction fallback (fully optional).
4. Optional: `DASH_PASSWORD=...` before using a LAN/tunnel URL (see Security above).

Files produced: `applications.db`, `job_tracker.csv`, `sync_state.json`, `cv_profile.txt`,
`ai_cache.json`, `opencode_session.txt`, plus `backups/<timestamp>/` snapshots (DB + CSV +
state + CV + AI cache) when you click **💾 Backup data**.
