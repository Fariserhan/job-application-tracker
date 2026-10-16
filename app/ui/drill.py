"""Drill-down and the application sheet: every number, chart mark and row in the app opens
exactly the applications behind it, and any one of them opens in one more click.

* ``open_set(title, filters)`` pops a dialog listing the set (filtered in SQL through
  ``Filters``), with a one-line summary, Gmail links and "Open all in Pipeline".
* ``open_application(id)`` pops the application sheet: status history, one-click status
  changes, undo, edit, notes, mark as noise, delete.
* ``application_sheet(id)`` is the same sheet inline (the Pipeline page uses it).

Dialogs keep the reader's place: closing one returns to the exact scroll position and
filters they came from.
"""
from __future__ import annotations

import dataclasses

import pandas as pd
import streamlit as st

from jobtracker import domain
from jobtracker.warehouse.metrics import Filters

from . import components as ui
from . import data, theme

QUICK_STATUSES = [domain.APPLIED, domain.ASSESSMENT, domain.INTERVIEW, domain.OFFER,
                  domain.REJECTED, domain.GHOSTED, domain.WITHDRAWN]

STATUS_ICON = {domain.APPLIED: ":material/send:", domain.ASSESSMENT: ":material/quiz:",
               domain.INTERVIEW: ":material/event:", domain.OFFER: ":material/celebration:",
               domain.REJECTED: ":material/close:", domain.GHOSTED: ":material/schedule:",
               domain.WITHDRAWN: ":material/undo:"}

LIST_COLUMNS = ["company_name", "role_title", "current_status", "platform", "applied_date",
                "days_since_activity", "gmail_link"]


def list_config() -> dict:
    return {
        "company_name": st.column_config.TextColumn("Company", width="medium"),
        "role_title": st.column_config.TextColumn("Role", width="medium"),
        "current_status": st.column_config.TextColumn("Status", width="small"),
        "platform": st.column_config.TextColumn("Channel", width="small"),
        "applied_date": st.column_config.DateColumn("Applied", format="D MMM YYYY"),
        "days_since_activity": st.column_config.NumberColumn(
            "Quiet for", format="%d days",
            help="Days since the last email or update on this application."),
        "gmail_link": st.column_config.LinkColumn("Email", display_text="Open in Gmail",
                                                  width="small"),
    }


def summary_line(frame: pd.DataFrame) -> str:
    """'22 applications · 9 still open · 3 offers · 7 rejected · 3 ghosted'."""
    if frame.empty:
        return "No applications"
    counts = frame["current_status"].value_counts()
    parts = [f"<b>{len(frame):,}</b> application{'s' if len(frame) != 1 else ''}"]
    open_n = int(frame["is_active"].sum())
    if open_n:
        parts.append(f"{open_n} still open")
    for status, word in ((domain.OFFER, "offer"), (domain.REJECTED, "rejected"),
                         (domain.GHOSTED, "ghosted")):
        n = int(counts.get(status, 0))
        if n:
            parts.append(f"{n} {word}{'s' if word == 'offer' and n != 1 else ''}")
    return " · ".join(parts)


# ============================================================================ the set
def pipeline_params(f: Filters) -> dict[str, str | list[str]]:
    """Query parameters that make the Pipeline page show exactly ``f``."""
    out: dict[str, str | list[str]] = {"f": "1", "view": "table", **data.global_params()}
    for name, param in (("platforms", "platform"), ("statuses", "status"),
                        ("role_families", "role"), ("industries", "industry"),
                        ("job_types", "jobtype"), ("reached", "reached"),
                        ("companies", "company"), ("seniorities", "seniority"),
                        ("channels", "channel")):
        values = getattr(f, name)
        if values:
            out[param] = [str(v) for v in values]
    if f.month:
        out["month"] = f.month
    if f.date_from and f.date_to and f.date_from == f.date_to:
        out["day"] = f.date_from.isoformat()
    if f.search:
        out["q"] = f.search
    return out


@st.dialog("Applications", width="large", on_dismiss="rerun")
def _set_dialog(title: str, fkey: tuple) -> None:
    opened = st.session_state.get("_jt_dlg_app")
    if opened:
        if st.button("Back to the list", icon=":material/arrow_back:", type="tertiary",
                     key="dlg_back"):
            st.session_state.pop("_jt_dlg_app", None)
            st.rerun(scope="fragment")
        application_sheet(opened, in_dialog=True)
        return
    f = data._filters_from_key(fkey)
    frame = data.metric("applications", filters=f)
    ui.render(f'<p class="jt-dlg-title">{ui.esc(title)}</p>'
              f'<p class="jt-dlg-sub">{summary_line(frame)}</p>')
    if frame.empty:
        ui.empty_state("Nothing here", "No applications match this, with the current filters.")
        return
    event = st.dataframe(frame, key="dlg_table", on_select="rerun",
                         selection_mode="single-row", hide_index=True, height=min(
                             430, 42 + 35 * len(frame)), column_order=LIST_COLUMNS,
                         column_config=list_config(), placeholder="")
    rows = event.selection.rows if event is not None else []
    if rows:
        st.session_state["_jt_dlg_app"] = str(frame.iloc[rows[0]]["application_id"])
        st.rerun(scope="fragment")
    st.caption("Click a row to open it: history, status, notes and edits.")
    if st.button(f"Open all {len(frame):,} in Pipeline", icon=":material/table_view:",
                 key="dlg_all"):
        st.switch_page("views/pipeline.py", query_params=pipeline_params(f))


def open_set(title: str, filters: Filters) -> None:
    """Pop the dialog listing ``filters`` (call it in response to a click)."""
    st.session_state.pop("_jt_dlg_app", None)
    st.session_state.pop("dlg_table", None)
    _set_dialog(title, data._filters_key(filters))


def narrowed(**parts) -> Filters:
    """The current global filters narrowed by drill-down parts, e.g.
    ``narrowed(reached=["interview"], platforms=["LinkedIn"])``."""
    f = dataclasses.replace(data.current_filters())
    for name, value in parts.items():
        setattr(f, name, value)
    return f


def see_button(label: str, title: str, filters: Filters, key: str,
               help: str | None = None) -> None:
    """A quiet text button ("See all 22") that opens the set."""
    if st.button(label, key=key, type="tertiary", icon=":material/arrow_outward:", help=help):
        open_set(title, filters)


# ============================================================================ one application
@st.dialog("Application", width="large", on_dismiss="rerun")
def _app_dialog(thread_id: str) -> None:
    application_sheet(thread_id, in_dialog=True)


def open_application(thread_id: str) -> None:
    _app_dialog(thread_id)


def _after_write(message: str, in_dialog: bool) -> None:
    data.rebuild(None if in_dialog else message)
    if in_dialog:
        st.toast(message, icon=":material/check_circle:")
        st.rerun(scope="fragment")
    st.rerun()


def application_sheet(thread_id: str, in_dialog: bool = False) -> None:
    repo = data.repo()
    record = repo.get(thread_id)
    if not record:
        st.info("This application no longer exists (it may have been deleted or re-parsed).")
        return
    status = domain.normalise_status(record.get("current_status"))
    k = f"{'d' if in_dialog else 'p'}_{thread_id}"

    ui.render(f'<div class="jt-sheet-head"><div>'
              f'<p class="jt-detail-title">{ui.esc(record.get("company_name") or "Unknown")}</p>'
              f'<p class="jt-detail-sub">{ui.esc(record.get("role_title") or "(role not stated)")}'
              f'</p></div><div>{ui.status_mark(status)}</div></div>')
    confidence = record.get("parse_confidence")
    source = ("Logged by hand" if record.get("origin") == "manual" else
              "Read from Gmail" + (" (worth a check)" if confidence is not None
                                   and float(confidence) < 0.75 else ""))
    ui.facts([
        ("Channel", str(record.get("source_platform") or "Not known")),
        ("Applied", ui.fmt_date(record.get("application_date"))),
        ("Last activity", ui.fmt_date(record.get("last_updated"))),
        ("Source", source),
        ("Location", str(record.get("location") or "Not stated")),
    ])
    links = [(u, lbl) for u, lbl in ((record.get("gmail_link"), "Open the email in Gmail"),
                                     (record.get("job_url"), "Open the job posting"))
             if isinstance(u, str) and u.startswith("http")]
    if links:
        row = st.container(horizontal=True, gap="small")
        for url, label in links:
            row.link_button(label, url, icon=":material/open_in_new:")

    # ---- one-click status
    ui.label("Move it to")
    bar = st.container(horizontal=True, gap="small")
    for option in QUICK_STATUSES:
        if option == status:
            continue
        if bar.button(theme.SHORT_STATUS.get(option, option), key=f"qs_{option}_{k}",
                      icon=STATUS_ICON.get(option, ":material/arrow_forward:")):
            repo.set_status(thread_id, option, source="manual")
            _after_write(f"{record.get('company_name')}: now {option}", in_dialog)
    last = repo.last_status_change(thread_id)
    if last and st.button(f"Undo the last change (back to {last['from_status']})",
                          key=f"undo_{k}", icon=":material/undo:", type="tertiary"):
        restored = repo.undo_last_status(thread_id)
        _after_write(f"Undone: back to {restored}", in_dialog)

    left, right = st.columns([5, 6], gap="large")
    with left:
        ui.label("What happened, in order")
        ui.timeline(repo.events(thread_id))
    with right:
        t_notes, t_edit, t_remove = st.tabs(["Notes", "Fix details", "Remove"])
        with t_notes:
            notes = st.text_area("Notes", value=record.get("notes") or "", height=120,
                                 key=f"notes_{k}", label_visibility="collapsed",
                                 placeholder="Recruiter name, salary discussed, next step...")
            if st.button("Save notes", key=f"save_notes_{k}", type="primary"):
                repo.update_fields(thread_id, notes=notes)
                _after_write("Notes saved", in_dialog)
        with t_edit, st.form(f"edit_{k}", border=False):
            st.caption("If the email reader got the company or role wrong, fix it here. "
                       "Your fix is kept on future syncs.")
            company = st.text_input("Company", value=record.get("company_name") or "")
            role = st.text_input("Role", value=record.get("role_title") or "")
            url = st.text_input("Job posting link", value=record.get("job_url") or "")
            if st.form_submit_button("Save", type="primary"):
                repo.update_fields(thread_id, company_name=company, role_title=role,
                                   job_url=url)
                _after_write("Details saved", in_dialog)
        with t_remove:
            st.markdown("**Not a real application?** Hide it from every number and chart. "
                        "A later sync will not bring it back, and you can restore it from "
                        "Settings.")
            if st.button("It's not a job application", icon=":material/block:",
                         key=f"noise_{k}"):
                repo.set_valid(thread_id, False, "manual: marked as noise in the app")
                _after_write("Hidden from the tracker", in_dialog)
            st.divider()
            sure = st.checkbox("Delete it for good, with its history", key=f"del_ok_{k}")
            if st.button("Delete", icon=":material/delete:", disabled=not sure,
                         key=f"del_{k}"):
                repo.delete_application(thread_id)
                st.session_state.pop("_jt_dlg_app", None)
                data.rebuild("Application deleted")
                st.rerun()
