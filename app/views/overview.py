"""Overview: what changed since the last sync, the headline numbers, then the search as a
story (where applications go, which channels work, how long replies take, your rhythm,
what to do next). Every number and mark opens the applications behind it."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.ui import charts, data, drill, narrative
from app.ui import components as ui
from jobtracker import benchmarks as bm

SOURCE = "Source: your Gmail, read by jobtracker into a DuckDB warehouse"

k = data.kpis()
s = data.settings()

# ---------------------------------------------------------------- since the last sync
narrative_changes = data.latest_changes()
if narrative_changes is not None:
    with st.container(key="jt-changes"):
        title, items, more = narrative_changes
        ui.render(f'<div class="jt-changes-head"><span class="jt-pulse"></span>{ui.esc(title)}'
                  f'</div>')
        for i, item in enumerate(items):
            if st.button(item["label"], key=f"chg_{i}_{item['id']}", type="tertiary",
                         icon=item["icon"]):
                drill.open_application(item["id"])
        if more:
            ui.render(f'<p class="jt-changes-more">and {more} more, all in the Pipeline.</p>')

if not k or not int(k.get("applications") or 0):
    ui.lede("Overview", "Nothing to show yet.",
            "No applications match the current filters. Clear the filters, or click Sync Gmail "
            "to read your job emails.")
    st.stop()

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
buckets = data.metric("response_time_buckets")
median_reply = k.get("median_days_to_response")
km_fig, marks = charts.survival(
    km, median_reply=None if pd.isna(median_reply) else float(median_reply))
notes = narrative.notes(k, platforms, channels, queue, marks, period, s)
best = narrative.best_platform(platforms)

ui.lede("Your job search so far", narrative.lede_segments(k),
        "Everything here is read from your Gmail automatically. Hover any (i) for what a number "
        "means, and click any number, bar or dot to see the applications behind it.")

# ---------------------------------------------------------------- headline numbers
change = int(period.get("change") or 0)
interview_rate = float(k.get("interview_rate_pct") or 0)
offer_row = funnel[funnel["stage"] == "Offer"]
offer_vs = str(offer_row.iloc[0]["vs_benchmark"]) if not offer_row.empty else ""
reply_row = timing[timing["milestone"] == "First reply"]
middle = ""
if not reply_row.empty and pd.notna(reply_row.iloc[0]["p25_days"]):
    middle = (f"Most within {ui.fmt_num(reply_row.iloc[0]['p25_days'])} to "
              f"{ui.fmt_num(reply_row.iloc[0]['p75_days'])} days")
band = str(k.get("response_band") or "")
follow_n = int(k["needs_follow_up"] or 0)
stats = [
    (ui.Stat("Applications", int(k["applications"]),
             context=f"{int(k['companies'])} employers, {ui.fmt_pct(k['direct_share_pct'], 0)} "
                     "applied direct",
             verdict=f"{change:+d} vs the 30 days before", spark=weekly["applications"].tail(16).tolist(),
             help="Every job application found in your Gmail, plus any you logged by hand."),
     "Every application", drill.narrowed()),
    (ui.Stat("Reply rate", float(k["response_rate_pct"]), 1, "%",
             context=f"Likely between {ui.fmt_num(k['response_rate_lo'], 0)} and "
                     f"{ui.fmt_num(k['response_rate_hi'], 0)}%",
             verdict=f"{band} (typical is 2 to 5%)", tone=ui.BAND_TONE.get(band, ""),
             help="The share of applications where the employer wrote back at all: a test, an "
                  "interview invite, an offer or a rejection. The range is how sure we can be "
                  "with this many applications."),
     "Applications that got a reply", drill.narrowed(reached=["response"])),
    (ui.Stat("Interview rate", interview_rate, 1, "%",
             context=f"Likely between {ui.fmt_num(k['interview_rate_lo'], 0)} and "
                     f"{ui.fmt_num(k['interview_rate_hi'], 0)}%",
             verdict=f"{'Above' if interview_rate >= bm.DIRECT_INTERVIEW_RATE_PCT else 'Below'} "
                     f"the {bm.DIRECT_INTERVIEW_RATE_PCT}% published rate",
             tone="good" if interview_rate >= bm.DIRECT_INTERVIEW_RATE_PCT else "warn",
             help="The share of applications that got as far as an interview, even if they "
                  "were rejected afterwards."),
     "Applications that reached an interview", drill.narrowed(reached=["interview"])),
    (ui.Stat("Offers", int(k["offers"]),
             context=f"{ui.fmt_num(k['offer_rate_pct'])} per 100 sent",
             verdict={"Ahead": "Ahead of the usual 0 to 1 per 100",
                      "On par": "In the usual 0 to 1 per 100",
                      "Behind": "Behind the usual 0 to 1 per 100"}.get(offer_vs, ""),
             tone={"Ahead": "good", "Behind": "warn"}.get(offer_vs, ""),
             help="Applications that reached an offer."),
     "Offers", drill.narrowed(reached=["offer"])),
    (ui.Stat("Typical reply time", None if pd.isna(median_reply) else float(median_reply), 1,
             "days", context=middle or "No dated replies yet",
             verdict=f"From {int(k.get('responses_with_known_date') or 0)} replies with a date",
             help="Half of the replies came faster than this and half slower (the median). "
                  "Only replies with a known date count."),
     "Replies with a known date", drill.narrowed(reached=["dated_reply"])),
    (ui.Stat("Follow-ups due", follow_n, context=f"of {int(k['active'])} still open",
             verdict=f"No news for {s.follow_up_after_days}+ days" if follow_n else "All caught up",
             tone="warn" if follow_n else "good",
             help=f"Open applications with no news for {s.follow_up_after_days} days or more. "
                  "A short, polite nudge is worth sending."),
     "Due a follow-up", drill.narrowed(reached=["follow_up"])),
]
with st.container(key="jt-kpis"):
    tiles = st.columns(len(stats), gap="small")
    for i, (col, (stat, title, f)) in enumerate(zip(tiles, stats, strict=True)):
        with col:
            ui.kpi_tile(stat, i)
            drill.see_button("See them", title, f, key=f"kpi_{i}")


# ---------------------------------------------------------------- 01 flow
def _flow(source: str, target: str) -> int:
    hit = flows[(flows["source"] == source) & (flows["target"] == target)]["applications"]
    return int(hit.iloc[0]) if not hit.empty else 0


apps = int(k["applications"])
OUTCOMES = [("Waiting for a first reply", "awaiting"), ("In progress", "in_progress"),
            ("Offer", "offer"), ("Rejected", "rejected"), ("Ghosted", "ghosted")]
with ui.section(1, "Where applications go",
                f"{_flow('Applied', 'Ghosted'):,} of {apps:,} applications went silent, and "
                f"{int(k['interviews']):,} reached an interview"):
    left, right = st.columns([8, 4], gap="large")
    with left:
        ui.explain("Read it left to right. Every application starts on the left. Each band "
                   "shows how many moved on to the next step, and the right side is where they "
                   "are today. Blue is the path to an interview, green is an offer, red is a "
                   "rejection, grey is silence.",
                   "Click a box below the chart to see those applications.")
        charts.show(charts.sankey(flows), key="sankey")
        outcome_bar = st.container(horizontal=True, gap="small", key="jt-outcomes")
        for label_, key_ in OUTCOMES:
            n = int(data.kpis(drill.narrowed(reached=[key_])).get("applications") or 0)
            if outcome_bar.button(f"{label_}  **{n}**", key=f"out_{key_}", disabled=not n):
                drill.open_set(label_, drill.narrowed(reached=[key_]))
        ui.source(f"{SOURCE}. An application rejected after its interview still counts as an "
                  "interview here.")
    with right:
        ui.label("You against published hiring data")
        rows = []
        for r in funnel.itertuples(index=False):
            if pd.isna(r.benchmark_per_100_lo):
                continue
            tone = {"Ahead": "good", "On par": "", "Behind": "bad"}.get(str(r.vs_benchmark), "")
            rows.append((str(r.stage), f"{r.pct_of_applied:.1f}",
                         f"{r.benchmark_per_100_lo:g} to {r.benchmark_per_100_hi:g}",
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
    title = (f"{best} replies most often, and the gap is real" if separated else
             f"{best} replies most often, though it could still be luck")
with ui.section(2, "Which channels work", title):
    left, right = st.columns([8, 4], gap="large")
    with left:
        ui.explain(f"Each dot is the share of applications on that site that got a reply. The "
                   f"grey bar is the {ui.term('likely range', 'With this few applications the true rate could be anywhere along the bar. Short bar: confident. Long bar: still guessing.')}"
                   f"; a hollow dot means too few applications to judge. The black line is your "
                   f"overall reply rate ({overall:.0f}%).",
                   "Dots to the right of the line work better than average for you. Put more "
                   "applications there. Click a dot to see its applications.")
        point = charts.show(charts.rate_ci(platforms, "platform", reference=overall,
                                           reference_label=f"Your overall {overall:.0f}%",
                                           highlight=best, confidence_col="sample_confidence"),
                            key="channels", clickable=True)
        if point and point.get("y"):
            drill.open_set(f"{point['y']}: every application",
                           drill.narrowed(platforms=[str(point["y"])]))
        ui.source(f"{SOURCE}. With samples this small, treat the ranking as a lead, not proof.")
    with right:
        ui.notes([n for key in ("channel", "direct") if (n := notes.get(key))])

# ---------------------------------------------------------------- 03 timing
km21 = marks.get(21)
with ui.section(3, "How long employers take",
                (f"By day 21, {km21:.0f}% have heard back. After that, replies rarely come"
                 if km21 is not None else "How long a first reply takes")):
    left, right = st.columns([8, 4], gap="large")
    with left:
        ui.explain("The line starts at 100% on the day you apply and drops each time an "
                   "employer replies. Where it flattens, replies have stopped coming. "
                   f"Applications still waiting are counted until today "
                   f"({ui.term('the small ticks', 'An application still waiting counts for as long as it has been waiting, then stops counting. Ignoring them would make employers look faster than they are.')}).",
                   "If an application has been quiet past the point where the line goes flat, "
                   "follow up once and then move on.")
        charts.show(km_fig, key="km")
        if not buckets.empty:
            ui.subhead("When the replies came")
            charts.show(charts.reply_buckets(buckets, height=230), key="buckets")
        ui.source(f"{SOURCE}. Replies with an unknown date are left out, never counted as zero.")
    with right:
        ui.notes([n for key in ("timing",) if (n := notes.get(key))])
        if not reply_row.empty:
            ui.label("Typical waits")
            ui.compare_table([
                (str(r.milestone), ui.fmt_num(r.median_days),
                 f"{ui.fmt_num(r.p25_days)} to {ui.fmt_num(r.p75_days)}",
                 f"from {int(r.applications)}", "")
                for r in timing.dropna(subset=["median_days"]).itertuples(index=False)],
                head=("Days to", "Typical", "Most fall in", "Based on"))

# ---------------------------------------------------------------- 04 rhythm
with ui.section(4, "Rhythm",
                f"{int(period.get('last_30') or 0)} applications in the last 30 days, "
                f"{int(period.get('prev_30') or 0)} in the 30 before"):
    left, right = st.columns([8, 4], gap="large")
    with left:
        ui.explain("One square per day for the last 26 weeks. The darker the square, the more "
                   "applications you sent that day.",
                   "Steady weekly volume beats bursts. Click a month to see what you sent then.")
        ui.activity_calendar(data.metric("activity_calendar"))
        cohorts = data.metric("monthly_cohorts")
        if not cohorts.empty:
            month_bar = st.container(horizontal=True, gap="small", key="jt-months")
            for row in cohorts.itertuples(index=False):
                label_ = data.month_name(str(row.cohort_month))
                if month_bar.button(f"{label_.split()[0][:3]}  **{int(row.applications)}**",
                                    key=f"month_{row.cohort_month}"):
                    drill.open_set(f"Sent in {label_}",
                                   drill.narrowed(month=str(row.cohort_month)))
        ui.source(f"{SOURCE}. Dated by the first email in each application.")
    with right:
        ui.notes([n for key in ("pace",) if (n := notes.get(key))])

# ---------------------------------------------------------------- 05 next actions
with ui.section(5, "What to do next",
                f"{len(queue)} open application{'s' if len(queue) != 1 else ''} "
                f"{'are' if len(queue) != 1 else 'is'} due a follow-up" if len(queue)
                else "Nothing is waiting on you"):
    if queue.empty:
        ui.empty_state("All caught up", "Nothing has been quiet long enough to chase.")
    else:
        ui.explain(f"Open applications with no news for {s.follow_up_after_days}+ days, "
                   "longest silence first.",
                   "Click one to open it, or go to Follow-ups for ready-to-send emails.")
        for r in queue.head(6).itertuples(index=False):
            if st.button(f"**{r.company_name}**, {r.role_title}  \n"
                         f"{r.current_status} · quiet for {int(r.days_since_activity)} days · "
                         f"{r.suggested_action}", key=f"card-fu-{r.application_id}",
                         width="stretch"):
                drill.open_application(str(r.application_id))
        st.page_link("views/follow_ups.py", label=f"All {len(queue)} follow-ups, with emails "
                     "ready to send", icon=":material/arrow_forward:")
