"""Job Search Analytics: the Streamlit entry point.

    streamlit run app/streamlit_app.py            # your data (JOBTRACKER_DATA_DIR or ./data)
    JOBTRACKER_DEMO=1 streamlit run app/streamlit_app.py   # synthetic demo

Resolves the runtime (real data or demo), loads the one stylesheet, renders the global
masthead (filters + sync), then hands over to the selected page. Pages live in ``app/views``.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="Job Search Analytics", page_icon=":material/insights:",
                   layout="wide", initial_sidebar_state="collapsed")

from app.ui import data, masthead, theme  # noqa: E402

PAGES = [
    st.Page("views/overview.py", title="Overview", icon=":material/space_dashboard:",
            url_path="overview", default=True),
    st.Page("views/pipeline.py", title="Pipeline", icon=":material/view_kanban:",
            url_path="pipeline"),
    st.Page("views/analytics.py", title="Analytics", icon=":material/monitoring:",
            url_path="analytics"),
    st.Page("views/follow_ups.py", title="Follow-ups", icon=":material/forward_to_inbox:",
            url_path="follow-ups"),
    st.Page("views/data_model.py", title="Data & model", icon=":material/account_tree:",
            url_path="data-model"),
    st.Page("views/settings.py", title="Settings", icon=":material/tune:",
            url_path="settings"),
]

theme.load_css()
runtime = data.refresh_runtime()
page = st.navigation(PAGES, position="top")
data.sync_url()  # filters <-> URL, so drill-down links and Back land on the same view
masthead.render(runtime, show_filters=page.url_path != "settings")
data.show_flash()
for warning in runtime.warnings:
    st.warning(warning, icon=":material/warning:")
seen = f"_jt_seen_{page.url_path}"
if st.session_state.get(seen):
    st.html('<div class="jt-calm"></div>')  # motion only on a page's first view
st.session_state[seen] = True
page.run()
