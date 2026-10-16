"""Analytics: the detail behind the headline, by stage, channel, timing, segment, month and
employer. Every chart says how to read it and what it means, and every bar, dot and row
opens the applications behind it."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.ui import charts, data, drill, theme
from app.ui import components as ui
from jobtracker import benchmarks as bm

SRC = "Source: your Gmail, read by jobtracker into a DuckDB warehouse"
STAGE_SET = {"Applied": [], "Employer responded": ["response"], "Assessment": ["assessment"],
             "Interview": ["interview"], "Offer": ["offer"]}

k = data.kpis()
if not k or not int(k.get("applications") or 0):
    ui.lede("Analytics", "Nothing to analyse yet.",
            "No applications match the current filters.")
    st.stop()

overall = float(k["response_rate_pct"])
ui.lede("Analytics", [
    (f"Where the {int(k['interviews'])} interviews came from", "em"),
    (f", and what the {int(k['ghosted'])} silences have in common.", ""),
], "The same numbers as the Overview, cut every way: by stage, channel, timing, type of role, "
   "month and employer. Click any bar, dot or row to see the applications behind it.")


def _pick_table(frame: pd.DataFrame, key: str, **kwargs) -> pd.Series | None:
    """A table whose rows open their applications; returns the clicked row once."""
    seq = int(st.session_state.get(f"_jt_seq_{key}", 0))
    event = st.dataframe(frame, key=f"{key}-{seq}", on_select="rerun",
                         selection_mode="single-row", hide_index=True, **kwargs)
    rows = event.selection.rows if event is not None else []
    if rows:
        st.session_state[f"_jt_seq_{key}"] = seq + 1
        return frame.iloc[rows[0]]
    return None


t_flow, t_chan, t_time, t_seg, t_coh, t_emp = st.tabs(
    ["Stages", "Channels", "Timing", "Types of role", "Months", "Employers"])

# ---------------------------------------------------------------- stages
with t_flow:
    funnel = data.metric("funnel")
    replied = funnel.loc[funnel["stage"] == "Employer responded", "pct_of_applied"]
    left, right = st.columns([7, 5], gap="large")
    with left:
        ui.subhead(f"{float(replied.iloc[0]):.0f} in every 100 applications get any reply"
                   if not replied.empty else "How far applications get")
        ui.explain("Each blue bar is how many of every 100 applications got at least that far. "
                   "The grey strip under a bar is what published hiring data says is normal.",
                   "The biggest drop between two bars is where your search loses most "
                   "applications, so that is the step to work on. Click a bar to see them.")
        point = charts.show(charts.funnel(funnel), key="h-funnel", clickable=True)
        if point and point.get("customdata"):
            stage = str(point["customdata"][0])
            drill.open_set(f"Got as far as: {stage}",
                           drill.narrowed(reached=STAGE_SET.get(stage, [])))
        ui.source(f"{SRC}. Benchmark: the published 100-application funnel.")
    with right:
        timing = data.metric("stage_timing")
        first = timing[timing["milestone"] == "First reply"]
        inter = timing[timing["milestone"] == "Interview"]
        ui.subhead(f"A first reply takes about {charts.fmt_days(first.iloc[0]['median_days'])}; "
                   f"an interview about {charts.fmt_days(inter.iloc[0]['median_days'])}"
                   if not first.empty and not inter.empty else "Time to each step")
        ui.explain("The dot is the typical number of days from applying (half are faster, half "
                   "slower). The grey bar covers the middle half of cases.",
                   "If an application is well past the end of its grey bar, it is late. "
                   "A short follow-up is reasonable.")
        charts.show(charts.stage_timing(timing), key="timing")
        ui.source(f"{SRC}. Only replies with a known date are counted.")

    moves = data.metric("transitions")
    real = moves[moves["from_status"] != "(new)"]
    top = real.iloc[0] if not real.empty else None
    left, right = st.columns([7, 5], gap="large")
    with left:
        ui.subhead(f"The most common move is {top['from_status']} to {top['to_status']} "
                   f"({int(top['transitions'])} times)" if top is not None
                   else "How statuses change")
        ui.explain("Each row is where an application was, each column where it went next, and "
                   "the number is how many times that happened. Darker means more often.",
                   "A big number from Applied straight to Ghosted means many applications "
                   "never get read. Tailoring fewer applications usually beats sending more.")
        charts.show(charts.transitions(moves), key="transitions")
        ui.source(f"{SRC}. One count per status change in the event log.")
    with right:
        ui.subhead("Where everything ended up")
        ui.explain("The same flow as on the Overview: left is how far each application got, "
                   "right is where it stands today.")
        charts.show(charts.sankey(data.metric("sankey"), height=360), key="flow-a")

# ---------------------------------------------------------------- channels
with t_chan:
    perf = data.metric("platform_performance")
    ok = perf[perf["sample_confidence"] != "Too small"].sort_values("response_rate_pct",
                                                                     ascending=False)
    best = str(ok.iloc[0]["platform"]) if not ok.empty else None
    left, right = st.columns([6, 6], gap="large")
    with left:
        ui.subhead(f"{best} gets the most replies" if best else "Replies by site")
        ui.explain("Each dot is the share of applications on that site that got a reply. The "
                   "grey bar is the likely range given how few applications there are; hollow "
                   "dots have too few to judge. The black line is your average.",
                   "Sites to the right of the line work better for you. Click a dot to see "
                   "its applications.")
        point = charts.show(charts.rate_ci(perf, "platform", reference=overall,
                                           reference_label=f"Your average {overall:.0f}%",
                                           highlight=best, confidence_col="sample_confidence"),
                            key="chan-ci", clickable=True)
        if point and point.get("y"):
            drill.open_set(f"{point['y']}: every application",
                           drill.narrowed(platforms=[str(point["y"])]))
        ui.source(f"{SRC}. With samples this small, treat it as a lead, not proof.")
    with right:
        direct = perf[perf["is_direct"]]
        ui.subhead("Applying direct clears the published 6.87%"
                   if not direct.empty and (direct["interview_rate_pct"] >= bm.DIRECT_INTERVIEW_RATE_PCT).any()
                   else "Interviews by site")
        ui.explain(f"Each bar is the share of applications on that site that reached an "
                   f"interview. Blue bars are applications made directly to the employer. The "
                   f"two lines are published figures: {bm.DIRECT_INTERVIEW_RATE_PCT}% for "
                   f"applying direct and {bm.JOB_BOARD_INTERVIEW_RATE_PCT}% for one-click "
                   f"Easy Apply.",
                   "A bar past the right-hand line is doing better than most people manage. "
                   "Click a bar to see its applications.")
        point = charts.show(charts.interview_rates(perf), key="h-interview", clickable=True)
        if point and point.get("y"):
            drill.open_set(f"{point['y']}: every application",
                           drill.narrowed(platforms=[str(point["y"])]))
        ui.source(f"{SRC}. Benchmark: 1.24M tracked applications (direct vs job board).")
    left, right = st.columns([6, 6], gap="large")
    with left:
        ui.subhead("Where you apply most is not where replies come from")
        ui.explain("Across: how many applications you sent on each site. Up: the share that "
                   "got a reply. The grey whiskers are the likely range.",
                   "Sites in the top left reply well but get little of your time. Try sending "
                   "more there.")
        point = charts.show(charts.volume_vs_rate(perf), key="chan-scatter", clickable=True)
        if point and point.get("customdata"):
            name = str(point["customdata"][-1])
            drill.open_set(f"{name}: every application", drill.narrowed(platforms=[name]))
    with right:
        ui.subhead("The numbers")
        ui.explain("Click a row to see that site's applications.")
        row = _pick_table(
            perf, "chan-table",
            column_order=["platform", "applications", "response_rate_pct", "response_rate_lo",
                          "response_rate_hi", "interview_rate_pct", "vs_direct_benchmark_pp",
                          "sample_confidence"],
            column_config={
                "platform": "Site",
                "applications": st.column_config.NumberColumn("Sent", width="small"),
                "response_rate_pct": st.column_config.NumberColumn("Reply %", format="%.1f",
                                                                   width="small"),
                "response_rate_lo": st.column_config.NumberColumn(
                    "Likely low", format="%.1f", width="small",
                    help="The lowest the true reply rate is likely to be."),
                "response_rate_hi": st.column_config.NumberColumn(
                    "Likely high", format="%.1f", width="small",
                    help="The highest the true reply rate is likely to be."),
                "interview_rate_pct": st.column_config.NumberColumn("Interview %",
                                                                    format="%.1f", width="small"),
                "vs_direct_benchmark_pp": st.column_config.NumberColumn(
                    "vs 6.87%", format="%+.1f", width="small",
                    help="Interview rate minus the published rate for applying direct, in "
                         "percentage points."),
                "sample_confidence": st.column_config.TextColumn(
                    "Enough data?", width="small",
                    help="Adequate: 30+ applications. Indicative: 10 to 29. Too small: fewer."),
            })
        if row is not None:
            drill.open_set(f"{row['platform']}: every application",
                           drill.narrowed(platforms=[str(row["platform"])]))

# ---------------------------------------------------------------- timing
with t_time:
    km = data.metric("response_survival")
    median_reply = k.get("median_days_to_response")
    km_fig, marks = charts.survival(
        km, median_reply=None if pd.isna(median_reply) else float(median_reply))
    ui.subhead(f"By day 21, {marks[21]:.0f}% have heard back. After that, replies rarely come"
               if 21 in marks else "Time to a first reply")
    ui.explain("The line starts at 100% on the day you apply and drops each time an employer "
               "replies. Where it goes flat, replies have stopped. Applications still waiting "
               "are counted for as long as they have waited (the small ticks), so employers "
               "don't look faster than they are.",
               "Once an application is past the point where the line flattens, send one "
               "follow-up and then put your energy into new applications.")
    charts.show(km_fig, key="km-a")
    ui.source(f"{SRC}. Replies with an unknown date are left out.")
    left, right = st.columns(2, gap="large")
    with left:
        buckets = data.metric("response_time_buckets")
        peak = buckets.loc[buckets["responses"].idxmax(), "bucket"] if not buckets.empty else None
        ui.subhead(f"Most replies arrive within {peak}" if peak else "Reply time")
        ui.explain("Replies with a known date, grouped by how long they took. The blue bar is "
                   "the most common wait.")
        charts.show(charts.reply_buckets(buckets), key="buckets")
    with right:
        days = data.metric("weekday_performance")
        ui.subhead("Which day you apply barely matters")
        ui.explain(f"Reply rate by the weekday you sent the application. Faded bars have fewer "
                   f"than {bm.SAMPLE_INDICATIVE} applications, so ignore them.",
                   "Apply when the job is posted rather than waiting for a particular day.")
        charts.show(charts.weekday(days), key="weekday")

# ---------------------------------------------------------------- types of role
SEGMENTS = {"role_family": "Role family", "industry": "Industry", "seniority": "Seniority",
            "job_type": "Job type", "channel": "Direct vs job board"}
FILTER_FIELD = {"role_family": "role_families", "industry": "industries",
                "seniority": "seniorities", "job_type": "job_types", "channel": "channels"}
with t_seg:
    seg = st.segmented_control("Group by", list(SEGMENTS), format_func=SEGMENTS.get,
                               default="role_family", key="seg_dim", required=True)
    frame = data.metric("segment_performance", segment=seg)
    lead = frame[frame["applications"] >= bm.SAMPLE_INDICATIVE].sort_values(
        "response_rate_pct", ascending=False)
    left, right = st.columns([7, 5], gap="large")
    with left:
        ui.subhead(f"{lead.iloc[0]['segment']} gets the most replies"
                   if not lead.empty else f"Replies by {SEGMENTS[seg].lower()}")
        ui.explain(f"Each dot is the reply rate for one {SEGMENTS[seg].lower()}, with its likely "
                   f"range in grey. Hollow dots have fewer than {bm.SAMPLE_INDICATIVE} "
                   "applications. The line is your average.",
                   "Lean towards the kinds of role to the right of the line. Click a dot to "
                   "see those applications.")
        point = charts.show(charts.rate_ci(frame, "segment", reference=overall,
                                           reference_label=f"Your average {overall:.0f}%",
                                           highlight=str(lead.iloc[0]["segment"]) if not lead.empty else None),
                            key="seg-ci", clickable=True)
        if point and point.get("y"):
            drill.open_set(f"{point['y']}: every application",
                           drill.narrowed(**{FILTER_FIELD[seg]: [str(point["y"])]}))
        ui.source(f"{SRC}.")
    with right:
        ui.subhead("The numbers")
        ui.explain("Click a row to see its applications.")
        row = _pick_table(frame, f"seg-table-{seg}", column_config={
            "segment": SEGMENTS[seg], "applications": "Sent", "responses": "Replies",
            "interviews": "Interviews", "offers": "Offers",
            "response_rate_pct": st.column_config.NumberColumn("Reply %", format="%.1f"),
            "response_rate_lo": st.column_config.NumberColumn("Likely low", format="%.1f"),
            "response_rate_hi": st.column_config.NumberColumn("Likely high", format="%.1f"),
            "interview_rate_pct": st.column_config.NumberColumn("Interview %", format="%.1f"),
            "median_days_to_response": st.column_config.NumberColumn("Typical reply (days)",
                                                                     format="%.1f"),
        })
        if row is not None:
            drill.open_set(f"{row['segment']}: every application",
                           drill.narrowed(**{FILTER_FIELD[seg]: [str(row["segment"])]}))

# ---------------------------------------------------------------- months
COHORT_SET = {"Offer": ["offer"], "In progress": ["active"], "Ghosted": ["ghosted"],
              "Rejected": ["rejected"]}
with t_coh:
    cohorts = data.metric("monthly_cohorts")
    if cohorts.empty:
        ui.empty_state("No months yet", "This appears once applications span a month.")
    else:
        mature = cohorts[~cohorts["is_maturing"]
                         & (cohorts["applications"] >= bm.SAMPLE_INDICATIVE)]
        best_month = (mature.loc[mature["interview_rate_pct"].idxmax()] if not mature.empty
                      else None)
        left, right = st.columns(2, gap="large")
        with left:
            ui.subhead("Most months end in silence, not rejection")
            ui.explain("One bar per month you applied in, split by where those applications "
                       "stand today. The latest months are still playing out.",
                       "Click a coloured block to see exactly those applications.")
            ui.render('<div class="jt-key">' + "".join(
                f'<span><i style="background:{theme.FLOW_COLORS[name]}"></i>{name}</span>'
                for _, name in charts.COHORT_PARTS) + "</div>")
            point = charts.show(charts.cohort_outcomes(cohorts), key="cohorts", clickable=True)
            if point and point.get("customdata"):
                month = str(point["customdata"][0])
                part = charts.COHORT_PARTS[int(point.get("curve_number") or 0)][1]
                drill.open_set(f"{part}, applied in {data.month_name(month)}",
                               drill.narrowed(month=month, reached=COHORT_SET[part]))
            ui.source(f"{SRC}.")
        with right:
            label = (pd.Timestamp(best_month["cohort_month"] + "-01").strftime("%B")
                     if best_month is not None else "")
            ui.subhead(f"{label} was the best month for interviews" if label
                       else "Reply and interview rate by month")
            ui.explain("The blue line is the share of each month's applications that got a "
                       "reply; the grey line the share that reached an interview. Hollow points "
                       "are recent months that are still playing out, so they will rise. "
                       f"Months with fewer than {bm.SAMPLE_INDICATIVE} applications are not "
                       "ranked.",
                       "Compare what you did differently in your best month. Click a point to "
                       "see that month's applications.")
            point = charts.show(charts.cohort_rates(cohorts), key="cohort-rates", clickable=True)
            if point and point.get("customdata"):
                month = str(point["customdata"][0])
                drill.open_set(f"Applied in {data.month_name(month)}", drill.narrowed(month=month))

# ---------------------------------------------------------------- employers
with t_emp:
    board = data.metric("company_leaderboard")
    ui.subhead(f"{board.iloc[0]['company_name']} has had the most applications"
               if not board.empty else "Employers")
    ui.explain("Every employer you applied to, with how far you got. Repeat applications to "
               "the same employer are common in a long search.",
               "Click an employer to see every application you sent them.")
    row = _pick_table(board, "employers", height=min(560, 42 + 35 * len(board)), column_config={
        "company_name": st.column_config.TextColumn("Employer"),
        "industry": "Industry", "applications": "Sent", "distinct_roles": "Roles",
        "responses": "Replies", "interviews": "Interviews", "offers": "Offers", "open": "Open",
        "response_rate_pct": st.column_config.ProgressColumn("Reply rate", min_value=0,
                                                             max_value=100, format="%.0f%%"),
        "last_applied": st.column_config.DateColumn("Last applied", format="D MMM YYYY"),
    })
    if row is not None:
        drill.open_set(f"{row['company_name']}: every application",
                       drill.narrowed(companies=[str(row["company_name"])]))
    ui.source(f"{SRC}.")
