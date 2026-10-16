"""Cached, read-only access to the warehouse, and the one place the app writes from.

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

import dataclasses
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import duckdb
import pandas as pd
import streamlit as st

from jobtracker import warehouse
from jobtracker.config import PROJECT_ROOT, Settings, load_settings
from jobtracker.store import Repository
from jobtracker.warehouse.metrics import REACHED, Filters, Metrics

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
    # Your own data folder, when the demo is only shown because nothing has been synced
    # into it yet. Sync Gmail writes there, so the button is never taken away from you.
    real: Settings | None = None

    @property
    def can_sync_gmail(self) -> bool:
        return not self.demo or self.real is not None

    @property
    def sync_settings(self) -> Settings:
        """Where a Gmail sync writes: your data folder, never the demo's."""
        return self.real or self.settings


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def holds_demo_data(settings: Settings) -> bool:
    """A data dir created by ``jobtracker demo``: safe to regenerate, never real mail."""
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
        raise RuntimeError(f"{path} holds real application data. Refusing to overwrite it.")
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
                       f"no applications.db in {base.data_dir} yet", real=base)
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
    """Hashable cache key covering every Filters field (lists become tuples)."""
    return tuple((fld.name, tuple(v) if isinstance(v := getattr(f, fld.name), list) else v)
                 for fld in dataclasses.fields(Filters))


def _filters_from_key(key: tuple) -> Filters:
    return Filters(**{name: list(v) if isinstance(v, tuple) else v for name, v in key})


# ============================================================================ URL state
# Global filters live in session state (the widgets) and are mirrored into the URL, so a
# drill-down link, a bookmark or the browser's Back button lands on exactly the same view.
# A URL carrying the marker ``f=1`` holds the complete filter state; anything else (e.g. a
# click on the top navigation, which clears the query string) leaves the session as it is.
PERIOD_SLUGS = {"All time": "all", "Last 30 days": "30d", "Last 90 days": "90d",
                "Year to date": "ytd", "Custom": "custom"}
LIST_PARAMS = {"platform": "f_platforms", "status": "f_statuses", "role": "f_roles",
               "industry": "f_industries", "jobtype": "f_job_types"}
GLOBAL_PARAMS = ("f", "period", "start", "end", "q", *LIST_PARAMS)


def global_params() -> dict[str, str | list[str]]:
    """The current global filters as URL parameters (empty values omitted)."""
    state = st.session_state
    out: dict[str, str | list[str]] = {}
    period = state.get("f_period") or "All time"
    if period != "All time":
        out["period"] = PERIOD_SLUGS.get(period, "all")
        dates = state.get("f_dates")
        if period == "Custom" and isinstance(dates, (list, tuple)) and len(dates) == 2:
            out["start"], out["end"] = dates[0].isoformat(), dates[1].isoformat()
    for param, key in LIST_PARAMS.items():
        values = [str(v) for v in state.get(key) or []]
        if values:
            out[param] = values
    search = str(state.get("f_search") or "").strip()
    if search:
        out["q"] = search
    return out


def _url_globals() -> dict[str, str | list[str]]:
    qp = st.query_params
    out: dict[str, str | list[str]] = {}
    for key in GLOBAL_PARAMS:
        if key in qp:
            out[key] = qp.get_all(key) if key in LIST_PARAMS else qp[key]
    return out


def _adopt(url: dict[str, str | list[str]]) -> None:
    reset_filters()
    slug_to_period = {v: k for k, v in PERIOD_SLUGS.items()}
    period = slug_to_period.get(str(url.get("period", "all")), "All time")
    st.session_state["f_period"] = period
    if period == "Custom":
        try:
            st.session_state["f_dates"] = (date.fromisoformat(str(url["start"])),
                                           date.fromisoformat(str(url["end"])))
        except (KeyError, ValueError):
            st.session_state["f_period"] = "All time"
    for param, key in LIST_PARAMS.items():
        values = url.get(param)
        if values:
            st.session_state[key] = [values] if isinstance(values, str) else list(values)
    if url.get("q"):
        st.session_state["f_search"] = str(url["q"])


def sync_url() -> None:
    """Reconcile filters with the URL once per run (call before any filter widget renders)."""
    url = _url_globals()
    signature = urlencode(sorted((k, str(v)) for k, v in url.items()))
    if url.get("f") == "1" and signature != st.session_state.get("_jt_url_sig"):
        _adopt(url)  # arrived by a link, a bookmark or Back: the URL is the truth
    wanted = {"f": "1", **global_params()}
    for key in GLOBAL_PARAMS:
        if key not in wanted and key in st.query_params:
            del st.query_params[key]
    for key, value in wanted.items():
        st.query_params[key] = value
    st.session_state["_jt_url_sig"] = urlencode(
        sorted((k, str(v)) for k, v in wanted.items()))


def href(page: str, **params) -> str:
    """Relative link to ``page`` carrying the current filters plus ``params``.

    ``None`` / empty values are dropped; lists become repeated keys. Used for every
    drill-down link, so the target page shows exactly the set the number stood for."""
    query: dict[str, str | list[str]] = {"f": "1", **global_params()}
    for key, value in params.items():
        if value is None or value == "" or value == []:
            query.pop(key, None)
        else:
            query[key] = [str(v) for v in value] if isinstance(value, (list, tuple)) else str(value)
    return f"{page}?{urlencode(query, doseq=True)}"


# ============================================================================ drill-down
REACHED_LABELS = {
    "response": "got a reply",
    "no_response": "no reply yet",
    "assessment": "reached an assessment",
    "interview": "reached an interview",
    "offer": "got an offer",
    "rejected": "rejected",
    "ghosted": "ghosted (no reply for weeks)",
    "active": "still open",
    "awaiting": "waiting for a first reply",
    "in_progress": "in progress (assessment or interview)",
    "follow_up": "due a follow-up",
    "dated_reply": "replied (reply date known)",
}
DRILL_PARAMS = ("reached", "month", "day", "company", "seniority", "channel")


@dataclass(frozen=True)
class Drill:
    """The drill-down part of a Pipeline URL: which set of applications a link stood for."""
    reached: tuple[str, ...] = ()
    month: str = ""
    day: date | None = None
    companies: tuple[str, ...] = ()
    seniorities: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return bool(self.reached or self.month or self.day or self.companies
                    or self.seniorities or self.channels)

    def chips(self) -> list[tuple[str, str, str]]:
        """(url param, value, plain-language label) for every active part."""
        out = [("reached", r, REACHED_LABELS.get(r, r)) for r in self.reached]
        if self.month:
            out.append(("month", self.month, f"applied in {month_name(self.month)}"))
        if self.day:
            out.append(("day", self.day.isoformat(),
                        f"applied on {self.day.day} {self.day:%b %Y}"))
        out += [("company", c, c) for c in self.companies]
        out += [("seniority", v, v) for v in self.seniorities]
        out += [("channel", v, v) for v in self.channels]
        return out


def month_name(month: str) -> str:
    try:
        return datetime.strptime(month + "-01", "%Y-%m-%d").strftime("%B %Y")
    except ValueError:
        return month


def drill_from_url() -> Drill:
    qp = st.query_params
    reached = tuple(r for r in qp.get_all("reached") if r in REACHED)
    day = None
    if qp.get("day"):
        try:
            day = date.fromisoformat(str(qp["day"]))
        except ValueError:
            day = None
    return Drill(reached=reached, month=str(qp.get("month") or ""), day=day,
                 companies=tuple(qp.get_all("company")),
                 seniorities=tuple(qp.get_all("seniority")),
                 channels=tuple(qp.get_all("channel")))


def with_drill(drill: Drill, base: Filters | None = None) -> Filters:
    """The global filters narrowed by a drill-down. Every part is applied in SQL."""
    f = dataclasses.replace(base or current_filters())
    f.reached = list(drill.reached)
    f.month = drill.month
    f.companies = list(drill.companies)
    f.seniorities = list(drill.seniorities)
    f.channels = list(drill.channels)
    if drill.day:
        f.date_from = max(filter(None, [f.date_from, drill.day]))
        f.date_to = min(filter(None, [f.date_to, drill.day]))
    return f


def drill(**parts) -> Filters:
    """Shorthand used by charts: the current filters narrowed by keyword parts, e.g.
    ``drill(reached=["interview"], platforms=["LinkedIn"])``."""
    f = dataclasses.replace(current_filters())
    for name, value in parts.items():
        setattr(f, name, value)
    return f


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


def last_sync(where: Settings | None = None) -> str | None:
    try:
        return Repository(where or settings()).load_state().get("last_sync_at")
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


CHANGE_ICONS = {"Interview": ":material/event:", "Offer": ":material/celebration:",
                "Rejected": ":material/close:", "Ghosted": ":material/schedule:",
                "Assessment / OA": ":material/quiz:", "Withdrawn": ":material/undo:"}


def latest_changes(limit: int = 6) -> tuple[str, list[dict], int] | None:
    """What the last sync (and anything done by hand since) changed, newest and most
    interesting first: status moves, then new applications. None before the first sync."""
    try:
        r = Repository(settings())
        state = r.load_state()
        since = state.get("last_sync_started")
        changes = r.changes_since(since) if since else None
    except Exception:
        return None
    if changes is None:
        return None
    when = humanize_since(state.get("last_sync_at"))
    if not changes:
        return f"Nothing new since your last sync ({when}).", [], 0
    moved = [c for c in changes if c.get("from_status")]
    new = [c for c in changes if not c.get("from_status")]
    parts = []
    if new:
        parts.append(f"{len(new)} new application{'s' if len(new) != 1 else ''}")
    if moved:
        parts.append(f"{len(moved)} status change{'s' if len(moved) != 1 else ''}")
    title = f"Since your last sync ({when}): " + " and ".join(parts)
    rank = {"Offer": 0, "Interview": 1, "Assessment / OA": 2, "Rejected": 3, "Withdrawn": 4,
            "Ghosted": 6}
    moved.sort(key=lambda c: rank.get(str(c.get("to_status")), 5))
    ordered = [c for c in moved if c.get("to_status") != "Ghosted"] + new + [
        c for c in moved if c.get("to_status") == "Ghosted"]
    items = []
    for c in ordered[:limit]:
        who = f"{c.get('company_name') or 'Unknown'}, {c.get('role_title') or 'role not stated'}"
        if c.get("from_status"):
            label = f"{who}: {c['from_status']} → {c['to_status']}"
            icon = CHANGE_ICONS.get(str(c["to_status"]), ":material/arrow_forward:")
        else:
            label, icon = f"New: {who}", ":material/add_circle:"
        items.append({"id": str(c["thread_id"]), "label": label, "icon": icon})
    return title, items, max(0, len(changes) - len(items))


def background_sync_status() -> tuple[str, str] | None:
    """The last line of sync.log (written by ``jobtracker sync --background``, the Windows
    schedule set up by auto_sync.bat), as (plain text, tone). None when it never ran."""
    try:
        log = settings().data_dir / "sync.log"
        lines = log.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return None
    if not lines:
        return None
    stamp, _, rest = lines[-1].partition("  ")
    when = stamp[11:16] if stamp[:10] == date.today().isoformat() else stamp[:10]
    if rest.strip().startswith("ok"):
        return f"Auto-synced at {when}", ""
    if "SignInRequired" in rest:
        return "Background sync needs you to click Sync Gmail once", "warn"
    return f"Background sync at {when} failed", "warn"
