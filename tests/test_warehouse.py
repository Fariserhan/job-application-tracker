"""Warehouse build, the dbt runner, and the metric layer — every SQL answer is cross-checked
against an independent Python computation over the same rows."""
from __future__ import annotations

import math
from datetime import date

import duckdb
import pandas as pd
import pytest

from jobtracker import benchmarks
from jobtracker.warehouse import build, connect, manifest, runner
from jobtracker.warehouse.metrics import Filters, Metrics, compose


@pytest.fixture
def facts(demo) -> pd.DataFrame:
    with connect(demo) as con:
        return con.execute("SELECT * FROM fct_applications").df()


def wilson_py(x: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = x / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return round(100 * max(0, centre - half), 1), round(100 * min(1, centre + half), 1)


# --------------------------------------------------------------------------- build

def test_build_runs_every_model_and_all_data_tests_pass(demo):
    info = manifest(demo)
    assert set(runner.discover()) <= set(info["models"])
    assert info["tests"] and not any(info["tests"].values())
    assert info["counts"]["fct_applications"] > 150


def test_build_is_atomic_and_repeatable(demo):
    first = build(demo)
    second = build(demo)
    assert first["fct_applications"] == second["fct_applications"]
    assert not list(demo.data_dir.glob("*.building"))


def test_runner_rejects_unsupported_jinja(tmp_path):
    (tmp_path / "models").mkdir()
    (tmp_path / "seeds").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "models" / "bad.sql").write_text("select {{ var('x') }} as x")
    with pytest.raises(runner.UnsupportedJinja):
        runner.discover(tmp_path)


def test_runner_orders_models_by_ref(tmp_path):
    for d in ("models", "seeds", "tests"):
        (tmp_path / d).mkdir()
    (tmp_path / "models" / "b.sql").write_text("select x + 1 as y from {{ ref('a') }}")
    (tmp_path / "models" / "a.sql").write_text("{{ config(materialized='view') }} select 41 as x")
    con = duckdb.connect()
    result = runner.run(con, tmp_path)
    assert result.models == ["a", "b"]
    assert con.execute('select y from "b"').fetchone()[0] == 42


def test_email_bodies_never_reach_the_warehouse(demo):
    with connect(demo) as con:
        cols = {r[0] for r in con.execute(
            "select column_name from information_schema.columns "
            "where table_name = 'processed_messages'").fetchall()}
    assert "body_text" not in cols


# --------------------------------------------------------------------------- model rules

def test_noise_and_saved_rows_are_excluded(demo, facts):
    import sqlite3

    with sqlite3.connect(demo.db_path) as conn:
        valid = conn.execute("select count(*) from applications where is_valid = 1 "
                             "and current_status <> 'Saved / Planning'").fetchone()[0]
    assert len(facts) == valid


def test_funnel_uses_furthest_stage_reached(facts):
    rejected_after_interview = facts[facts.is_rejected & facts.reached_interview]
    assert len(rejected_after_interview) > 0, "demo should contain post-interview rejections"
    assert (facts.reached_offer <= facts.reached_interview).all()
    assert (facts.reached_interview <= facts.reached_assessment
            | (facts.max_stage_reached == 3) | (facts.max_stage_reached == 4)).all()


def test_unknown_reply_dates_stay_null(facts):
    assert facts.loc[~facts.is_response, "days_to_response"].isna().all()
    known = facts.days_to_response.dropna()
    assert (known >= 0).all()


# --------------------------------------------------------------------------- metrics

def test_kpis_match_independent_computation(metrics, facts):
    k = metrics.kpis()
    n = len(facts)
    responses = int(facts.is_response.sum())
    interviews = int(facts.reached_interview.sum())
    assert k["applications"] == n
    assert k["responses"] == responses
    assert k["interviews"] == interviews
    assert k["response_rate_pct"] == round(100 * responses / n, 1)
    assert (k["response_rate_lo"], k["response_rate_hi"]) == wilson_py(responses, n)
    assert (k["interview_rate_lo"], k["interview_rate_hi"]) == wilson_py(interviews, n)
    assert k["median_days_to_response"] == facts.days_to_response.dropna().median()
    assert k["response_band"] == benchmarks.response_band(k["response_rate_pct"])


def test_funnel_is_monotonic_and_benchmarked(metrics):
    funnel = metrics.query("funnel")
    counts = funnel.applications.tolist()
    assert counts == sorted(counts, reverse=True)
    responded = funnel.set_index("stage").loc["Employer responded"]
    assert (responded.benchmark_per_100_lo, responded.benchmark_per_100_hi) == \
        benchmarks.FUNNEL_PER_100["Employer responded"]


def test_sankey_flows_conserve_applications(metrics, facts):
    sankey = metrics.query("sankey")
    out_of_applied = sankey[sankey.source == "Applied"].applications.sum()
    assert out_of_applied == len(facts)
    into_interview = sankey[sankey.target == "Interview"].applications.sum()
    assert into_interview == facts.reached_interview.sum()


def test_kaplan_meier_matches_reference_implementation(metrics, facts):
    """Reference KM in plain Python: survival = prod(1 - d_t / n_t)."""
    rows = facts[~(facts.is_response & facts.days_to_response.isna())]
    durations = [(int(r.days_to_response) if r.is_response else int(r.days_since_applied),
                  bool(r.is_response)) for r in rows.itertuples()]
    surv, expected = 1.0, {}
    for t in sorted({d for d, _ in durations}):
        at_risk = sum(1 for d, _ in durations if d >= t)
        events = sum(1 for d, e in durations if d == t and e)
        surv *= 1 - events / at_risk
        expected[t] = round(100 * surv, 2)
    km = metrics.query("response_survival").set_index("day")
    for t, value in expected.items():
        assert km.loc[t, "still_waiting_pct"] == pytest.approx(value, abs=0.01)
    assert km.still_waiting_pct.is_monotonic_decreasing


def test_platform_intervals_and_sample_labels(metrics):
    platforms = metrics.query("platform_performance")
    assert platforms.applications.sum() == metrics.kpis()["applications"]
    for row in platforms.itertuples():
        assert (row.response_rate_lo, row.response_rate_hi) == wilson_py(row.responses,
                                                                         row.applications)
        assert row.sample_confidence == benchmarks.sample_confidence(row.applications)
        assert row.vs_direct_benchmark_pp == pytest.approx(
            100 * row.interviews / row.applications - benchmarks.DIRECT_INTERVIEW_RATE_PCT, abs=0.06)


def test_filters_apply_to_every_metric(demo):
    with connect(demo) as con:
        everything = Metrics(con, demo).kpis()["applications"]
        linkedin = Metrics(con, demo, Filters(platforms=["LinkedIn"]))
        n = linkedin.kpis()["applications"]
        assert 0 < n < everything
        assert linkedin.query("applications").platform.eq("LinkedIn").all()
        assert linkedin.query("platform_performance").platform.tolist() == ["LinkedIn"]
        dated = Metrics(con, demo, Filters(date_from=date(2026, 9, 1)))
        assert (pd.to_datetime(dated.query("applications").applied_date)
                >= pd.Timestamp("2026-09-01")).all()
        searched = Metrics(con, demo, Filters(search="actuarial"))
        hits = searched.query("applications")
        assert (hits.role_title + " " + hits.company_name).str.contains(
            "actuarial", case=False).all()


def test_every_metric_runs_on_an_empty_warehouse(settings):
    from jobtracker.store import Repository

    Repository(settings).init()
    build(settings)
    with connect(settings) as con:
        results = Metrics(con, settings).all()
    assert results["kpis"].iloc[0]["applications"] == 0
    assert results["funnel"].applications.eq(0).all()


def test_follow_up_queue_respects_threshold(demo):
    with connect(demo) as con:
        queue = Metrics(con, demo).query("follow_up_queue", follow_up_days=10)
    assert (queue.days_since_activity >= 10).all()
    assert set(queue.current_status) <= {"Applied", "Assessment / OA", "Interview"}


def test_compose_merges_leading_with():
    sql = compose("WITH base AS (SELECT 1)\n", "-- note\nWITH x AS (SELECT 2) SELECT * FROM x")
    assert sql.count("WITH") == 1 and ", x AS" in sql.replace("\n", " ")


def test_segment_whitelist(metrics):
    with pytest.raises(ValueError):
        metrics.query("segment_performance", segment="1; drop table x")
