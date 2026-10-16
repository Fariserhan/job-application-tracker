"""Auto-written copy: the Overview lede and the margin notes beside each chart.

Pure functions over metric results (dicts / frames from the SQL layer), so they are unit
tested without Streamlit. House rules:

* every figure quoted is read from a metric row — the copy never computes a new analytic
* a rate is never quoted without its sample size, and a comparison between segments is
  never stated without its interval and the "indicative" caveat when samples are small
* unknown reply dates are excluded upstream (NULL); the copy says "dated replies"
"""
from __future__ import annotations

import html

import pandas as pd

from jobtracker import benchmarks as bm

from .components import Note, Segment, fmt_date, fmt_num, fmt_pct

esc = html.escape


def one_in(rate_pct) -> str:
    """'1 in 4' for rates ≥ 5%, else the percentage (1 in 37 reads worse than 2.7%)."""
    try:
        rate = float(rate_pct)
    except (TypeError, ValueError):
        return "—"
    if rate >= 5:
        return f"1 in {round(100 / rate):d}"
    return fmt_pct(rate)


def lede_segments(k: dict) -> list[Segment]:
    """'168 applications to 28 employers since 23 Apr. 1 in 4 got a reply — strong against
    the 2–5% published benchmark.'"""
    apps = int(k.get("applications") or 0)
    if not apps:
        return [("No applications match these filters yet.", "")]
    companies = int(k.get("companies") or 0)
    since = fmt_date(k.get("first_applied"), "%d %B")
    band = str(k.get("response_band") or "No data")
    reply = one_in(k.get("response_rate_pct"))
    verb = " got a reply" if reply.startswith("1 in") else " of applications got a reply"
    verdict = {"Strong": "well ahead of", "Good": "ahead of", "Average": "in line with",
               "Below average": "behind"}.get(band, "measured against")
    return [
        (f"{apps:,} application{'s' if apps != 1 else ''}", "em"),
        (f" to {companies:,} employer{'s' if companies != 1 else ''} since {since}. ", ""),
        (reply, "em"),
        (f"{verb} — {verdict} the 2–5% that published hiring data leads you to expect.", "muted"),
    ]


def _best_segment(perf: pd.DataFrame) -> pd.Series | None:
    ok = perf[perf["sample_confidence"] != "Too small"] if "sample_confidence" in perf else perf
    ok = ok.dropna(subset=["response_rate_pct"])
    if ok.empty:
        return None
    return ok.sort_values(["response_rate_pct", "applications"], ascending=False).iloc[0]


def best_platform(platforms: pd.DataFrame) -> str | None:
    best = _best_segment(platforms) if not platforms.empty else None
    return None if best is None else str(best["platform"])


def notes(k: dict, platforms: pd.DataFrame, channels: pd.DataFrame,
          follow_ups: pd.DataFrame, survival_marks: dict, period: dict,
          settings) -> dict[str, Note]:
    """Margin notes keyed by topic: channel, direct, silence, timing, pace, follow_up."""
    out: dict[str, Note] = {}
    if not int(k.get("applications") or 0):
        return out

    best = _best_segment(platforms) if not platforms.empty else None
    if best is not None:
        overall_hi = k.get("response_rate_hi")
        separated = (pd.notna(best["response_rate_lo"]) and overall_hi is not None
                     and float(best["response_rate_lo"]) > float(overall_hi))
        caveat = ("Its interval sits clearly above your overall rate."
                  if separated else
                  "Its interval overlaps your overall rate, so treat it as a lead, not a verdict.")
        out["channel"] = Note(
            "Best channel", f"{best['platform']} replies most often",
            f"<b>{fmt_pct(best['response_rate_pct'])}</b> replied (95% interval "
            f"{fmt_num(best['response_rate_lo'])}–{fmt_num(best['response_rate_hi'])}%, "
            f"n = {int(best['applications'])}, {str(best['sample_confidence']).lower()} sample). "
            f"{caveat}", "good" if separated else "")

    if not channels.empty:
        direct = channels[channels["segment"] == "Employer careers site"]
        boards = channels[channels["segment"] == "Job board"]
        if not direct.empty:
            d = direct.iloc[0]
            rate = float(d["interview_rate_pct"] or 0)
            board_txt = (f", against <b>{fmt_pct(boards.iloc[0]['interview_rate_pct'])}</b> "
                         f"through job boards" if not boards.empty else "")
            ahead = rate >= bm.DIRECT_INTERVIEW_RATE_PCT
            out["direct"] = Note(
                "Direct applications",
                "Applying direct pays — do more of it" if ahead
                else "Direct applications lag the benchmark",
                f"Careers-page applications reached interview <b>{fmt_pct(rate)}</b> of the "
                f"time (n = {int(d['applications'])}){board_txt}. The published direct rate is "
                f"<b>{bm.DIRECT_INTERVIEW_RATE_PCT}%</b>; only "
                f"{fmt_pct(k.get('direct_share_pct'), 0)} of your volume goes direct.",
                "good" if ahead else "warn")

    ghost = k.get("ghost_rate_pct")
    if ghost is not None and pd.notna(ghost):
        out["silence"] = Note(
            "Silence", f"{fmt_pct(ghost, 0)} never heard back",
            f"<b>{int(k.get('ghosted') or 0):,}</b> applications went "
            f"{settings.ghost_after_days}+ days without any reply and were marked ghosted. "
            "In cold applications silence is the norm — a volume problem, not a verdict.",
            "bad" if float(ghost) >= 50 else "")

    median = k.get("median_days_to_response")
    if median is not None and pd.notna(median):
        km21 = survival_marks.get(21)
        km_txt = (f" Counting the ones still waiting, <b>{fmt_pct(km21, 0)}</b> have heard "
                  "anything by day 21 — after that, the odds flatten." if km21 is not None else "")
        out["timing"] = Note(
            "Reply time", f"Replies that come, come within {fmt_num(median)} days",
            f"Median across <b>{int(k.get('responses_with_known_date') or 0)}</b> dated replies "
            f"(unknown dates are excluded, never zero-filled).{km_txt}")

    if period:
        last, prev = int(period.get("last_30") or 0), int(period.get("prev_30") or 0)
        word = "picked up" if last > prev else "eased" if last < prev else "held steady"
        out["pace"] = Note(
            "Momentum", f"Pace {word}: {last} sent in the last 30 days",
            f"Against {prev} in the 30 days before. Reply rate "
            f"<b>{fmt_pct(period.get('last_30_response_pct'))}</b> vs "
            f"{fmt_pct(period.get('prev_30_response_pct'))} — the newest applications are still "
            "maturing, so read that rate as a floor.")

    if not follow_ups.empty:
        post = int((follow_ups["current_status"] == "Interview").sum())
        out["follow_up"] = Note(
            "Next action", f"{len(follow_ups)} applications are due a nudge",
            f"Open and silent for {settings.follow_up_after_days}+ days"
            + (f"; <b>{post}</b> of them after an interview — chase those first." if post else ".")
            , "warn")
    return out
