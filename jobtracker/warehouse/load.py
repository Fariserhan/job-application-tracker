"""Extract-load: the operational SQLite tables -> DuckDB ``raw`` schema (unchanged).

Python reads SQLite (no DuckDB extension download needed, so it works offline and on
locked-down networks) and hands DuckDB the rows as DataFrames. Every column is loaded as text so the
staging models own all typing decisions in one place.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

RAW_TABLES = ("applications", "status_events", "processed_messages")
# processed_messages carries the body cache. Never copy email bodies into the warehouse.
EXCLUDED_COLUMNS = {"processed_messages": {"body_text"}}


def extract(db_path: Path) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    with sqlite3.connect(db_path) as conn:
        for table in RAW_TABLES:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            keep = [c for c in cols if c not in EXCLUDED_COLUMNS.get(table, set())]
            if not keep:
                frames[table] = pd.DataFrame()
                continue
            frames[table] = pd.read_sql_query(
                f"SELECT {', '.join(keep)} FROM {table}", conn).astype("string")
    return frames


def load(con, frames: dict[str, pd.DataFrame]) -> dict[str, int]:
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    counts = {}
    for table, frame in frames.items():
        con.register("_incoming", frame)
        con.execute(f'CREATE OR REPLACE TABLE raw."{table}" AS SELECT * FROM _incoming')
        con.unregister("_incoming")
        counts[f"raw.{table}"] = len(frame)
    return counts
