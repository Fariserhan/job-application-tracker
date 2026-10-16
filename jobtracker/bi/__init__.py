"""The BI pack: Excel workbook, Power BI (DAX + model + theme) and Tableau, from one metric layer.

Every number is defined once in SQL (``jobtracker/warehouse/metrics.py``) over the dbt star
schema; this package reproduces those definitions in Excel formulas, DAX and Tableau
calculations and generates every threshold from ``jobtracker/benchmarks.py``.
``tests/test_bi.py`` recalculates the workbook in LibreOffice and checks the formulas
against the SQL, and checks every DAX / Tableau field reference against the warehouse.

Public API (used by the app and ``python -m jobtracker export``):

* ``build_workbook(settings) -> bytes``
* ``export_all(settings, out_dir=None) -> dict[str, Path]``
* ``dax_measures() -> dict[str, str]``
* ``tableau_calculations() -> dict[str, str]``
* ``model_spec() -> dict``
"""
from __future__ import annotations

from .dax import dax_measures
from .export import export_all
from .model import model_spec, powerbi_theme
from .tableau import tableau_calculations
from .workbook import build_workbook

__all__ = ["build_workbook", "dax_measures", "export_all", "model_spec", "powerbi_theme",
           "tableau_calculations"]
