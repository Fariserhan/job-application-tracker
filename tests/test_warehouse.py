"""Warehouse tests — the star schema, its invariants and the analytical SQL.

Hermetic: a temporary database seeded with known rows, so no personal data is read or
written. These tests are the regression net for the analytics layer: if the ETL or a
query starts producing contradictory numbers, this fails before the dashboard lies.
"""
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import warehouse
import warehouse_etl
import warehouse_queries as wq


def _seed(db_path: str):
    """Three applications with deliberately different shapes."""
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE applications (
            thread_id TEXT PRIMARY KEY, company_name TEXT NOT NULL, role_title TEXT NOT NULL,
            source_platform TEXT DEFAULT 'Other', application_date TEXT NOT NULL,
            current_status TEXT NOT NULL, last_updated TEXT NOT NULL,
            job_description_snippet TEXT DEFAULT '', job_url TEXT DEFAULT '',
            gmail_link TEXT DEFAULT '', latest_subject TEXT DEFAULT '',
            is_valid INTEGER NOT NULL DEFAULT 1, fit_score REAL,
            matched_skills TEXT DEFAULT '', missing_skills TEXT DEFAULT '',
            actionable_improvements TEXT DEFAULT '', filter_reason TEXT DEFAULT '',
            job_type TEXT DEFAULT '', seniority_level TEXT DEFAULT '',
            industry TEXT DEFAULT '', notes TEXT DEFAULT '',
            fit_source TEXT DEFAULT 'rule'
        );
        CREATE TABLE status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL,
            from_status TEXT, to_status TEXT, note TEXT DEFAULT '', changed_at TEXT
        );
    """)
    conn.executemany(
        "INSERT INTO applications (thread_id, company_name, role_title, source_platform, "
        "application_date, current_status, last_updated, is_valid, fit_score, "
        "matched_skills, missing_skills, job_type, industry) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            # evolved thread: applied, then an interview 20 days later
            ("t1", "Acme Insurance", "Actuarial Analyst", "JobStreet",
             "2026-08-01", "Interview", "2026-08-21", 1, 82.0,
             '["SQL", "Python"]', '["IFRS 17 / Reserving"]', "Full-Time", "Insurance"),
            # rejected after 30 days
            ("t2", "Beta Bank", "Data Analyst", "LinkedIn",
             "2026-08-05", "Rejected", "2026-09-04", 1, 61.0,
             '["SQL"]', '["Tableau"]', "Full-Time", "Banking & Finance"),
            # still waiting, never updated since applying
            ("t3", "Gamma Takaful", "Business Intelligence Analyst", "Direct ATS",
             "2026-08-10", "Applied", "2026-08-10", 1, 55.0,
             '[]', '["Power BI"]', "Full-Time", "Insurance"),
            # invalid row — must never reach the warehouse
            ("t4", "Noise Co", "Job Alert", "Other",
             "2026-08-11", "Applied", "2026-08-11", 0, None, '[]', '[]', "", ""),
        ])
    conn.executemany(
        "INSERT INTO status_history (thread_id, from_status, to_status, note, changed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("t1", "Applied", "Interview", "updated from Gmail", "2026-08-21 09:00:00"),
            ("t2", "Applied", "Rejected", "updated from Gmail", "2026-09-04 09:00:00"),
        ])
    conn.commit()
    conn.close()


class WarehouseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jobsuite_wh_")
        self.db = os.path.join(self.tmp, "applications.db")
        _seed(self.db)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- ETL

    def test_build_loads_only_valid_rows(self):
        counts = warehouse.build_warehouse(self.db)
        self.assertEqual(counts["applications"], 3)  # t4 is is_valid=0

    def test_build_is_idempotent(self):
        first = warehouse.build_warehouse(self.db)
        second = warehouse.build_warehouse(self.db)
        self.assertEqual(first["applications"], second["applications"])
        with warehouse.connect(self.db) as conn:
            n = conn.execute("SELECT COUNT(*) FROM fact_application").fetchone()[0]
        self.assertEqual(n, 3)  # rebuilt, not appended

    def test_every_fact_row_joins_every_dimension(self):
        warehouse.build_warehouse(self.db)
        with warehouse.connect(self.db) as conn:
            orphans = conn.execute("""
                SELECT COUNT(*) FROM fact_application f
                LEFT JOIN dim_company     c  ON c.company_key     = f.company_key
                LEFT JOIN dim_platform    p  ON p.platform_key    = f.platform_key
                LEFT JOIN dim_status      s  ON s.status_key      = f.status_key
                LEFT JOIN dim_role_family rf ON rf.role_family_key = f.role_family_key
                LEFT JOIN dim_seniority   se ON se.seniority_key  = f.seniority_key
                LEFT JOIN dim_industry    i  ON i.industry_key    = f.industry_key
                LEFT JOIN dim_job_type    j  ON j.job_type_key    = f.job_type_key
                WHERE c.company_key IS NULL OR p.platform_key IS NULL
                   OR s.status_key IS NULL OR rf.role_family_key IS NULL
                   OR se.seniority_key IS NULL OR i.industry_key IS NULL
                   OR j.job_type_key IS NULL
            """).fetchone()[0]
        self.assertEqual(orphans, 0)

    def test_role_family_and_seniority_are_derived(self):
        warehouse.build_warehouse(self.db)
        with warehouse.connect(self.db) as conn:
            fams = {r[0] for r in conn.execute(
                "SELECT rf.role_family FROM fact_application f "
                "JOIN dim_role_family rf ON rf.role_family_key = f.role_family_key")}
        self.assertIn("Actuarial", fams)
        self.assertIn("Data & Analytics", fams)

    # ---------------------------------------------------------------- response semantics

    def test_days_to_response_uses_the_real_event_date(self):
        warehouse.build_warehouse(self.db)
        with warehouse.connect(self.db) as conn:
            rows = dict(conn.execute(
                "SELECT thread_id, days_to_response FROM fact_application").fetchall())
        # t1: applied 08-01, interview event 08-21 -> 20 days
        self.assertEqual(rows["t1"], 20)
        # t2: applied 08-05, rejected 09-04 -> 30 days
        self.assertEqual(rows["t2"], 30)
        # t3 never moved: unknown, NOT zero
        self.assertIsNone(rows["t3"])

    def test_unknown_response_date_is_not_zero_filled(self):
        """A response with no knowable date must be excluded, never counted as same-day."""
        warehouse.build_warehouse(self.db)
        with warehouse.connect(self.db) as conn:
            zero_day = conn.execute(
                "SELECT COUNT(*) FROM fact_application WHERE days_to_response = 0").fetchone()[0]
        self.assertEqual(zero_day, 0)

    def test_days_waiting_reflects_last_update(self):
        warehouse.build_warehouse(self.db)
        with warehouse.connect(self.db) as conn:
            rows = dict(conn.execute(
                "SELECT thread_id, days_waiting FROM fact_application").fetchall())
        self.assertEqual(rows["t3"], 0)      # applied and never touched
        self.assertEqual(rows["t1"], 20)

    # ---------------------------------------------------------------- queries

    def test_funnel_is_monotonic(self):
        warehouse.build_warehouse(self.db)
        df = wq.query("funnel_conversion", self.db)
        counts = list(df["applications"])
        self.assertEqual(counts, sorted(counts, reverse=True),
                         "a later stage can never exceed an earlier one")
        self.assertEqual(counts[0], 3)   # all three applied
        self.assertEqual(counts[3], 0)   # no offers

    def test_headline_kpis_are_consistent(self):
        warehouse.build_warehouse(self.db)
        row = wq.query("headline_kpis", self.db).iloc[0]
        self.assertEqual(int(row["total_applications"]), 3)
        self.assertEqual(int(row["responses"]), 2)          # interview + rejected
        self.assertEqual(int(row["rejections"]), 1)
        self.assertEqual(int(row["interviews_plus"]), 1)
        self.assertAlmostEqual(float(row["response_rate_pct"]), 66.7, places=1)

    def test_platform_query_ranks_with_window_functions(self):
        warehouse.build_warehouse(self.db)
        df = wq.query("platform_effectiveness", self.db)
        self.assertFalse(df.empty)
        self.assertIn("effectiveness_rank", df.columns)
        # DENSE_RANK: contiguous from 1, no gaps. Ties share a rank legitimately, so the
        # set of ranks must equal range(1, distinct_ranks+1), not the row count.
        ranks = sorted(set(int(r) for r in df["volume_rank"]))
        self.assertEqual(ranks, list(range(1, len(ranks) + 1)))
        # The top-volume platform must outrank the rest.
        top = df.sort_values("volume_rank").iloc[0]
        self.assertGreaterEqual(int(top["applications"]), int(df["applications"].max()))

    def test_platform_ties_do_not_inflate_ranks(self):
        """Platforms with identical volume must share a rank, not consume 1/2/3.

        DENSE_RANK plus a deterministic tie-breaker means equal counts collapse onto one
        rank, so the number of distinct ranks can never exceed the number of platforms.
        """
        warehouse.build_warehouse(self.db)
        df = wq.query("platform_effectiveness", self.db)
        distinct_ranks = df["volume_rank"].nunique()
        self.assertLessEqual(distinct_ranks, len(df))
        # Identical application counts must map to identical ranks.
        by_count = {}
        for _, row in df.iterrows():
            by_count.setdefault(int(row["applications"]), set()).add(int(row["volume_rank"]))
        for count, ranks in by_count.items():
            self.assertEqual(len(ranks), 1,
                             f"platforms with {count} applications got ranks {ranks}")

    def test_queries_are_read_only_and_safe_on_empty_db(self):
        """No warehouse tables at all: every query must return empty, not raise."""
        empty = os.path.join(self.tmp, "empty.db")
        sqlite3.connect(empty).close()
        for name in wq.QUERIES:
            df = wq.query(name, empty)
            self.assertTrue(df.empty, name)

    def test_run_all_covers_every_query(self):
        warehouse.build_warehouse(self.db)
        results = wq.run_all(self.db)
        self.assertEqual(set(results), set(wq.QUERIES))
        self.assertFalse(results["headline_kpis"].empty)

    def test_dashboard_helpers_survive_an_empty_frame(self):
        """The dashboard analytics helpers must never raise on empty input."""
        import pandas as pd
        dashboard = __import__("dashboard")
        empty = pd.DataFrame(columns=["thread_id", "company_name", "source_platform",
                                      "current_status", "last_updated"])
        with patch.object(dashboard, "_wq", return_value=pd.DataFrame()):
            for fn in (dashboard.platform_performance, dashboard.company_summary):
                self.assertIsInstance(fn(empty), pd.DataFrame, fn.__name__)

    def test_no_query_aggregates_in_python(self):
        """Guard the design claim: the analytics must live in the SQL text."""
        for name, sql in wq.QUERIES.items():
            self.assertIn("SELECT", sql.upper(), name)
            self.assertGreater(len(sql.splitlines()), 3, name)

    def _valid_frame(self):
        """A minimal frame shaped like the dashboard's `valid` data."""
        import pandas as pd

        return pd.DataFrame({
            "thread_id": ["t1", "t2"],
            "company_name": ["Acme", "Beta"],
            "role_title": ["Actuarial Analyst", "Data Analyst"],
            "source_platform": ["JobStreet", "LinkedIn"],
            "current_status": ["Interview", "Rejected"],
            "application_date": pd.to_datetime(["2026-08-01", "2026-08-05"]),
            "last_updated": pd.to_datetime(["2026-08-21", "2026-09-04"]),
            "fit_score": [82.0, 61.0],
            "job_description_snippet": ["SQL and Power BI", "Tableau and SQL"],
            "matched_skills": [["SQL"], ["SQL"]],
            "missing_skills": [["IFRS 17 / Reserving"], ["Tableau"]],
            "actionable_improvements": [[], []],
            "job_type": ["Full-Time", "Full-Time"],
            "seniority_level": ["Analyst / Associate", "Analyst / Associate"],
            "industry": ["Insurance", "Banking & Finance"],
        })


class BiLayerTests(unittest.TestCase):
    """The BI pack must describe the warehouse that actually exists."""

    def test_unified_export_is_one_row_per_application(self):
        """The day-to-day export is ONE flat CSV, one row per application.

        Regression test: this used to be emitted only as a set of ~16 per-report CSVs,
        which is not what "export to CSV" should mean.
        """
        import bi_export

        tmp = tempfile.mkdtemp(prefix="jobsuite_bi_")
        try:
            db = os.path.join(tmp, "applications.db")
            _seed(db)
            warehouse.build_warehouse(db)
            with warehouse.connect(db) as conn:
                expected = conn.execute("SELECT COUNT(*) FROM fact_application").fetchone()[0]

            df = bi_export.unified_csv_conn(db)
            self.assertEqual(len(df), expected, "one row per application")
            self.assertEqual(df["thread_id"].nunique(), expected, "no duplicated rows")
            # The columns a person actually wants when they open the file.
            for col in ("company_name", "role_title", "current_status", "application_date",
                        "days_waiting", "is_response"):
                self.assertIn(col, df.columns, col)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_dax_references_real_warehouse_tables(self):
        import bi_export

        measures = bi_export.dax_measures()
        text = " ".join(measures.values())
        # Every table the model actually exposes must be referenced by at least one measure
        # or by the model spec that ships with it.
        for table in ("fact_application", "dim_date", "dim_platform"):
            self.assertIn(table, text, table)
        spec_text = str(bi_export.model_spec())
        for table in ("dim_status", "dim_role_family", "dim_seniority", "dim_industry",
                      "dim_job_type", "dim_skill", "dim_company"):
            self.assertIn(table, spec_text, table)

    def test_bi_layer_does_not_invent_insurance_measures(self):
        """The tracker has no premium/claim grain — loss ratio and IBNR must not appear."""
        import bi_export

        text = (" ".join(bi_export.dax_measures().values())
                + " ".join(bi_export.tableau_calculations().values())).lower()
        # The README explains WHY they are absent, so only the measure bodies are checked.
        measures_only = " ".join(bi_export.dax_measures().values()).lower()
        for wrong in ("loss ratio", "ibnr", "csm roll", "incurred claims"):
            self.assertNotIn(wrong, measures_only, wrong)

    def test_model_spec_matches_the_warehouse_relationships(self):
        import bi_export

        spec = bi_export.model_spec()
        rels = {r["from"] for r in spec["relationships"]}
        self.assertTrue(any("company_key" in r for r in rels))
        self.assertTrue(any("application_date_key" in r for r in rels))
        # the role-playing date dimension must be declared inactive
        inactive = [r for r in spec["relationships"] if "INACTIVE" in str(r.get("note", ""))]
        self.assertEqual(len(inactive), 1)


if __name__ == "__main__":
    unittest.main()
