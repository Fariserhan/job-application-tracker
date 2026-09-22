import html
import hmac
import io
import json
import importlib
import os
import re
import socket
import sqlite3
import sys
import traceback
from collections import Counter
from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

import enricher
import main as sync_engine
import parser
import repair
import storage
from parser import STATUSES

st.set_page_config(page_title="Job Application Suite", page_icon="🎯", layout="wide")

DB_PATH = storage.DB_PATH
CSV_PATH = storage.CSV_PATH

STATUS_ORDER = STATUSES
FUNNEL_STAGES = ["Applied", "Assessment / OA", "Interview", "Offer"]
PLATFORM_ORDER = ["LinkedIn", "JobStreet", "Hiredly", "Prosple", "Direct ATS", "Other"]
STALE_DAYS = 21
FOLLOWUP_AFTER_DAYS = 5
FOLLOWUP_WATCH_STATUSES = ["Applied", "Assessment / OA"]

# URL shapes that are *never* a real job page: board email click-tracking redirects
# (e.g. JobStreet's url.jobstreet.com/ss/... resolves to a logo image) and raw image/CDN assets.
TRACKING_URL_RE = re.compile(
    r"silverpop|url\.jobstreet\.com/ss|/ss/c/|e2ma\.net|seekcdn\.com|"
    r"\.(?:png|jpe?g|gif|webp|svg|ico)(?:[?#]|$)",
    re.IGNORECASE,
)


def is_broken_job_url(url) -> bool:
    """True when a stored posting URL can never open the real job page."""
    if url is None:
        return False
    try:
        if pd.isna(url):
            return False
    except Exception:
        pass
    u = str(url).strip()
    if not u or u.lower() in ("", "nan", "none"):
        return False
    return bool(TRACKING_URL_RE.search(u))


def esc(value) -> str:
    """Escape email-derived text before it goes into unsafe_allow_html markdown.

    Company names, role titles, subjects and JD snippets come from untrusted email
    content — without escaping they could inject arbitrary HTML into the dashboard.
    """
    return html.escape("" if value is None else str(value), quote=True)


def safe_url(url) -> str:
    """Only http(s) links may ever be rendered as a clickable markdown link or button."""
    text = str(url or "").strip()
    if re.match(r"^https?://", text, re.IGNORECASE):
        return text
    return ""


def role_display(role) -> str:
    """Placeholder for roles that the email never named, so the cell isn't blank."""
    text = str(role or "").strip()
    return text if text else "—"

STATUS_COLORS = {
    "Saved / Planning": "#78909C",
    "Applied": "#636EFA",
    "Assessment / OA": "#AB63FA",
    "Interview": "#FFA15A",
    "Offer": "#00CC96",
    "Rejected": "#EF553B",
    "Ghosted": "#B0BEC5",
}

PLATFORM_COLORS = {
    "LinkedIn": "#0A66C2",
    "JobStreet": "#0057E0",
    "Hiredly": "#FF6B4A",
    "Prosple": "#7C3AED",
    "Direct ATS": "#00A67E",
    "Other": "#9AA0A6",
}

TABLE_COLUMNS = ["Company", "Role", "Platform", "Type", "Status", "Application Date", "Last Update", "Waiting", "Gmail Thread"]

GARBAGE_SUBJECT_PATTERNS = [
    r"quora", r"\bdigest\b", r"job alert", r"newsletter", r"jobs? you may",
    r"recommended jobs?", r"based on your (?:profile|skills|search)", r"weekly job",
    r"top stories",
]

STAGE_PROGRESS = {
    "gmail": 0.10, "metadata": 0.35, "parse": 0.55, "enrich": 0.75, "fit": 0.90, "done": 1.00,
}
STAGE_LABELS = {
    "gmail": "Step 1/6 — Querying Gmail API...",
    "metadata": "Step 2/6 — Batch fetching metadata (50/batch)...",
    "parse": "Step 3/6 — Filtering noise & parsing applications...",
    "enrich": "Step 4/6 — Scraping job links concurrently (ThreadPoolExecutor)...",
    "fit": "Step 5/6 — Running CV fit evaluations...",
    "done": "Step 6/6 — Done.",
}

st.markdown("""
<style>
    /* ============ Suite theme: dark + snappy motion / hover-reactive ============ */
    @keyframes fadeUp {
        from {opacity: 0; transform: translateY(8px) scale(.985);}
        to {opacity: 1; transform: none;}
    }
    @keyframes gradientShift {
        0% {background-position: 0% 50%;}
        50% {background-position: 100% 50%;}
        100% {background-position: 0% 50%;}
    }
    html, body, [data-testid="stAppViewContainer"] {
        font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif;
    }

    /* Gradient headline — a living, animated sheen */
    h1 {
        background: linear-gradient(90deg, #fafafa 0%, #9db9ff 30%, #4F8CFF 50%, #9db9ff 70%, #fafafa 100%);
        background-size: 250% 100%;
        -webkit-background-clip: text; background-clip: text;
        color: transparent; letter-spacing: -0.5px;
        animation: gradientShift 5s ease infinite;
    }

    /* ---- KPI metric cards: big pop + glow on hover ---- */
    div[data-testid="stMetric"], div[data-testid="metric-container"] {
        background: linear-gradient(180deg, #1b1f28 0%, #141721 100%);
        border: 1px solid #2b2f3a; border-radius: 12px;
        padding: 14px 18px;
        box-shadow: 0 2px 14px rgba(0,0,0,.35);
        transition: transform .13s cubic-bezier(.16,1,.3,1), border-color .13s ease,
                    box-shadow .13s ease, background .13s ease;
        animation: fadeUp .28s cubic-bezier(.16,1,.3,1) both;
        cursor: default;
    }
    div[data-testid="stMetric"]:hover, div[data-testid="metric-container"]:hover {
        transform: translateY(-7px) scale(1.08);
        border: 1px solid #4F8CFF;
        background: linear-gradient(180deg, #1d2230 0%, #161a26 100%);
        box-shadow: 0 18px 44px rgba(79,140,255,.38), 0 0 0 1px rgba(79,140,255,.5) inset,
                    0 0 24px rgba(79,140,255,.22);
        z-index: 2;
        position: relative;
    }
    div[data-testid="stColumn"]:nth-child(1) div[data-testid="stMetric"] {animation-delay: .03s;}
    div[data-testid="stColumn"]:nth-child(2) div[data-testid="stMetric"] {animation-delay: .08s;}
    div[data-testid="stColumn"]:nth-child(3) div[data-testid="stMetric"] {animation-delay: .13s;}
    div[data-testid="stColumn"]:nth-child(4) div[data-testid="stMetric"] {animation-delay: .18s;}
    div[data-testid="stColumn"]:nth-child(5) div[data-testid="stMetric"] {animation-delay: .23s;}

    /* ---- Expanders: professional — border tint only, no scaling, no wall borders ---- */
    [data-testid="stExpander"] {
        background: #141721;
        border: 1px solid #242a35; border-radius: 12px;
        overflow: hidden;
        animation: fadeUp .24s cubic-bezier(.16,1,.3,1) both;
        transition: border-color .14s ease, box-shadow .14s ease;
    }
    [data-testid="stExpander"]:hover {
        border-color: rgba(79,140,255,.35);
        box-shadow: 0 6px 20px rgba(0,0,0,.28);
    }
    [data-testid="stExpander"] details > summary {padding: .7rem 1rem; transition: color .15s ease;}
    [data-testid="stExpander"] details > summary:hover {color: #9db9ff;}

    /* ---- Bordered cards: quiet left-accent on hover instead of a loud box ---- */
    [data-testid="stVerticalBlockBorderWrapper"] {
        animation: fadeUp .26s cubic-bezier(.16,1,.3,1) both;
        transition: border-color .14s ease, box-shadow .14s ease;
    }
    [data-testid="stVerticalBlockBorderWrapper"]:hover {
        border-color: #303844;
        box-shadow: inset 3px 0 0 #4F8CFF, 0 6px 18px rgba(0,0,0,.25);
    }

    /* ---- Buttons: punchy pop on hover, press on click ---- */
    .stButton > button {
        border-radius: 10px;
        border: 1px solid #2b2f3a;
        font-weight: 600;
        transition: transform .12s cubic-bezier(.16,1,.3,1), border-color .12s ease,
                    box-shadow .12s ease, background .12s ease, filter .12s ease;
    }
    .stButton > button:hover {
        transform: translateY(-3px) scale(1.1);
        border-color: #4F8CFF;
        background: #1c2431;
        box-shadow: 0 12px 30px rgba(79,140,255,.34), 0 0 0 1px rgba(79,140,255,.25);
    }
    .stButton > button:active {transform: translateY(0) scale(.94);}
    button[data-testid="stBaseButton-primary"],
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #4F8CFF 0%, #2f6fe0 100%);
        border: none; color: #fff;
        position: relative; overflow: hidden;
    }
    button[data-testid="stBaseButton-primary"]:hover {
        filter: brightness(1.15);
        box-shadow: 0 14px 34px rgba(79,140,255,.5), 0 0 0 1px rgba(79,140,255,.3);
        background: linear-gradient(135deg, #5b97ff 0%, #3679ea 100%);
    }
    /* light sheen sweeping across primary buttons */
    button[data-testid="stBaseButton-primary"]::after {
        content: ""; position: absolute; top: 0; left: -130%; width: 60%; height: 100%;
        background: linear-gradient(90deg, transparent, rgba(255,255,255,.35), transparent);
        transform: skewX(-20deg); transition: left .55s ease;
    }
    button[data-testid="stBaseButton-primary"]:hover::after {left: 160%;}

    /* ---- Progress bars glow ---- */
    div[data-testid="stProgress"] > div {
        transition: box-shadow .2s ease, filter .2s ease;
    }
    div[data-testid="stProgress"]:hover > div {filter: brightness(1.15);}

    /* ---- Charts & tables: quiet presence, soft ring on hover ---- */
    [data-testid="stPlotlyChart"], [data-testid="stDataFrame"], [data-testid="stDataFrameResizable"] {
        animation: fadeUp .45s ease both;
        border-radius: 12px;
        transition: box-shadow .2s ease;
    }
    [data-testid="stPlotlyChart"]:hover, [data-testid="stDataFrame"]:hover {
        box-shadow: 0 0 0 1px rgba(79,140,255,.28), 0 8px 24px rgba(0,0,0,.3);
    }

    /* ---- Tabs ---- */
    [data-testid="stTabs"] button[role="tab"] {
        font-weight: 600; color: #8b93a7;
        transition: color .18s ease, transform .18s ease;
        border-radius: 8px;
    }
    [data-testid="stTabs"] button[role="tab"]:hover {
        color: #fafafa; transform: translateY(-2px) scale(1.04);
        background: rgba(79,140,255,.08);
    }
    [data-testid="stTabs"] button[aria-selected="true"] {color: #4F8CFF;}

    /* ---- Sidebar ---- */
    [data-testid="stSidebar"] {background: #0c0e13; border-right: 1px solid #1c202a;}
    [data-testid="stSidebar"] hr {border-color: #1c202a;}
    [data-testid="stSidebar"] .stButton > button:hover {
        transform: translateY(-1px) scale(1.03);
    }

    /* ---- Fit chips ---- */
    .fit-chip {
        border-radius: 8px; padding: 2px 8px; font-weight: 600; display:inline-block;
        margin: 2px 3px 2px 0;
        transition: transform .12s cubic-bezier(.16,1,.3,1), box-shadow .12s ease;
    }
    .fit-chip:hover {
        transform: scale(1.28) translateY(-2px);
        box-shadow: 0 6px 16px rgba(0,0,0,.5);
    }
    .fit-green {background: rgba(0,204,150,.18); color: #00CC96;}
    .fit-amber {background: rgba(255,161,90,.18); color: #FFA15A;}
    .fit-red {background: rgba(239,85,59,.18); color: #EF553B;}
    div[data-testid="stMetric"] {
        background: linear-gradient(180deg, #1b1f28 0%, #141721 100%);
    }

    /* ---- Status / alerts / download ---- */
    [data-testid="stStatus"] {border-radius: 12px; animation: fadeUp .26s cubic-bezier(.16,1,.3,1) both;}
    [data-testid="stAlert"] {border-radius: 10px; animation: fadeUp .26s cubic-bezier(.16,1,.3,1) both;}
    [data-testid="stDownloadButton"] > button {
        border-radius: 10px; transition: transform .12s ease, border-color .12s ease,
        box-shadow .12s ease;
    }
    [data-testid="stDownloadButton"] > button:hover {
        transform: translateY(-3px) scale(1.08);
        border-color: #00CC96;
        box-shadow: 0 12px 30px rgba(0,204,150,.28);
    }

/* ---- Scrollbars ---- */
    ::-webkit-scrollbar {width: 9px; height: 9px;}
    ::-webkit-scrollbar-track {background: transparent;}
    ::-webkit-scrollbar-thumb {background: #2b2f3a; border-radius: 8px; transition: background .2s ease;}
    ::-webkit-scrollbar-thumb:hover {background: #4F8CFF;}

    /* Long text blocks (JDs): quiet quote-block, never a boxy wall */
    .jd-block {
        background: #111419;
        border-left: 3px solid #313947;
        border-radius: 0 8px 8px 0;
        padding: 10px 14px;
        color: #b9c2cf;
        font-size: 0.88rem;
        line-height: 1.55;
        max-height: 240px;
        overflow-y: auto;
    }
    .jd-block:hover {border-left-color: #4F8CFF;}
    .compact-meta {
        color: #8b93a7; font-size: 0.82rem; line-height: 1.5;
    }

    /* ============ Inside-the-chart animation ============ */
    /* MODE selection: bars/funnel/scatter animations OR native tooltips.
       Pies ALWAYS keep native pointer events, so donut tooltips never break. */
    .js-plotly-plot .pielayer path {
        transform-origin: 50% 50%;
        transform-box: fill-box;
        transition: transform .16s cubic-bezier(.34,1.56,.64,1), filter .16s ease,
                    opacity .16s ease;
    }
    .js-plotly-plot .pielayer path:hover {
        transform: scale(1.16); filter: brightness(1.55) drop-shadow(0 0 10px rgba(79,140,255,.9));
    }
    .js-plotly-plot .hoverlayer {pointer-events: none !important;}

    /* Chic animated dividers */
    hr {
        border: none; height: 1px;
        background: linear-gradient(90deg, transparent, #2b3549 18%, #4F8CFF 50%, #2b3549 82%, transparent);
        opacity: .55;
    }

    /* Section headers rise in */
    h2, h3 {
        animation: fadeUp .32s ease both;
        transition: color .2s ease;
    }

    /* Form widgets highlight on hover, glow on focus */
    [data-testid="stTextInput"] > div > div,
    [data-testid="stMultiSelect"] > div > div,
    [data-testid="stSelectbox"] > div > div {
        transition: border-color .18s ease, box-shadow .18s ease, background .18s ease;
    }
    [data-testid="stTextInput"] > div > div:hover,
    [data-testid="stMultiSelect"] > div > div:hover,
    [data-testid="stSelectbox"] > div > div:hover {
        border-color: rgba(79,140,255,.55) !important;
        box-shadow: 0 0 0 1px rgba(79,140,255,.25);
        background: #1a1f2a;
    }
    [data-testid="stTextInput"] input:focus,
    [data-testid="stTextArea"] textarea:focus {
        border-color: #4F8CFF !important;
        box-shadow: 0 0 0 1px #4F8CFF !important;
    }

    /* Slider: thumb enlarges, track glows */
    [data-testid="stSlider"] [role="slider"] {
        transition: transform .15s cubic-bezier(.34,1.56,.64,1);
    }
    [data-testid="stSlider"] [role="slider"]:hover {transform: scale(1.3);}
    [data-testid="stSlider"] [data-testid="stSliderTrack"] {transition: filter .2s ease;}
    [data-testid="stSlider"]:hover [data-testid="stSliderTrack"] {filter: brightness(1.2);}

    /* Checkbox / toggle lift */
    [data-testid="stCheckbox"] {transition: transform .15s ease;}
    [data-testid="stCheckbox"]:hover {transform: scale(1.08);}

    /* Link columns & data links glow */
    a {transition: color .15s ease, text-shadow .15s ease;}
    a:hover {color: #9db9ff; text-shadow: 0 0 10px rgba(79,140,255,.35);}

    /* Multiselect pills pop */
    [data-testid="stMultiSelect"] span[data-bas-web="tablet"] span,
    span[style*="border-radius"] {
        transition: transform .12s ease;
    }

    /* Chart columns stagger */
    div[data-testid="stColumn"]:nth-child(2) [data-testid="stPlotlyChart"] {animation-delay: .05s;}
</style>
""", unsafe_allow_html=True)

COLORWAY = ["#4F8CFF", "#00CC96", "#FFA15A", "#AB63FA", "#EF553B", "#FFD166", "#22D3EE"]


def style_fig(fig, height=340):
    """Uniform polished dark styling for every chart."""
    fig.update_layout(
        height=height,
        margin={"t": 16, "b": 12, "l": 16, "r": 16},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#c9d1d9", size=12, family="Inter, sans-serif"),
        colorway=COLORWAY,
        legend=dict(bgcolor="rgba(0,0,0,0)", borderwidth=0, font=dict(size=11)),
        hoverlabel=dict(bgcolor="#1a1d24", bordercolor="#4F8CFF",
                        font=dict(color="#fafafa", size=13)),
        hovermode="closest",
        transition=None,
    )
    fig.update_xaxes(gridcolor="#242833", zerolinecolor="#242833",
                     linecolor="#242833", tickfont=dict(size=10), showline=True)
    fig.update_yaxes(gridcolor="#242833", zerolinecolor="#242833",
                     linecolor="#242833", tickfont=dict(size=10), showline=True)
    return fig


def style_pie(fig, height=340):
    """High-contrast donut/pie labels + hover styling (label + % always readable).

    Generous margins: with `textposition="auto"` the small slices get outside labels,
    which Plotly clips if the top/bottom margins are too tight.
    """
    fig.update_traces(
        textposition="auto",
        texttemplate="<b>%{label}</b><br>%{percent:.0%}",
        textfont=dict(color="#ffffff", size=12),
        insidetextfont=dict(color="#ffffff", size=12),
        outsidetextfont=dict(color="#fafafa", size=12),
        marker=dict(line=dict(color="#0e1117", width=2)),
        hovertemplate="<b>%{label}</b><br>%{value} application(s) · %{percent}<extra></extra>",
    )
    fig.update_layout(
        height=height,
        margin={"t": 64, "b": 28, "l": 40, "r": 40},
        showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#c9d1d9", family="Inter, sans-serif"),
        hoverlabel=dict(bgcolor="#1a1d24", bordercolor="#4F8CFF",
                        font=dict(color="#fafafa", size=13)),
    )
    return fig


# ------------------------------------------------------------------ loading

@st.cache_data(show_spinner="Reading applications.db...")
def load_db_file(path: str, mtime: float) -> pd.DataFrame:
    conn = sqlite3.connect(path)
    try:
        return pd.read_sql_query("SELECT * FROM applications", conn)
    finally:
        conn.close()


@st.cache_data(show_spinner="Reading activity log...")
def load_activity_cached(path: str, mtime: float) -> list:
    return storage.get_recent_activity(60)


@st.cache_data(show_spinner="Parsing uploaded CSV...")
def load_csv_bytes(content: bytes) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(content), encoding="utf-8-sig")


@st.cache_data(show_spinner="Reading job_tracker.csv...")
def load_csv_file(path: str, mtime: float) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig")


def mtime_of(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def resolve_source():
    upload = st.session_state.get("_upload")
    if upload is not None:
        return load_csv_bytes(upload.getvalue()), f"Uploaded CSV: {upload.name}"
    if os.path.exists(DB_PATH):
        return load_db_file(DB_PATH, mtime_of(DB_PATH)), \
            f"applications.db (modified {datetime.fromtimestamp(mtime_of(DB_PATH)):%Y-%m-%d %H:%M})"
    if os.path.exists(CSV_PATH):
        return load_csv_file(CSV_PATH, mtime_of(CSV_PATH)), "job_tracker.csv (fallback)"
    return None, None


def _parse_json_list(value) -> list:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]

    defaults = {
        "company_name": "", "role_title": "", "source_platform": "Other",
        "latest_subject": "", "job_url": "", "gmail_link": "",
        "job_description_snippet": "", "notes": "",
    }
    for col, default in defaults.items():
        if col not in df.columns:
            df[col] = default
        df[col] = df[col].fillna(default).astype(str).str.strip()

    for col in ("application_date", "last_updated"):
        if col not in df.columns:
            df[col] = None
        df[col] = pd.to_datetime(df[col], errors="coerce")

    df["current_status"] = df.get("current_status", pd.Series("Applied", index=df.index)) \
        .fillna("Applied").astype(str).str.strip()
    df["current_status"] = df["current_status"].replace(
        {"Screening/OA": "Assessment / OA", "Ghosted/Inactive": "Ghosted"})

    if "is_valid" in df.columns:
        df["is_valid"] = pd.to_numeric(df["is_valid"], errors="coerce").fillna(1) == 1
    elif "is_valid_application" in df.columns:
        df["is_valid"] = pd.to_numeric(df["is_valid_application"], errors="coerce").fillna(1) == 1
    else:
        blob = df["latest_subject"].str.lower()
        heuristic = pd.Series(False, index=df.index)
        for pattern in GARBAGE_SUBJECT_PATTERNS:
            heuristic |= blob.str.contains(pattern, regex=True, na=False)
        heuristic |= df["company_name"].str.lower().isin(["unknown", "", "nan"])
        df["is_valid"] = ~heuristic

    df["source_platform"] = df["source_platform"].replace({"nan": "", "None": ""})
    df.loc[df["source_platform"] == "", "source_platform"] = "Other"

    for col in ("job_type", "seniority_level", "industry"):
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str).str.strip()

    return df


def compute_stale(row) -> bool:
    if row["current_status"] != "Applied":
        return False
    ref = row["last_updated"] if pd.notna(row["last_updated"]) else row["application_date"]
    if pd.isna(ref):
        return False
    return (pd.Timestamp.today().normalize() - ref.normalize()).days > stale_days()


def stale_days() -> int:
    """Auto-ghost threshold, tunable from the sidebar."""
    try:
        return max(1, int(st.session_state.get("stale_days", STALE_DAYS)))
    except (TypeError, ValueError):
        return STALE_DAYS


def followup_days() -> int:
    """Days after which an Applied-but-silent role enters the follow-up nudge list."""
    try:
        return max(1, int(st.session_state.get("followup_days", FOLLOWUP_AFTER_DAYS)))
    except (TypeError, ValueError):
        return FOLLOWUP_AFTER_DAYS


# ------------------------------------------------------------------ analytics

def generate_insights(valid: pd.DataFrame, stale_count: int) -> list:
    tips = []
    try:
        progressed = valid[valid["current_status"].isin(["Assessment / OA", "Interview", "Offer", "Rejected"])]
        response_rate = (len(progressed) / len(valid) * 100) if len(valid) else 0
        tips.append(f"📬 **{response_rate:.0f}% response rate** — {len(progressed)} of {len(valid)} "
                    f"applications got a human response.")

        perf = valid.copy()
        perf["_prog"] = perf["current_status"].isin(["Assessment / OA", "Interview", "Offer", "Rejected"])
        byp = perf.groupby("source_platform").agg(n=("thread_id", "count"), resp=("_prog", "sum"))
        byp = byp[byp["n"] >= 3]
        if len(byp) >= 2:
            byp["rate"] = byp["resp"] / byp["n"]
            best, worst = byp["rate"].idxmax(), byp["rate"].idxmin()
            tips.append(f"🏆 **{best} converts best** ({byp.loc[best, 'rate']*100:.0f}% response) vs "
                        f"{worst} ({byp.loc[worst, 'rate']*100:.0f}%). Weight future applications toward {best}.")
        elif len(byp) == 1:
            tips.append(f"🏆 Only **{byp.index[0]}** has ≥3 tracked applications so far — spread channels for comparison.")

        resp = progressed.dropna(subset=["application_date", "last_updated"]).copy()
        if len(resp):
            days = (resp["last_updated"] - resp["application_date"]).dt.days
            days = days[(days >= 0) & (days < 120)]
            if len(days):
                tips.append(f"⏱️ **Median employer response: {days.median():.0f} days** "
                            f"(fastest {days.min():.0f}d, slowest {days.max():.0f}d).")

        if stale_count:
            tips.append(f"👻 {stale_count} application(s) applied >{stale_days()}d ago with no update — "
                        f"a polite follow-up email can revive them.")
        elif int((valid["current_status"] == "Ghosted").sum()):
            tips.append(f"👻 {int((valid['current_status'] == 'Ghosted').sum())} auto-ghosted thread(s) on record.")

        comp = valid["company_name"].value_counts()
        if len(comp) and comp.iloc[0] > 1:
            tips.append(f"🏢 Most-targeted employer: **{comp.index[0]}** ({comp.iloc[0]} applications) — "
                        f"a referral or recruiter ping could differentiate you there.")

        wk = valid["application_date"].dt.to_period("W-SUN").value_counts()
        if len(wk):
            tips.append(f"📈 Peak application week: **{wk.index[0].start_time:%d %b %Y}** ({wk.iloc[0]} applications).")

        offers = int((valid["current_status"] == "Offer").sum())
        if offers:
            tips.append(f"🎉 {offers} offer(s) on the table — negotiate before accepting; "
                        f"compete offers against each other for leverage.")
    except Exception:
        pass
    return tips




# ------------------------------------------------------------------ analytics layer
#
# The numbers below come from the SQL warehouse (warehouse.py / warehouse_queries.py)
# rather than being recomputed in pandas. That is the whole point of the rebuild: the
# same query feeds the dashboard, the Excel export, the Power BI measures and the
# Tableau fields, so the four can never disagree. Each helper falls back to the original
# pandas computation when the warehouse is unavailable, so a stale warehouse can never
# break the page.

@st.cache_data(show_spinner=False)
def _warehouse_ready(path: str, mtime: float) -> bool:
    return os.path.exists(path)


@st.cache_data(show_spinner="Running analytics SQL...")
def _wq(name: str, db_mtime: float):
    """Run one warehouse query, cached until the database changes."""
    try:
        import warehouse_queries

        return warehouse_queries.query(name)
    except Exception:
        return pd.DataFrame()


def refresh_warehouse() -> bool:
    """Rebuild the star schema from the operational tables. Returns True on success."""
    try:
        import warehouse

        warehouse.build_warehouse()
        return True
    except Exception:
        return False


def warehouse_kpis() -> dict:
    """Headline KPIs straight from SQL. Empty dict when the warehouse is not built yet."""
    df = _wq("headline_kpis", mtime_of(DB_PATH))
    if df is None or df.empty:
        return {}
    return {k: v for k, v in df.iloc[0].to_dict().items() if pd.notna(v)}


def platform_performance(valid: pd.DataFrame) -> pd.DataFrame:
    """Platform effectiveness from SQL, falling back to the pandas computation.

    The SQL is the source of truth for the numbers; the column mapping below is derived
    from whatever the query actually returned rather than assumed, so adding or renaming a
    column in warehouse_queries.py cannot crash the dashboard.
    """
    wdf = _wq("platform_effectiveness", mtime_of(DB_PATH))
    if wdf is not None and not wdf.empty:
        renamed = wdf.rename(columns={
            "platform_name": "source_platform", "applications": "Applications",
            "responses": "Progressed", "rejected": "Rejected",
            "interviews": "Interviews",
            "response_rate_pct": "Response %", "interview_rate_pct": "Interview %",
        })
        wanted = ["source_platform", "Applications", "Progressed", "Interviews",
                  "Rejected", "Response %", "Interview %"]
        keep = [c for c in wanted if c in renamed.columns]
        out = renamed[keep].copy()
        # A column the query does not provide must be present (as NA) so callers that
        # reference it keep working instead of raising KeyError.
        for col in wanted:
            if col not in out.columns:
                out[col] = pd.NA
        return out[wanted].sort_values("Applications", ascending=False).reset_index(drop=True)

    df = valid.copy()
    df["_prog"] = df["current_status"].isin(["Assessment / OA", "Interview", "Offer", "Rejected"])
    g = df.groupby("source_platform").agg(
        Applications=("thread_id", "count"),
        Progressed=("_prog", "sum"),
        Interviews=("current_status", lambda s: int(s.isin(["Interview", "Offer"]).sum())),
        Rejected=("current_status", lambda s: int((s == "Rejected").sum())),
    ).reset_index()
    g["Response %"] = (100 * g["Progressed"] / g["Applications"]).round(1)
    g["Interview %"] = (100 * g["Interviews"] / g["Applications"]).round(1)
    return g.sort_values("Applications", ascending=False)


def company_summary(valid: pd.DataFrame, limit: int = 20) -> pd.DataFrame:
    """Employer view from SQL, falling back to the pandas computation."""
    wdf = _wq("company_pipeline", mtime_of(DB_PATH))
    if wdf is not None and not wdf.empty:
        out = wdf.rename(columns={
            "applications": "Applications",
            "last_applied": "LastUpdate",
        })
        wanted = ["company_name", "Applications", "LastUpdate"]
        keep = [c for c in wanted if c in out.columns]
        out = out[keep].copy()
        for col in wanted:
            if col not in out.columns:
                out[col] = pd.NA
        return out[wanted].head(limit).reset_index(drop=True)

    df = valid.sort_values("last_updated")
    latest_status = df.groupby("company_name")["current_status"].last()
    g = valid.groupby("company_name").agg(
        Applications=("thread_id", "count"),
        LastUpdate=("last_updated", "max"),
    ).reset_index()
    g["Latest Status"] = g["company_name"].map(latest_status)
    return g.sort_values(["Applications", "LastUpdate"], ascending=[False, False]).head(limit)


def run_sync(force_full: bool, zone=None):
    st.session_state["sync_running"] = True
    st.toast("⏳ Sync started — watch the progress below the header…", icon="⏳")
    summary = {}
    with (zone.container() if zone is not None else st.container()):
        with st.status("▶ Syncing Gmail...", expanded=True) as status:
            bar = st.progress(0.0, text=STAGE_LABELS["gmail"])
            log = st.empty()
            try:
                def notify(stage, message):
                    bar.progress(STAGE_PROGRESS.get(stage, 0), text=STAGE_LABELS.get(stage, stage))
                    log.markdown(f"`{datetime.now():%H:%M:%S}` {message}")

                summary = sync_engine.run(force_full=force_full, notify=notify)
                bar.progress(1.0, text="Step 6/6 — Done.")
                status.update(label="✅ Gmail sync complete", state="complete", expanded=False)
                st.session_state["last_sync_summary"] = summary
            except FileNotFoundError as exc:
                status.update(label="❌ OAuth credentials missing", state="error", expanded=True)
                st.error(str(exc))
            except Exception as exc:
                status.update(label="❌ Sync failed", state="error", expanded=True)
                st.error(f"{type(exc).__name__}: {exc}")
                with st.expander("Traceback", expanded=False):
                    st.code(traceback.format_exc())
            finally:
                st.session_state["sync_running"] = False
    st.cache_data.clear()
    if summary:
        if summary.get("found", 0) == 0 and not force_full:
            st.toast("No new emails since your last sync — tick **Force Full Re-sync** to "
                     "re-process the whole mailbox", icon="ℹ️")
        else:
            st.toast(f"Synced in {summary['elapsed']:.1f}s — {summary['inserted']} new, "
                     f"{summary['updated']} updated, {summary['garbage']} noise", icon="✅")
        if summary.get("csv_error"):
            st.warning(f"Sync succeeded, but the CSV export was skipped "
                       f"({summary['csv_error']}). The database is safe — close "
                       f"job_tracker.csv if it is open and re-run the sync.")
    st.rerun()


def persist_ghosting():
    """Persist the lazy >21d auto-ghosting rule into the DB (mirrors sync behaviour)."""
    try:
        n = storage.apply_ghosting(stale_days=stale_days())
    except Exception as exc:
        st.error(f"Could not auto-ghost: {exc}")
        return
    st.cache_data.clear()
    if n:
        st.toast(f"👻 Auto-ghosted {n} application(s) with no update for >{stale_days()} days")
    else:
        st.toast("Nothing stale — every Applied row has an update within the last "
                 f"{stale_days()} days", icon="✅")
    st.rerun()


# ------------------------------------------------------------------ shared renders

def safe_post_link(url) -> tuple:
    """Return (usable_url, was_broken). Broken = email click-tracking/logo link that would
    never open the real job page (e.g. JobStreet's url.jobstreet.com/ss/... → logo image).
    Non-http(s) schemes are refused outright."""
    if url is None:
        return "", False
    try:
        if pd.isna(url):
            return "", False
    except Exception:
        pass
    u = str(url).strip()
    if not u or u.lower() in ("", "nan", "none"):
        return "", False
    if is_broken_job_url(u):
        return "", True
    clean = safe_url(u)
    if not clean:
        return "", False
    return clean, False


# ------------------------------------------------------------------ reports
def render_reports(stale_count: int = 0):
    """The reporting section: what the data says, not how it is computed.

    Every figure comes from the SQL warehouse (`warehouse_queries.py`), so these reports
    and the tracker KPIs can never disagree. Tabs use `on_change="rerun"` so only the tab
    you are looking at does any work.
    """
    st.subheader("📊 Reports")
    st.caption("Deeper cuts of your pipeline than the headline KPIs. All figures are computed "
               "in SQL from the same warehouse that feeds the export and the BI model.")

    tabs = st.tabs(["🔻 Funnel & pace", "🏆 Where it works",
                    "⏳ Waiting & follow-up", "⬇️ Export & model"], on_change="rerun")
    open_index = next((i for i, t in enumerate(tabs) if t.open), 0)

    if open_index == 0:
        with tabs[0]:
            _report_funnel_and_pace()
    elif open_index == 1:
        with tabs[1]:
            _report_where_it_works()
    elif open_index == 2:
        with tabs[2]:
            _report_waiting()
    else:
        with tabs[3]:
            _report_export()


def _report_funnel_and_pace():
    """Where applications die, and whether the pace is up or down."""
    funnel = _wq("funnel_conversion", mtime_of(DB_PATH))
    if funnel.empty:
        st.info("No applications yet — sync Gmail first.")
        return

    applied = int(funnel["applications"].iloc[0]) if len(funnel) else 0
    left, right = st.columns([1, 1.2])
    with left:
        st.markdown("**Where applications stop**")
        fig = px.funnel(funnel, x="applications", y="stage",
                        color_discrete_sequence=["#4F8CFF"])
        fig.update_traces(textinfo="value+percent initial")
        style_fig(fig, 340)
        st.plotly_chart(fig, width="stretch")
    with right:
        st.markdown("**Stage by stage**")
        show = funnel.rename(columns={
            "stage": "Stage", "applications": "Reached",
            "step_conversion_pct": "From previous %", "dropped_at_stage": "Lost here"})
        st.dataframe(show[["Stage", "Reached", "From previous %", "Lost here"]],
                     width="stretch", hide_index=True)
        screening = funnel[funnel["stage"] == "Assessment / OA"]
        if not screening.empty and applied:
            advanced = int(screening["applications"].iloc[0])
            st.caption(f"**{applied - advanced} of {applied}** applications never got past "
                       f"screening ({100 * advanced / applied:.0f}% advanced).")

    st.divider()
    st.markdown("**Pace — applications per month**")
    vel = _wq("monthly_velocity_running", mtime_of(DB_PATH))
    if vel.empty:
        st.caption("Not enough history yet.")
        return
    a, b = st.columns([1.4, 1.1])
    with a:
        fig = px.bar(vel, x="year_month", y="applications", text="applications",
                     labels={"year_month": "", "applications": "Applications"})
        fig.update_traces(marker_color="#4F8CFF", textposition="outside")
        style_fig(fig, 300)
        st.plotly_chart(fig, width="stretch")
    with b:
        st.markdown("**Running total**")
        st.dataframe(vel[["year_month", "applications", "responses", "running_total",
                          "mom_change"]].rename(columns={
            "year_month": "Month", "applications": "Applied", "responses": "Responses",
            "running_total": "Total to date", "mom_change": "vs prev"}),
            width="stretch", hide_index=True)
    last = vel.iloc[-1]
    if pd.notna(last.get("mom_change")):
        direction = "up" if last["mom_change"] > 0 else "down"
        st.caption(f"**{int(last['applications'])}** applications in {last['year_month']} — "
                   f"{direction} {abs(int(last['mom_change']))} on the month before. "
                   f"Three-month average: **{float(last.get('rolling_3m_avg') or 0):.0f}**/month.")


def _report_where_it_works():
    """Platform and employer effectiveness — where the responses actually come from."""
    perf = _wq("platform_effectiveness", mtime_of(DB_PATH))
    if perf.empty:
        st.info("No applications yet — sync Gmail first.")
        return

    st.markdown("**Which platform produces responses**")
    st.caption("Ranked by response rate. Sample sizes are small — treat gaps of a few points "
               "as noise, not as evidence.")
    p = perf.sort_values("response_rate_pct", ascending=True)
    fig = px.bar(p, x="response_rate_pct", y="platform_name", orientation="h",
                 text="response_rate_pct",
                 labels={"response_rate_pct": "Response rate %", "platform_name": ""},
                 color="response_rate_pct",
                 color_continuous_scale=["#EF553B", "#FFA15A", "#00CC96"])
    fig.update_traces(texttemplate="%{text:.0f}%")
    fig.update_layout(coloraxis_showscale=False)
    style_fig(fig, 300)
    st.plotly_chart(fig, width="stretch")

    st.dataframe(
        perf.rename(columns={
            "platform_name": "Platform", "applications": "Applied", "responses": "Responses",
            "interviews": "Interviews", "offers": "Offers", "ghosted": "Ghosted",
            "response_rate_pct": "Response %", "interview_rate_pct": "Interview %",
            "avg_days_to_response": "Avg days to reply",
            "share_of_applications_pct": "Share of applications"}),
        width="stretch", hide_index=True,
        column_config={
            "Response %": st.column_config.ProgressColumn(min_value=0, max_value=100,
                                                          format="%.1f%%"),
        })

    ranked = perf.sort_values("response_rate_pct", ascending=False)
    best, worst = ranked.iloc[0], ranked.iloc[-1]
    if int(best["applications"]) >= 5 and int(worst["applications"]) >= 5:
        st.caption(f"**{best['platform_name']}** returns "
                   f"{float(best['response_rate_pct'] or 0):.0f}% versus "
                   f"**{worst['platform_name']}** at "
                   f"{float(worst['response_rate_pct'] or 0):.0f}% — worth shifting effort if "
                   f"the gap holds up.")

    st.divider()
    st.markdown("**Employers you keep coming back to**")
    comp = _wq("company_pipeline", mtime_of(DB_PATH))
    if not comp.empty:
        repeats = comp[comp["applications"] > 1].sort_values("applications", ascending=False)
        if repeats.empty:
            st.caption("No employer has more than one application yet.")
        else:
            st.caption("More applications at the same firm is not automatically better odds — "
                       "pick the strongest fit and chase a referral for that one.")
            st.dataframe(
                repeats.rename(columns={
                    "company_name": "Employer", "applications": "Applied",
                    "distinct_roles": "Roles", "responses": "Responses",
                    "interviews": "Interviews", "still_open": "Still open",
                    "response_rate_pct": "Response %",
                    "last_applied": "Last applied"}),
                width="stretch", hide_index=True,
                column_config={
                    "Response %": st.column_config.ProgressColumn(min_value=0, max_value=100,
                                                                  format="%.1f%%"),
                })


def _report_waiting():
    """Everything still silent, ordered by how long you have been waiting."""
    cohort = _wq("silence_cohort", mtime_of(DB_PATH))
    if cohort.empty:
        st.success("Nothing is waiting — every application has had a response.")
        return

    waiting = int(cohort["applications"].sum())
    oldest = cohort.iloc[-1]
    c1, c2 = st.columns(2)
    c1.metric("Still waiting", waiting)
    c2.metric("Longest bucket", str(oldest["waiting_bucket"]).split(". ")[-1],
              help="You can chase anything quiet for 5+ days.")

    st.markdown("**How long you have been waiting**")
    fig = px.bar(cohort, x="waiting_bucket", y="applications", text="applications",
                 labels={"waiting_bucket": "", "applications": "Applications"})
    fig.update_traces(marker_color=["#00CC96", "#4F8CFF", "#FFA15A", "#EF553B", "#B0BEC5"],
                      textposition="outside")
    style_fig(fig, 300)
    st.plotly_chart(fig, width="stretch")

    st.dataframe(
        cohort.rename(columns={
            "waiting_bucket": "Waiting", "applications": "Applications",
            "avg_days_waiting": "Avg days"}),
        width="stretch", hide_index=True)
    st.caption("Use the **📬 Follow-up queue** on the tracker page to draft and send the "
               "nudges for these.")

    st.divider()
    st.markdown("**How your applications actually moved**")
    trans = _wq("status_transition_matrix", mtime_of(DB_PATH))
    if not trans.empty:
        st.dataframe(
            trans.rename(columns={
                "from_status": "From", "to_status": "To", "transitions": "Times",
                "pct_of_from_status": "% of that status",
                "avg_days_from_application": "Avg days from applying"}),
            width="stretch", hide_index=True)
        st.caption("The audit trail behind the funnel — how applications moved, not just "
                   "where they ended up.")

    recent = _wq("recent_activity", mtime_of(DB_PATH))
    if not recent.empty:
        with st.expander(f"📜 Last {min(len(recent), 30)} status changes", expanded=False):
            st.dataframe(
                recent.head(30)[["changed_at", "company_name", "role_title",
                                 "from_status", "to_status", "event_type"]].rename(columns={
                    "changed_at": "When", "company_name": "Employer", "role_title": "Role",
                    "from_status": "From", "to_status": "To", "event_type": "Type"}),
                width="stretch", hide_index=True)


def _report_export():
    """The single usable export, plus the model facts behind it."""
    try:
        import bi_export

        df = bi_export.unified_csv()
    except Exception as exc:
        st.error(f"Could not build the export: {type(exc).__name__}: {exc}")
        return

    if df is None or df.empty:
        st.info("Nothing to export yet — sync Gmail first.")
        return

    st.markdown("**Your data as one file**")
    st.caption("Every application, every useful column, one row each. Open it in Excel and "
               "everything is there — this is the file to use.")
    st.download_button(
        f"⬇️ Download job_applications.csv ({len(df)} rows)",
        data=df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"job_applications_{datetime.now():%Y%m%d}.csv",
        mime="text/csv", type="primary", key="dl_unified")

    q = _wq("data_quality_audit", mtime_of(DB_PATH))
    if not q.empty:
        row = q.iloc[0]
        c1, c2 = st.columns(2)
        c1.metric("Applications", int(row.get("rows", 0) or 0))
        c2.metric("Missing JD text", int(row.get("missing_jd", 0) or 0),
                  help="Use Scrape missing JDs on the tracker to fill these in.")

    with st.expander("The model behind these reports", expanded=False,
                     icon=":material/database:"):
        st.caption("The warehouse the reports read from. Useful if you want to import the data "
                   "into Power BI or Tableau — otherwise ignore this.")
        try:
            import warehouse

            counts = warehouse.build_warehouse()
            st.markdown(
                f"- **Fact table** — one row per application: "
                f"{counts.get('applications', 0)} rows\n"
                f"- **Dimensions** — {counts.get('companies', 0)} employers · "
                f"{counts.get('role_families', 0)} role families · "
                f"{counts.get('seniorities', 0)} seniority tiers · "
                f"{counts.get('industries', 0)} industries · "
                f"{counts.get('platforms', 0)} platforms\n"
                f"- **Status events** — {counts.get('events', 0)} transitions tracked")
        except Exception as exc:
            st.caption(f"Model unavailable ({type(exc).__name__})")

        try:
            import bi_export

            spec = bi_export.model_spec()
            st.markdown(f"- **Grain**: {spec['grain']['fact_application']}")
            with st.expander("Full model specification + stated limitations", expanded=False):
                st.json(spec)
        except Exception:
            pass

        st.caption("The SQL, the DAX measures and the Tableau calculated fields live in "
                   "`warehouse_queries.py` and `bi_export.py` — everything above is their "
                   "output, not a demo.")

    with st.expander("Per-report CSVs (for a Power BI / Tableau / Excel model)",
                     expanded=False, icon=":material/table_chart:"):
        st.caption("Only needed if you are building the BI model. Ignore otherwise.")
        if st.button("Write the per-report CSVs", key="gen_excel"):
            with st.spinner("Writing report CSVs..."):
                try:
                    import bi_export

                    st.session_state["excel_files"] = bi_export.export_excel()
                except Exception as exc:
                    st.error(f"Export failed: {type(exc).__name__}: {exc}")
        files = st.session_state.get("excel_files")
        if files:
            st.success(f"{len(files)} file(s) written to `bi_pack/excel/`")
            for name, path in files.items():
                st.markdown(f"- `{name}` → `{path}`")

# ------------------------------------------------------------------ applications

def render_applications_tab(valid: pd.DataFrame, view: pd.DataFrame, stale_count: int = 0):
    total_valid = len(valid)
    interviews = int(valid["current_status"].isin(["Interview", "Offer"]).sum())
    rejected = int((valid["current_status"] == "Rejected").sum())

    interview_rate = (interviews / total_valid * 100) if total_valid else 0.0
    rejection_rate = (rejected / total_valid * 100) if total_valid else 0.0

    progressed = valid[valid["current_status"].isin(["Assessment / OA", "Interview", "Offer", "Rejected"])]
    response_rate = (len(progressed) / total_valid * 100) if total_valid else 0.0
    active = int(valid["current_status"].isin(["Applied", "Assessment / OA"]).sum())
    waiting_now = int((valid["current_status"] == "Applied").sum())

    # How the response rate reads against the published benchmark bands.
    if response_rate >= 5.0:
        response_band = "Strong (5%+)"
    elif response_rate >= 3.0:
        response_band = "Good (3-5%)"
    elif response_rate >= 2.0:
        response_band = "Average (2-3%)"
    else:
        response_band = "Below average (<2%)"

    cutoff_30 = pd.Timestamp.today().normalize() - pd.Timedelta(days=30)
    recent_30 = int((valid["application_date"] >= cutoff_30).sum())
    prev_30 = int(((valid["application_date"] >= cutoff_30 - pd.Timedelta(days=30))
                   & (valid["application_date"] < cutoff_30)).sum())
    delta_30 = (recent_30 - prev_30) if (recent_30 or prev_30) else None

    # Median reply time comes from the warehouse, which only counts responses with a
    # KNOWN date. Computing it here from `last_updated` counted every still-silent
    # application as a zero-day reply and dragged the median to 0.
    med_days = None
    try:
        _kpi = _wq("headline_kpis", mtime_of(DB_PATH))
        if not _kpi.empty:
            value = _kpi.iloc[0].get("avg_days_to_response")
            med_days = float(value) if pd.notna(value) else None
    except Exception:
        med_days = None

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Applications", total_valid, help="Noise-filtered application emails")
    k2.metric("Response rate", f"{response_rate:.1f}%",
              delta=response_band, delta_color="off",
              help="Any employer reply that is not a form rejection. Published benchmarks: "
                   "2–3% average, 3–5% good, 5%+ strong.")
    k3.metric("Interview rate", f"{interview_rate:.1f}%",
              help="Reached interview or offer. Benchmark: 5–10% for well-targeted "
                   "applications; 6.87% when applying on a company's own careers page.")
    k4.metric("Still active", active, help="Applied or Assessment/OA — the pipeline is alive")

    k5, k6, k7, k8 = st.columns(4)
    k5.metric("Waiting on a reply", waiting_now, delta_color="off",
              help="Applied with no response yet — see the action queue below.")
    k6.metric("Median reply time", f"{med_days:.0f} days" if med_days is not None else "—",
              help="Application date to the first employer response, where the date is known.")
    k7.metric("Applied in last 30 days", recent_30,
              delta=(f"{delta_30:+d} vs previous 30" if delta_30 is not None else None),
              delta_color="normal" if (delta_30 or 0) >= 0 else "inverse",
              help=f"Previous 30 days: {prev_30}")
    k8.metric("Rejection rate", f"{rejection_rate:.0f}%", help="Rejected / total applications")

    st.space("small")

    with st.expander("Insights & recommendations", expanded=False, icon=":material/lightbulb:"):
        tips = generate_insights(valid, stale_count)[:6]
        if tips:
            for tip in tips:
                st.markdown(f"- {tip}")

    render_followup_queue(valid)
    render_activity_feed()

    st.subheader("Pipeline")
    funnel_counts = [
        total_valid,
        int(valid["current_status"].isin(FUNNEL_STAGES[1:]).sum()),
        int(valid["current_status"].isin(FUNNEL_STAGES[2:]).sum()),
        int((valid["current_status"] == "Offer").sum()),
    ]
    st.markdown("**Pipeline Funnel** — click a stage to dig into it")
    if total_valid:
        fig = px.funnel(x=funnel_counts, y=FUNNEL_STAGES,
                        labels={"x": "Applications", "y": ""},
                        color_discrete_sequence=["#4F8CFF"])
        fig.update_traces(textinfo="value+percent initial",
                          marker={"line": {"width": 2, "color": "#16181d"}},
                          customdata=FUNNEL_STAGES)
        style_fig(fig, 360)
        ev = st.plotly_chart(fig, width="stretch", on_select="rerun",
                             selection_mode="points", key="funnel_chart")
        cum_oa, cum_int, cum_off = funnel_counts[1], funnel_counts[2], funnel_counts[3]
        conv = []
        if funnel_counts[0] and cum_oa:
            conv.append(f"Applied → Assessment: **{100 * cum_oa / funnel_counts[0]:.0f}%**")
        if cum_oa and cum_int:
            conv.append(f"Assessment → Interview: **{100 * cum_int / cum_oa:.0f}%**")
        if cum_int and cum_off:
            conv.append(f"Interview → Offer: **{100 * cum_off / cum_int:.0f}%**")
        if conv:
            st.caption("Conversion: " + " · ".join(conv))
        stage = consume_selection("funnel_chart", ev, labels=FUNNEL_STAGES)
        if stage is not None:
            k = FUNNEL_STAGES.index(stage) if stage in FUNNEL_STAGES else 0
            stage_rows = valid[valid["current_status"].isin(FUNNEL_STAGES[k:])] if k else valid
            if not stage_rows.empty:
                inline_insights(f"Pipeline stage: {stage} and beyond", stage_rows)
    else:
        st.info("No valid applications yet.")

    st.markdown("**Application Velocity** (color = status)")
    if view.empty:
        st.info("No applications match the current filters.")
    else:
        granularity = st.radio("Group by", ["Week", "Month"], horizontal=True, key="gran")
        fmt = "%Y-%m-%d"
        if granularity == "Week":
            bucket = view["application_date"].dt.to_period("W-SUN").dt.start_time.dt.strftime(fmt)
            xlabel = "Week starting"
        else:
            bucket = view["application_date"].dt.to_period("M").dt.start_time.dt.strftime(fmt)
            xlabel = "Month"
        velocity = (view.assign(_bucket=bucket)
                    .groupby(["_bucket", "current_status"]).size().reset_index(name="count"))
        # Force a stable stack order (Applied → … → Ghosted) in every bucket, so the
        # same colour sits in the same place across the whole chart.
        _rank = {status: i for i, status in enumerate(STATUS_ORDER)}
        velocity["_rank"] = velocity["current_status"].map(_rank).fillna(len(_rank))
        velocity = velocity.sort_values(["_bucket", "_rank"])
        totals = velocity.groupby("_bucket")["count"].sum()
        fig = px.bar(velocity, x="_bucket", y="count", color="current_status",
                     custom_data=["_bucket"],
                     color_discrete_map=STATUS_COLORS,
                     category_orders={
                         "_bucket": sorted(velocity["_bucket"].unique()),
                         "current_status": list(STATUS_ORDER),
                     },
                     labels={"_bucket": xlabel, "count": "Applications", "current_status": "Status"})
        fig.update_layout(barmode="stack", legend_traceorder="normal")
        style_fig(fig, 360)
        _lift = max(totals.max() * 0.04, 0.4)
        for x, total in totals.items():
            fig.add_annotation(x=x, y=total + _lift, text=f"<b>{total}</b>",
                               showarrow=False, font=dict(color="#9db9ff", size=12))
        fig.update_yaxes(range=[0, totals.max() + _lift * 4])
        ev = st.plotly_chart(fig, width="stretch", on_select="rerun",
                             selection_mode="points", key="velocity_chart")
        bucket_id = consume_selection("velocity_chart", ev)
        if bucket_id is not None:
            bucket_rows = view[view["application_date"].dt.strftime(fmt) == str(bucket_id)]
            if not bucket_rows.empty:
                inline_insights(f"Week starting {bucket_id}", bucket_rows)

    st.subheader("Platform, Job Type & Industry")
    perf = platform_performance(valid)
    platform_counts = valid.groupby("source_platform").size().reset_index(name="count")
    left, right = st.columns([1, 1.4])
    with left:
        st.markdown("**Platform Donut**")
        if not platform_counts.empty:
            fig = px.pie(platform_counts, names="source_platform", values="count", hole=0.58,
                         color="source_platform",
                         color_discrete_map={k: v for k, v in PLATFORM_COLORS.items()
                                             if k in set(platform_counts["source_platform"])})
            style_pie(fig, 370)
            ev = st.plotly_chart(fig, width="stretch", on_select="rerun",
                                 selection_mode="points", key="platform_pie")
            plat = consume_selection("platform_pie", ev,
                                     labels=platform_counts["source_platform"].tolist())
            if plat:
                plat_rows = view[view["source_platform"] == str(plat)]
                if not plat_rows.empty:
                    inline_insights(f"Platform: {plat}", plat_rows)
    with right:
        st.markdown("**Platform Performance** (where you actually get responses)")
        if not perf.empty:
            st.dataframe(
                perf.rename(columns={"source_platform": "Platform"}),
                width="stretch", hide_index=True,
                column_config={
                    "Response %": st.column_config.NumberColumn("Response %", format="%.1f%%"),
                    "Interview %": st.column_config.NumberColumn("Interview %", format="%.1f%%"),
                },
            )

    jt_counts = valid.groupby("job_type").size().reset_index(name="count").sort_values("count", ascending=False)
    ind_counts = valid.groupby("industry").size().reset_index(name="count").sort_values("count")
    left, right = st.columns(2)
    with left:
        st.markdown("**Job Type Mix** (FT · Internship · Protege · Contract) — click a slice")
        if not jt_counts.empty:
            _jt_short = {"Graduate Programme / Trainee": "Graduate / Trainee",
                         "Part-Time / Casual": "Part-Time"}
            jt_plot = jt_counts.copy()
            jt_plot["_label"] = jt_plot["job_type"].map(lambda v: _jt_short.get(v, v))
            fig = px.pie(jt_plot, names="_label", values="count", hole=0.58,
                         custom_data=["job_type"], color_discrete_sequence=COLORWAY)
            style_pie(fig, 370)
            ev = st.plotly_chart(fig, width="stretch", on_select="rerun",
                                 selection_mode="points", key="jt_pie")
            jt = consume_selection("jt_pie", ev,
                                   labels=jt_counts["job_type"].tolist())
            if jt:
                jt_rows = view[view["job_type"].fillna("") == str(jt)]
                if not jt_rows.empty:
                    inline_insights(f"Job type: {jt}", jt_rows)
    with right:
        st.markdown("**Industries Applied To** — click a bar")
        if not ind_counts.empty:
            fig = px.bar(ind_counts, x="count", y="industry", orientation="h",
                         color_discrete_sequence=["#4F8CFF"],
                         custom_data=["industry"],
                         labels={"count": "Applications", "industry": ""})
            fig.update_traces(text=ind_counts["count"], textposition="outside", cliponaxis=False)
            style_fig(fig, 320)
            ev = st.plotly_chart(fig, width="stretch", on_select="rerun",
                                 selection_mode="points", key="ind_chart")
            ind = consume_selection("ind_chart", ev,
                                    labels=ind_counts["industry"].tolist())
            if ind:
                ind_rows = view[view["industry"].fillna("") == str(ind)]
                if not ind_rows.empty:
                    inline_insights(f"Industry: {ind}", ind_rows)

    # Seniority mix — rows without a parsed role can't have a seniority tier, so exclude
    # them (otherwise "Not Specified" misleadingly dominates the chart).
    sen_source = valid[valid["role_title"].fillna("").astype(str).str.strip() != ""]
    sen_excluded = len(valid) - len(sen_source)
    sen_counts = sen_source.groupby("seniority_level").size().reset_index(name="count").sort_values("count")
    if not sen_counts.empty:
        st.markdown("**Seniority Mix** — click a bar")
        if sen_excluded:
            st.caption(f"{sen_excluded} application(s) whose email didn't name the role are "
                       f"excluded (their seniority can't be determined).")
        fig = px.bar(sen_counts, x="count", y="seniority_level", orientation="h",
                     custom_data=["seniority_level"],
                     labels={"count": "Applications", "seniority_level": ""})
        n_bars = len(sen_counts)
        fig.update_traces(
            marker=dict(color=[COLORWAY[i % len(COLORWAY)] for i in range(n_bars)]),
            text=sen_counts["count"], textposition="outside", cliponaxis=False)
        style_fig(fig, 260)
        fig.update_layout(showlegend=False)
        ev = st.plotly_chart(fig, width="stretch", on_select="rerun",
                             selection_mode="points", key="sen_chart")
        sen = consume_selection("sen_chart", ev,
                                labels=sen_counts["seniority_level"].tolist())
        if sen:
            sen_rows = valid[valid["seniority_level"].fillna("") == str(sen)]
            if not sen_rows.empty:
                inline_insights(f"Seniority: {sen}", sen_rows)

    comp_df = company_summary(valid)
    left, right = st.columns(2)
    with left:
        st.markdown("**Company Pipeline Summary** (top 20)")
        if not comp_df.empty:
            st.dataframe(
                comp_df.rename(columns={"company_name": "Company",
                                        "LastUpdate": "Last Update"}),
                width="stretch", hide_index=True,
                column_config={
                    "Last Update": st.column_config.DateColumn(format="YYYY-MM-DD"),
                },
            )
        else:
            st.caption("No companies yet.")

    render_company_dupes(valid)
    render_reports(stale_count=stale_count)

    st.divider()
    head_col, btn_col = st.columns([3, 1])
    with head_col:
        st.subheader(f"Interactive tracker ({len(view)} shown of {total_valid})")
    with btn_col:
        url_str = valid["job_url"].astype(str)
        thin = int(((url_str != "") & (url_str != "nan")
                    & (valid["job_description_snippet"].str.len() < 240)
                    & (url_str.apply(lambda u: not is_broken_job_url(u)))).sum())
        st.button(f"Scrape missing JDs ({thin})",
                  help="Re-scrapes the posting links already stored on your applications to fill "
                       "in job descriptions. Broken/logo links are skipped.",
                  disabled=thin == 0, width="stretch", key="backfill_btn")

    if st.session_state.get("backfill_btn"):
        run_jd_backfill(valid)

    url_vals = valid["job_url"].astype(str)
    broken_n = int(url_vals.apply(lambda u: is_broken_job_url(u)).sum())
    if broken_n:
        st.caption(f"{broken_n} posting link(s) open a JobStreet logo instead of the job page — "
                   "these are hidden from the table.")

    render_tracker(view, valid)

    st.download_button(
        label="Export filtered view to CSV",
        data=view.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"filtered_applications_{datetime.now():%Y%m%d_%H%M}.csv",
        mime="text/csv",
    )



def render_company_dupes(valid: pd.DataFrame):
    """Where the same employer gets multiple applications — an awareness + consolidation view."""
    if valid.empty:
        return
    g = valid.groupby("company_name").agg(
        Applications=("thread_id", "count"),
        Roles=("role_title", lambda s: " · ".join(sorted({str(x) for x in s if str(x) not in ("", "nan")}))[:200]),
    ).reset_index()
    g = g[g["Applications"] > 1].sort_values("Applications", ascending=False)
    if g.empty:
        return
    with st.expander(f"🏢 Employers you've applied to multiple times ({len(g)})", expanded=False):
        st.caption("More applications ≠ better odds at the same firm. Pick the strongest fit, "
                   "tailor it hard, and chase a referral or recruiter ping for that one.")
        st.dataframe(g, width="stretch", hide_index=True)


def render_activity_feed():
    """A single scrollable timeline: status changes (from status_history) + recent updates."""
    if not os.path.exists(DB_PATH):
        return
    feed = load_activity_cached(DB_PATH, mtime_of(DB_PATH))
    if not feed:
        return
    with st.expander(f"📜 Recent activity ({len(feed)} events)", expanded=False):
        st.caption("Status changes are logged every time you override a status, Gmail updates a "
                   "thread, or auto-ghosting fires. Recent application updates appear too.")
        for ev in feed[:30]:
            ts = esc(ev.get("ts") or "?")
            comp = esc(ev.get("company_name") or "?")
            role = esc(ev.get("role_title") or "")
            if ev.get("kind") == "status":
                frm = esc(ev.get("from_status") or "—")
                to = ev.get("to_status") or "?"
                color = STATUS_COLORS.get(to, "#9db9ff")
                line = (f"`{ts}` **{comp}** — {role}: {frm} → "
                        f"<span style='color:{color};font-weight:600'>{esc(to)}</span>")
                note = esc(ev.get("detail") or "")
                if note:
                    line += f" <span class='compact-meta'>— {note[:80]}</span>"
            else:
                stt = ev.get("current_status") or ev.get("to_status") or "?"
                color = STATUS_COLORS.get(stt, "#9db9ff")
                line = (f"`{ts}` **{comp}** — {role} "
                        f"<span style='color:{color};font-weight:600'>{esc(stt)}</span>")
            st.markdown(line, unsafe_allow_html=True)
        if len(feed) > 30:
            st.caption(f"+ {len(feed) - 30} older events")


def run_jd_backfill(valid: pd.DataFrame):
    url_col = valid["job_url"].astype(str)
    targets = valid[(url_col != "") & (url_col != "nan")
                    & (valid["job_description_snippet"].str.len() < 240)
                    & (url_col.apply(lambda u: not is_broken_job_url(u)))]
    if targets.empty:
        st.toast("All JDs with usable links are already scraped — broken/logo links are skipped "
                 "(use Look up JDs & fix broken links)", icon="✅")
        return
    n = len(targets)
    with st.status(f"🧲 Scraping {n} job posting(s) — 16 workers, 4s timeout...", expanded=True) as status:
        enriched = enricher.enrich_many(dict(zip(targets["thread_id"], targets["job_url"].astype(str))))
        updated = blocked = 0
        for tid, payload in enriched.items():
            if not payload.get("ok"):
                blocked += 1
                continue
            title = (payload.get("og_title") or "").strip()
            desc = (payload.get("og_description") or "").strip()
            combined = f"{title} — {desc}".strip(" —")
            extra = " ".join(f"[{k}: {payload[k]}]" for k in
                             ("company", "location", "salary", "employment_type") if payload.get(k))
            if extra:
                combined = f"{combined} {extra}".strip()
            if len(combined) < 40:
                blocked += 1
                continue
            storage.update_snippet(tid, combined)
            ld_company = (payload.get("company") or "").strip()
            og_role = og_company = ""
            if " - " in title:
                parts = [p.strip() for p in title.split(" - ") if p.strip()]
                if len(parts) >= 2:
                    og_role, og_company = parts[0], parts[1]
            if (ld_company or (og_company and og_company.lower() not in ("jobstreet", "jobstreet.com", "linkedin"))):
                storage.refine_og_company_role(tid, ld_company or og_company, og_role)
            updated += 1
        status.update(label=f"✅ {updated} JD(s) enriched · {blocked} blocked by the platform",
                      state="complete")
    st.cache_data.clear()
    st.rerun()



def render_noise_editor(noise_view: pd.DataFrame):
    with st.expander(
            f"🚫 Filtered Out Noise / Spam ({len(noise_view)} rows) — tick Recover to rescue a real application",
            expanded=False):
        editor_df = noise_view[["thread_id", "application_date", "latest_subject",
                                "company_name", "source_platform", "filter_reason"]].copy()
        editor_df["Recover"] = False
        editor_df = editor_df.rename(columns={
            "application_date": "Date", "latest_subject": "Subject",
            "company_name": "Company", "source_platform": "Platform",
            "filter_reason": "Filter Reason"})
        edited = st.data_editor(
            editor_df, width="stretch", hide_index=True, height=320, key="noise_editor",
            column_config={
                "thread_id": st.column_config.Column("Thread", disabled=True),
                "Date": st.column_config.DateColumn("Date", disabled=True, format="YYYY-MM-DD"),
                "Subject": st.column_config.TextColumn("Subject", disabled=True),
                "Company": st.column_config.TextColumn("Company", disabled=True),
                "Platform": st.column_config.Column("Platform", disabled=True),
                "Filter Reason": st.column_config.Column("Filter Reason", disabled=True),
                "Recover": st.column_config.CheckboxColumn(
                    "Recover", help="Mark as a valid application"),
            },
        )
        recovered = edited[edited["Recover"] == True]  # noqa: E712
        if not recovered.empty:
            for tid in recovered["thread_id"]:
                storage.set_valid(tid, True, "manual recovery")
            st.toast(f"Recovered {len(recovered)} row(s) into your tracker")
            st.cache_data.clear()
            st.rerun()


def _pget(p, attr, default=None):
    """Streamlit selection points may be dicts or objects — read safely from both."""
    try:
        if isinstance(p, dict):
            return p.get(attr, default)
        return getattr(p, attr, default)
    except Exception:
        return default


def _selection_value(event, labels=None) -> object | None:
    """Extract the clicked value from a plotly selection event.

    Priority: customdata → label/name → y (horizontal bars) → x; with a `labels`
    list, `point_index` maps back to the exact category that was clicked.
    """
    try:
        pts = (event.selection.points
               if event is not None and hasattr(event, "selection") else [])
        if not pts:
            return None
        p = pts[0]
        cd = _pget(p, "customdata")
        if cd is not None:
            if isinstance(cd, (list, tuple)) and len(cd):
                return cd[0]
            if isinstance(cd, str) and cd:
                return cd
        for attr in ("label", "name"):
            v = _pget(p, attr)
            if isinstance(v, str) and v:
                return v
        if labels:
            idx = _pget(p, "point_index", _pget(p, "point_number"))
            try:
                if idx is not None and 0 <= int(idx) < len(labels):
                    return labels[int(idx)]
            except (TypeError, ValueError):
                pass
        for attr in ("y", "x"):
            v = _pget(p, attr)
            if v not in (None, ""):
                return v
    except Exception:
        return None
    return None


def consume_selection(chart_key: str, event, labels=None) -> object | None:
    """Return the newly-clicked value once (None otherwise)."""
    val = _selection_value(event, labels)
    if val is None:
        return None
    key = str(val)
    if st.session_state.get(f"{chart_key}_consumed") == key:
        return None
    st.session_state[f"{chart_key}_consumed"] = key
    return val


def inline_insights(title: str, rows: pd.DataFrame, _dialog_note: str = ""):
    """Chart-click drill-down: an inline expander with a summary + the applications involved."""
    if rows is None or rows.empty:
        st.toast("No matching applications to show", icon="ℹ️")
        return
    with st.expander(f"🔍 Full insights — {title}", expanded=True):
        progressed = int(rows["current_status"].isin(
            ["Assessment / OA", "Interview", "Offer"]).sum())
        rejected = int((rows["current_status"] == "Rejected").sum())
        stats = [f"**{len(rows)}** application(s)"]
        stats.append(f"**{progressed}** progressed · **{rejected}** rejected")
        st.caption(" · ".join(stats))
        shown = rows.sort_values("application_date", ascending=False).head(12)
        for _, row in shown.iterrows():
            chips = [f"<span class='compact-meta'>{esc(row['current_status'])} · "
                     f"{esc(row.get('job_type') or '—')}</span>"]
            links = []
            glink = safe_url(row.get("gmail_link", ""))
            jurl, _ = safe_post_link(row.get("job_url", ""))
            if glink:
                links.append("[📧 Gmail ↗](" + glink + ")")
            if jurl:
                links.append("[🔗 posting ↗](" + jurl + ")")
            st.markdown(
                f"**{esc(row['company_name'])}** — {esc(role_display(row['role_title']))}  \n"
                + " ".join(chips) + ("  \n" + " · ".join(links) if links else ""),
                unsafe_allow_html=True)
        if len(rows) > 12:
            st.caption(f"+ {len(rows) - 12} more — see the Interactive Tracker below.")


def detail_body(row, key_prefix: str = "dlg"):
    """Full application detail: JD text, requirements, status, notes — for dialogs."""
    snippet = str(row.get("job_description_snippet", "") or "")
    snippet = snippet.replace("%str_to_replace_open_tracking%", "")
    snippet = "" if snippet.lower() in ("", "nan") else snippet.strip()

    bcol, _ = st.columns([0.16, 4.2], vertical_alignment="center")
    with bcol:
        if st.button("← Back", key=f"{key_prefix}_back", width="stretch",
                     help="Close this pop-up and deselect the row"):
            st.session_state["_detail_tids"] = None
            st.session_state["_table_seq"] = int(st.session_state.get("_table_seq", 0)) + 1
            st.rerun()

    st.markdown(f"### {esc(row['company_name'])} — {esc(role_display(row['role_title']))}")
    st.markdown(
        f"<span class='compact-meta'>{esc(row['source_platform'])} · "
        f"{esc(row.get('job_type') or '—')} · "
        f"{esc(row.get('seniority_level') or '—')} · {esc(row.get('industry') or '—')} · "
        f"applied {row['application_date']:%d %b %Y} · "
        f"status: {esc(row['current_status'])}</span>",
        unsafe_allow_html=True)

    c1, c2 = st.columns([1.15, 1])
    with c1:
        st.markdown("**Fetched Job Description**")
        if snippet:
            st.markdown(f"<div class='jd-block'>{esc(snippet[:1400])}</div>",
                        unsafe_allow_html=True)
        elif safe_post_link(row.get("job_url"))[0]:
            st.markdown(f"🔗 [Open original job posting ↗]({safe_post_link(row.get('job_url'))[0]})")
            st.caption("No description cached for this application.")
        else:
            if safe_post_link(row.get("job_url"))[1]:
                st.caption("⚠️ The stored posting link was a JobStreet logo/tracking link, not the "
                           "job page — run **🔎 Look up JDs & fix broken links** above to replace it.")
            st.caption("No description or link available for this application.")
        gmail = safe_url(row.get("gmail_link", ""))
        if gmail:
            st.markdown(f"📧 [Open Gmail thread ↗]({gmail})")
    with c2:
        current = row["current_status"]
        idx = STATUSES.index(current) if current in STATUSES else 0
        new_status = st.selectbox("Status override", options=STATUSES, index=idx,
                                  key=f"{key_prefix}_status",
                                  label_visibility="collapsed")
        if new_status != current:
            storage.update_status(row["thread_id"], new_status)
            st.toast(f"{row['company_name']} → {new_status}")
            st.cache_data.clear()

        render_notes_history(row, key_prefix=f"{key_prefix}_nh_{str(row['thread_id'])[-10:]}")

        with st.expander("✏️ Correct company / role", expanded=False):
            st.caption("Fix rows where a platform name (JobStreet, MyWorkday, myHR, …) was stored "
                       "instead of the real employer, or where the role was parsed as a sentence.")
            ec1, ec2 = st.columns(2)
            edit_company = ec1.text_input("Company", value=str(row.get("company_name") or ""),
                                          key=f"{key_prefix}_eco")
            edit_role = ec2.text_input("Role", value=str(row.get("role_title") or ""),
                                       key=f"{key_prefix}_ero")
            if st.button("💾 Save company / role", key=f"{key_prefix}_save_cr",
                         width="stretch"):
                if storage.update_company_role(row["thread_id"], edit_company, edit_role):
                    st.cache_data.clear()
                    st.toast("Company / role updated")
                    st.rerun()
                else:
                    st.warning("Both company and role must be non-empty.")

        with st.expander("↩ Undo last status change", expanded=False):
            try:
                hist = storage.get_status_history(row["thread_id"])
            except Exception:
                hist = []
            if hist:
                last = hist[0]
                frm = last.get("from_status")
                to = last.get("to_status")
                if frm and to and frm != to:
                    st.caption(f"Last change: **{frm} → {to}** ({last.get('changed_at')})")
                    if st.button(f"↩ Revert to {frm}", key=f"{key_prefix}_undo"):
                        storage.update_status(row["thread_id"], frm, note=f"undo — reverted from {to}")
                        st.toast(f"Reverted to {frm}")
                        st.cache_data.clear()
                else:
                    st.caption("Last event isn't a reversible status change.")
            else:
                st.caption("No status changes recorded for this application yet.")

        with st.expander("🚫 Not a real application?", expanded=False):
            st.caption("Mark this thread as noise if it is not an application at all (e.g. a "
                       "marketing email that slipped through). It will stay filtered out even "
                       "if Gmail syncs the thread again, and you can undo it from the noise "
                       "table in the sidebar.")
            if st.button("🚫 Mark as noise", key=f"{key_prefix}_noise", width="stretch"):
                storage.set_valid(row["thread_id"], False, "manual: marked as noise")
                st.cache_data.clear()
                st.toast(f"{row['company_name']} marked as noise")
                st.rerun()


@st.dialog("🔍 Application details", width="large")
def detail_modal(rows: pd.DataFrame, suffix: str = "a"):
    rows = rows.sort_values("application_date", ascending=False)
    if len(rows) > 1:
        labels = [f"{r['company_name'][:30]} — {role_display(r['role_title'])[:44]} · {r['current_status']}"
                  for _, r in rows.iterrows()]
        chosen_label = st.selectbox("Application", labels, key=f"dlg_sel_{suffix}")
        chosen = rows.iloc[labels.index(chosen_label)]
    else:
        chosen = rows.iloc[0]
    detail_body(chosen, key_prefix=f"{suffix}_dlg")


def maybe_open_dialog(valid: pd.DataFrame):
    """Open the detail pop-up once, right after a row was clicked (one-shot flag). Because it
    runs inside the tracker fragment, it opens instantly without re-rendering the whole app."""
    if not st.session_state.pop("_dialog_pending", False):
        return
    tids = st.session_state.get("_detail_tids")
    if not tids:
        return
    rows = valid[valid["thread_id"].astype(str).isin([str(t) for t in tids])]
    if not rows.empty:
        detail_modal(rows, suffix="auto")


@st.fragment
def render_tracker(view: pd.DataFrame, valid: pd.DataFrame):
    """Tracker table + instant pop-up. The whole section is a fragment, so clicking a row only
    re-renders this section (the pop-up appears immediately, no full-app rerun)."""
    seq = int(st.session_state.get("_table_seq", 0))
    table = view.rename(columns={
        "company_name": "Company", "role_title": "Role", "source_platform": "Platform",
        "current_status": "Status", "application_date": "Application Date",
        "last_updated": "Last Update", "gmail_link": "Gmail Thread",
        "job_type": "Type",
    }).copy()
    table["Gmail Thread"] = table["Gmail Thread"].replace({"": None, "nan": None})
    table["Role"] = table["Role"].fillna("").map(role_display)

    today_n = pd.Timestamp.today().normalize()
    ref = table["Last Update"].fillna(table["Application Date"])
    waiting = (today_n - pd.to_datetime(ref).dt.normalize()).dt.days.clip(lower=0)
    table["Waiting"] = waiting.where(ref.notna())

    event = st.dataframe(
        table[TABLE_COLUMNS],
        width="stretch", hide_index=True, height=420, key=f"table_{seq}",
        on_select="rerun", selection_mode="single-row",
        column_config={
            "Application Date": st.column_config.DateColumn(format="YYYY-MM-DD"),
            "Last Update": st.column_config.DateColumn(format="YYYY-MM-DD"),
            "Waiting": st.column_config.NumberColumn("Waiting (days)", format="%d",
                                                     help="Days since the last update (or the "
                                                          "application date)"),
            "Gmail Thread": st.column_config.LinkColumn(
                "Gmail Thread", display_text="Open in Gmail ↗",
                validate=r"^https://mail\.google\.com/.*",
            ),
        },
    )

    sel_rows = event.selection.rows if hasattr(event, "selection") else []
    if sel_rows:
        st.session_state["_detail_tids"] = [str(view.iloc[sel_rows[0]]["thread_id"])]
        st.session_state["_dialog_pending"] = True

    maybe_open_dialog(valid)


def render_status_history_panel(thread_id: str, max_items: int = 10):
    """A compact timeline of status changes for one application (reads status_history)."""
    try:
        hist = storage.get_status_history(thread_id)
    except Exception:
        hist = []
    if not hist:
        st.caption("No status changes recorded yet — overrides & auto-ghosting will appear here.")
        return
    for ev in hist[:max_items]:
        changed = (ev.get("changed_at") or "?")[:16].replace("T", " ")
        frm = esc(ev.get("from_status") or "—")
        to = ev.get("to_status") or "—"
        note = esc(ev.get("note") or "")
        c_to = STATUS_COLORS.get(to, "#9db9ff")
        suffix = f" — _{note}_" if note else ""
        st.markdown(
            f"`{esc(changed)}` {frm} → "
            f"<span style='color:{c_to};font-weight:600'>{esc(to)}</span>{suffix}",
            unsafe_allow_html=True)
    if len(hist) > max_items:
        st.caption(f"+ {len(hist) - max_items} older change(s)")


def render_notes_editor(thread_id: str, current_note: str, key_prefix: str = "note"):
    """Per-application persistent notes box (saved to the applications.notes column)."""
    st.markdown("**🗒 My notes**")
    val = st.text_area(
        "Notes", value=current_note or "", key=f"{key_prefix}_notes",
        label_visibility="collapsed", height=84,
        placeholder="E.g. referral from Jenny · sent follow-up 12 Sep · recruiter call Friday")
    if st.button("💾 Save note", key=f"{key_prefix}_save_note"):
        storage.update_note(thread_id, val)
        st.cache_data.clear()
        st.toast("Note saved")


def render_notes_history(row, key_prefix: str):
    with st.expander("🗒 Notes & 📜 status history", expanded=False):
        render_notes_editor(row["thread_id"], row.get("notes") or "", key_prefix=key_prefix)
        st.divider()
        st.markdown("**📜 Status history**")
        render_status_history_panel(row["thread_id"])


def _row_wait_days(row) -> int | None:
    ref = row.get("last_updated")
    if ref is None or pd.isna(ref):
        ref = row.get("application_date")
    if ref is None or pd.isna(ref):
        return None
    try:
        return (pd.Timestamp.today().normalize() - pd.Timestamp(ref).normalize()).days
    except Exception:
        return None


def _status_pill(status: str) -> str:
    color = STATUS_COLORS.get(status, "#9db9ff")
    return (f"<span class='fit-chip' style='background:rgba(0,0,0,0);border:1px solid "
            f"{color};color:{color}'>{esc(status)}</span>")


def _followup_draft(row, tier: str) -> str:
    company = (row.get("company_name") or "").strip()
    role = (row.get("role_title") or "").strip()
    date = row.get("application_date")
    date_s = f"{pd.Timestamp(date):%d %b %Y}" if pd.notna(date) else ""
    if tier == "nudge":
        return (
            f"Subject: Following up on my application — {role}\n\n"
            f"Hi {company} team,\n\n"
            f"I applied for the {role} position on {date_s} and wanted to gently follow up. "
            f"I remain very interested in the opportunity and would welcome the chance to "
            f"discuss how my background in maths & statistics, Python/SQL analysis and "
            f"modelling could add value.\n\n"
            f"I'm happy to provide any additional information, a portfolio walkthrough, or "
            f"references at your convenience.\n\n"
            f"Thanks for your time,\nFaris Erhan")
    return (
        f"Subject: {role} at {company} — closing the loop\n\n"
        f"Hi {company} team,\n\n"
        f"I applied for the {role} role on {date_s}. I understand you may have moved on or "
        f"filled the position. If the role is still open, I'd be glad to resurface my "
        f"application — and if not, I'd really appreciate brief feedback on my candidacy.\n\n"
        f"Either way, I'd love to stay on your radar for future opportunities.\n\n"
        f"Thanks,\nFaris Erhan")


def _row_links_html(row) -> str:
    links = []
    glink = safe_url(row.get("gmail_link", ""))
    jurl, _ = safe_post_link(row.get("job_url", ""))
    if glink:
        links.append("[📧 thread ↗]({})".format(glink))
    if jurl:
        links.append("[🔗 posting ↗]({})".format(jurl))
    return " · ".join(links)


def render_followup_queue(valid: pd.DataFrame):
    """Actionable 'who to chase next' queue: Applied-but-quiet roles + ghosted threads."""
    queue = []
    for _, r in valid.iterrows():
        stt = r["current_status"]
        wait = _row_wait_days(r)
        if wait is None:
            continue
        if stt == "Applied" and wait >= followup_days():
            queue.append((r, "nudge", wait))
        elif stt == "Ghosted":
            queue.append((r, "ghost", wait))
    if not queue:
        return
    queue.sort(key=lambda q: q[2], reverse=True)
    n_nudge = sum(1 for _, t, _ in queue if t == "nudge")
    n_ghost = len(queue) - n_nudge
    label = (f"📬 Follow-up queue — {n_nudge} to nudge · {n_ghost} ghosted to revive/close")
    shown = queue[:6]
    drafts = {}
    with st.expander(label, expanded=False):
        st.caption("Polite nudges are most effective 5–10 days after applying. Ghosted threads "
                   "(>21 days, no reply) are worth one last closing-the-loop email — then let them go.")
        for row, tier, wait in shown:
            with st.container(border=True):
                meta, actions = st.columns([3, 1.05], vertical_alignment="center")
                with meta:
                    st.markdown(
                        f"**{esc(row['company_name'])}** — {esc(role_display(row['role_title']))}  \n"
                        + " ".join([
                            _status_pill("Nudge" if tier == "nudge" else "Ghosted"),
                            f"<span class='compact-meta'>waited {wait}d</span>",
                        ]),
                        unsafe_allow_html=True)
                    links = _row_links_html(row)
                    if links:
                        st.markdown(links)
                with actions:
                    if tier == "nudge":
                        if st.button("👻 Ghost it", key=f"fu_ghost_{row['thread_id']}",
                                     help="Mark as ghosted (no update) and drop it from the nudge list"):
                            storage.update_status(row["thread_id"], "Ghosted",
                                                  note="manually closed — follow-up sent / no reply")
                            st.cache_data.clear()
                            st.rerun()
                    else:
                        c = st.columns(2)
                        if c[0].button("↩", key=f"fu_reopen_{row['thread_id']}",
                                       help="Re-open as Applied (keeps original date)"):
                            storage.update_status(row["thread_id"], "Applied",
                                                  note="re-opened — recruiter replied after all")
                            st.cache_data.clear()
                            st.rerun()
                        if c[1].button("❌", key=f"fu_rej_{row['thread_id']}",
                                       help="Mark as Rejected"):
                            storage.update_status(row["thread_id"], "Rejected",
                                                  note="manually marked rejected")
                            st.cache_data.clear()
                            st.rerun()
                with st.expander(f"✍️ Draft follow-up email"):
                    draft = drafts.get(str(row["thread_id"])) or _followup_draft(row, tier)
                    st.code(draft, language=None)
        if len(queue) > 6:
            st.caption(f"+ {len(queue) - 6} more — select their rows in the tracker to update status.")


# ------------------------------------------------------------------ main

_MODULE_MTIMES = {}


def _ensure_fresh_modules():
    """Reload any support module whose file changed since this process loaded it.

    Streamlit only hot-reloads the main script — sibling modules (main, gmail_fetcher, ai, …)
    stay cached in sys.modules, which used to make the sync crash or behave like an old version
    after code edits until the server was restarted. Comparing each module's import mtime with
    the file on disk detects staleness and reloads it in place. Names imported with `from x
    import y` are re-bound afterwards so they cannot stay pinned to a pre-reload object.
    """
    global STATUSES
    modules = {}
    for name in ("main", "storage", "parser", "gmail_fetcher", "enricher",
                 "warehouse", "warehouse_etl", "warehouse_queries", "bi_export"):
        mod = sys.modules.get(name)
        if mod is None or not getattr(mod, "__file__", None):
            continue
        modules[name] = mod
    reloaded = False
    for name, mod in modules.items():
        try:
            m = os.path.getmtime(mod.__file__)
        except OSError:
            continue
        if _MODULE_MTIMES.get(name) != m:
            try:
                importlib.reload(mod)
            except Exception:
                continue  # never let a reload break the dashboard
            _MODULE_MTIMES[name] = os.path.getmtime(mod.__file__)
            reloaded = True
    if reloaded:
        STATUSES = parser.STATUSES


def require_password() -> bool:
    """Optional password gate for LAN / tunnel exposure.

    Set DASH_PASSWORD to require a password before anything renders (the dashboard binds to
    0.0.0.0 and may be tunneled publicly). No password set = unchanged local behaviour.
    """
    expected = os.environ.get("DASH_PASSWORD", "").strip()
    if not expected or st.session_state.get("_authed"):
        return True
    st.title("🎯 Job Application Suite")
    st.caption("🔒 This dashboard is protected — `DASH_PASSWORD` is set on the host.")
    with st.form("auth_form"):
        entered = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Unlock", type="primary")
    if submitted:
        if hmac.compare_digest(entered or "", expected):
            st.session_state["_authed"] = True
            st.rerun()
        else:
            st.error("Wrong password.")
    st.stop()
    return False


def main():
    _ensure_fresh_modules()
    try:
        storage.init_db()
    except Exception:
        pass  # migrations are additive & idempotent — never block the dashboard on them

    require_password()

    # Self-heal: a "running" flag only has meaning inside the single run that starts the work.
    # If a previous sync was aborted (browser closed mid-run, crash, tunnel drop), the flag
    # can be left True in the session, which would disable the buttons forever. A fresh run never
    # legitimately starts with it True — so clear it.
    st.session_state["sync_running"] = False

    st.title("🎯 Job Application Suite")
    header_cap = st.caption("Personal analytics for your job hunt — loading data…")
    sync_zone = st.empty()  # live sync progress is rendered here (main column, always visible)

    with st.sidebar:
        st.header("Sync", icon=":material/sync:")
        running = st.session_state.get("sync_running", False)
        force_full = st.toggle("Force full re-sync", value=False,
                               help="Re-evaluates the whole mailbox with the current filter rules "
                                    "— every cached email is re-parsed offline and any missing "
                                    "body is re-downloaded once, so improved classification "
                                    "recovers older false negatives.")
        if st.button("Sync Gmail now", type="primary", disabled=running, width="stretch",
                     icon=":material/download:",
                     help="Fetches new emails, filters the noise, and refreshes every report"):
            run_sync(force_full, zone=sync_zone)
        summary = st.session_state.get("last_sync_summary")
        if summary:
            st.caption(f"Last sync: **{summary['elapsed']:.1f}s** · found **{summary.get('found', 0)}** · "
                       f"{summary['inserted']} new · {summary['updated']} updated · "
                       f"{summary['garbage']} noise")
            deep = int(summary.get("bodies_cached", 0)) + int(summary.get("bodies_refetched", 0))
            if deep:
                st.caption(f"🔎 Deep re-parse: {summary.get('bodies_cached', 0)} cached · "
                           f"{summary.get('bodies_refetched', 0)} re-downloaded")
            if summary.get("truncated"):
                st.warning("Search hit the message cap — some mailbox items may be missing. "
                           "Raise GMAIL_MAX_RESULTS and re-run the sync.")

        with st.expander("🛠 Maintenance", expanded=False):
            m1, m2 = st.columns(2)
            if m1.button("👻 Ghost stale >{}d".format(stale_days()), width="stretch",
                         help=f"Persist the auto-ghosting rule: Applied rows with no update for "
                              f">{stale_days()} days become Ghosted in the database"):
                persist_ghosting()
            if st.button("🧹 Fix platform company names", width="stretch",
                         help="Repair legacy rows whose company/role was stored as JobStreet / "
                              "MyWorkday / myHR / LinkedIn. Only strict improvements are applied "
                              "(a platform name replaced by a real employer)."):
                try:
                    n = storage.refine_platform_rows()
                except Exception as exc:
                    st.error(f"Cleanup failed: {exc}")
                else:
                    st.cache_data.clear()
                    st.toast(f"Repaired {n} row(s)" if n else
                             "Nothing to repair — company names are already clean")
                    st.rerun()
            if st.button("🔧 Repair & recover (audit)", width="stretch",
                         help="Re-checks every stored email with the current filter rules: marks "
                              "verification/account and platform-digest false positives as noise, "
                              "fixes company/role names from the subject/JD, and recovers real "
                              "applications that were filtered out. Backs up first; safe to "
                              "re-run any time (idempotent)."):
                try:
                    with st.spinner("Auditing every stored email..."):
                        _repairs, counts = repair.run(apply=True)
                except Exception as exc:
                    st.error(f"Repair failed: {exc}")
                else:
                    st.cache_data.clear()
                    st.toast(f"Audited {counts['rows']} row(s) — {counts['invalidated']} noise "
                             f"removed · {counts['recovered']} recovered · "
                             f"{counts['company']} companies · {counts['role']} roles fixed")
                    st.rerun()
            if st.button("💾 Backup data", width="stretch",
                         help="Snapshot applications.db + job_tracker.csv + sync state + CV "
                              "profile into backups/<timestamp>/ — safe before big "
                              "syncs or filter changes."):
                try:
                    folder = storage.backup_all()
                    st.toast(f"Backup saved to {folder}", icon="💾")
                except Exception as exc:
                    st.error(f"Backup failed: {exc}")

        with st.expander("⚙️ Rules", expanded=False):
            r1, r2 = st.columns(2)
            r1.number_input("Ghost after (days)", min_value=3, max_value=120,
                            value=int(stale_days()), key="stale_days", help="Applied rows older than "
                            "this with no update count as auto-ghosted (KPI + follow-up).")
            r2.number_input("Follow up after (days)", min_value=1, max_value=60,
                            value=int(followup_days()), key="followup_days",
                            help="Applied-but-silent roles enter the Follow-up queue after this many "
                                 "days.")

        st.header("🔎 Filters")
        upload = st.file_uploader("Upload CSV (optional)", type=["csv"], key="_upload")
        raw, source_label = resolve_source()
        if raw is None:
            st.error("No `applications.db`, `job_tracker.csv`, or upload found. "
                     "Click **▶ Sync Gmail Now** to build the database.")
            show_noise = False
            valid = pd.DataFrame()
        else:
            st.caption(f"Loaded: **{source_label}**")
            df = normalize(raw)
            show_noise = st.toggle("Show Filtered Out Noise / Spam", value=False)
            applied_only = df[df["current_status"] != "Saved / Planning"]
            valid = applied_only[applied_only["is_valid"]].copy()
            stale_count = int(valid.apply(compute_stale, axis=1).sum())
            valid.attrs["stale_count"] = stale_count
            valid["current_status"] = valid.apply(
                lambda r: "Ghosted" if compute_stale(r) else r["current_status"], axis=1
            )
            noise_count = int((~df["is_valid"]).sum())
            st.caption(f"🚫 {noise_count} noise/spam row(s) "
                       f"{'shown below' if show_noise else 'hidden'}")

        if valid is not None and not valid.empty:
            search_all = st.text_input(
                "🔍 Search (company · role · subject · JD)", "",
                help="Filters the tracker to rows whose company, role, email subject or job "
                     "description contains the text (case-insensitive).")

            statuses_present = [s for s in STATUS_ORDER if s in valid["current_status"].unique()]
            other = sorted(set(valid["current_status"]) - set(STATUS_ORDER))
            status_options = statuses_present + other
            selected_statuses = st.multiselect("Status", options=status_options,
                                               default=status_options)

            platforms_present = [p for p in PLATFORM_ORDER if p in valid["source_platform"].unique()]
            other_platforms = sorted(set(valid["source_platform"]) - set(PLATFORM_ORDER))
            platform_options = platforms_present + other_platforms
            selected_platforms = st.multiselect("Source Platform", options=platform_options,
                                                default=platform_options)

            job_types_present = sorted({t for t in valid["job_type"].fillna("") if t})
            selected_job_types = st.multiselect("Job Type", options=job_types_present,
                                                default=job_types_present,
                                                help="Full-Time · Internship · Graduate Programme / Trainee · Contract")

            industries_present = sorted({t for t in valid["industry"].fillna("") if t})
            selected_industries = st.multiselect("Industry", options=industries_present,
                                                 default=industries_present)

            companies = sorted(valid["company_name"].dropna().unique())
            company_search = st.text_input("Search company", "").strip().lower()
            matching = [c for c in companies if company_search in c.lower()] if company_search else companies
            selected_companies = st.multiselect("Company", options=matching)

            min_d = valid["application_date"].min()
            max_d = valid["application_date"].max()
            if pd.isna(min_d):
                selected_range = None
            else:
                lo = min_d.date()
                hi = max(max_d.date(), lo + timedelta(days=1))
                selected_range = st.slider("Application date range", min_value=lo, max_value=hi,
                                           value=(lo, hi), format="YYYY-MM-DD")
        else:
            selected_statuses = selected_platforms = selected_companies = selected_range = None
            selected_job_types = selected_industries = None

        st.divider()

    noise_view = None
    if raw is not None:
        df_all = normalize(raw)
        if show_noise:
            noise_view = df_all[~df_all["is_valid"]].copy()

    if raw is None or valid is None or valid.empty:
        if raw is None:
            header_cap.caption("Personal analytics for your job hunt — connect your Gmail to get "
                               "started. Everything runs from this browser.")
        else:
            header_cap.caption("Personal analytics for your job hunt — no valid applications yet.")
        if show_noise and noise_view is not None and not noise_view.empty:
            render_noise_editor(noise_view)
        if raw is None:
            st.info("No data yet — hit **▶ Sync Gmail Now** in the sidebar.")
        else:
            st.info("No valid applications yet — hit **▶ Sync Gmail Now** (toggle "
                    "**Force Full Re-sync** to re-classify the full mailbox), or turn on "
                    "**Show Filtered Out Noise / Spam** to inspect and recover what was dropped.")
        render_reports(stale_count=0)
        return

    view = valid.copy()
    # Compare against the full option lists so deselecting everything shows nothing (an empty
    # multiselect used to be treated as "no filter" and silently showed all rows).
    if selected_statuses is not None and len(selected_statuses) < len(status_options):
        view = view[view["current_status"].isin(selected_statuses)]
    if selected_platforms is not None and len(selected_platforms) < len(platform_options):
        view = view[view["source_platform"].isin(selected_platforms)]
    if selected_job_types is not None and len(selected_job_types) < len(job_types_present):
        view = view[view["job_type"].fillna("").isin(selected_job_types)]
    if selected_industries is not None and len(selected_industries) < len(industries_present):
        view = view[view["industry"].fillna("").isin(selected_industries)]
    if selected_companies:
        view = view[view["company_name"].isin(selected_companies)]
    if selected_range:
        view = view[
            (view["application_date"] >= pd.Timestamp(selected_range[0]))
            & (view["application_date"] <= pd.Timestamp(selected_range[1]) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1))
        ]

    search_q = search_all.strip().lower()
    if search_q:
        hay = (view["company_name"].fillna("") + " " + view["role_title"].fillna("") + " "
               + view["latest_subject"].fillna("") + " " + view["job_description_snippet"].fillna(""))
        view = view[hay.str.lower().str.contains(search_q, regex=False)]

    view = view.sort_values("application_date", ascending=False)

    header_cap.caption(f"Personal analytics for your job hunt — **{len(valid)}** valid "
                       f"applications · **{len(view)}** shown. Everything runs from this browser.")

    if show_noise and noise_view is not None and not noise_view.empty:
        render_noise_editor(noise_view)
    render_applications_tab(valid, view, stale_count=int(valid.attrs.get("stale_count", 0) or 0))


if __name__ == "__main__":
    main()
