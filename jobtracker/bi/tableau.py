"""Tableau calculated fields over ``job_applications.csv`` (= ``rpt_application_analysis``).

Same definitions as the SQL metric layer and the DAX measures; thresholds generated from
``jobtracker.benchmarks``. Field names are the CSV column names, so the calculations paste
straight into Tableau's calculation editor after connecting to the CSV.

Notes on Tableau semantics that matter for parity:
* ``MEDIAN`` / ``AVG`` ignore NULLs, so blank ``days_to_response`` stays excluded.
* FIXED LODs ignore dimension filters unless those filters are added to context - the guide
  says which filters to promote.
* Table calculations (rolling average, running total, rank) depend on the view's
  "Compute using" setting; each field says what to set.
"""
from __future__ import annotations

from dataclasses import dataclass

from .. import benchmarks as bm
from ..warehouse.metrics import Z

PARAMETERS = {
    "Follow-up Days": "Integer parameter, current value = settings.follow_up_after_days "
                      "(range 1-60). Drives [Needs Follow-up].",
}


@dataclass(frozen=True)
class Calc:
    name: str
    group: str
    formula: str
    kind: str  # Aggregate | Row-level | LOD | Table calc
    description: str
    sql: str = ""


def _num(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _flag_sum(field: str) -> str:
    return f"SUM(IIF([{field}], 1, 0))"


def _wilson(x: str, n: str, bound: str) -> str:
    sign = "-" if bound == "lo" else "+"
    z = _num(Z)
    z2 = _num(Z * Z)
    p = f"({x} / {n})"
    return (f"// Wilson score 95% interval ({'lower' if bound == 'lo' else 'upper'} bound), z = {z}\n"
            f"IF {n} > 0 THEN\n"
            f"    MAX(0, MIN(1,\n"
            f"        ({p} + {z2} / (2 * {n})\n"
            f"         {sign} {z} * SQRT({p} * (1 - {p}) / {n} + {z2} / (4 * {n} * {n})))\n"
            f"        / (1 + {z2} / {n})))\n"
            f"END")


def response_band_tableau(rate: str = "[Response Rate %]") -> str:
    bands = bm.RESPONSE_RATE_BANDS
    lines = ["// Bands from jobtracker/benchmarks.py; rate rounded to 1 dp like the SQL",
             f"IF ISNULL({rate}) THEN 'No data'"]
    for band in bands[:-1]:
        lines.append(f"ELSEIF ROUND({rate} * 100, 1) >= {_num(band.lower_pct)} THEN '{band.label}'")
    lines.append(f"ELSE '{bands[-1].label}'")
    lines.append("END")
    return "\n".join(lines)


def _sample_conf(n: str) -> str:
    return (f"IF {n} >= {bm.SAMPLE_ADEQUATE} THEN 'Adequate'\n"
            f"ELSEIF {n} >= {bm.SAMPLE_INDICATIVE} THEN 'Indicative'\n"
            f"ELSE 'Too small'\nEND")


def calculations() -> list[Calc]:
    c: list[Calc] = []

    def add(name, group, formula, kind, description, sql=""):
        c.append(Calc(name, group, formula.strip(), kind, description, sql))

    agg, row, lod, tc = "Aggregate", "Row-level", "LOD", "Table calc"
    # -------------------------------------------------------------- counts
    add("Total Applications", "Volume", "SUM([application_count])", agg,
        "Applications in the view (application_count is always 1).", "kpis.applications")
    add("Companies", "Volume", "COUNTD([company_name])", agg, "Distinct employers.",
        "kpis.companies")
    for name, field, sql in [("Responses", "is_response", "kpis.responses"),
                             ("Assessments", "reached_assessment", "kpis.assessments"),
                             ("Interviews", "reached_interview", "kpis.interviews"),
                             ("Offers", "reached_offer", "kpis.offers"),
                             ("Rejections", "is_rejected", "kpis.rejections"),
                             ("Ghosted Applications", "is_ghosted", "kpis.ghosted"),
                             ("Active Pipeline", "is_active", "kpis.active")]:
        add(name, "Funnel", _flag_sum(field), agg, f"Applications where [{field}] is TRUE.", sql)
    # -------------------------------------------------------------- rates
    for name, num, sql in [("Response Rate %", "[Responses]", "kpis.response_rate_pct"),
                           ("Interview Rate %", "[Interviews]", "kpis.interview_rate_pct"),
                           ("Offer Rate %", "[Offers]", "kpis.offer_rate_pct"),
                           ("Rejection Rate %", "[Rejections]", "kpis.rejection_rate_pct"),
                           ("Ghost Rate %", "[Ghosted Applications]", "kpis.ghost_rate_pct")]:
        add(name, "Rates", f"{num} / [Total Applications]", agg,
            f"{num} / applications. Format as Percentage, 1 decimal.", sql)
    add("Direct Channel Share %", "Rates", f"{_flag_sum('is_direct')} / [Total Applications]", agg,
        "Share of applications made directly with the employer.", "kpis.direct_share_pct")
    add("Response Rate CI Low %", "Reliability",
        _wilson("[Responses]", "[Total Applications]", "lo"), agg,
        "Wilson 95% lower bound of the response rate.", "kpis.response_rate_lo")
    add("Response Rate CI High %", "Reliability",
        _wilson("[Responses]", "[Total Applications]", "hi"), agg,
        "Wilson 95% upper bound of the response rate.", "kpis.response_rate_hi")
    add("Response Rate CI Width %", "Reliability",
        "[Response Rate CI High %] - [Response Rate CI Low %]", agg,
        "Interval width - the Size of a Gantt bar that starts at [Response Rate CI Low %].")
    add("Interview Rate CI Low %", "Reliability",
        _wilson("[Interviews]", "[Total Applications]", "lo"), agg,
        "Wilson 95% lower bound of the interview rate.", "kpis.interview_rate_lo")
    add("Interview Rate CI High %", "Reliability",
        _wilson("[Interviews]", "[Total Applications]", "hi"), agg,
        "Wilson 95% upper bound of the interview rate.", "kpis.interview_rate_hi")
    add("Response Band", "Reliability", response_band_tableau(), agg,
        "Band of the response rate vs published hiring data.", "kpis.response_band")
    add("Sample Confidence", "Reliability", _sample_conf("[Total Applications]"), agg,
        "Label every per-segment rate with this.", "platform_performance.sample_confidence")
    # -------------------------------------------------------------- timing
    add("Median Days To Response", "Timing", "MEDIAN([days_to_response])", agg,
        "Median over KNOWN reply dates (MEDIAN ignores NULL; unknowns are never zero-filled).",
        "kpis.median_days_to_response")
    add("Avg Days To Response", "Timing", "AVG([days_to_response])", agg,
        "Mean over known reply dates.", "kpis.avg_days_to_response")
    add("Responses With Known Reply Date", "Timing", "COUNT([days_to_response])", agg,
        "COUNT skips NULL: replies whose date is known.", "kpis.responses_with_known_date")
    add("Median Days To Interview", "Timing", "MEDIAN([days_to_interview])", agg,
        "Median days to first interview (known dates only).", "kpis.median_days_to_interview")
    add("Needs Follow-up Flag", "Timing",
        "[is_active] AND [days_since_activity] >= [Parameters].[Follow-up Days]", row,
        "TRUE for open applications silent for at least the Follow-up Days parameter.",
        "follow_up_queue")
    add("Needs Follow-up", "Timing", "SUM(IIF([Needs Follow-up Flag], 1, 0))", agg,
        "Count of applications needing a follow-up.", "kpis.needs_follow_up")
    add("Days To Response Bucket", "Timing", """
IF ISNULL([days_to_response]) THEN NULL
ELSEIF [days_to_response] <= 3 THEN '0-3 days'
ELSEIF [days_to_response] <= 7 THEN '4-7 days'
ELSEIF [days_to_response] <= 14 THEN '1-2 weeks'
ELSEIF [days_to_response] <= 30 THEN '2-4 weeks'
ELSE '1 month+'
END""", row, "Reply-time bucket (known reply dates only).", "response_time_buckets.bucket")
    add("Applications Last 30 Days", "Timing", "SUM(IIF([days_since_applied] < 30, 1, 0))", agg,
        "Applied in the 30 days up to the warehouse build (days_since_applied is a build-time snapshot).",
        "period_compare.last_30")
    add("Applications Prev 30 Days", "Timing",
        "SUM(IIF([days_since_applied] >= 30 AND [days_since_applied] < 60, 1, 0))", agg,
        "Applied 30-59 days before the build.", "period_compare.prev_30")
    add("Applications Change 30d", "Timing",
        "[Applications Last 30 Days] - [Applications Prev 30 Days]", agg,
        "Volume change vs the previous 30 days.", "period_compare.change")
    # -------------------------------------------------------------- LOD
    add("Platform Response Rate %", "Channels", """
{ FIXED [platform] : SUM(IIF([is_response], 1, 0)) }
/ { FIXED [platform] : SUM([application_count]) }""", lod,
        "Per-platform response rate regardless of the view's level of detail. Add the date "
        "filter to context so it respects it.", "platform_performance.response_rate_pct")
    add("Platform Applications", "Channels", "{ FIXED [platform] : SUM([application_count]) }",
        lod, "Applications per platform (for the sample-confidence label).",
        "platform_performance.applications")
    add("Platform Sample Confidence", "Channels", _sample_conf("[Platform Applications]"), lod,
        "Sample label at platform grain.", "platform_performance.sample_confidence")
    add("Response Rate vs Overall (pp)", "Channels", """
ROUND(([Response Rate %]
       - { SUM(IIF([is_response], 1, 0)) } / { SUM([application_count]) }) * 100, 1)""", lod,
        "Gap (percentage points) between this mark's response rate and the overall rate.")
    add("Interview Rate vs Direct Benchmark (pp)", "Channels",
        f"ROUND([Interview Rate %] * 100 - {_num(bm.DIRECT_INTERVIEW_RATE_PCT)}, 1)", agg,
        f"Interview rate minus the {_num(bm.DIRECT_INTERVIEW_RATE_PCT)}% direct-application benchmark.",
        "platform_performance.vs_direct_benchmark_pp")
    add("Platform Response Rank", "Channels", "RANK_DENSE([Response Rate %], 'desc')", tc,
        "Compute using: platform. 1 = highest response rate.",
        "platform_performance.response_rank")
    add("Cohort Response Rate %", "Cohorts", """
{ FIXED [applied_month] : SUM(IIF([is_response], 1, 0)) }
/ { FIXED [applied_month] : SUM([application_count]) }""", lod,
        "Response rate of the month-of-application cohort.", "monthly_cohorts.response_rate_pct")
    add("Cohort Is Maturing", "Cohorts",
        "{ FIXED [applied_month] : MAX([days_since_applied]) } < 30", lod,
        "TRUE while the cohort is younger than 30 days - read its rates as floors.",
        "monthly_cohorts.is_maturing")
    add("Company First Applied", "Cohorts", "{ FIXED [company_name] : MIN([applied_date]) }",
        lod, "First application date to each employer.")
    add("Is Repeat Employer", "Cohorts",
        "{ FIXED [company_name] : SUM([application_count]) } > 1", lod,
        "TRUE when you applied to the employer more than once.")
    # -------------------------------------------------------------- table calcs
    add("Rolling 4-Week Avg Applications", "Velocity",
        "WINDOW_AVG(SUM([application_count]), -3, 0)", tc,
        "Compute using: applied_week (Show Missing Values on the week axis so empty weeks count "
        "as 0, like the SQL).", "weekly_velocity.rolling_4w_avg")
    add("Cumulative Applications", "Velocity", "RUNNING_SUM(SUM([application_count]))", tc,
        "Compute using: applied_week.", "weekly_velocity.cumulative")
    # -------------------------------------------------------------- funnel vs benchmark
    for label, measure, stage in [("Interviews", "[Interview Rate %]", "Interview"),
                                  ("Offers", "[Offer Rate %]", "Offer")]:
        lo, hi = bm.FUNNEL_PER_100[stage]
        add(f"{label} per 100", "Benchmarks", f"{measure} * 100", agg,
            f"{label} per 100 applications.", "funnel.pct_of_applied")
        add(f"Benchmark {label} per 100 (Mid)", "Benchmarks", _num(bm.midpoint((lo, hi))), agg,
            f"Midpoint of the published '{stage}' per-100 range ({_num(lo)}-{_num(hi)}).",
            "benchmarks.FUNNEL_PER_100")
        add(f"{label} vs Benchmark", "Benchmarks",
            f"IF ISNULL([{label} per 100]) THEN NULL\n"
            f"ELSEIF [{label} per 100] > {_num(hi)} THEN 'Ahead'\n"
            f"ELSEIF [{label} per 100] >= {_num(lo)} THEN 'On par'\n"
            f"ELSE 'Behind'\nEND", agg,
            f"Position vs the published range {_num(lo)}-{_num(hi)} per 100.", "funnel.vs_benchmark")
    add("Direct Interview Benchmark %", "Benchmarks",
        _num(bm.DIRECT_INTERVIEW_RATE_PCT / 100), agg,
        "Published direct-application interview rate (reference line).",
        "benchmarks.DIRECT_INTERVIEW_RATE_PCT")
    return c


def tableau_calculations() -> dict[str, str]:
    return {x.name: x.formula for x in calculations()}


def render_markdown(follow_up_days: int = 7, as_of: str = "") -> str:
    items = calculations()
    out = [
        "# Tableau calculated fields",
        "",
        f"Generated by `jobtracker.bi`{(' on ' + as_of) if as_of else ''} - {len(items)} fields "
        "over `job_applications.csv` (the flat `rpt_application_analysis` view: one row per "
        "application). Every field reproduces the SQL metric named under it "
        "(`jobtracker/warehouse/metrics.py`), and every threshold is generated from "
        "`jobtracker/benchmarks.py`.",
        "",
        "Create each field with **Analysis > Create Calculated Field**, paste the name and the "
        "formula. Create the parameter first.",
        "",
        "## Parameter",
        "",
    ]
    for name, desc in PARAMETERS.items():
        out.append(f"- **{name}** - {desc} Default: `{int(follow_up_days)}`.")
    out += ["", "## Honesty notes", "",
            "- `days_to_response` is NULL when the reply date is unknown. `MEDIAN`/`AVG` skip "
            "NULLs - do **not** wrap it in `ZN()`.",
            "- Per-platform and per-cohort rates are indicative at this sample size: show "
            "`Sample Confidence` / the Wilson CI fields next to them.",
            ""]
    group = None
    for x in items:
        if x.group != group:
            group = x.group
            out += [f"## {group}", ""]
        out.append(f"### {x.name}")
        out.append("")
        meta = f"*{x.kind}* - {x.description}"
        if x.sql:
            meta += f" SQL: `{x.sql}`."
        out += [meta, "", "```", x.formula, "```", ""]
    return "\n".join(out)
