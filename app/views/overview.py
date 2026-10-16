"""Overview — the search as a story: lede, KPI strip, flow, channels, timing, rhythm, actions."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.ui import charts, data, narrative
from app.ui import components as ui
from jobtracker import benchmarks as bm

SOURCE = "Source: Gmail via jobtracker → DuckDB warehouse"

k = data.kpis()
if not k or not int(k.get("applications") or 0):
    ui.lede("Overview", "Nothing to show yet.",
            "No applications match the current filters. Clear the filters, or sync Gmail to "
            "pull in your job mail.")
    st.stop()

s = data.settings()
period_frame = data.metric("period_compare")
period = period_frame.iloc[0].to_dict() if not period_frame.empty else {}
weekly = data.metric("weekly_velocity")
funnel = data.metric("funnel")
timing = data.metric("stage_timing")
flows = data.metric("sankey")
platforms = data.metric("platform_performance")
channels = data.metric("segment_performance", segment="channel")
queue = data.metric("follow_up_queue")
km = data.metric("response_survival")
median_reply = k.get("median_days_to_response")
km_fig, marks = charts.survival(
    km, median_reply=None if pd.isna(median_reply) else float(median_reply))
notes = narrative.notes(k, platforms, channels, queue, marks, period, s)
best = narrative.best_platform(platforms)

ui.lede("The search so far", narrative.lede_segments(k),
        "Every figure on this page is computed once, in SQL, over a star-schema warehouse fed by "
        "Gmail — then compared with published hiring benchmarks. Rates carry 95% intervals, "
        "because one person's search is a small sample.")

# ---------------------------------------------------------------- KPI strip
change = int(period.get("change") or 0)
interview_rate = float(k.get("interview_rate_pct") or 0)
offer_row = funnel[funnel["stage"] == "Offer"]
offer_vs = str(offer_row.iloc[0]["vs_benchmark"]) if not offer_row.empty else ""
reply_row = timing[timing["milestone"] == "First reply"]
middle = ""
if not reply_row.empty and pd.notna(reply_row.iloc[0]["p25_days"]):
    middle = (f"Middle half {ui.fmt_num(reply_row.iloc[0]['p25_days'])}–"
              f"{ui.fmt_num(reply_row.iloc[0]['p75_days'])} days")
band = str(k.get("response_band") or "")
ui.kpi_strip([
    ui.Stat("Applications", int(k["applications"]),
            context=f"{int(k['companies'])} employers · {ui.fmt_pct(k['direct_share_pct'], 0)} direct",
            verdict=f"{change:+d} vs the prior 30 days", tone="",
            spark=weekly["applications"].tail(16).tolist()),
    ui.Stat("Response rate", float(k["response_rate_pct"]), 1, "%",
            context=f"95% CI {ui.fmt_num(k['response_rate_lo'])}–{ui.fmt_num(k['response_rate_hi'])}%",
            verdict=f"{band} against 2–5%", tone=ui.BAND_TONE.get(band, "")),
    ui.Stat("Interview rate", interview_rate, 1, "%",
            context=f"95% CI {ui.fmt_num(k['interview_rate_lo'])}–{ui.fmt_num(k['interview_rate_hi'])}%",
            verdict=f"{'Above' if interview_rate >= bm.DIRECT_INTERVIEW_RATE_PCT else 'Below'} "
                    f"the {bm.DIRECT_INTERVIEW_RATE_PCT}% direct rate",
            tone="good" if interview_rate >= bm.DIRECT_INTERVIEW_RATE_PCT else "warn"),
    ui.Stat("Offers", int(k["offers"]),
            context=f"{ui.fmt_num(k['offer_rate_pct'])} per 100 sent",
            verdict={"Ahead": "Ahead of 0–1 per 100", "On par": "In the 0–1 per 100 range",
                     "Behind": "Behind 0–1 per 100"}.get(offer_vs, ""),
            tone={"Ahead": "good", "Behind": "warn"}.get(offer_vs, "")),
    ui.Stat("Median reply", None if pd.isna(median_reply) else float(median_reply), 1, "days",
            context=middle or "No dated replies yet",
            verdict=f"{int(k.get('responses_with_known_date') or 0)} dated replies", tone=""),
    ui.Stat("Follow-ups due", int(k["needs_follow_up"]),
            context=f"of {int(k['active'])} open applications",
            verdict=f"Silent {s.follow_up_after_days}+ days" if int(k["needs_follow_up"] or 0)
            else "All caught up", tone="warn" if int(k["needs_follow_up"] or 0) else "good"),
])

# ---------------------------------------------------------------- 01 flow
def _flow(source: str, target: str) -> int:
    hit = flows[(flows["source"] == source) & (flows["target"] == target)]["applications"]
    return int(hit.iloc[0]) if not hit.empty else 0


apps = int(k["applications"])
with ui.section(1, "Where applications go",
                f"{_flow('Applied', 'Ghosted'):,} of {apps:,} applications went silent; "
                f"{int(k['interviews']):,} reached an interview",
                "Each application's furthest stage and where it stands today. Blue traces the path "
                "to interview, green to an offer, brick to a rejection; grey is everything else."):
    left, right = st.columns([8, 4], gap="large")
    with left:
        charts.show(charts.sankey(flows), key="sankey")
        ui.source(f"{SOURCE}. Furthest stage reached, so a rejection after an interview still "
                  "counts as an interview.")
    with right:
        ui.label("You against the published funnel")
        rows = []
        for r in funnel.itertuples(index=False):
            if pd.isna(r.benchmark_per_100_lo):
                continue
            tone = {"Ahead": "good", "On par": "", "Behind": "bad"}.get(str(r.vs_benchmark), "")
            rows.append((str(r.stage).replace("Employer responded", "Replied"),
                         f"{r.pct_of_applied:.1f}",
                         f"{r.benchmark_per_100_lo:g}–{r.benchmark_per_100_hi:g}",
                         str(r.vs_benchmark), tone))
        ui.compare_table(rows)
        if "silence" in notes:
            ui.notes([notes["silence"]])

# ---------------------------------------------------------------- 02 channels
overall = float(k["response_rate_pct"])
best_row = platforms[platforms["platform"] == best] if best else pd.DataFrame()
title = "Which channels get a reply"
if not best_row.empty:
    separated = float(best_row.iloc[0]["response_rate_lo"]) > float(k["response_rate_hi"])
    title = (f"{best} replies most often — and the gap is real" if separated else
             f"{best} replies most often, but the intervals overlap")
with ui.section(2, "Which channels work", title,
                "Response rate by channel. The dot is the rate, the grey bar its 95% Wilson "
                "interval; hollow dots have too few applications to read. The line is your "
                "overall rate."):
    left, right = st.columns([8, 4], gap="large")
    with left:
        charts.show(charts.rate_ci(platforms, "platform", reference=overall,
                                   reference_label=f"Overall {overall:.0f}%", highlight=best,
                                   confidence_col="sample_confidence"), key="channels")
        ui.source(f"{SOURCE}. Platform comparisons are indicative, not statistically "
                  "significant at this sample size.")
    with right:
        ui.notes([n for key in ("channel", "direct") if (n := notes.get(key))])

# ---------------------------------------------------------------- 03 timing
km21 = marks.get(21)
with ui.section(3, "How long employers take",
                (f"By day 21, {km21:.0f}% have heard back — after that, the odds flatten"
                 if km21 is not None else "How long a first reply takes"),
                "Kaplan–Meier estimate of the share still waiting for any reply. Applications "
                "that are still open count until today and then drop out (ticks), instead of "
                "being ignored — ignoring them would make employers look faster than they are."):
    left, right = st.columns([8, 4], gap="large")
    with left:
        charts.show(km_fig, key="km")
        ui.source(f"{SOURCE}. Replies with an unknown date are excluded, never zero-filled.")
    with right:
        ui.notes([n for key in ("timing",) if (n := notes.get(key))])
        if not reply_row.empty:
            ui.label("Milestones")
            ui.compare_table([
                (str(r.milestone), ui.fmt_num(r.median_days),
                 f"{ui.fmt_num(r.p25_days)}–{ui.fmt_num(r.p75_days)}", f"n = {int(r.applications)}",
                 "") for r in timing.dropna(subset=["median_days"]).itertuples(index=False)],
                head=("Days to", "Median", "Middle half", "Sample"))

# ---------------------------------------------------------------- 04 rhythm
with ui.section(4, "Rhythm",
                f"{int(period.get('last_30') or 0)} applications in the last 30 days, "
                f"{int(period.get('prev_30') or 0)} in the 30 before",
                "One square per day over the last 26 weeks; darker means more applications sent."):
    left, right = st.columns([8, 4], gap="large")
    with left:
        ui.activity_calendar(data.metric("activity_calendar"))
        ui.source(f"{SOURCE}. Dated by the first email in each application thread.")
    with right:
        ui.notes([n for key in ("pace",) if (n := notes.get(key))])

# ---------------------------------------------------------------- 05 next actions
with ui.section(5, "What to do next",
                f"{len(queue)} open application{'s' if len(queue) != 1 else ''} "
                f"{'are' if len(queue) != 1 else 'is'} due a follow-up" if len(queue)
                else "Nothing is waiting on you",
                f"Open applications silent for {s.follow_up_after_days}+ days, longest silence "
                "first. Drafts for each are on the Follow-ups page."):
    if queue.empty:
        ui.empty_state("All caught up", "Nothing has been quiet long enough to chase.")
    else:
        ui.ledger(["Application", "Stage", "Suggested action", "Quiet"], [
            (f"<b>{ui.esc(str(r.company_name))}</b><span class='sub'>{ui.esc(str(r.role_title))}"
             f"</span>", ui.status_mark(str(r.current_status)), ui.esc(str(r.suggested_action)),
             f"{int(r.days_since_activity)}<small>days</small>")
            for r in queue.head(6).itertuples(index=False)])
        st.page_link("views/follow_ups.py", label=f"All {len(queue)} follow-ups, with drafts",
                     icon=":material/arrow_forward:")
