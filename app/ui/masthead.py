"""Masthead: wordmark, filters, search and sync on one line; a quiet meta line beneath.

Filters write to ``st.session_state`` and every page reads them back through
``data.current_filters()``, so all charts on all pages are filtered identically. The
masthead replaces a sidebar: the page stays one editorial column.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from jobtracker import domain

from . import components as ui
from . import data

SETUP_HELP = """
**Connect Gmail (one-off, about five minutes)**

1. In Google Cloud Console create a project and enable the **Gmail API**.
2. *APIs & Services → Credentials → Create credentials → OAuth client ID → Desktop app*.
3. Download the JSON and save it as `credentials.json` in your data folder
   (`{data_dir}`).
4. Press **Sync Gmail** again — a browser tab asks you to approve read-only access once.

Access is `gmail.readonly`: nothing is ever sent, moved or deleted.
"""


def _options(frame: pd.DataFrame, dim: str) -> list[str]:
    if frame.empty:
        return []
    return [str(v) for v in frame.loc[frame["dim"] == dim, "value"].dropna().tolist()]


def _sanitize(key: str, options: list[str]) -> None:
    """Drop stale selections (e.g. a platform that vanished after a re-parse)."""
    chosen = st.session_state.get(key)
    if chosen:
        st.session_state[key] = [c for c in chosen if c in options]


def _filter_panel() -> None:
    opts = data.unfiltered("filter_options")
    bounds = data.unfiltered("date_bounds")
    st.selectbox("Applied", data.PERIODS, key="f_period")
    if st.session_state.get("f_period") == "Custom":
        first = last = date.today()
        if not bounds.empty and pd.notna(bounds.iloc[0]["first"]):
            first = pd.Timestamp(bounds.iloc[0]["first"]).date()
            last = max(pd.Timestamp(bounds.iloc[0]["last"]).date(), date.today())
        st.date_input("Date range", value=(first, last), min_value=first, max_value=last,
                      key="f_dates", format="DD/MM/YYYY")
    statuses = [s for s in domain.STATUSES if s in set(_options(opts, "status"))]
    for label, key, options in (("Channel", "f_platforms", _options(opts, "platform")),
                                ("Status", "f_statuses", statuses),
                                ("Role family", "f_roles", _options(opts, "role_family")),
                                ("Industry", "f_industries", _options(opts, "industry")),
                                ("Job type", "f_job_types", _options(opts, "job_type"))):
        _sanitize(key, options)
        st.multiselect(label, options, key=key, placeholder="All")
    count = data.active_filter_count()
    st.button("Clear all filters", on_click=data.reset_filters, icon=":material/close:",
              type="tertiary", disabled=not count)


def _filter_summary() -> str:
    s = st.session_state
    parts = []
    if (s.get("f_period") or "All time") != "All time":
        parts.append(str(s.get("f_period")))
    for key in ("f_platforms", "f_statuses", "f_roles", "f_industries", "f_job_types"):
        values = s.get(key) or []
        if values:
            parts.append(", ".join(values[:2]) + (f" +{len(values) - 2}" if len(values) > 2 else ""))
    if str(s.get("f_search") or "").strip():
        parts.append(f"“{str(s.get('f_search')).strip()}”")
    return " · ".join(parts)


def run_gmail_sync(full: bool = False) -> None:
    """Live sync with staged progress. Leaves the status panel open with the summary."""
    settings = data.settings()
    with st.status("Full re-parse from Gmail…" if full else "Syncing Gmail…",
                   expanded=True) as status:
        try:
            from jobtracker.gmail import GmailClient

            source = GmailClient.from_settings(settings)
        except FileNotFoundError:
            status.update(label="Gmail is not connected yet", state="error")
            st.markdown(SETUP_HELP.format(data_dir=settings.data_dir))
            return
        except ImportError as exc:
            status.update(label="Gmail libraries are missing", state="error")
            st.code(f"pip install google-api-python-client google-auth-oauthlib\n# {exc}")
            return
        except Exception as exc:
            status.update(label="Could not connect to Gmail", state="error")
            st.error(f"{type(exc).__name__}: {exc}")
            return

        from jobtracker.enrich import enrich_many
        from jobtracker.sync import SyncEngine

        bar = st.progress(0.0, text="Starting…")

        def progress(stage: str, message: str, fraction: float) -> None:
            bar.progress(max(0.0, min(1.0, fraction)), text=message)

        try:
            summary = SyncEngine(settings, source, enricher=enrich_many).run(
                full=full, progress=progress)
        except Exception as exc:
            status.update(label="Sync failed — your stored data is unchanged", state="error")
            st.error(f"{type(exc).__name__}: {exc}")
            return
        for warning in summary.warnings:
            st.warning(warning)
        st.caption(summary.headline())
        status.update(label="Sync complete", state="complete", expanded=bool(summary.warnings))
    data.clear_caches()
    data.flash(summary.headline())


def regenerate_demo() -> None:
    settings = data.settings()
    with st.status("Regenerating the synthetic demo…", expanded=True) as status:
        st.write("Building a fake mailbox and running it through the real sync, parser, "
                 "store and dbt models…")
        try:
            result = data.regenerate_demo(settings.data_dir)
        except Exception as exc:
            status.update(label="Demo rebuild failed", state="error")
            st.error(f"{type(exc).__name__}: {exc}")
            return
        apps = result["warehouse"].get("fct_applications", 0)
        status.update(label=f"Demo rebuilt — {apps} applications from "
                            f"{result['messages']} synthetic emails", state="complete",
                      expanded=False)
    data.flash(f"Demo regenerated: {apps} applications")


def render(rt: data.Runtime, show_filters: bool = True) -> None:
    clicked = False
    with st.container(horizontal=True, vertical_alignment="center", gap="small",
                      key="jt-mast"):
        st.html('<div class="jt-wordmark"><b>Job Search Analytics</b>'
                '<span>Gmail → DuckDB → decisions</span></div>', width="content")
        st.space("stretch")
        if show_filters:
            st.text_input("Search", key="f_search", label_visibility="collapsed",
                          placeholder="Search company or role", icon=":material/search:",
                          width=230)
            count = data.active_filter_count()
            with st.popover(f"Filter · {count}" if count else "Filter",
                            icon=":material/tune:", width="content"):
                _filter_panel()
        if rt.demo:
            clicked = st.button("Regenerate demo", icon=":material/autorenew:",
                                help="Rebuilds the synthetic dataset through the real "
                                     "pipeline. Gmail sync is disabled in the demo.")
        else:
            clicked = st.button("Sync Gmail", icon=":material/sync:", type="primary",
                                help="Incremental: only mail since the last sync is fetched.")

    built = data.manifest().get("built_at")
    today = date.today()
    bits = [f"<span>{today:%A} {today.day} {today:%B %Y}</span>",
            f"<span>Last sync <b>{ui.esc(data.humanize_since(data.last_sync()))}</b></span>",
            f"<span>Warehouse built <b>{ui.esc(data.humanize_since(built))}</b></span>"]
    summary = _filter_summary() if show_filters else ""
    if summary:
        bits.append(f"<span>Filtered to <b>{ui.esc(summary)}</b></span>")
    if rt.demo:
        bits.append('<span class="jt-demo"><i>Demo</i> — synthetic data generated by the real '
                    'pipeline; no real applications are shown.</span>')
    st.html(f'<div class="jt-meta">{"".join(bits)}</div>')
    if clicked:
        regenerate_demo() if rt.demo else run_gmail_sync()
