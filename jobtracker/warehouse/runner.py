"""A dependency-free executor for this project's dbt models.

dbt is the development tool (tests, docs and lineage, run in CI). The app only needs the
models *executed*, and shipping dbt-core to every laptop and to the hosted demo would add
~100 MB and seconds of start-up for no benefit. This runner executes the very same
``dbt/models/**/*.sql`` files against DuckDB:

* resolves ``{{ ref('x') }}`` / ``{{ source('raw', 'x') }}`` / ``{{ config(materialized=...) }}``
* loads ``dbt/seeds/*.csv``
* orders models by their refs (topological sort) and materialises them as tables / views
* runs the singular data tests in ``dbt/tests`` and reports failing row counts

Anything beyond that Jinja subset is rejected loudly (``UnsupportedJinja``), and CI runs the
real ``dbt build`` over the same files, so the two can never silently diverge.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from graphlib import TopologicalSorter
from pathlib import Path

from ..config import PROJECT_ROOT

DBT_DIR = PROJECT_ROOT / "dbt"

_REF = re.compile(r"\{\{\s*ref\(\s*['\"](\w+)['\"]\s*\)\s*\}\}")
_SOURCE = re.compile(r"\{\{\s*source\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]\s*\)\s*\}\}")
_CONFIG = re.compile(r"\{\{\s*config\(([^)]*)\)\s*\}\}")
_MATERIALIZED = re.compile(r"materialized\s*=\s*['\"](\w+)['\"]")
_ANY_JINJA = re.compile(r"\{\{|\{%")


class UnsupportedJinja(ValueError):
    pass


@dataclass
class Model:
    name: str
    path: Path
    sql: str
    refs: set[str]
    materialized: str


@dataclass
class RunResult:
    models: list[str] = field(default_factory=list)
    seeds: list[str] = field(default_factory=list)
    tests: dict[str, int] = field(default_factory=dict)  # test name -> failing rows

    @property
    def failed_tests(self) -> dict[str, int]:
        return {k: v for k, v in self.tests.items() if v}


def _default_materialization(path: Path) -> str:
    parts = path.parts
    return "view" if ("staging" in parts or "intermediate" in parts) else "table"


def _render(sql: str, origin: str) -> tuple[str, set[str], str | None]:
    refs = set(_REF.findall(sql))
    config = _CONFIG.search(sql)
    materialized = None
    if config:
        found = _MATERIALIZED.search(config.group(1))
        materialized = found.group(1) if found else None
    rendered = _CONFIG.sub("", sql)
    rendered = _REF.sub(lambda m: f'"{m.group(1)}"', rendered)
    rendered = _SOURCE.sub(lambda m: f'"{m.group(1)}"."{m.group(2)}"', rendered)
    if _ANY_JINJA.search(rendered):
        raise UnsupportedJinja(f"{origin}: only ref/source/config are supported by the "
                               "built-in runner. Run real dbt for anything else.")
    return rendered.strip().rstrip(";"), refs, materialized


def discover(dbt_dir: Path = DBT_DIR) -> dict[str, Model]:
    models: dict[str, Model] = {}
    for path in sorted((dbt_dir / "models").rglob("*.sql")):
        sql, refs, materialized = _render(path.read_text(encoding="utf-8"), str(path))
        models[path.stem] = Model(path.stem, path, sql, refs,
                                  materialized or _default_materialization(path))
    return models


def seeds(dbt_dir: Path = DBT_DIR) -> list[Path]:
    return sorted((dbt_dir / "seeds").glob("*.csv"))


def run(con, dbt_dir: Path = DBT_DIR, with_tests: bool = True) -> RunResult:
    """Execute seeds, models (dependency order) and singular tests on a DuckDB connection."""
    result = RunResult()
    for seed in seeds(dbt_dir):
        con.execute(f'CREATE OR REPLACE TABLE "{seed.stem}" AS '
                    f"SELECT * FROM read_csv_auto(?, header = true)", [str(seed)])
        result.seeds.append(seed.stem)
    seed_names = set(result.seeds)

    models = discover(dbt_dir)
    graph = {name: {r for r in m.refs if r not in seed_names} for name, m in models.items()}
    missing = {r for deps in graph.values() for r in deps if r not in models}
    if missing:
        raise ValueError(f"unknown ref(s): {sorted(missing)}")
    for name in TopologicalSorter(graph).static_order():
        model = models[name]
        kind = "VIEW" if model.materialized == "view" else "TABLE"
        con.execute(f'DROP VIEW IF EXISTS "{name}"')
        con.execute(f'DROP TABLE IF EXISTS "{name}"')
        con.execute(f'CREATE {kind} "{name}" AS {model.sql}')
        result.models.append(name)

    if with_tests:
        for path in sorted((dbt_dir / "tests").glob("*.sql")):
            sql, _, _ = _render(path.read_text(encoding="utf-8"), str(path))
            failing = con.execute(f"SELECT COUNT(*) FROM ({sql}) AS t").fetchone()[0]
            result.tests[path.stem] = int(failing)
    return result
