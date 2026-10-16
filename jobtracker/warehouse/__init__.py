"""Analytics warehouse: DuckDB star schema built from the dbt models.

``build(settings)`` = extract-load (SQLite -> ``raw``) + run every dbt model + run the data
tests, into a fresh file that atomically replaces the live warehouse — readers never see a
half-built model, and a failed build leaves the previous warehouse untouched.
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import duckdb

from . import load as _load
from . import runner


def build(settings) -> dict:
    settings.ensure_dirs()
    target = Path(settings.warehouse_path)
    staging = target.with_name(target.name + ".building")
    for leftover in (staging, Path(str(staging) + ".wal")):
        leftover.unlink(missing_ok=True)

    started = time.time()
    frames = _load.extract(Path(settings.db_path))
    con = duckdb.connect(str(staging))
    try:
        counts = _load.load(con, frames)
        result = runner.run(con)
        for table in ("fct_applications", "fct_status_events"):
            row = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
            counts[table] = int(row[0]) if row else 0
        manifest = {
            "built_at": datetime.now().isoformat(timespec="seconds"),
            "seconds": round(time.time() - started, 2),
            "models": result.models,
            "seeds": result.seeds,
            "tests": result.tests,
            "counts": counts,
        }
        con.execute("CREATE OR REPLACE TABLE _build_manifest AS SELECT ? AS manifest",
                    [json.dumps(manifest)])
        con.execute("CHECKPOINT")
    finally:
        con.close()

    for attempt in range(6):
        try:
            os.replace(staging, target)
            break
        except PermissionError:  # Windows: a reader still holds the file — retry briefly
            if attempt == 5:
                raise
            time.sleep(0.5 * (attempt + 1))
    return {**counts, "tests_failed": len(result.failed_tests), "seconds": manifest["seconds"]}


@contextmanager
def connect(settings, read_only: bool = True):
    path = Path(settings.warehouse_path)
    if not path.exists():
        raise FileNotFoundError(f"No warehouse at {path} — run a sync or `jobtracker build`.")
    con = duckdb.connect(str(path), read_only=read_only)
    try:
        yield con
    finally:
        con.close()


def manifest(settings) -> dict:
    try:
        with connect(settings) as con:
            row = con.execute("SELECT manifest FROM _build_manifest").fetchone()
        return json.loads(row[0]) if row else {}
    except Exception:
        return {}


__all__ = ["build", "connect", "manifest", "runner"]
