"""Run the dashboard against demo.db (synthetic data) for README screenshots.

Your real applications.db is never opened: storage paths are redirected before
the dashboard module loads.

Run: venv\\Scripts\\python.exe -m streamlit run demo_wrapper.py
"""
import pathlib

import storage
import warehouse

_here = pathlib.Path(__file__).parent
_demo = str(_here / "demo.db")
storage.DB_PATH = _demo
storage.CSV_PATH = str(_here / "demo_job_tracker.csv")
storage.STATE_PATH = str(_here / "demo_sync_state.json")
warehouse.DB_PATH = _demo

import runpy

runpy.run_path(str(_here / "dashboard.py"), run_name="__main__")
