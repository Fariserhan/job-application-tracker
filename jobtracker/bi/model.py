"""``model_spec()`` (-> ``powerbi/model.json``) and the Power BI report theme (-> ``theme.json``)."""
from __future__ import annotations

from .. import benchmarks as bm
from . import catalog, style
from .dax import HOME_TABLE, measures

_PBI_TYPES = {"text": "string", "date": "dateTime", "integer": "int64", "decimal": "double",
              "boolean": "boolean"}


def model_spec(follow_up_days: int = 7) -> dict:
    """Tables, columns, relationships, the marked date table and the measures, as data."""
    tables = []
    for name, meta in catalog.TABLES.items():
        hidden = set(catalog.HIDDEN_COLUMNS.get(name, []))
        columns = []
        for col, kind, text in meta["columns"]:
            entry = {"name": col, "dataType": _PBI_TYPES[kind], "description": text,
                     "isHidden": col in hidden}
            if kind in catalog.FORMAT_STRINGS:
                entry["formatString"] = catalog.FORMAT_STRINGS[kind]
            # Only true additive counts may implicitly SUM; everything else goes through the
            # measures (e.g. days_to_response must be MEDIAN over known values, never SUM).
            entry["summarizeBy"] = ("sum" if col in ("application_count", "event_count", "applications")
                                    else "none")
            sort_by = catalog.SORT_BY.get((name, col))
            if sort_by:
                entry["sortByColumn"] = sort_by
            columns.append(entry)
        table = {"name": name, "kind": meta["kind"], "grain": meta["grain"],
                 "primaryKey": meta["key"], "source": f"star_schema/{name}.csv",
                 "columns": columns}
        if meta["kind"] == "date":
            table["isMarkedDateTable"] = True
            table["dateColumn"] = "date_day"
        tables.append(table)

    relationships = []
    for frm, fcol, to, tcol, active, note in catalog.RELATIONSHIPS:
        relationships.append({
            "name": f"{frm}[{fcol}] -> {to}[{tcol}]",
            "fromTable": frm, "fromColumn": fcol, "toTable": to, "toColumn": tcol,
            "cardinality": "manyToOne",
            "crossFilteringBehavior": "oneDirection",
            "isActive": active,
            "note": note,
        })

    return {
        "name": "Job search analytics",
        "description": ("Star schema built by dbt in DuckDB (dbt/models/marts). Import the CSVs in "
                        "star_schema/ and recreate these relationships; every measure is in "
                        "powerbi/measures.dax."),
        "culture": "en-MY",
        "tables": tables,
        "relationships": relationships,
        "dateTable": {"table": "dim_date", "column": "date_day", "markAsDateTable": True,
                      "activeRelationship": "fct_applications[applied_date]",
                      "rolePlaying": ["fct_applications[first_response_date]",
                                      "fct_applications[last_activity_date]"],
                      "note": ("Role-playing dates use inactive relationships; measures activate "
                               "them with USERELATIONSHIP.")},
        "measures": [
            {"name": x.name, "table": HOME_TABLE, "displayFolder": x.folder,
             "formatString": x.format, "description": x.description, "expression": x.expression,
             "reproducesSql": x.sql}
            for x in measures(follow_up_days)
        ],
        "parameters": [{"name": "Follow-up Days", "type": "what-if", "min": 1, "max": 60,
                        "increment": 1, "default": int(follow_up_days),
                        "usedBy": "Follow-up Threshold (days)"}],
        "benchmarks": {
            "response_rate_bands_pct": {b.label: b.lower_pct for b in bm.RESPONSE_RATE_BANDS},
            "direct_interview_rate_pct": bm.DIRECT_INTERVIEW_RATE_PCT,
            "job_board_interview_rate_pct": bm.JOB_BOARD_INTERVIEW_RATE_PCT,
            "funnel_per_100": {k: list(v) for k, v in bm.FUNNEL_PER_100.items()},
            "sample_adequate": bm.SAMPLE_ADEQUATE,
            "sample_indicative": bm.SAMPLE_INDICATIVE,
        },
    }


def powerbi_theme() -> dict:
    """Power BI report theme matching the app's light editorial look.

    Paper canvas, white visual surfaces, ink text, hairline borders; dataColors start with the
    accent (the "you" series) followed by the pipeline-status palette. Georgia stands in for
    the app's Newsreader headings and Segoe UI for Inter body text.
    """
    h = style.hex_rgb
    ink, secondary, muted, rule = h(style.INK), h(style.SECONDARY), h(style.MUTED), h(style.RULE)
    heading, body = "Georgia", "Segoe UI"

    def solid(colour: str) -> dict:
        return {"solid": {"color": colour}}

    return {
        "name": "Job Search Analytics - Editorial",
        "dataColors": [h(c) for c in style.CATEGORICAL],
        "background": h(style.PAPER),
        "secondaryBackground": h(style.SURFACE),
        "foreground": ink,
        "foregroundNeutralSecondary": secondary,
        "foregroundNeutralTertiary": muted,
        "backgroundLight": h(style.ACCENT_SOFT),
        "backgroundNeutral": rule,
        "tableAccent": h(style.ACCENT),
        "good": h(style.POSITIVE),
        "neutral": h(style.CAUTION),
        "bad": h(style.NEGATIVE),
        "maximum": h(style.ACCENT),
        "center": h(style.ACCENT_SOFT),
        "minimum": h(style.SURFACE),
        "null": h(style.CONTEXT),
        "hyperlink": h(style.ACCENT),
        "visitedHyperlink": h(style.STATUS["Assessment / OA"]),
        "textClasses": {
            "callout": {"fontSize": 28, "fontFace": heading, "color": ink},
            "title": {"fontSize": 13, "fontFace": heading, "color": ink},
            "header": {"fontSize": 11, "fontFace": body, "color": ink},
            "label": {"fontSize": 10, "fontFace": body, "color": secondary},
        },
        "visualStyles": {
            "*": {
                "*": {
                    "background": [{"show": True, "color": solid(h(style.SURFACE)),
                                    "transparency": 0}],
                    "border": [{"show": True, "color": solid(rule), "radius": 0}],
                    "dropShadow": [{"show": False}],
                    "title": [{"show": True, "fontColor": solid(ink), "fontSize": 13,
                               "fontFamily": heading, "alignment": "left"}],
                    "categoryAxis": [{"labelColor": solid(secondary), "titleColor": solid(secondary),
                                      "showAxisTitle": False, "gridlineShow": False}],
                    "valueAxis": [{"labelColor": solid(secondary), "titleColor": solid(secondary),
                                   "gridlineColor": solid(rule), "gridlineStyle": "solid"}],
                    "legend": [{"labelColor": solid(secondary), "position": "TopLeft"}],
                    "labels": [{"color": solid(ink)}],
                }
            },
            "page": {
                "*": {
                    "background": [{"color": solid(h(style.PAPER)), "transparency": 0}],
                    "outspace": [{"color": solid(h(style.PAPER))}],
                }
            },
            "card": {
                "*": {
                    "labels": [{"color": solid(h(style.ACCENT)), "fontSize": 28,
                                "fontFamily": heading}],
                    "categoryLabels": [{"color": solid(secondary), "fontSize": 10}],
                }
            },
            "slicer": {
                "*": {
                    "items": [{"fontColor": solid(ink), "background": solid(h(style.SURFACE))}],
                    "header": [{"fontColor": solid(secondary)}],
                }
            },
            "tableEx": {
                "*": {
                    "grid": [{"gridHorizontal": True, "gridHorizontalColor": solid(rule),
                              "gridVertical": False, "outlineColor": solid(ink)}],
                    "columnHeaders": [{"fontColor": solid(ink), "backColor": solid(h(style.PAPER)),
                                       "fontFamily": body}],
                    "values": [{"fontColorPrimary": solid(ink),
                                "backColorPrimary": solid(h(style.SURFACE)),
                                "fontColorSecondary": solid(ink),
                                "backColorSecondary": solid(h(style.PAPER))}],
                }
            },
        },
    }
