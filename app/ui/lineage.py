"""Graphviz diagrams of the real model: dbt lineage and the star schema.

The lineage is read from ``jobtracker.warehouse.runner.discover()`` — the same parser that
executes the models — so the diagram cannot drift from the SQL. Sources are found from the
rendered ``"raw"."table"`` references, seeds from refs that are not models.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from jobtracker.warehouse import runner

from . import theme

_RAW = re.compile(r'"raw"\."(\w+)"')


@dataclass(frozen=True)
class Graph:
    models: dict[str, str]          # name -> layer
    sources: list[str]
    seeds: list[str]
    edges: list[tuple[str, str]]


def _layer(model: runner.Model) -> str:
    folder = model.path.parent.name
    if folder != "marts":
        return folder
    name = model.name
    return ("fact" if name.startswith("fct_") else "dim" if name.startswith("dim_")
            else "report")


def discover() -> Graph:
    models = runner.discover()
    seeds = sorted({r for m in models.values() for r in m.refs if r not in models})
    sources = sorted({s for m in models.values() for s in _RAW.findall(m.sql)})
    edges = [(ref, m.name) for m in models.values() for ref in sorted(m.refs)]
    edges += [(f"raw.{s}", m.name) for m in models.values() for s in _RAW.findall(m.sql)]
    return Graph({n: _layer(m) for n, m in models.items()}, sources, seeds, edges)


_STYLE = {
    "source": (theme.PAPER, theme.GREY_DARK, theme.INK_2),
    "seed": (theme.PAPER, theme.GREY, theme.INK_2),
    "staging": (theme.SURFACE, theme.GREY_DARK, theme.INK),
    "intermediate": (theme.SURFACE, theme.GREY_DARK, theme.INK),
    "dim": (theme.SURFACE, theme.ACCENT, theme.ACCENT),
    "fact": (theme.ACCENT, theme.ACCENT, "#FFFFFF"),
    "report": (theme.ACCENT_SOFT, theme.ACCENT, theme.INK),
}


def _node(name: str, label: str, kind: str) -> str:
    fill, line, ink = _STYLE[kind]
    return (f'"{name}" [label="{label}", fillcolor="{fill}", color="{line}", '
            f'fontcolor="{ink}"];')


def lineage_dot(graph: Graph) -> str:
    lines = [
        "digraph lineage {",
        'graph [rankdir=LR, bgcolor="transparent", nodesep=0.18, ranksep=0.65, '
        'fontname="Inter", pad=0.1];',
        'node [shape=box, style="rounded,filled", fontname="Inter", fontsize=10.5, '
        'height=0.32, margin="0.14,0.05", penwidth=1.1];',
        f'edge [color="{theme.GREY}", arrowsize=0.5, penwidth=1];',
    ]
    for s in graph.sources:
        lines.append(_node(f"raw.{s}", f"raw.{s}", "source"))
    for s in graph.seeds:
        lines.append(_node(s, f"{s} (seed)", "seed"))
    for name, layer in graph.models.items():
        lines.append(_node(name, name, layer))
    for layer in ("staging", "intermediate"):
        members = [n for n, lyr in graph.models.items() if lyr == layer]
        if members:
            lines.append("{rank=same; " + " ".join(f'"{m}"' for m in members) + "}")
    for a, b in graph.edges:
        lines.append(f'"{a}" -> "{b}";')
    lines.append("}")
    return "\n".join(lines)


FACT_KEYS = ["application_id", "company_key", "platform_key", "status_key", "role_family_key",
             "seniority_key", "industry_key", "job_type_key", "applied_date"]
FACT_MEASURES = ["is_response", "reached_interview", "reached_offer", "days_to_response",
                 "days_since_activity", "max_stage_reached"]


def _record(name: str, rows: list[str], header_fill: str, header_ink: str,
            pos: str = "") -> str:
    def cell(text: str) -> str:
        key = text.endswith("_key") or text in ("application_id", "event_id", "date_day")
        body = f"<B>{text}</B>" if key else text
        ink = theme.INK if key else theme.INK_2
        return (f'<TR><TD ALIGN="LEFT"><FONT POINT-SIZE="9.5" COLOR="{ink}">{body}</FONT>'
                "</TD></TR>")

    cells = "".join(cell(r) for r in rows)
    pin = f'pos="{pos}!", ' if pos else ""
    return (f'"{name}" [{pin}shape=plain, label=<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" '
            f'CELLPADDING="5" COLOR="{theme.GREY}" BGCOLOR="{theme.SURFACE}">'
            f'<TR><TD BGCOLOR="{header_fill}" ALIGN="LEFT"><B><FONT COLOR="{header_ink}">'
            f"{name}</FONT></B></TD></TR>{cells}</TABLE>>];")


def star_dot(columns: pd.DataFrame) -> str:
    """Star schema: the fact pinned in the centre, dimensions in two flanking columns.

    Pinned positions (``pos="x,y!"``) make the layout deterministic. Labels use Helvetica:
    Graphviz sizes boxes from its own font metrics, so a font it can measure never overflows.
    """
    import math

    tables = sorted(set(columns["table_name"])) if not columns.empty else []
    dims = [t for t in tables if t.startswith("dim_")]
    lines = [
        "graph star {",
        'graph [layout=neato, bgcolor="transparent", splines=true, fontname="Helvetica"];',
        'node [fontname="Helvetica", fontsize=10];',
        f'edge [color="{theme.GREY_DARK}", penwidth=1];',
    ]
    fact_rows = [*FACT_KEYS, "…", *FACT_MEASURES, "…"]
    lines.append(_record("fct_applications", fact_rows, theme.ACCENT, "#FFFFFF", "0,0"))
    lines.append(_record("fct_status_events",
                         ["event_id", "application_id", "from_status", "to_status", "occurred_on"],
                         theme.ACCENT_SOFT, theme.INK, "0,-3.9"))
    half = math.ceil(len(dims) / 2)
    for i, d in enumerate(dims):
        side, slot = (-1, i) if i < half else (1, i - half)
        rows = half if side < 0 else len(dims) - half
        x = 3.6 * side
        y = (rows - 1) / 2 * 1.55 - slot * 1.55
        cols = columns.loc[columns["table_name"] == d, "column_name"].tolist()
        shown = cols[:3] + (["…"] if len(cols) > 3 else [])
        lines.append(_record(d, shown, theme.PAPER, theme.INK, f"{x:.2f},{y:.2f}"))
        lines.append(f'"fct_applications" -- "{d}";')
    lines.append('"fct_applications" -- "fct_status_events" [style=dashed];')
    lines.append("}")
    return "\n".join(lines)
