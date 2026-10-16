"""``export_all``: write the whole BI pack (workbook, star-schema CSVs, Power BI, Tableau, README)."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from .. import benchmarks as bm
from . import catalog
from .dax import measures, render_dax_file
from .model import model_spec, powerbi_theme
from .tableau import calculations, render_markdown
from .workbook import SHEETS, build_workbook

WORKBOOK_NAME = "job_search_analytics.xlsx"
FLAT_CSV = "job_applications.csv"


def _write(path: Path, data: bytes | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    if isinstance(data, str):
        tmp.write_text(data, encoding="utf-8", newline="\n")
    else:
        tmp.write_bytes(data)
    os.replace(tmp, path)
    return path


def _csv(frame) -> str:
    # ISO dates, TRUE/FALSE-friendly booleans, blanks for NULL (never 0) - Power Query and
    # Tableau both type these columns automatically.
    return frame.to_csv(index=False, date_format="%Y-%m-%d", lineterminator="\n")


def _readme(as_of: str, follow_up_days: int, counts: dict[str, int]) -> str:
    n_dax = len(measures(follow_up_days))
    n_tab = len(calculations())
    tables = "\n".join(
        f"| `star_schema/{name}.csv` | {meta['kind']} | {meta['grain']} | {counts.get(name, 0)} |"
        for name, meta in catalog.TABLES.items())
    rels = "\n".join(
        f"| `{f}[{fc}]` | `{t}[{tc}]` | many-to-one, single | {'active' if a else '**inactive**'} |"
        for f, fc, t, tc, a, _ in catalog.RELATIONSHIPS)
    sheets = ", ".join(SHEETS)
    bands = ", ".join(f"{b.label} >= {b.lower_pct:g}%" for b in bm.RESPONSE_RATE_BANDS[:-1])
    return f"""# Job search analytics - BI pack

Generated {as_of} by `python -m jobtracker export`. Everything here is built from the same
dbt/DuckDB star schema and the same SQL metric layer (`jobtracker/warehouse/metrics.py`), so the
workbook, the Power BI report and the Tableau workbook cannot disagree with the app.

| File | Use it for |
|---|---|
| `{WORKBOOK_NAME}` | The analyst workbook: live-formula dashboard, report sheets, data dictionary ({sheets}). |
| `{FLAT_CSV}` | Flat one-row-per-application file (`rpt_application_analysis`) - Tableau, or a quick Excel pivot. |
| `star_schema/*.csv` | Every fact and dimension table - Power BI *Get Data > Text/CSV*. |
| `powerbi/measures.dax` | {n_dax} DAX measures as one DAX query - paste into DAX query view, *Update model*. |
| `powerbi/model.json` | Tables, column types, relationships (cardinality, direction, active/inactive), the marked date table, measure metadata. |
| `powerbi/theme.json` | Report theme matching the app (navy canvas, blue primary, colour-blind-safe series). |
| `tableau/calculations.md` | {n_tab} Tableau calculated fields over `{FLAT_CSV}`. |

Build guides: `docs/bi/power-bi-guide.md`, `docs/bi/tableau-guide.md`, `docs/bi/excel-guide.md`.

## Star schema

| File | Kind | Grain | Rows |
|---|---|---|---|
{tables}

## Relationships

| From | To | Cardinality / filter | State |
|---|---|---|---|
{rels}

Mark `dim_date` as the date table (column `date_day`). `first_response_date` and
`last_activity_date` are role-playing dates: their relationships are inactive and the measures
switch them on with `USERELATIONSHIP`.

## Honesty rules the pack follows

- `days_to_response` is blank when the reply date is unknown - never zero-filled. Every median /
  average skips blanks (`MEDIANX` over non-blank rows in DAX, `MEDIAN` in Tableau and Excel).
- Rates carry Wilson 95% intervals; per-platform comparisons are indicative at this sample size
  (`Sample Confidence`: Adequate n >= {bm.SAMPLE_ADEQUATE}, Indicative n >= {bm.SAMPLE_INDICATIVE}).
- Benchmarks come from `jobtracker/benchmarks.py` only (response bands: {bands}; direct-application
  interview rate {bm.DIRECT_INTERVIEW_RATE_PCT}%). Change them there and re-export.
- Scope: job-application pipeline data. Insurance portfolio measures do not apply and are absent.
- Day counts are as of the warehouse build ({as_of}); follow-up threshold = {follow_up_days} days.
"""


def export_all(settings, out_dir: Path | None = None) -> dict[str, Path]:
    """Write the BI pack to ``out_dir`` (default ``settings.exports_dir``); returns {name: path}."""
    from ..warehouse import connect, manifest

    out = Path(out_dir) if out_dir else Path(settings.exports_dir)
    out.mkdir(parents=True, exist_ok=True)
    follow_up_days = int(getattr(settings, "follow_up_after_days", 7))
    built = manifest(settings).get("built_at") or datetime.now().isoformat(timespec="seconds")
    as_of = str(built).replace("T", " ")
    written: dict[str, Path] = {}

    written["workbook"] = _write(out / WORKBOOK_NAME, build_workbook(settings))

    counts: dict[str, int] = {}
    with connect(settings) as con:
        flat = con.execute(
            f"SELECT * FROM {catalog.FLAT_VIEW} ORDER BY applied_date, application_id").df()
        written["job_applications.csv"] = _write(out / FLAT_CSV, _csv(flat))
        for name, meta in catalog.TABLES.items():
            frame = con.execute(f'SELECT * FROM "{name}" ORDER BY "{meta["key"]}"').df()
            counts[name] = len(frame)
            written[f"star_schema/{name}.csv"] = _write(out / "star_schema" / f"{name}.csv",
                                                        _csv(frame))

    written["powerbi/measures.dax"] = _write(out / "powerbi" / "measures.dax",
                                             render_dax_file(follow_up_days, as_of))
    written["powerbi/model.json"] = _write(out / "powerbi" / "model.json",
                                           json.dumps(model_spec(follow_up_days), indent=2))
    written["powerbi/theme.json"] = _write(out / "powerbi" / "theme.json",
                                           json.dumps(powerbi_theme(), indent=2))
    written["tableau/calculations.md"] = _write(out / "tableau" / "calculations.md",
                                                render_markdown(follow_up_days, as_of))
    written["tableau/calculations.json"] = _write(
        out / "tableau" / "calculations.json",
        json.dumps([{"name": c.name, "group": c.group, "type": c.kind, "formula": c.formula,
                     "description": c.description, "reproducesSql": c.sql}
                    for c in calculations()], indent=2))
    written["README.md"] = _write(out / "README.md", _readme(as_of, follow_up_days, counts))
    return written
