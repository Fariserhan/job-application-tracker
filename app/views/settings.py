"""Settings: check the email reader's work, syncing, rules, the optional Claude review,
maintenance and storage."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from app.ui import components as ui
from app.ui import data, drill, masthead

rt = data.runtime()
s = rt.settings
repo = data.repo()

ui.lede("Settings", [("Set it once,", "em"), (" then let the sync do the work.", "")],
        "Check anything the email reader was unsure about, choose how often it syncs, and tune "
        "the rules. Saved to "
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


# ---------------------------------------------------------------- review
queue = repo.review_queue()
noise = repo.noise()
with ui.section(1, "Check the email reader's work",
                f"{len(queue)} email{'s' if len(queue) != 1 else ''} worth a quick look"
                if queue else "Nothing needs checking"):
    ui.explain("The email reader is sure about almost everything. When it was unsure whether "
               "an email was a real application, or who the employer was, it lands here. One "
               "click confirms or corrects it, and it never asks about that email again.")
    if not queue:
        ui.empty_state("All checked", "Every application was read with confidence.")
    for r in queue:
        with st.container(key=f"jt-review-{r['thread_id']}"):
            ui.render(f'<div class="jt-fu-title">{ui.esc(r["company_name"] or "Unknown")}</div>'
                      f'<div class="jt-fu-sub">{ui.esc(r["role_title"] or "(role not stated)")}'
                      f'</div><div class="jt-fu-meta">Email: "{ui.esc(r["latest_subject"] or "")}"'
                      f' · {ui.fmt_date(r["application_date"])} · {ui.esc(r["current_status"])}'
                      '</div>')
            row = st.container(horizontal=True, gap="small")
            if row.button("Looks right", key=f"ok_{r['thread_id']}", icon=":material/check:",
                          type="primary"):
                repo.set_valid(r["thread_id"], True, "manual: confirmed in review")
                data.rebuild("Confirmed")
                st.rerun()
            if row.button("Fix it", key=f"fix_{r['thread_id']}", icon=":material/edit:"):
                drill.open_application(r["thread_id"])
            if row.button("Not a job application", key=f"no_{r['thread_id']}",
                          icon=":material/block:", type="tertiary"):
                repo.set_valid(r["thread_id"], False, "manual: rejected in review")
                data.rebuild("Removed from the tracker")
                st.rerun()
            if isinstance(r.get("gmail_link"), str) and r["gmail_link"].startswith("http"):
                row.link_button("Read the email", r["gmail_link"], type="tertiary",
                                icon=":material/open_in_new:")

    with st.expander(f"Emails filtered out as not job applications ({len(noise)})",
                     icon=":material/filter_alt:"):
        st.caption("Job alerts, newsletters and account emails end up here. If a real "
                   "application was filtered out by mistake, tick Bring back and save.")
        if noise:
            frame = pd.DataFrame(noise)
            frame.insert(0, "recover", False)
            edited = st.data_editor(
                frame, hide_index=True, height=360, key="noise_editor",
                column_order=["recover", "application_date", "latest_subject", "company_name",
                              "filter_reason", "gmail_link"],
                disabled=["application_date", "latest_subject", "company_name", "filter_reason",
                          "gmail_link"],
                column_config={
                    "recover": st.column_config.CheckboxColumn("Bring back", width="small"),
                    "application_date": st.column_config.DateColumn("Date", format="D MMM YYYY"),
                    "latest_subject": st.column_config.TextColumn("Email subject", width="large"),
                    "company_name": "From",
                    "filter_reason": st.column_config.TextColumn("Why it was filtered"),
                    "gmail_link": st.column_config.LinkColumn("Email", display_text="Open"),
                })
            picked = edited[edited["recover"]]
            if st.button(f"Bring back {len(picked)} selected", type="primary",
                         disabled=picked.empty, icon=":material/restore:"):
                for tid in picked["thread_id"]:
                    repo.set_valid(str(tid), True, "manual: recovered from the noise filter")
                data.rebuild(f"Brought back {len(picked)} application(s)")
                st.rerun()

# ---------------------------------------------------------------- syncing
with ui.section(2, "Syncing", "Your tracker keeps itself up to date"):
    left, right = st.columns([7, 5], gap="large")
    with left, st.form("syncing", border=False):
        hours = st.number_input(
            "When the app opens, sync automatically if the last sync is older than (hours). "
            "0 turns it off.", 0, 168, int(getattr(s, "auto_sync_hours", 6)))
        if st.form_submit_button("Save", type="primary"):
            _save("Sync settings saved", auto_sync_hours=int(hours))
    with right:
        ui.label("Sync even when the app is closed")
        st.markdown("Double-click **`auto_sync.bat`** in the project folder once. Windows will "
                    "then read new job emails every morning and each time you sign in, with no "
                    "window popping up. Run `auto_sync.bat off` to stop it.")
        background = data.background_sync_status()
        st.caption(background[0] if background else "No background sync has run yet.")
    c1, c2 = st.columns(2, gap="large")
    with c1:
        if st.button("Re-read the whole mailbox", icon=":material/cloud_sync:",
                     disabled=not rt.can_sync_gmail,
                     help="Disabled in the demo." if rt.demo else
                          "Re-judges every job email with the current rules. Takes a few "
                          "minutes."):
            masthead.run_gmail_sync(full=True)
    with c2:
        if st.button("Re-check stored emails (offline)", icon=":material/replay:",
                     help="Applies the current rules to emails already downloaded. Fast."):
            from jobtracker.sync import reparse_from_cache

            with st.spinner("Re-checking stored emails..."):
                touched = reparse_from_cache(s)
                data.rebuild(f"Re-checked stored emails: {touched} application(s) updated")
            st.rerun()

# ---------------------------------------------------------------- rules
with ui.section(3, "Rules", "When silence becomes a status"), st.form("rules", border=False):
    ui.explain("These decide when the app suggests a follow-up and when it gives up on an "
               "application. Follow-up timing changes every page straight away; the ghosting "
               "rule applies on the next sync.")
    c1, c2 = st.columns(2, gap="large")
    ghost = c1.number_input("Mark as ghosted after this many days with no reply", 7, 120,
                            int(s.ghost_after_days))
    follow = c2.number_input("Suggest a follow-up after this many quiet days", 2, 60,
                             int(s.follow_up_after_days))
    c3, c4 = st.columns(2, gap="large")
    since = c3.date_input("Only read Gmail from this date", _gmail_date(s.gmail_after),
                          format="DD/MM/YYYY", max_value=date.today())
    cap = c4.number_input("Most emails to read per sync", 100, 20000, int(s.gmail_max_results),
                          step=100)
    enrich = st.toggle("Open job posting links to fill in the title, location and description",
                       s.enrich_enabled)
    if st.form_submit_button("Save rules", type="primary"):
        if follow >= ghost:
            st.error("The follow-up should come before an application is marked ghosted.")
        else:
            _save("Rules saved", ghost_after_days=int(ghost), follow_up_after_days=int(follow),
                  gmail_after=since.strftime("%Y/%m/%d"), gmail_max_results=int(cap),
                  enrich_enabled=bool(enrich))

# ---------------------------------------------------------------- Claude review
spend = repo.llm_spend()
with ui.section(4, "Claude review (optional)", "A second opinion for the hard emails"):
    ui.explain("Off unless you turn it on. Only emails the rules are unsure about are sent, "
               "each one once, and it stops at your monthly budget. On Claude Haiku it costs "
               "about US$0.0001 per email.")
    left, right = st.columns([7, 5], gap="large")
    with left, st.form("llm", border=False):
        enabled = st.toggle("Ask Claude about emails the rules are unsure of", s.llm_enabled)
        model = st.text_input("Model", s.llm_model)
        c1, c2 = st.columns(2)
        budget = c1.number_input("Monthly budget (USD)", 0.0, 50.0,
                                 float(s.llm_monthly_budget_usd), step=0.5, format="%.2f")
        calls = c2.number_input("Most emails to check per sync", 0, 2000,
                                int(s.llm_max_calls_per_sync), step=10)
        st.caption("Needs the optional `anthropic` package and an `ANTHROPIC_API_KEY` "
                   "environment variable. Nothing is sent while this is off.")
        if st.form_submit_button("Save", type="primary"):
            _save("Claude review settings saved", llm_enabled=bool(enabled),
                  llm_model=model.strip() or s.llm_model,
                  llm_monthly_budget_usd=float(budget), llm_max_calls_per_sync=int(calls))
    with right:
        ui.label(f"This month ({spend['month']})")
        ui.compare_table([
            ("Spend", f"${spend['cost_usd']:.4f}", f"of ${s.llm_monthly_budget_usd:.2f}", "", ""),
            ("Emails checked", f"{spend['calls']:,}", "", "", ""),
            ("Tokens in / out", f"{spend['input_tokens']:,}", f"{spend['output_tokens']:,}", "", ""),
        ], head=("", "Used", "Limit", ""))

# ---------------------------------------------------------------- maintenance
with ui.section(5, "Maintenance", "Tidy-ups you rarely need"):
    ui.explain("Every job that changes data takes a backup first.")
    c1, c2, c3 = st.columns(3, gap="large")
    with c1:
        ui.subhead("Fix names and roles")
        st.caption("Re-checks every stored application with the current rules: platform names "
                   "stored as the employer, junk role titles, newsletters that slipped through. "
                   "Your own fixes are never touched.")
        if st.button("Preview fixes", icon=":material/rule:"):
            from jobtracker.repair import run as repair_run

            plan, _counts = repair_run(s, apply=False)
            st.session_state["repair_plan"] = plan
        plan = st.session_state.get("repair_plan")
        if plan is not None:
            if not plan:
                st.caption("Nothing to fix. Every stored row agrees with the current rules.")
            else:
                st.dataframe(pd.DataFrame(plan), hide_index=True, height=220)
                if st.button(f"Apply {len(plan)} fix{'es' if len(plan) != 1 else ''}",
                             type="primary", icon=":material/build:"):
                    from jobtracker.repair import run as repair_run

                    _plan, applied = repair_run(s, apply=True)
                    st.session_state.pop("repair_plan", None)
                    data.rebuild(f"Fixed {applied['rows']} row(s) (backup taken first)")
                    st.rerun()
    with c2:
        ui.subhead("Fill in job details")
        pending = repo.threads_needing_enrichment()
        st.caption(f"{len(pending)} application(s) have a job posting link but no title or "
                   "description yet. This opens each link and reads them.")
        if st.button("Read the job postings", icon=":material/travel_explore:",
                     disabled=not pending or rt.demo,
                     help="Disabled in the demo." if rt.demo else None):
            from jobtracker.enrich import enrich_many

            with st.spinner(f"Reading {len(pending)} job postings..."):
                filled = repo.apply_enrichment(enrich_many(pending))
                data.rebuild(f"Filled in {filled} job posting(s)")
            st.rerun()
    with c3:
        ui.subhead("Backup and rebuild")
        st.caption("Backups keep the newest 15.")
        if st.button("Back up now", icon=":material/save:"):
            path = repo.backup("manual")
            data.flash(f"Backed up to {path.name}")
            st.rerun()
        if st.button("Rebuild the warehouse", icon=":material/database:"):
            with st.spinner("Running the dbt models and data tests..."):
                result = data.rebuild()
            data.flash(f"Warehouse rebuilt in {result['seconds']} s, "
                       f"{result['tests_failed']} failing tests")
            st.rerun()

# ---------------------------------------------------------------- storage
counts = repo.counts()


def _size(path: Path) -> str:
    try:
        return f"{path.stat().st_size / 1024:,.0f} KB"
    except OSError:
        return "-"


with ui.section(6, "Storage", "Where your data lives"):
    ui.explain("Everything stays on this computer. Your Google sign-in files (credentials.json, "
               "token.json) are read from the data folder and never shown here.")
    ui.facts([
        ("Data folder", str(s.data_dir)),
        ("applications.db", _size(Path(s.db_path))),
        ("warehouse.duckdb", _size(Path(s.warehouse_path))),
        ("Emails processed", f"{counts['messages']:,}"),
        ("Applications", f"{counts['valid']:,}"),
        ("Filtered as noise", f"{counts['noise']:,}"),
    ])
