"""Analytics — the detail behind the headline: flow, channels, timing, segments, cohorts."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.ui import charts, data, theme
from app.ui import components as ui
from jobtracker import benchmarks as bm

SRC = "Source: Gmail via jobtracker → DuckDB warehouse"

k = data.kpis()
if not k or not int(k.get("applications") or 0):
    ui.lede("Analytics", "Nothing to analyse yet.",
            "No applications match the current filters.")
    st.stop()

ui.lede("Analytics", [
    (f"Where the {int(k['interviews'])} interviews came from", "em"),
    (f", and what the {int(k['ghosted'])} silences have in common.", ""),
], "The same SQL metric layer as the overview, cut by stage, channel, timing, segment, "
   "month and employer. Every rate carries its sample size and a 95% Wilson interval; "
   "small samples are labelled rather than hidden.")

t_flow, t_chan, t_time, t_seg, t_coh, t_emp = st.tabs(
    ["Funnel & flow", "Channels", "Timing", "Segments", "Cohorts", "Employers"])

# ---------------------------------------------------------------- funnel & flow
with t_flow:
    funnel = data.metric("funnel")
    replied = funnel.loc[funnel["stage"] == "Employer responded", "pct_of_applied"]
    left, right = st.columns([7, 5], gap="large")
    with left:
        ui.subhead(f"Only {float(replied.iloc[0]):.0f} in 100 applications get any reply"
                   if not replied.empty else "Application funnel",
                   "Applications per 100 sent reaching each stage (blue), against the "
                   "published per-100 range (grey).")
        charts.show(charts.funnel(funnel), key="h-funnel")
        ui.source(f"{SRC}. Benchmark: the published 100-application funnel.")
    with right:
        timing = data.metric("stage_timing")
        first = timing[timing["milestone"] == "First reply"]
        inter = timing[timing["milestone"] == "Interview"]
        ui.subhead(f"First replies take a median {charts.fmt_days(first.iloc[0]['median_days'])}; "
                   f"interviews {charts.fmt_days(inter.iloc[0]['median_days'])}"
                   if not first.empty and not inter.empty else "Time to each milestone",
                   "Median days from applying (dot) and the middle half of cases (bar). "
                   "Known dates only.")
        charts.show(charts.stage_timing(timing), key="timing")
        ui.source(f"{SRC}. Unknown dates are excluded, never zero-filled.")

    moves = data.metric("transitions")
    real = moves[moves["from_status"] != "(new)"]
    top = real.iloc[0] if not real.empty else None
    left, right = st.columns([7, 5], gap="large")
    with left:
        ui.subhead(f"The most common move is {top['from_status']} → {top['to_status']} "
                   f"({int(top['transitions'])} times)" if top is not None
                   else "Status transitions",
                   "Every status change in the event log: rows are where an application was, "
                   "columns where it went next.")
        charts.show(charts.transitions(moves), key="transitions")
        ui.source(f"{SRC} · fct_status_events (one row per status change).")
    with right:
        ui.subhead("Where everything ended up", "Furthest stage → current status.")
        charts.show(charts.sankey(data.metric("sankey"), height=360), key="flow-a")

# ---------------------------------------------------------------- channels
with t_chan:
    perf = data.metric("platform_performance")
    ok = perf[perf["sample_confidence"] != "Too small"].sort_values("response_rate_pct",
                                                                     ascending=False)
    best = str(ok.iloc[0]["platform"]) if not ok.empty else None
    left, right = st.columns([6, 6], gap="large")
    with left:
        ui.subhead(f"{best} leads on replies" if best else "Replies by channel",
                   "Response rate with its 95% Wilson interval; hollow = too few applications. "
                   "The line is your overall rate.")
        charts.show(charts.rate_ci(perf, "platform", reference=float(k["response_rate_pct"]),
                                   reference_label=f"Overall {float(k['response_rate_pct']):.0f}%",
                                   highlight=best, confidence_col="sample_confidence"),
                    key="chan-ci")
        ui.source(f"{SRC}. Indicative only — not statistically significant at this sample size.")
    with right:
        direct = perf[perf["is_direct"]]
        ui.subhead("Direct applications clear the 6.87% benchmark"
                   if not direct.empty and (direct["interview_rate_pct"] >= bm.DIRECT_INTERVIEW_RATE_PCT).any()
                   else "Interview rate by channel",
                   "Share reaching interview. Blue = direct to the employer; lines = published "
                   f"rates for direct ({bm.DIRECT_INTERVIEW_RATE_PCT}%) and Easy Apply "
                   f"({bm.JOB_BOARD_INTERVIEW_RATE_PCT}%).")
        charts.show(charts.interview_rates(perf), key="h-interview")
        ui.source(f"{SRC}. Benchmark: 1.24M tracked applications (direct vs job board).")
    left, right = st.columns([6, 6], gap="large")
    with left:
        ui.subhead("Volume and reply rate rarely live in the same place",
                   "Applications sent against response rate, with 95% intervals.")
        charts.show(charts.volume_vs_rate(perf), key="chan-scatter")
    with right:
        ui.subhead("The numbers", "Straight from platform_performance.")
        st.dataframe(
            perf, hide_index=True,
            column_order=["platform", "applications", "response_rate_pct", "response_rate_lo",
                          "response_rate_hi", "interview_rate_pct", "vs_direct_benchmark_pp",
                          "sample_confidence"],
            column_config={
                "platform": "Channel",
                "applications": st.column_config.NumberColumn("Sent", width="small"),
                "response_rate_pct": st.column_config.NumberColumn("Reply %", format="%.1f",
                                                                   width="small"),
                "response_rate_lo": st.column_config.NumberColumn("Low", format="%.1f",
                                                                  width="small"),
                "response_rate_hi": st.column_config.NumberColumn("High", format="%.1f",
                                                                  width="small"),
                "interview_rate_pct": st.column_config.NumberColumn("Intv %", format="%.1f",
                                                                    width="small"),
                "vs_direct_benchmark_pp": st.column_config.NumberColumn(
                    "vs 6.87%", format="%+.1f", width="small",
                    help="Interview rate minus the published direct-application rate, in "
                         "percentage points."),
                "sample_confidence": st.column_config.TextColumn("Sample", width="small"),
            })

# ---------------------------------------------------------------- timing
with t_time:
    km = data.metric("response_survival")
    median_reply = k.get("median_days_to_response")
    km_fig, marks = charts.survival(
        km, median_reply=None if pd.isna(median_reply) else float(median_reply))
    ui.subhead(f"By day 21, {marks[21]:.0f}% have heard back; the curve barely moves after"
               if 21 in marks else "Time to a first reply",
               "Kaplan–Meier: the share still waiting for any reply. Open applications are "
               "censored at their current age (ticks) rather than dropped, which would flatter "
               "employers.")
    charts.show(km_fig, key="km-a")
    ui.source(f"{SRC}. Replies with an unknown date are excluded.")
    left, right = st.columns(2, gap="large")
    with left:
        buckets = data.metric("response_time_buckets")
        peak = buckets.loc[buckets["responses"].idxmax(), "bucket"] if not buckets.empty else None
        ui.subhead(f"Most replies land in {peak}" if peak else "Reply time",
                   "Replies with a known date, by how long they took.")
        charts.show(charts.reply_buckets(buckets), key="buckets")
    with right:
        days = data.metric("weekday_performance")
        ui.subhead("The weekday you apply barely matters",
                   "Response rate by the weekday the application was sent; faded bars have "
                   f"fewer than {bm.SAMPLE_INDICATIVE} applications. Indicative only.")
        charts.show(charts.weekday(days), key="weekday")

# ---------------------------------------------------------------- segments
SEGMENTS = {"role_family": "Role family", "industry": "Industry", "seniority": "Seniority",
            "job_type": "Job type", "channel": "Channel type"}
with t_seg:
    seg = st.segmented_control("Cut by", list(SEGMENTS), format_func=SEGMENTS.get,
                               default="role_family", key="seg_dim", required=True)
    frame = data.metric("segment_performance", segment=seg)
    lead = frame[frame["applications"] >= bm.SAMPLE_INDICATIVE].sort_values(
        "response_rate_pct", ascending=False)
    left, right = st.columns([7, 5], gap="large")
    with left:
        ui.subhead(f"{lead.iloc[0]['segment']} replies most often, within the noise"
                   if not lead.empty else f"Response rate by {SEGMENTS[seg].lower()}",
                   "Response rate with its 95% Wilson interval; hollow = fewer than "
                   f"{bm.SAMPLE_INDICATIVE} applications.")
        charts.show(charts.rate_ci(frame, "segment", reference=float(k["response_rate_pct"]),
                                   reference_label=f"Overall {float(k['response_rate_pct']):.0f}%",
                                   highlight=str(lead.iloc[0]["segment"]) if not lead.empty else None),
                    key="seg-ci")
        ui.source(f"{SRC} · segment_performance({seg}).")
    with right:
        ui.subhead("The numbers")
        st.dataframe(frame, hide_index=True, column_config={
            "segment": SEGMENTS[seg], "applications": "Sent", "responses": "Replies",
            "interviews": "Interviews", "offers": "Offers",
            "response_rate_pct": st.column_config.NumberColumn("Reply %", format="%.1f"),
            "response_rate_lo": st.column_config.NumberColumn("CI low", format="%.1f"),
            "response_rate_hi": st.column_config.NumberColumn("CI high", format="%.1f"),
            "interview_rate_pct": st.column_config.NumberColumn("Interview %", format="%.1f"),
            "median_days_to_response": st.column_config.NumberColumn("Median reply (d)",
                                                                     format="%.1f"),
        })

# ---------------------------------------------------------------- cohorts
with t_coh:
    cohorts = data.metric("monthly_cohorts")
    if cohorts.empty:
        ui.empty_state("No cohorts yet", "Cohorts appear once applications span a month.")
    else:
        mature = cohorts[~cohorts["is_maturing"]
                         & (cohorts["applications"] >= bm.SAMPLE_INDICATIVE)]
        best_month = (mature.loc[mature["interview_rate_pct"].idxmax()] if not mature.empty
                      else None)
        left, right = st.columns(2, gap="large")
        with left:
            ui.subhead("Most months end in silence, not rejection",
                       "Applications grouped by the month they were sent, by where they stand "
                       "today. Recent months are still maturing.")
            ui.render('<div class="jt-key">' + "".join(
                f'<span><i style="background:{theme.FLOW_COLORS[name]}"></i>{name}</span>'
                for _, name in charts.COHORT_PARTS) + "</div>")
            charts.show(charts.cohort_outcomes(cohorts), key="cohorts")
            ui.source(f"{SRC} · monthly_cohorts.")
        with right:
            label = (pd.Timestamp(best_month["cohort_month"] + "-01").strftime("%B")
                     if best_month is not None else "")
            ui.subhead(f"{label} was the best month for interviews" if label
                       else "Rates by month",
                       "Response and interview rate per monthly cohort; hollow points are "
                       "still maturing — read them as floors. Months under "
                       f"{bm.SAMPLE_INDICATIVE} applications are not ranked.")
            charts.show(charts.cohort_rates(cohorts), key="cohort-rates")

# ---------------------------------------------------------------- employers
with t_emp:
    board = data.metric("company_leaderboard")
    ui.subhead(f"{board.iloc[0]['company_name']} has had the most applications"
               if not board.empty else "Employers",
               "Repeat applications, outcomes and recency per employer.")
    st.dataframe(board, hide_index=True, height=520, column_config={
        "company_name": st.column_config.TextColumn("Employer", pinned=True),
        "industry": "Industry", "applications": "Sent", "distinct_roles": "Roles",
        "responses": "Replies", "interviews": "Interviews", "offers": "Offers", "open": "Open",
        "response_rate_pct": st.column_config.ProgressColumn("Reply rate", min_value=0,
                                                             max_value=100, format="%.0f%%"),
        "last_applied": st.column_config.DateColumn("Last applied", format="DD MMM YYYY"),
    })
    ui.source(f"{SRC} · company_leaderboard.")
