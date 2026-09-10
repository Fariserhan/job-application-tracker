# Job Application Suite - agent guide

Personal job-application tracker: Gmail sync -> parse/classify -> match/scoring -> Streamlit dashboard.

## Commands
- Run all tests (hermetic; touches no personal data): `venv\Scripts\python.exe -m unittest discover -s tests`
- Or double-click `run_tests.bat`
- Sync entry point: `main.py`  |  Dashboard: `streamlit run dashboard.py` (or `launch_dashboard.bat`)
- Install deps: `venv\Scripts\pip.exe install -r requirements.txt`

## Environment
- Windows + PowerShell, Python 3.12 inside `venv/`.
- Do NOT add new dependencies without asking. The app must keep working on the existing venv with only free/optional LLM providers.
- The app must run with AI disabled (`AI_DISABLE=1`) and degrade to deterministic logic.

## Never read or commit (secrets / personal data)
`credentials.json`, `token.json`, `applications.db`, `job_tracker.csv`, `cv_profile.txt`,
`application_audit.csv`, `ai_cache.json`, `opencode_session.txt`, `.streamlit/secrets.toml`,
`backups/`, `*.pdf`.

## Conventions
- `ai.py` is defensive by design: every function returns `None`/`{}` on failure and must never raise; callers fall back to deterministic logic. Preserve this property.
- LLM calls that need JSON go through `ai.batch_json` (native JSON mode + disk cache). Do not hand-roll model-output parsing elsewhere.
- AI fit scores are keyed by CV signature; keep cache keys stable so unchanged rows are never re-scored.
- The real CV text is sent to the fit scorer (`ai._candidate_brief`), so editing the CV changes scores.
- Each module has hermetic unit tests under `tests/` (`unittest`, network/LLM mocked). Add a test for any non-trivial logic change.

## Search
- Prefer `ast-grep` for structural code search (see the `ast-grep` skill). Scope explicit paths/files so it does not scan `venv/`.
