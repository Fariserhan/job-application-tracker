"""Cached, read-only access to the warehouse — and the one place the app writes from.

Why it is shaped this way:

* **Every number comes from ``jobtracker.warehouse.metrics``.** Pages ask for a metric by
  name; nothing here re-derives an analytic in pandas.
* **Caching is keyed by the warehouse file's mtime**, the metric name and the filters as a
  hashable tuple. A sync replaces the DuckDB file atomically, so the mtime changes and every
  cached frame is invalidated without anyone having to remember to clear it.
* **One short-lived read-only connection per query.** DuckDB locks the file; holding a
  connection open across reruns would block the sync's atomic swap on Windows.
* **Demo mode** builds the synthetic dataset through the real pipeline into a scratch
  directory (never the user's data dir), so the public demo runs the same code paths.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

from jobtracker import warehouse
from jobtracker.config import PROJECT_ROOT, Settings, load_settings
from jobtracker.store import Repository
from jobtracker.warehouse.metrics import Filters, Metrics

DEMO_ENV = "JOBTRACKER_DEMO"
DEMO_DIR_ENV = "JOBTRACKER_DEMO_DIR"
DEMO_HISTORY_ID = "demo"  # what jobtracker.demo's mailbox reports as its Gmail historyId

PERIODS = ["All time", "Last 30 days", "Last 90 days", "Year to date", "Custom"]
FILTER_KEYS = ("f_period", "f_dates", "f_platforms", "f_statuses", "f_roles",
               "f_industries", "f_job_types", "f_search")


# ============================================================================ runtime
@dataclass(frozen=True)
class Runtime:
    settings: Settings
    demo: bool = False
    demo_reason: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def can_sync_gmail(self) -> bool:
        return not self.demo


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def holds_demo_data(settings: Settings) -> bool:
    """A data dir created by ``jobtracker demo`` — safe to regenerate, never real mail."""
    try:
        state = json.loads(Path(settings.state_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(state, dict) and state.get("history_id") == DEMO_HISTORY_ID


def demo_dir() -> Path:
    """Where the app builds its own demo: env override, ``<project>/demo_data``, else temp."""
    override = os.environ.get(DEMO_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    preferred = PROJECT_ROOT / "demo_data"
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return preferred
    except OSError:
        return Path(tempfile.gettempdir()) / "jobtracker-demo"


def _safe_to_rebuild(path: Path) -> bool:
    if not path.exists():
        return True
    settings = Settings(data_dir=path)
    if not settings.db_path.exists():
        return True
    return holds_demo_data(settings)


def regenerate_demo(path: Path) -> dict:
    """Rebuild the synthetic dataset. Refuses to touch a directory holding real data."""
    from jobtracker.demo import build_demo

    if not _safe_to_rebuild(path):
        raise RuntimeError(f"{path} holds real application data — refusing to overwrite it.")
    result = build_demo(path)
    clear_caches()
    return result


@st.cache_resource(show_spinner="Generating the synthetic demo through the real pipeline…")
def _ensure_demo(path: str, day: str) -> str:
    """Build the demo once per server per day (the demo's dates are relative to today)."""
    target = Path(path)
    settings = Settings(data_dir=target)
    fresh = (settings.warehouse_path.exists() and holds_demo_data(settings)
             and str(warehouse.manifest(settings).get("built_at", ""))[:10] == day)
    if not fresh:
        regenerate_demo(target)
    return path


def _resolve() -> Runtime:
    base = load_settings()
    warnings: list[str] = []
    if _truthy(os.environ.get(DEMO_ENV)):
        if holds_demo_data(base):
            settings, reason = base, "JOBTRACKER_DEMO is set"
        else:
            path = _ensure_demo(str(demo_dir()), date.today().isoformat())
            settings, reason = load_settings(path), "JOBTRACKER_DEMO is set"
        return Runtime(settings, True, reason)
    if not base.db_path.exists():
        path = _ensure_demo(str(demo_dir()), date.today().isoformat())
        return Runtime(load_settings(path), True,
                       f"no applications.db in {base.data_dir} yet")
    if holds_demo_data(base):
        return Runtime(base, True, "this data directory holds the synthetic demo")
    if not base.warehouse_path.exists():
        try:
            warehouse.build(base)
        except Exception as exc:  # surfaced in the UI, never a crash
            warnings.append(f"Warehouse build failed: {type(exc).__name__}: {exc}")
    return Runtime(base, False, "", tuple(warnings))


def runtime() -> Runtime:
    """The runtime for this script run (resolved once per run by the entry point)."""
    cached = st.session_state.get("_jt_runtime")
    if isinstance(cached, Runtime):
        return cached
    rt = _resolve()
    st.session_state["_jt_runtime"] = rt
    return rt


def refresh_runtime() -> Runtime:
    st.session_state.pop("_jt_runtime", None)
    return runtime()


def settings() -> Settings:
    return runtime().settings


# ============================================================================ filters
def _period_bounds(period: str, custom) -> tuple[date | None, date | None]:
    today = date.today()
    if period == "Last 30 days":
        return today - timedelta(days=29), today
    if period == "Last 90 days":
        return today - timedelta(days=89), today
    if period == "Year to date":
        return date(today.year, 1, 1), today
    if period == "Custom" and isinstance(custom, (list, tuple)) and len(custom) == 2:
        return custom[0], custom[1]
    return None, None


def current_filters() -> Filters:
    state = st.session_state
    date_from, date_to = _period_bounds(state.get("f_period") or "All time", state.get("f_dates"))
    return Filters(
        date_from=date_from, date_to=date_to,
        platforms=list(state.get("f_platforms") or []),
        statuses=list(state.get("f_statuses") or []),
        role_families=list(state.get("f_roles") or []),
        industries=list(state.get("f_industries") or []),
        job_types=list(state.get("f_job_types") or []),
        search=str(state.get("f_search") or ""),
    )


def active_filter_count() -> int:
    state = st.session_state
    count = 0
    if (state.get("f_period") or "All time") != "All time":
        count += 1
    for key in ("f_platforms", "f_statuses", "f_roles", "f_industries", "f_job_types"):
        count += bool(state.get(key))
    count += bool(str(state.get("f_search") or "").strip())
    return count


def reset_filters() -> None:
    for key in FILTER_KEYS:
        st.session_state.pop(key, None)


def _filters_key(f: Filters) -> tuple:
    return (f.date_from, f.date_to, tuple(f.platforms), tuple(f.statuses),
            tuple(f.role_families), tuple(f.industries), tuple(f.job_types), f.search.strip())


def _filters_from_key(key: tuple) -> Filters:
    d_from, d_to, platforms, statuses, roles, industries, job_types, search = key
    return Filters(d_from, d_to, list(platforms), list(statuses), list(roles), list(industries),
                   list(job_types), search)


# ============================================================================ queries
def warehouse_stamp(s: Settings | None = None) -> int:
    path = Path((s or settings()).warehouse_path)
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


@st.cache_data(show_spinner=False, max_entries=2048, ttl=6 * 3600)
def _run_metric(data_dir: str, stamp: int, name: str, segment: str, fkey: tuple,
                follow_up_days: int, ghost_days: int) -> pd.DataFrame:
    rules = Settings(data_dir=Path(data_dir), follow_up_after_days=follow_up_days,
                     ghost_after_days=ghost_days)
    con = duckdb.connect(str(rules.warehouse_path), read_only=True)
    try:
        return Metrics(con, rules, _filters_from_key(fkey)).query(name, segment=segment)
    finally:
        con.close()


def metric(name: str, segment: str = "platform", filters: Filters | None = None) -> pd.DataFrame:
    """A named metric from the SQL metric layer, filtered by the sidebar (cached)."""
    s = settings()
    stamp = warehouse_stamp(s)
    if not stamp:
        return pd.DataFrame()
    f = filters if filters is not None else current_filters()
    return _run_metric(str(s.data_dir), stamp, name, segment, _filters_key(f),
                       int(s.follow_up_after_days), int(s.ghost_after_days)).copy()


def unfiltered(name: str) -> pd.DataFrame:
    return metric(name, filters=Filters())


def kpis(filters: Filters | None = None) -> dict:
    frame = metric("kpis", filters=filters)
    return {} if frame.empty else frame.iloc[0].to_dict()


@st.cache_data(show_spinner=False, max_entries=16)
def _manifest(data_dir: str, stamp: int) -> dict:
    return warehouse.manifest(Settings(data_dir=Path(data_dir)))


def manifest() -> dict:
    s = settings()
    return _manifest(str(s.data_dir), warehouse_stamp(s))


@st.cache_data(show_spinner=False, max_entries=16)
def _columns(data_dir: str, stamp: int) -> pd.DataFrame:
    """Column catalogue of the marts (metadata, for the star-schema diagram)."""
    con = duckdb.connect(str(Settings(data_dir=Path(data_dir)).warehouse_path), read_only=True)
    try:
        return con.execute(
            "SELECT table_name, column_name, data_type, ordinal_position "
            "FROM information_schema.columns "
            "WHERE table_name LIKE 'dim_%' OR table_name LIKE 'fct_%' "
            "ORDER BY table_name, ordinal_position").df()
    finally:
        con.close()


def columns() -> pd.DataFrame:
    s = settings()
    stamp = warehouse_stamp(s)
    return _columns(str(s.data_dir), stamp) if stamp else pd.DataFrame()


@st.cache_data(show_spinner="Building the Excel workbook…", max_entries=4)
def _workbook(data_dir: str, stamp: int) -> bytes:
    from jobtracker import bi

    return bi.build_workbook(load_settings(data_dir))


def workbook() -> bytes:
    """The BI pack's Excel workbook for the current warehouse (cached per build)."""
    s = settings()
    return _workbook(str(s.data_dir), warehouse_stamp(s))


def has_data() -> bool:
    k = kpis()
    return bool(k) and int(k.get("applications") or 0) > 0


# ============================================================================ writes
def repo() -> Repository:
    r = Repository(settings())
    r.init()
    return r


def clear_caches() -> None:
    _run_metric.clear()
    _manifest.clear()
    _columns.clear()
    _workbook.clear()


def rebuild(message: str | None = None) -> dict:
    """After any write: rebuild the warehouse, drop cached frames, queue a toast."""
    counts = warehouse.build(settings())
    clear_caches()
    if message:
        flash(message)
    return counts


def flash(message: str, icon: str = ":material/check_circle:") -> None:
    st.session_state["_jt_flash"] = (message, icon)


def show_flash() -> None:
    queued = st.session_state.pop("_jt_flash", None)
    if queued:
        st.toast(queued[0], icon=queued[1])


def last_sync() -> str | None:
    try:
        return Repository(settings()).load_state().get("last_sync_at")
    except Exception:
        return None


def humanize_since(stamp: str | None) -> str:
    if not stamp:
        return "never"
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return stamp
    seconds = max(0, int((datetime.now() - then).total_seconds()))
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} d ago"
