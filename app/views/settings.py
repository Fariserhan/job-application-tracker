"""Settings — pipeline rules, the optional Claude fallback, enrichment, maintenance."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from app.ui import components as ui
from app.ui import data, masthead

rt = data.runtime()
s = rt.settings
repo = data.repo()

ui.lede("Settings", [("Rules first, ", "em"), ("and everything else optional.", "")],
        "The parser is deterministic and runs offline. These settings tune the rules that turn "
        "silence into status, an optional Claude review for the few emails the rules cannot "
        "classify, and the maintenance jobs that keep the data honest. Saved to "
        f"<code>{ui.esc(str(Path(s.data_dir) / 'settings.json'))}</code>.")


def _save(message: str, **changes) -> None:
    s.with_updates(**changes).save()
    data.clear_caches()
    data.flash(message)
    st.rerun()


def _gmail_date(text: str) -> date:
    try:
        return datetime.strptime(text, "%Y/%m/%d").date()
    except ValueError:
        return date(date.today().year, 1, 1)


# ---------------------------------------------------------------- rules
with ui.section(1, "Pipeline rules", "When silence becomes a status",
                "Follow-up timing applies immediately to every page. The ghosting rule is applied "
                "on the next sync, dated the day the threshold was crossed."), \
        st.form("rules", border=False):
    c1, c2 = st.columns(2, gap="large")
    ghost = c1.number_input("Mark as ghosted after (days without any reply)", 7, 120,
                            int(s.ghost_after_days))
    follow = c2.number_input("Suggest a follow-up after (days of silence)", 2, 60,
                             int(s.follow_up_after_days))
    c3, c4 = st.columns(2, gap="large")
    since = c3.date_input("Read Gmail from", _gmail_date(s.gmail_after),
                          format="DD/MM/YYYY", max_value=date.today())
    cap = c4.number_input("Maximum emails per search", 100, 20000, int(s.gmail_max_results),
                          step=100)
    enrich = st.toggle("Read job postings for title, location and description "
                       "(OpenGraph / JSON-LD; links are SSRF-checked)", s.enrich_enabled)
    if st.form_submit_button("Save rules", type="primary"):
        if follow >= ghost:
            st.error("Follow-ups should come before an application is ghosted.")
        else:
            _save("Rules saved", ghost_after_days=int(ghost), follow_up_after_days=int(follow),
                  gmail_after=since.strftime("%Y/%m/%d"), gmail_max_results=int(cap),
                  enrich_enabled=bool(enrich))

# ---------------------------------------------------------------- Claude fallback
spend = repo.llm_spend()
with ui.section(2, "Claude fallback", "Off by default; a second opinion for ambiguous mail",
                "Rules classify almost everything. When they are unsure, and only then, the email "
                "can be sent to Claude for a structured verdict. On Haiku that costs roughly "
                "$0.0001 per email, results are cached per message, and a monthly budget hard-"
                "stops it."):
    left, right = st.columns([7, 5], gap="large")
    with left, st.form("llm", border=False):
        enabled = st.toggle("Use Claude for emails the rules cannot classify", s.llm_enabled)
        model = st.text_input("Model", s.llm_model)
        c1, c2 = st.columns(2)
        budget = c1.number_input("Monthly budget (USD)", 0.0, 50.0,
                                 float(s.llm_monthly_budget_usd), step=0.5, format="%.2f")
        calls = c2.number_input("Maximum calls per sync", 0, 2000, int(s.llm_max_calls_per_sync),
                                step=10)
        st.caption("Needs the optional `anthropic` package and an `ANTHROPIC_API_KEY` in the "
                   "environment. Nothing is sent while this is off.")
        if st.form_submit_button("Save", type="primary"):
            _save("Claude fallback settings saved", llm_enabled=bool(enabled),
                  llm_model=model.strip() or s.llm_model,
                  llm_monthly_budget_usd=float(budget), llm_max_calls_per_sync=int(calls))
    with right:
        ui.label(f"This month ({spend['month']})")
        ui.compare_table([
            ("Spend", f"${spend['cost_usd']:.4f}", f"of ${s.llm_monthly_budget_usd:.2f}", "", ""),
            ("Emails reviewed", f"{spend['calls']:,}", "", "", ""),
            ("Tokens in / out", f"{spend['input_tokens']:,}", f"{spend['output_tokens']:,}", "", ""),
        ], head=("", "Used", "Limit", ""))

# ---------------------------------------------------------------- maintenance
with ui.section(3, "Maintenance", "Keep the data honest",
                "Re-run improved rules over old mail, audit stored rows, and snapshot the "
                "database. Every destructive job takes a backup first."):
    c1, c2, c3 = st.columns(3, gap="large")
    with c1:
        ui.subhead("Re-parse", "Apply the current rules to mail already seen.")
        if st.button("Offline re-parse from cache", icon=":material/replay:"):
            from jobtracker.sync import reparse_from_cache

            with st.spinner("Re-parsing cached email bodies…"):
                touched = reparse_from_cache(s)
                data.rebuild(f"Re-parsed cached mail: {touched} application(s) updated")
            st.rerun()
        if st.button("Full re-parse from Gmail", icon=":material/cloud_sync:",
                     disabled=not rt.can_sync_gmail,
                     help="Disabled in the demo." if rt.demo else
                          "Re-downloads what the cache lacks and re-judges the whole mailbox."):
            masthead.run_gmail_sync(full=True)
    with c2:
        ui.subhead("Audit & repair", "Re-judge stored rows; manual verdicts are never touched.")
        if st.button("Preview repairs (dry run)", icon=":material/rule:"):
            from jobtracker.repair import run as repair_run

            plan, _counts = repair_run(s, apply=False)
            st.session_state["repair_plan"] = plan
        plan = st.session_state.get("repair_plan")
        if plan is not None:
            if not plan:
                st.caption("Nothing to repair — every stored row agrees with the current rules.")
            else:
                st.dataframe(pd.DataFrame(plan), hide_index=True, height=220)
                if st.button(f"Apply {len(plan)} repair{'s' if len(plan) != 1 else ''}",
                             type="primary", icon=":material/build:"):
                    from jobtracker.repair import run as repair_run

                    _plan, applied = repair_run(s, apply=True)
                    st.session_state.pop("repair_plan", None)
                    data.rebuild(f"Repaired {applied['rows']} row(s) (backup taken first)")
                    st.rerun()
    with c3:
        ui.subhead("Backup & rebuild", "Snapshots keep the newest 15.")
        if st.button("Back up now", icon=":material/save:"):
            path = repo.backup("manual")
            data.flash(f"Backed up to {path.name}")
            st.rerun()
        if st.button("Rebuild warehouse", icon=":material/database:"):
            with st.spinner("Running dbt models and data tests…"):
                result = data.rebuild()
            data.flash(f"Warehouse rebuilt in {result['seconds']} s · "
                       f"{result['tests_failed']} failing tests")
            st.rerun()

# ---------------------------------------------------------------- storage
counts = repo.counts()


def _size(path: Path) -> str:
    try:
        return f"{path.stat().st_size / 1024:,.0f} KB"
    except OSError:
        return "—"


with ui.section(4, "Storage", "Where your data lives",
                "Everything stays on this machine. Secrets (credentials.json, token.json) are "
                "read from the data folder and never shown here."):
    ui.facts([
        ("Data folder", str(s.data_dir)),
        ("applications.db", _size(Path(s.db_path))),
        ("warehouse.duckdb", _size(Path(s.warehouse_path))),
        ("Emails processed", f"{counts['messages']:,}"),
        ("Applications", f"{counts['valid']:,}"),
        ("Filtered as noise", f"{counts['noise']:,}"),
    ])
