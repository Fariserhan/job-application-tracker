"""Pipeline — the working view: Kanban board, editable table, detail panel, manual entries."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from app.ui import components as ui
from app.ui import data, theme
from jobtracker import domain

s = data.settings()
apps = data.metric("applications")

open_n = int(apps["is_active"].sum()) if not apps.empty else 0
ui.lede("Pipeline", [(f"{open_n} applications are still open", "em"),
                     (" — here is where each one stands.", "")],
        "Open applications by stage on the board; the table holds everything, with links back "
        "to the posting and the Gmail thread. Open any application to read its full status "
        "history and edit it.")

OPEN_LANES = [domain.APPLIED, domain.ASSESSMENT, domain.INTERVIEW, domain.OFFER]
CLOSED_LANES = [domain.REJECTED, domain.GHOSTED, domain.WITHDRAWN]
MANUAL_PLATFORMS = list(dict.fromkeys([*domain.PLATFORMS, "Company Site", "Referral"]))


# ---------------------------------------------------------------- toolbar
def _log_form() -> None:
    with st.form("log_application", clear_on_submit=True, border=False):
        st.caption("For applications that never produced an email — careers-page applies, "
                   "referrals, walk-ins. They join every metric like Gmail-sourced rows.")
        company = st.text_input("Company *")
        role = st.text_input("Role *")
        c1, c2 = st.columns(2)
        platform = c1.selectbox("Channel", MANUAL_PLATFORMS,
                                index=MANUAL_PLATFORMS.index("Company Site"))
        applied_on = c2.date_input("Applied on", value=date.today(), max_value=date.today(),
                                   format="DD/MM/YYYY")
        status = st.selectbox("Current status", [domain.APPLIED, domain.ASSESSMENT,
                                                 domain.INTERVIEW, domain.OFFER, domain.SAVED])
        job_url = st.text_input("Posting URL", placeholder="https://…")
        notes = st.text_area("Notes", height=80)
        if st.form_submit_button("Add application", type="primary", icon=":material/add:"):
            if not company.strip() or not role.strip():
                st.error("Company and role are required.")
                return
            data.repo().add_manual_application(company, role, platform,
                                               applied_on=applied_on.isoformat(),
                                               job_url=job_url, notes=notes, status=status)
            data.rebuild(f"Logged {role.strip()} at {company.strip()}")
            st.rerun()


bar = st.container(horizontal=True, vertical_alignment="center", gap="small")
with bar:
    view = st.segmented_control("View", ["Board", "Table"], default="Board", key="pipe_view",
                                label_visibility="collapsed", required=True)
    st.space("stretch")
    with st.popover("Log an application", icon=":material/add_circle:"):
        _log_form()
    st.download_button("Download CSV", apps.to_csv(index=False).encode("utf-8"),
                       file_name=f"applications-{date.today():%Y%m%d}.csv", mime="text/csv",
                       icon=":material/download:", disabled=apps.empty)

if apps.empty:
    ui.empty_state("No applications match these filters",
                   "Clear the sidebar filters, sync Gmail, or log an application manually.")
    st.stop()

counts = apps["current_status"].value_counts()
ui.mini_stats([("Open", f"{int(apps['is_active'].sum()):,}"),
          *[(theme.SHORT_STATUS.get(st_, st_), f"{int(counts.get(st_, 0)):,}")
            for st_ in [domain.APPLIED, domain.ASSESSMENT, domain.INTERVIEW, domain.OFFER,
                        domain.REJECTED, domain.GHOSTED]],
          ("Shown", f"{len(apps):,}")])


def _label(row) -> str:
    return f"{row.company_name} — {row.role_title} ({ui.fmt_date(row.applied_date, '%d %b')})"


selected: str | None = None
if view == "Board":
    by_activity = apps.sort_values(["last_activity_date", "applied_date"], ascending=False)
    ui.kanban([(lane, by_activity[by_activity["current_status"] == lane]) for lane in OPEN_LANES],
              s.follow_up_after_days)
    closed = by_activity[by_activity["current_status"].isin(CLOSED_LANES)]
    with st.expander(f"Closed — {len(closed)} applications "
                     f"({' · '.join(f'{theme.SHORT_STATUS.get(c, c)} {int(counts.get(c, 0))}' for c in CLOSED_LANES)})",
                     icon=":material/inventory_2:"):
        ui.kanban([(lane, closed[closed["current_status"] == lane]) for lane in CLOSED_LANES],
                  s.follow_up_after_days, per_lane=6)
    labels = {r.application_id: _label(r) for r in by_activity.itertuples(index=False)}
    selected = st.selectbox("Open an application", list(labels), index=None,
                            format_func=labels.get, key="pipe_pick",
                            placeholder="Search by company or role to view history and edit…")
else:
    table = apps.reset_index(drop=True)
    event = st.dataframe(
        table,
        key="pipe_table", on_select="rerun", selection_mode="single-row", hide_index=True,
        height=520, row_height=36, placeholder="—",
        column_order=["company_name", "role_title", "platform", "current_status",
                      "max_stage_reached", "applied_date", "last_activity_date",
                      "days_since_activity", "days_to_response", "industry", "job_url",
                      "gmail_link"],
        column_config={
            "company_name": st.column_config.TextColumn("Company", width="medium", pinned=True),
            "role_title": st.column_config.TextColumn("Role", width="medium"),
            "platform": st.column_config.TextColumn("Channel", width="small"),
            "current_status": st.column_config.TextColumn("Status", width="small"),
            "max_stage_reached": st.column_config.ProgressColumn(
                "Furthest stage", min_value=0, max_value=4, format="%d / 4", width="small",
                help="1 Applied · 2 Assessment · 3 Interview · 4 Offer — the furthest stage ever "
                     "reached, so a later rejection does not erase an interview."),
            "applied_date": st.column_config.DateColumn("Applied", format="DD MMM YYYY"),
            "last_activity_date": st.column_config.DateColumn("Last activity",
                                                              format="DD MMM YYYY"),
            "days_since_activity": st.column_config.NumberColumn("Quiet (d)", format="%d"),
            "days_to_response": st.column_config.NumberColumn(
                "Reply in (d)", format="%d",
                help="Blank when there was no reply or its date is unknown — never zero-filled."),
            "industry": st.column_config.TextColumn("Industry", width="small"),
            "job_url": st.column_config.LinkColumn("Posting", display_text="Open ↗",
                                                   width="small"),
            "gmail_link": st.column_config.LinkColumn("Thread", display_text="Gmail ↗",
                                                      width="small"),
        },
    )
    rows = event.selection.rows if event is not None else []
    if rows:
        selected = str(table.iloc[rows[0]]["application_id"])
    else:
        st.caption("Select a row to open its history and edit it.")


# ---------------------------------------------------------------- detail panel
def _detail(thread_id: str) -> None:
    repo = data.repo()
    record = repo.get(thread_id)
    if not record:
        st.info("This application no longer exists (it may have been deleted or re-parsed).")
        return
    row = apps[apps["application_id"] == thread_id]
    wh = row.iloc[0] if not row.empty else None
    status = domain.normalise_status(record.get("current_status"))

    with st.container(key="jt-sheet-detail"):
        ui.render(f'<div style="display:flex;justify-content:space-between;gap:1rem;'
                  f'flex-wrap:wrap;align-items:flex-start"><div>'
                  f'<p class="jt-detail-title">{ui.esc(record.get("company_name") or "Unknown")}</p>'
                  f'<p class="jt-detail-sub">{ui.esc(record.get("role_title") or "(role not stated)")}'
                  f'</p></div><div>{ui.status_mark(status)}</div></div>')
        ui.facts([
            ("Channel", str(record.get("source_platform") or "—")),
            ("Applied", ui.fmt_date(record.get("application_date"))),
            ("Last activity", ui.fmt_date(record.get("last_updated"))),
            ("Reply in", f"{int(wh['days_to_response'])} days" if wh is not None
             and pd.notna(wh["days_to_response"]) else "—"),
            ("Source", "Manual entry" if record.get("origin") == "manual" else
             f"Gmail · parsed by {record.get('parsed_by') or 'rules'}"),
            ("Location", str(record.get("location") or "—")),
        ])
        left, right = st.columns([5, 7], gap="large")
        with left:
            ui.label("Status history")
            ui.timeline(repo.events(thread_id))
            links = [(u, lbl) for u, lbl in ((record.get("job_url"), "Job posting"),
                                             (record.get("gmail_link"), "Gmail thread"))
                     if isinstance(u, str) and u.startswith("http")]
            for url, label in links:
                st.link_button(label, url, icon=":material/open_in_new:", type="tertiary")
        with right:
            t_status, t_edit, t_remove = st.tabs(["Update status", "Edit details", "Remove"])
            with t_status:
                choice = st.selectbox("Status", domain.STATUSES,
                                      index=domain.STATUSES.index(status),
                                      key=f"st_{thread_id}")
                note = st.text_input("Note (optional)", key=f"note_{thread_id}",
                                     placeholder="e.g. Recruiter called — second round on Friday")
                if st.button("Save status", type="primary", key=f"save_st_{thread_id}",
                             disabled=choice == status):
                    repo.set_status(thread_id, choice, note=note)
                    data.rebuild(f"Status set to {choice}")
                    st.rerun()
                st.caption("A manual override is logged as an event (source: manual) and wins "
                           "over older email evidence; newer emails can still move it forward.")
            with t_edit, st.form(f"edit_{thread_id}", border=False):
                company = st.text_input("Company", value=record.get("company_name") or "")
                role = st.text_input("Role", value=record.get("role_title") or "")
                url = st.text_input("Posting URL", value=record.get("job_url") or "")
                notes = st.text_area("Notes", value=record.get("notes") or "", height=110)
                if st.form_submit_button("Save details", type="primary"):
                    repo.update_fields(thread_id, company_name=company, role_title=role,
                                       job_url=url, notes=notes)
                    data.rebuild("Details saved")
                    st.rerun()
            with t_remove:
                st.markdown("**Not a real application?** Marking it as noise hides it from every "
                            "metric and teaches nothing to the parser — the verdict is sticky, "
                            "so a later sync will not bring it back.")
                if st.button("Mark as noise", icon=":material/block:", key=f"noise_{thread_id}"):
                    repo.set_valid(thread_id, False, "manual: marked as noise in the app")
                    data.rebuild("Marked as noise")
                    st.session_state.pop("pipe_pick", None)
                    st.rerun()
                st.divider()
                sure = st.checkbox("I understand this permanently deletes the record and its "
                                   "status history", key=f"del_ok_{thread_id}")
                if st.button("Delete application", icon=":material/delete:", disabled=not sure,
                             key=f"del_{thread_id}"):
                    repo.delete_application(thread_id)
                    data.rebuild("Application deleted")
                    st.session_state.pop("pipe_pick", None)
                    st.rerun()


if selected:
    _detail(selected)
