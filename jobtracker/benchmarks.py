"""Published hiring benchmarks — the single source the SQL, DAX and Tableau layers read.

Every surface that compares the tracker to the market (dashboard KPI bands, the funnel
benchmark chart, the DAX `Response Band` measure, the Tableau calculated field, the Excel
conditional formats) is generated from the constants below, so a band can only ever be
changed in one place.

Caveat kept visible on purpose: these are aggregate figures from public job-search
research (mixed seniority, mostly cold applications). One person's pipeline is a small
sample, so comparisons are indicative — the metric layer attaches Wilson confidence
intervals to every rate for exactly that reason.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Band:
    label: str
    lower_pct: float  # inclusive lower bound of the band, in percent

# Response rate bands: <2% below average · 2-3% average · 3-5% good · 5%+ strong.
RESPONSE_RATE_BANDS: tuple[Band, ...] = (
    Band("Strong", 5.0),
    Band("Good", 3.0),
    Band("Average", 2.0),
    Band("Below average", 0.0),
)

# Interview rate when applying directly on an employer's careers page vs a job board
# (1.24M tracked applications): 6.87% direct vs 1.95% LinkedIn Easy Apply.
DIRECT_INTERVIEW_RATE_PCT = 6.87
JOB_BOARD_INTERVIEW_RATE_PCT = 1.95
WELL_TARGETED_INTERVIEW_RATE_PCT = (5.0, 10.0)

# The published "100 applications" funnel, expressed as the benchmark midpoints per stage.
FUNNEL_PER_100 = {
    "Employer responded": (2.0, 3.0),
    "Interview": (5.0, 10.0),
    "Offer": (0.0, 1.0),
}

# Below these sample sizes a rate is shown but labelled; the DAX/Tableau `Sample
# Confidence` measure uses the same thresholds.
SAMPLE_ADEQUATE = 30
SAMPLE_INDICATIVE = 10


def midpoint(pair: tuple[float, float]) -> float:
    return round((pair[0] + pair[1]) / 2, 2)


def response_band(rate_pct: float | None) -> str:
    if rate_pct is None:
        return "No data"
    for band in RESPONSE_RATE_BANDS:
        if rate_pct >= band.lower_pct:
            return band.label
    return RESPONSE_RATE_BANDS[-1].label


def sample_confidence(n: int) -> str:
    if n >= SAMPLE_ADEQUATE:
        return "Adequate"
    if n >= SAMPLE_INDICATIVE:
        return "Indicative"
    return "Too small"


def response_band_sql(rate_expr: str) -> str:
    """CASE expression that maps a percentage expression to its band label."""
    whens = "\n".join(
        f"        WHEN {rate_expr} >= {b.lower_pct} THEN '{b.label}'"
        for b in RESPONSE_RATE_BANDS[:-1]
    )
    return (f"CASE\n        WHEN {rate_expr} IS NULL THEN 'No data'\n{whens}\n"
            f"        ELSE '{RESPONSE_RATE_BANDS[-1].label}'\n    END")
