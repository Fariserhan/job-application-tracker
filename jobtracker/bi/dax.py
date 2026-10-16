"""Power BI measures, written against the real star schema (fct_applications + dims).

Every measure reproduces the SQL definition in ``jobtracker/warehouse/metrics.py`` on the
same data. Thresholds (response bands, the 6.87% direct benchmark, the per-100 funnel, the
sample-size cut-offs, Wilson's z) are generated from ``jobtracker.benchmarks`` /
``metrics.Z`` — never typed twice.

``render_dax_file`` emits a DAX *query* (``DEFINE MEASURE ... EVALUATE``): paste it into
Power BI Desktop's DAX query view, run it to preview the numbers, then use "Update model
with changes" to add every measure to the model in one click.
"""
from __future__ import annotations

from dataclasses import dataclass

from .. import benchmarks as bm
from ..warehouse.metrics import Z

HOME_TABLE = "fct_applications"
F = "fct_applications"


@dataclass(frozen=True)
class Measure:
    name: str
    folder: str
    expression: str
    format: str
    description: str
    sql: str = ""  # the metric-layer query/column it reproduces, for the "one metric, four tools" table


def _num(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _count_where(condition: str) -> str:
    return f"CALCULATE ( COUNTROWS ( {F} ), KEEPFILTERS ( {condition} ) )"


def _wilson(successes: str, trials: str, bound: str) -> str:
    sign = "-" if bound == "lo" else "+"
    z = _num(Z)
    return f"""VAR _n = {trials}
VAR _p = DIVIDE ( {successes}, _n )
VAR _z = {z}
VAR _centre = _p + _z ^ 2 / ( 2 * _n )
VAR _margin = _z * SQRT ( _p * ( 1 - _p ) / _n + _z ^ 2 / ( 4 * _n ^ 2 ) )
RETURN
    IF ( _n > 0, MAX ( 0, MIN ( 1, ( _centre {sign} _margin ) / ( 1 + _z ^ 2 / _n ) ) ) )"""


def response_band_dax(rate_measure: str = "[Response Rate %]") -> str:
    """SWITCH over the bands in ``benchmarks.RESPONSE_RATE_BANDS`` (rounded to 1 dp like the SQL)."""
    lines = [f"VAR _pct = ROUND ( {rate_measure} * 100, 1 )", "RETURN", "    SWITCH (",
             "        TRUE (),", '        ISBLANK ( _pct ), "No data",']
    for band in bm.RESPONSE_RATE_BANDS[:-1]:
        lines.append(f'        _pct >= {_num(band.lower_pct)}, "{band.label}",')
    lines.append(f'        "{bm.RESPONSE_RATE_BANDS[-1].label}"')
    lines.append("    )")
    return "\n".join(lines)


def _sample_confidence(n_measure: str = "[Total Applications]") -> str:
    return f"""VAR _n = {n_measure}
RETURN
    SWITCH (
        TRUE (),
        _n >= {bm.SAMPLE_ADEQUATE}, "Adequate",
        _n >= {bm.SAMPLE_INDICATIVE}, "Indicative",
        "Too small"
    )"""


def _vs_benchmark(per100_measure: str, stage: str) -> str:
    lo, hi = bm.FUNNEL_PER_100[stage]
    return f"""VAR _per100 = {per100_measure}
RETURN
    SWITCH (
        TRUE (),
        ISBLANK ( _per100 ), BLANK (),
        _per100 > {_num(hi)}, "Ahead",
        _per100 >= {_num(lo)}, "On par",
        "Behind"
    )"""


def measures(follow_up_days: int = 7) -> list[Measure]:
    int_lo, int_hi = bm.FUNNEL_PER_100["Interview"]
    off_lo, off_hi = bm.FUNNEL_PER_100["Offer"]
    m: list[Measure] = []

    def add(name, folder, expression, fmt, description, sql=""):
        m.append(Measure(name, folder, expression.strip(), fmt, description, sql))

    vol, fun, rel, tim, chn, bmk = ("1 Volume", "2 Funnel", "3 Reliability", "4 Timing",
                                    "5 Channels", "6 Benchmarks")

    # ------------------------------------------------------------------ volume
    add("Total Applications", vol, f"COUNTROWS ( {F} )", "#,0",
        "Applications in the current filter context (grain of fct_applications).",
        "kpis.applications")
    add("Companies", vol, f"DISTINCTCOUNT ( {F}[company_name] )", "#,0",
        "Distinct employers applied to.", "kpis.companies")
    add("Active Pipeline", vol, _count_where(f"{F}[is_active] = TRUE ()"), "#,0",
        "Applications still open (Applied, Assessment / OA, Interview).", "kpis.active")
    add("Direct Channel Share %", vol,
        f"DIVIDE ( {_count_where(f'{F}[is_direct] = TRUE ()')}, [Total Applications] )", "0.0%",
        "Share of applications made directly with the employer (careers site, ATS, referral).",
        "kpis.direct_share_pct")
    add("Applications Last 30 Days", vol, """
VAR _end = MAX ( dim_date[date_day] )
RETURN
    CALCULATE (
        [Total Applications],
        DATESINPERIOD ( dim_date[date_day], _end, -30, DAY )
    )""", "#,0",
        "Applications in the 30 days ending on the last visible date (dim_date ends on the build date).",
        "period_compare.last_30")
    add("Applications Prev 30 Days", vol, """
VAR _end = MAX ( dim_date[date_day] ) - 30
RETURN
    CALCULATE (
        [Total Applications],
        DATESINPERIOD ( dim_date[date_day], _end, -30, DAY )
    )""", "#,0", "Applications in the 30 days before that window.", "period_compare.prev_30")
    add("Applications Change 30d", vol,
        "[Applications Last 30 Days] - [Applications Prev 30 Days]", "+#,0;-#,0;0",
        "Last 30 days minus the previous 30 days.", "period_compare.change")
    add("Applications Change 30d %", vol,
        "DIVIDE ( [Applications Change 30d], [Applications Prev 30 Days] )", "+0.0%;-0.0%;0.0%",
        "Relative change in volume vs the previous 30 days.")
    add("Cumulative Applications", vol, """
VAR _end = MAX ( dim_date[date_day] )
RETURN
    CALCULATE (
        [Total Applications],
        dim_date[date_day] <= _end,
        REMOVEFILTERS ( dim_date )
    )""", "#,0", "Running total of applications up to the last visible date.",
        "weekly_velocity.cumulative")
    add("Rolling 4-Week Avg Applications", vol, """
VAR _week = MAX ( dim_date[week_start] )
VAR _first = CALCULATE ( MIN ( fct_applications[applied_date] ), REMOVEFILTERS ( dim_date ) )
VAR _firstWeek = _first - WEEKDAY ( _first, 3 )
VAR _from = MAX ( _week - 21, _firstWeek )
VAR _weeks = INT ( DATEDIFF ( _from, _week, DAY ) / 7 ) + 1
VAR _apps =
    CALCULATE (
        [Total Applications],
        REMOVEFILTERS ( dim_date ),
        dim_date[week_start] >= _from,
        dim_date[week_start] <= _week
    )
RETURN
    IF ( NOT ISBLANK ( _first ) && _week >= _firstWeek, DIVIDE ( _apps, _weeks ) + 0 )""",
        "0.0",
        "Average weekly applications over this week and the 3 before (fewer at the start of the "
        "series, exactly like the SQL window ROWS BETWEEN 3 PRECEDING). Use on a week_start axis.",
        "weekly_velocity.rolling_4w_avg")

    # ------------------------------------------------------------------ funnel
    for name, col, desc, sql in [
        ("Responses", "is_response",
         "Applications with any employer reply (assessment, interview, offer or rejection).",
         "kpis.responses"),
        ("Assessments", "reached_assessment", "Applications that reached an assessment or further.",
         "kpis.assessments"),
        ("Interviews", "reached_interview",
         "Applications that reached an interview or further (furthest stage reached).",
         "kpis.interviews"),
        ("Offers", "reached_offer", "Applications that reached an offer.", "kpis.offers"),
        ("Rejections", "is_rejected", "Applications currently Rejected.", "kpis.rejections"),
        ("Ghosted", "is_ghosted", "Applications currently Ghosted.", "kpis.ghosted"),
    ]:
        add(name, fun, _count_where(f"{F}[{col}] = TRUE ()"), "#,0", desc, sql)
    for name, num, desc, sql in [
        ("Response Rate %", "[Responses]", "Any employer response / applications.",
         "kpis.response_rate_pct"),
        ("Interview Rate %", "[Interviews]", "Reached interview / applications.",
         "kpis.interview_rate_pct"),
        ("Offer Rate %", "[Offers]", "Offers / applications.", "kpis.offer_rate_pct"),
        ("Rejection Rate %", "[Rejections]", "Rejected / applications.", "kpis.rejection_rate_pct"),
        ("Ghost Rate %", "[Ghosted]", "Ghosted / applications.", "kpis.ghost_rate_pct"),
    ]:
        add(name, fun, f"DIVIDE ( {num}, [Total Applications] )", "0.0%", desc, sql)
    for name, num, den, sql in [
        ("Assessment Step Conversion %", "[Assessments]", "[Responses]", "funnel.step_conversion_pct (Assessment)"),
        ("Interview Step Conversion %", "[Interviews]", "[Assessments]", "funnel.step_conversion_pct (Interview)"),
        ("Offer Step Conversion %", "[Offers]", "[Interviews]", "funnel.step_conversion_pct (Offer)"),
    ]:
        add(name, fun, f"DIVIDE ( {num}, {den} )", "0.0%",
            f"Stage-to-stage conversion: {num} / {den} (the SQL funnel's LAG step).", sql)
    for name, num in [("Responses per 100", "[Responses]"), ("Interviews per 100", "[Interviews]"),
                      ("Offers per 100", "[Offers]")]:
        add(name, fun, f"DIVIDE ( {num}, [Total Applications] ) * 100", "0.0",
            f"{num} per 100 applications - the unit of the published funnel benchmark.",
            "funnel.pct_of_applied")
    add("Interviews vs Benchmark", fun, _vs_benchmark("[Interviews per 100]", "Interview"),
        "", "Ahead / On par / Behind the published interviews-per-100 range.", "funnel.vs_benchmark")
    add("Offers vs Benchmark", fun, _vs_benchmark("[Offers per 100]", "Offer"),
        "", "Ahead / On par / Behind the published offers-per-100 range.", "funnel.vs_benchmark")

    # ------------------------------------------------------------------ reliability
    add("Response Rate CI Low %", rel, _wilson("[Responses]", "[Total Applications]", "lo"), "0.0%",
        "Wilson score 95% interval, lower bound, for the response rate.", "kpis.response_rate_lo")
    add("Response Rate CI High %", rel, _wilson("[Responses]", "[Total Applications]", "hi"), "0.0%",
        "Wilson score 95% interval, upper bound, for the response rate.", "kpis.response_rate_hi")
    add("Interview Rate CI Low %", rel, _wilson("[Interviews]", "[Total Applications]", "lo"),
        "0.0%", "Wilson 95% lower bound for the interview rate.", "kpis.interview_rate_lo")
    add("Interview Rate CI High %", rel, _wilson("[Interviews]", "[Total Applications]", "hi"),
        "0.0%", "Wilson 95% upper bound for the interview rate.", "kpis.interview_rate_hi")
    add("Sample Confidence", rel, _sample_confidence(), "",
        f"Adequate (n >= {bm.SAMPLE_ADEQUATE}), Indicative (n >= {bm.SAMPLE_INDICATIVE}) or Too small. "
        "Show it next to every per-platform rate.", "platform_performance.sample_confidence")
    add("Response Band", rel, response_band_dax(), "",
        "Response rate band vs published hiring data: "
        + ", ".join(f"{b.label} >= {_num(b.lower_pct)}%" for b in bm.RESPONSE_RATE_BANDS[:-1])
        + f", else {bm.RESPONSE_RATE_BANDS[-1].label}.", "kpis.response_band")

    # ------------------------------------------------------------------ timing
    add("Median Days To Response", tim, f"""
MEDIANX (
    FILTER ( {F}, NOT ISBLANK ( {F}[days_to_response] ) ),
    {F}[days_to_response]
)""", "0.0",
        "Median days to first reply over KNOWN reply dates only (blanks excluded, never zero-filled).",
        "kpis.median_days_to_response")
    add("Average Days To Response", tim, f"AVERAGE ( {F}[days_to_response] )", "0.0",
        "Mean days to first reply; AVERAGE skips blanks, so unknown dates are excluded.",
        "kpis.avg_days_to_response")
    add("Responses With Known Reply Date", tim, f"COUNT ( {F}[days_to_response] )", "#,0",
        "Replies whose date is known - the denominator behind the timing measures.",
        "kpis.responses_with_known_date")
    add("Median Days To Interview", tim, f"""
MEDIANX (
    FILTER ( {F}, NOT ISBLANK ( {F}[days_to_interview] ) ),
    {F}[days_to_interview]
)""", "0.0", "Median days from application to first interview (known dates only).",
        "kpis.median_days_to_interview")
    add("Follow-up Threshold (days)", tim, str(int(follow_up_days)), "0",
        "Days of silence before an open application needs a follow-up (settings.follow_up_after_days). "
        "To make it interactive, create a what-if parameter 'Follow-up Days' and replace this "
        "body with SELECTEDVALUE ( 'Follow-up Days'[Follow-up Days], "
        f"{int(follow_up_days)} ).", "settings.follow_up_after_days")
    add("Needs Follow-up", tim, f"""
VAR _threshold = [Follow-up Threshold (days)]
RETURN
    CALCULATE (
        COUNTROWS ( {F} ),
        KEEPFILTERS ( {F}[is_active] = TRUE () ),
        KEEPFILTERS ( {F}[days_since_activity] >= _threshold )
    ) + 0""", "#,0",
        "Open applications silent for at least the follow-up threshold (days_since_activity is as of "
        "the warehouse build).", "kpis.needs_follow_up")
    add("Responses (by Reply Date)", tim, f"""
CALCULATE (
    [Responses],
    USERELATIONSHIP ( {F}[first_response_date], dim_date[date_day] )
)""", "#,0",
        "Replies counted on the date they arrived (activates the inactive first_response_date "
        "relationship). Replies with an unknown date have no date and drop out of a date axis.")
    add("Applications Touched (by Activity Date)", tim, f"""
CALCULATE (
    [Total Applications],
    USERELATIONSHIP ( {F}[last_activity_date], dim_date[date_day] )
)""", "#,0", "Applications whose latest status change fell on the date (inactive relationship).")
    add("Status Changes (by Event Date)", tim, """
CALCULATE (
    COUNTROWS ( fct_status_events ),
    REMOVEFILTERS ( dim_date ),
    TREATAS ( VALUES ( dim_date[date_day] ), fct_status_events[occurred_on] )
)""", "#,0",
        "Status changes on the date they happened. fct_status_events has no direct date "
        "relationship (it would be ambiguous), so the visible dates are applied with TREATAS.",
        "transitions / recent_activity")
    add("Last 30d Response Rate %", tim, """
CALCULATE (
    [Response Rate %],
    DATESINPERIOD ( dim_date[date_day], MAX ( dim_date[date_day] ), -30, DAY )
)""", "0.0%", "Response rate of the applications sent in the last 30 days (still maturing).",
        "period_compare.last_30_response_pct")
    add("Prev 30d Response Rate %", tim, """
CALCULATE (
    [Response Rate %],
    DATESINPERIOD ( dim_date[date_day], MAX ( dim_date[date_day] ) - 30, -30, DAY )
)""", "0.0%", "Response rate of the applications sent in the 30 days before that.",
        "period_compare.prev_30_response_pct")

    # ------------------------------------------------------------------ channels
    add("Platform Response Rank", chn, """
IF (
    HASONEVALUE ( dim_platform[platform] ),
    RANKX (
        FILTER ( ALL ( dim_platform[platform] ), [Total Applications] > 0 ),
        [Response Rate %],
        ,
        DESC,
        DENSE
    )
)""", "0", "Dense rank of the platform by response rate (1 = best). Read with Sample Confidence.",
        "platform_performance.response_rank")
    add("Overall Response Rate %", chn,
        "CALCULATE ( [Response Rate %], REMOVEFILTERS ( dim_platform ) )", "0.0%",
        "Response rate across all platforms (ignores the platform filter only).")
    add("Response Rate vs Overall (pp)", chn, """
VAR _rate = [Response Rate %]
RETURN
    IF ( NOT ISBLANK ( _rate ), ROUND ( ( _rate - [Overall Response Rate %] ) * 100, 1 ) )""",
        "+0.0;-0.0;0.0", "Percentage-point gap between this platform's response rate and the overall rate.")
    add("Interview Rate vs Direct Benchmark (pp)", chn, f"""
VAR _rate = [Interview Rate %]
RETURN
    IF ( NOT ISBLANK ( _rate ), ROUND ( _rate * 100 - {_num(bm.DIRECT_INTERVIEW_RATE_PCT)}, 1 ) )""",
        "+0.0;-0.0;0.0",
        f"Interview rate minus the {_num(bm.DIRECT_INTERVIEW_RATE_PCT)}% direct-application benchmark, in "
        "percentage points.", "platform_performance.vs_direct_benchmark_pp")

    # ------------------------------------------------------------------ benchmarks (constants)
    add("Direct Interview Benchmark %", bmk, _num(bm.DIRECT_INTERVIEW_RATE_PCT / 100), "0.00%",
        "Published interview rate for direct applications. From jobtracker/benchmarks.py.",
        "benchmarks.DIRECT_INTERVIEW_RATE_PCT")
    add("Job Board Interview Benchmark %", bmk, _num(bm.JOB_BOARD_INTERVIEW_RATE_PCT / 100), "0.00%",
        "Published interview rate for job-board quick-apply.",
        "benchmarks.JOB_BOARD_INTERVIEW_RATE_PCT")
    for label, stage, lo, hi in [("Interviews", "Interview", int_lo, int_hi),
                                 ("Offers", "Offer", off_lo, off_hi)]:
        add(f"Benchmark {label} per 100 (Low)", bmk, _num(lo), "0.0",
            f"Published '{stage}' per 100 applications, low end.", "benchmarks.FUNNEL_PER_100")
        add(f"Benchmark {label} per 100 (High)", bmk, _num(hi), "0.0",
            f"Published '{stage}' per 100 applications, high end.", "benchmarks.FUNNEL_PER_100")
        add(f"Benchmark {label} per 100 (Mid)", bmk, _num(bm.midpoint((lo, hi))), "0.0",
            f"Midpoint of the published '{stage}' per-100 range (bar for the funnel chart).",
            "benchmarks.FUNNEL_PER_100")
    return m


def dax_measures(follow_up_days: int = 7) -> dict[str, str]:
    return {x.name: x.expression for x in measures(follow_up_days)}


def _indent(text: str, pad: str) -> str:
    return "\n".join(pad + line if line.strip() else line for line in text.splitlines())


def render_dax_file(follow_up_days: int = 7, as_of: str = "") -> str:
    items = measures(follow_up_days)
    out = [
        "// ============================================================================",
        "// Job-search analytics - Power BI measures",
        f"// Generated by jobtracker.bi{(' on ' + as_of) if as_of else ''}. {len(items)} measures.",
        "//",
        "// HOW TO LOAD: Power BI Desktop > DAX query view > paste this whole file > Run",
        "// (previews the headline numbers) > click 'Update model with changes' to add every",
        f"// measure to the {HOME_TABLE} table. Then, in Model view, set each measure's",
        "// Display folder and Format string from the comments above it (or leave the defaults).",
        "//",
        "// Thresholds come from jobtracker/benchmarks.py; every measure reproduces the SQL",
        "// metric named in its 'SQL:' comment (jobtracker/warehouse/metrics.py).",
        "// Unknown reply dates stay BLANK in days_to_response and are never zero-filled.",
        "// Per-platform rates are indicative at this sample size - pair them with",
        "// [Sample Confidence] and the Wilson CI measures.",
        "// ============================================================================",
        "DEFINE",
    ]
    folder = None
    for x in items:
        if x.folder != folder:
            folder = x.folder
            out += ["", f"    // ----- Display folder: {folder} " + "-" * max(4, 50 - len(folder))]
        out.append("")
        out.append(f"    // {x.name}: {x.description}")
        meta = [f"Folder: {x.folder}"]
        if x.format:
            meta.append(f"Format: {x.format}")
        if x.sql:
            meta.append(f"SQL: {x.sql}")
        out.append("    // " + " | ".join(meta))
        body = x.expression
        if "\n" in body:
            out.append(f"    MEASURE {HOME_TABLE}[{x.name}] =")
            out.append(_indent(body, "        "))
        else:
            out.append(f"    MEASURE {HOME_TABLE}[{x.name}] = {body}")
    preview = ["Total Applications", "Responses", "Response Rate %", "Response Rate CI Low %",
               "Response Rate CI High %", "Interview Rate %", "Offers", "Active Pipeline",
               "Needs Follow-up", "Median Days To Response", "Direct Channel Share %",
               "Response Band", "Sample Confidence", "Applications Last 30 Days",
               "Applications Prev 30 Days"]
    out += ["", "// Preview: one row of headline measures (matches the app's KPI strip).",
            "EVALUATE", "    ROW ("]
    rows = [f'        "{name}", [{name}]' for name in preview]
    out.append(",\n".join(rows))
    out.append("    )")
    out += ["", "// Preview: the per-platform table (matches the Platforms sheet / SQL).",
            "EVALUATE",
            "    SUMMARIZECOLUMNS (",
            "        dim_platform[platform],",
            '        "Applications", [Total Applications],',
            '        "Response Rate %", [Response Rate %],',
            '        "CI Low %", [Response Rate CI Low %],',
            '        "CI High %", [Response Rate CI High %],',
            '        "Rank", [Platform Response Rank],',
            '        "Sample", [Sample Confidence]',
            "    )",
            "    ORDER BY [Applications] DESC", ""]
    return "\n".join(out)
