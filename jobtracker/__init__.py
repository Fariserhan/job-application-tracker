"""jobtracker: Gmail-to-warehouse job application analytics.

Pipeline: Gmail -> deterministic parser (optional Claude fallback for ambiguous mail)
-> SQLite operational store (append-only status events) -> DuckDB star schema (dbt models)
-> semantic SQL metric layer -> Streamlit app + Excel / Power BI / Tableau pack.
"""

__version__ = "2.0.0"
