"""Masthead: wordmark, filters, search and sync on one line; a quiet meta line beneath.

Filters write to ``st.session_state`` and every page reads them back through
``data.current_filters()``, so all charts on all pages are filtered identically. The
masthead replaces a sidebar: the page stays one editorial column.
"""
from __future__ import annotations

from datetime import date, datetime

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
4. Press **Sync Gmail** again. A browser tab asks you to approve read-only access once.

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


def _auto_sync_due(settings) -> bool:
    """Sync by itself once per visit when the last sync is stale, but only once Gmail has
    been connected (a saved sign-in exists), so it never pops a Google window unasked."""
    hours = int(getattr(settings, "auto_sync_hours", 0) or 0)
    if hours <= 0 or st.session_state.get("_jt_autosynced"):
        return False
    if not settings.secret_path("token.json").exists():
        return False
    last = data.last_sync()
    if not last:
        return True
    try:
        age = (datetime.now() - datetime.fromisoformat(last)).total_seconds()
    except ValueError:
        return True
    return age >= hours * 3600


def run_gmail_sync(full: bool = False, auto: bool = False) -> None:
    """Live sync with staged progress, then a fresh run so every page (and the "since your
    last sync" panel) shows the new data. ``auto`` syncs never open a Google sign-in."""
    settings = data.runtime().sync_settings
    label = ("Checking Gmail for new job emails…" if auto else
             "Re-reading your whole mailbox…" if full else "Syncing Gmail…")
    with st.status(label, expanded=not auto) as status:
        try:
            from jobtracker.gmail import GmailClient
            from jobtracker.gmail.auth import SignInRequired

            source = GmailClient.from_settings(settings, interactive=not auto)
        except FileNotFoundError:
            if auto:
                status.update(label="Gmail is not connected yet", state="error", expanded=False)
                return
            status.update(label="Gmail is not connected yet", state="error")
            st.markdown(SETUP_HELP.format(data_dir=settings.data_dir))
            return
        except ImportError as exc:
            status.update(label="Gmail libraries are missing", state="error")
            st.code(f"pip install google-api-python-client google-auth-oauthlib\n# {exc}")
            return
        except SignInRequired:
            status.update(label="Google needs you to sign in again. Click Sync Gmail.",
                          state="error", expanded=False)
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
            status.update(label="Sync failed. Your stored data is unchanged", state="error")
            st.error(f"{type(exc).__name__}: {exc}")
            return
        for warning in summary.warnings:
            st.warning(warning)
        st.caption(summary.headline())
        status.update(label="Sync complete", state="complete", expanded=bool(summary.warnings))
    data.clear_caches()
    found = (f"{summary.inserted} new, {summary.updated} updated" if summary.inserted
             or summary.updated else "nothing new")
    data.flash(f"Gmail synced: {found}", ":material/mark_email_read:")
    if not summary.warnings:
        st.rerun()


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
        status.update(label=f"Demo rebuilt: {apps} applications from "
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
        regenerate = False
        if rt.can_sync_gmail:
            clicked = st.button("Sync Gmail", icon=":material/sync:", type="primary",
                                key="jt-sync",
                                help="Reads your new job emails and updates everything.")
        else:
            regenerate = st.button("Regenerate demo", icon=":material/autorenew:",
                                   help="Rebuilds the made-up demo data. This public demo "
                                        "cannot read anyone's Gmail.")

    today = date.today()
    bits = [f"<span>{today:%A} {today.day} {today:%B %Y}</span>",
            f"<span>Last sync <b>{ui.esc(data.humanize_since(data.last_sync(rt.sync_settings)))}"
            "</b></span>"]
    background = data.background_sync_status()
    if background:
        text, tone = background
        bits.append(f'<span class="{ui.TONE_CLASS.get(tone, "")}">{ui.esc(text)}</span>')
    summary = _filter_summary() if show_filters else ""
    if summary:
        bits.append(f"<span>Filtered to <b>{ui.esc(summary)}</b></span>")
    if rt.demo and rt.real is not None:
        bits.append('<span class="jt-demo"><i>Demo</i>: you are looking at made-up data until '
                    'your first sync. Click <b>Sync Gmail</b> to read your own job emails.</span>')
    elif rt.demo:
        bits.append('<span class="jt-demo"><i>Demo</i>: made-up data run through the real '
                    'pipeline; no real applications are shown.</span>')
    st.html(f'<div class="jt-meta">{"".join(bits)}</div>')
    if regenerate:
        regenerate_demo()
    elif clicked:
        run_gmail_sync()
    elif not rt.demo and _auto_sync_due(rt.sync_settings):
        st.session_state["_jt_autosynced"] = True
        run_gmail_sync(auto=True)
