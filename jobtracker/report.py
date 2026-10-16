"""Written findings report: the job search, analysed — generated from the metric layer.

``python -m jobtracker report`` turns the warehouse into a Markdown analysis a reader can
act on: headline, funnel vs benchmark, which channels work (with confidence intervals),
how long employers take (Kaplan–Meier), cohort trend, segments, and the caveats. Every
number is read from ``Metrics`` — the same SQL the app, the workbook and the BI pack use.

Statements are deliberately hedged by sample size: a finding is only called a finding
when the confidence intervals say so.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from . import benchmarks as bm
from .warehouse import connect, manifest
from .warehouse.metrics import Filters, Metrics


def _pct(value) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{float(value):.1f}%"


def _num(value, unit: str = "") -> str:
    if value is None or pd.isna(value):
        return "n/a"
    value = float(value)
    text = f"{value:.0f}" if value.is_integer() else f"{value:.1f}"
    return f"{text}{unit}"


def _table(frame: pd.DataFrame, columns: dict[str, str]) -> str:
    if frame.empty:
        return "_No data._\n"
    head = "| " + " | ".join(columns.values()) + " |"
    rule = "|" + "|".join("---" for _ in columns) + "|"
    rows = []
    for _, row in frame.iterrows():
        cells = []
        for col in columns:
            v = row[col]
            if isinstance(v, float) and not pd.isna(v):
                cells.append(f"{v:.1f}")
            elif v is None or (not isinstance(v, str) and pd.isna(v)):
                cells.append("–")
            else:
                cells.append(str(v))
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([head, rule, *rows]) + "\n"


def _median_wait(km: pd.DataFrame) -> str:
    reached = km[km.still_waiting_pct <= 50]
    if reached.empty:
        last = km.iloc[-1] if not km.empty else None
        return ("fewer than half of applications ever got a reply within "
                f"{int(last.day)} days" if last is not None else "no data")
    return f"half of all applications had a reply by day {int(reached.iloc[0].day)}"


def build_report(settings, filters: Filters | None = None, demo: bool = False) -> str:
    with connect(settings) as con:
        m = Metrics(con, settings, filters)
        k = m.kpis()
        funnel = m.query("funnel")
        platforms = m.query("platform_performance")
        km = m.query("response_survival")
        cohorts = m.query("monthly_cohorts")
        families = m.query("segment_performance", segment="role_family")
        channels = m.query("segment_performance", segment="channel")
        timing = m.query("stage_timing")
        quality = m.query("data_quality")
        period = m.query("period_compare").iloc[0].to_dict()
    built = manifest(settings)

    n = int(k.get("applications") or 0)
    lines: list[str] = []
    title = "Job search analysis" + (" — synthetic demo data" if demo else "")
    lines += [f"# {title}", "",
              f"_Generated {date.today():%d %B %Y} from the analytics warehouse "
              f"({built.get('built_at', 'unknown build')}). Every figure comes from the SQL "
              f"metric layer; rates carry 95% Wilson intervals._", ""]
    if demo:
        lines += ["> **Demo dataset.** These numbers describe the synthetic mailbox that ships "
                  "with the project, generated to exercise the real pipeline. They are not a "
                  "real person's job search. Run `python -m jobtracker report` on your own "
                  "data for a real analysis.", ""]
    if n == 0:
        return "\n".join([*lines, "No applications yet — run a sync first."])

    # ------------------------------------------------------------------ headline
    lines += ["## Headline", "",
              f"- **{n} applications** to **{int(k['companies'])} employers** between "
              f"{pd.Timestamp(k['first_applied']):%d %b %Y} and "
              f"{pd.Timestamp(k['last_applied']):%d %b %Y}.",
              f"- **{_pct(k['response_rate_pct'])} got an employer response** "
              f"(95% CI {_pct(k['response_rate_lo'])}–{_pct(k['response_rate_hi'])}) — "
              f"**{k['response_band']}** against the published bands "
              f"(<2% below average · 2–3% average · 3–5% good · 5%+ strong).",
              f"- **{_pct(k['interview_rate_pct'])} reached an interview** "
              f"(95% CI {_pct(k['interview_rate_lo'])}–{_pct(k['interview_rate_hi'])}); "
              f"well-targeted applications are quoted at "
              f"{bm.WELL_TARGETED_INTERVIEW_RATE_PCT[0]:.0f}–"
              f"{bm.WELL_TARGETED_INTERVIEW_RATE_PCT[1]:.0f}%.",
              f"- **{int(k['offers'])} offer(s)**, {int(k['rejections'])} rejections, "
              f"{int(k['ghosted'])} ghosted ({_pct(k['ghost_rate_pct'])}), "
              f"{int(k['active'])} still open.",
              f"- Median time to a first reply: **{_num(k['median_days_to_response'], ' days')}** "
              f"(known reply dates: {int(k['responses_with_known_date'])} of "
              f"{int(k['responses'])}).",
              f"- Last 30 days: {int(period['last_30'] or 0)} applications vs "
              f"{int(period['prev_30'] or 0)} in the 30 before; response rate "
              f"{_pct(period['last_30_response_pct'])} vs {_pct(period['prev_30_response_pct'])} "
              f"(recent applications have had less time to get a reply).",
              ""]

    # ------------------------------------------------------------------ funnel
    lines += ["## Where applications stop", "",
              "Stages count the **furthest stage ever reached**, so an application rejected "
              "after its interview still counts as an interview.", "",
              _table(funnel, {"stage": "Stage", "applications": "Applications",
                              "pct_of_applied": "% of applied",
                              "step_conversion_pct": "Step conversion %",
                              "vs_benchmark": "vs benchmark"})]
    biggest = funnel.dropna(subset=["lost_at_step"]).sort_values("lost_at_step", ascending=False)
    if not biggest.empty:
        step = biggest.iloc[0]
        lines += [f"The largest drop is into **{step.stage}** "
                  f"({int(step.lost_at_step)} applications lost at that step).", ""]

    # ------------------------------------------------------------------ channels
    lines += ["## Which channels work", "",
              _table(platforms, {"platform": "Platform", "applications": "Apps",
                                 "response_rate_pct": "Response %",
                                 "response_rate_lo": "CI low", "response_rate_hi": "CI high",
                                 "interview_rate_pct": "Interview %",
                                 "sample_confidence": "Sample"})]
    adequate = platforms[platforms.applications >= bm.SAMPLE_INDICATIVE]
    if len(adequate) >= 2:
        best = adequate.sort_values("response_rate_pct", ascending=False).iloc[0]
        worst = adequate.sort_values("response_rate_pct").iloc[0]
        separated = best.response_rate_lo > worst.response_rate_hi
        lines += [f"**{best.platform}** has the highest response rate "
                  f"({_pct(best.response_rate_pct)}) and **{worst.platform}** the lowest "
                  f"({_pct(worst.response_rate_pct)}). "
                  + ("Their 95% intervals do not overlap, so the gap is unlikely to be luck."
                     if separated else
                     "Their 95% intervals overlap, so treat the ranking as indicative, "
                     "not proven."), ""]
    direct = channels[channels.segment == "Employer careers site"]
    if not direct.empty:
        d = direct.iloc[0]
        lines += [f"Applying on employers' own careers sites: "
                  f"{_pct(d.interview_rate_pct)} interview rate "
                  f"vs the {bm.DIRECT_INTERVIEW_RATE_PCT}% published benchmark for direct "
                  f"applications ({bm.JOB_BOARD_INTERVIEW_RATE_PCT}% via job boards).", ""]

    # ------------------------------------------------------------------ timing
    lines += ["## How long employers take", "",
              "Time to first reply is estimated with a **Kaplan–Meier** survival curve: "
              "applications still waiting count as *censored* observations rather than being "
              "dropped, which would make employers look faster than they are.", "",
              f"- {_median_wait(km).capitalize()}.",
              *(f"- {row.milestone}: median {_num(row.median_days, ' days')} "
                f"(IQR {_num(row.p25_days)}–{_num(row.p75_days)} days, n={int(row.applications)})"
                for row in timing.itertuples() if row.applications), ""]

    # ------------------------------------------------------------------ cohorts & segments
    lines += ["## Monthly cohorts", "",
              "Recent cohorts are still maturing — their rates are floors, not final.", "",
              _table(cohorts, {"cohort_month": "Month", "applications": "Apps",
                               "response_rate_pct": "Response %",
                               "interview_rate_pct": "Interview %", "still_open": "Open"}),
              "## Role families", "",
              _table(families, {"segment": "Role family", "applications": "Apps",
                                "response_rate_pct": "Response %",
                                "interview_rate_pct": "Interview %"})]

    # ------------------------------------------------------------------ caveats
    lines += ["## Data quality & caveats", "",
              _table(quality.assign(coverage=(100.0 * quality.passing
                                              / quality.total.where(quality.total > 0))),
                     {"check_name": "Check", "passing": "Passing", "total": "Total",
                      "coverage": "Coverage %"}),
              "- One person's search is a small sample: rates are shown with intervals and "
              "rankings with sample-size labels for that reason.",
              "- Unknown reply dates are excluded from timing metrics, never treated as zero.",
              "- Benchmarks are aggregate figures from public job-search research (mixed "
              "seniority, mostly cold applications) and are context, not targets.",
              ""]
    return "\n".join(lines)


def write_report(settings, out: Path | None = None, demo: bool = False) -> Path:
    path = out or (Path(settings.exports_dir) / "job-search-report.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_report(settings, demo=demo), encoding="utf-8")
    return path
