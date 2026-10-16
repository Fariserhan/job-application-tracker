"""Pipeline: every application, as a board or a table. Any card or row opens the
application sheet. Links from the rest of the app land here already filtered (e.g.
``pipeline?view=table&reached=interview&platform=LinkedIn``), with removable chips."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from app.ui import components as ui
from app.ui import data, drill, theme
from jobtracker import domain

s = data.settings()
qp = st.query_params
focus = data.drill_from_url()
filters = data.with_drill(focus)
apps = data.metric("applications", filters=filters)

OPEN_LANES = [domain.APPLIED, domain.ASSESSMENT, domain.INTERVIEW, domain.OFFER]
CLOSED_LANES = [domain.REJECTED, domain.GHOSTED, domain.WITHDRAWN]
MANUAL_PLATFORMS = list(dict.fromkeys([*domain.PLATFORMS, "Company Site", "Referral"]))

open_n = int(apps["is_active"].sum()) if not apps.empty else 0
if focus.active:
    ui.lede("Pipeline", [(f"{len(apps)} application{'s' if len(apps) != 1 else ''}", "em"),
                         (" match what you clicked.", "")],
            "Click any row to open it. Remove a chip below to widen the list again.")
else:
    ui.lede("Pipeline", [(f"{open_n} applications are still open.", "em"),
                         (" Here is where each one stands.", "")],
            "The board shows open applications by stage; the table holds everything. Click any "
            "card or row to see its history, change its status, add notes or fix details.")


# ---------------------------------------------------------------- what is shown
def _drop(param: str, value: str) -> None:
    remaining = [v for v in qp.get_all(param) if v != value]
    if remaining:
        qp[param] = remaining
    else:
        del qp[param]


if focus.active:
    chips = st.container(horizontal=True, gap="small", vertical_alignment="center",
                         key="jt-chips")
    chips.markdown("**Showing:**")
    for param, value, label in focus.chips():
        if chips.button(label, key=f"chip_{param}_{value}", icon=":material/close:",
                        help="Remove this filter"):
            _drop(param, value)
            st.rerun()
    if chips.button("Show everything", key="chip_all", type="tertiary"):
        for param in data.DRILL_PARAMS:
            if param in qp:
                del qp[param]
        st.rerun()


# ---------------------------------------------------------------- toolbar
def _log_form() -> None:
    with st.form("log_application", clear_on_submit=True, border=False):
        st.caption("For jobs you applied to without an email trail: a careers page, a "
                   "referral, a walk-in. They count in every number like the Gmail ones.")
        company = st.text_input("Company *")
        role = st.text_input("Role *")
        c1, c2 = st.columns(2)
        platform = c1.selectbox("Where you applied", MANUAL_PLATFORMS,
                                index=MANUAL_PLATFORMS.index("Company Site"))
        applied_on = c2.date_input("Applied on", value=date.today(), max_value=date.today(),
                                   format="DD/MM/YYYY")
        status = st.selectbox("Where it stands", [domain.APPLIED, domain.ASSESSMENT,
                                                  domain.INTERVIEW, domain.OFFER, domain.SAVED])
        job_url = st.text_input("Job posting link", placeholder="https://...")
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


default_view = "Table" if (qp.get("view") == "table" or focus.active) else "Board"
if st.session_state.get("_jt_pipe_focus") != repr(focus):
    st.session_state["_jt_pipe_focus"] = repr(focus)
    st.session_state["pipe_view"] = default_view
bar = st.container(horizontal=True, vertical_alignment="center", gap="small")
with bar:
    view = st.segmented_control("View", ["Board", "Table"], key="pipe_view",
                                label_visibility="collapsed", required=True)
    st.space("stretch")
    with st.popover("Log an application", icon=":material/add_circle:"):
        _log_form()
    st.download_button("Download CSV", apps.to_csv(index=False).encode("utf-8"),
                       file_name=f"applications-{date.today():%Y%m%d}.csv", mime="text/csv",
                       icon=":material/download:", disabled=apps.empty)

if apps.empty:
    ui.empty_state("No applications match",
                   "Remove a filter above, sync Gmail, or log an application by hand.")
    st.stop()

# Counts that open their own set in one click.
counts = apps["current_status"].value_counts()
count_bar = st.container(horizontal=True, gap="small", key="jt-countbar")
for status in [domain.APPLIED, domain.ASSESSMENT, domain.INTERVIEW, domain.OFFER,
               domain.REJECTED, domain.GHOSTED]:
    n = int(counts.get(status, 0))
    if count_bar.button(f"{theme.SHORT_STATUS.get(status, status)}  **{n}**",
                        key=f"count_{status}", disabled=not n):
        drill.open_set(f"{theme.SHORT_STATUS.get(status, status)}: where each one stands",
                       drill.narrowed(statuses=[status], **{
                           "reached": filters.reached, "month": filters.month,
                           "companies": filters.companies, "channels": filters.channels}))


# ---------------------------------------------------------------- board
def _card_label(row) -> str:
    quiet = None if pd.isna(row.days_since_activity) else int(row.days_since_activity)
    stale = (quiet is not None and quiet >= s.follow_up_after_days
             and row.current_status in domain.ACTIVE)
    days = "" if quiet is None else f"{quiet} day{'s' if quiet != 1 else ''} quiet"
    age = f":orange[**{days}**]" if stale else days
    return (f"**{row.company_name}**  \n{row.role_title}  \n"
            f"{row.platform} · {ui.fmt_date(row.applied_date, '%d %b')} · {age}")


def _lane(column, status: str, frame: pd.DataFrame, per_lane: int) -> None:
    with column:
        name = theme.SHORT_STATUS.get(status, status)
        ui.render(f'<div class="jt-col-head" style="--lane:{theme.status_color(status)}">'
                  f'<strong>{ui.esc(name)}</strong><span>{len(frame)}</span></div>')
        if frame.empty:
            ui.render('<div class="jt-col-empty">Nothing here right now.</div>')
        for row in frame.head(per_lane).itertuples(index=False):
            if st.button(_card_label(row), key=f"card-{row.application_id}", width="stretch"):
                drill.open_application(str(row.application_id))
        more = len(frame) - per_lane
        if more > 0 and st.button(f"See all {len(frame)}", key=f"more_{status}",
                                  type="tertiary", icon=":material/arrow_outward:"):
            drill.open_set(f"{name}: every application", drill.narrowed(statuses=[status]))


if view == "Board":
    by_activity = apps.sort_values(["last_activity_date", "applied_date"], ascending=False)
    lanes = st.columns(len(OPEN_LANES), gap="medium")
    for col, lane in zip(lanes, OPEN_LANES, strict=True):
        _lane(col, lane, by_activity[by_activity["current_status"] == lane], 8)
    closed = by_activity[by_activity["current_status"].isin(CLOSED_LANES)]
    with st.expander(f"Closed: {len(closed)} applications "
                     f"({', '.join(f'{theme.SHORT_STATUS.get(c, c)} {int(counts.get(c, 0))}' for c in CLOSED_LANES)})",
                     icon=":material/inventory_2:"):
        lanes = st.columns(len(CLOSED_LANES), gap="medium")
        for col, lane in zip(lanes, CLOSED_LANES, strict=True):
            _lane(col, lane, closed[closed["current_status"] == lane], 6)
else:
    table = apps.reset_index(drop=True)
    seq = int(st.session_state.get("_jt_table_seq", 0))
    event = st.dataframe(
        table, key=f"pipe_table_{seq}", on_select="rerun", selection_mode="single-row",
        hide_index=True, height=min(560, 42 + 35 * len(table)), row_height=35, placeholder="",
        column_order=["company_name", "role_title", "current_status", "platform",
                      "max_stage_reached", "applied_date", "last_activity_date",
                      "days_since_activity", "days_to_response", "industry", "job_url",
                      "gmail_link"],
        column_config={
            **drill.list_config(),
            "max_stage_reached": st.column_config.ProgressColumn(
                "Got as far as", min_value=0, max_value=4, format="%d of 4", width="small",
                help="1 applied, 2 assessment, 3 interview, 4 offer. The furthest it ever got, "
                     "so a rejection after an interview still shows the interview."),
            "last_activity_date": st.column_config.DateColumn("Last activity",
                                                              format="D MMM YYYY"),
            "days_to_response": st.column_config.NumberColumn(
                "Replied after", format="%d days",
                help="Blank when there was no reply yet, or its date is unknown."),
            "industry": st.column_config.TextColumn("Industry", width="small"),
            "job_url": st.column_config.LinkColumn("Posting", display_text="Open",
                                                   width="small"),
        },
    )
    rows = event.selection.rows if event is not None else []
    if rows:
        st.session_state["_jt_table_seq"] = seq + 1  # clear the highlight after closing
        drill.open_application(str(table.iloc[rows[0]]["application_id"]))
    st.caption("Click any row to open it.")

# A link straight to one application (from Follow-ups, the change log, a bookmark).
target = qp.get("app")
if target and st.session_state.get("_jt_opened_app") != target:
    st.session_state["_jt_opened_app"] = target
    drill.open_application(str(target))
