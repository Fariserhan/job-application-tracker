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
import matcher
import parser
import repair
import storage
import ai
from parser import STATUSES

st.set_page_config(page_title="Job Application Suite", page_icon="🎯", layout="wide")

DB_PATH = storage.DB_PATH
CSV_PATH = storage.CSV_PATH

STATUS_ORDER = STATUSES
FUNNEL_STAGES = ["Applied", "Assessment / OA", "Interview", "Offer"]
PLATFORM_ORDER = ["LinkedIn", "JobStreet", "Hiredly", "Prosple", "Direct ATS", "Other"]
STALE_DAYS = 21
FOLLOWUP_AFTER_DAYS = 5
DISCOVERY_ROLES = ["Actuarial Analyst", "Risk Analyst", "Data Analyst", "Business Intelligence Analyst"]

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

TABLE_COLUMNS = ["Company", "Role", "Platform", "Type", "Status", "Application Date", "Fit Score", "Last Update", "Waiting", "Gmail Thread"]

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

PE_CSS_ANIM = """
    .js-plotly-plot .barlayer path,
    .js-plotly-plot .scatterlayer path,
    .js-plotly-plot .funnellayer path {
        pointer-events: all !important;
        transition: transform .12s cubic-bezier(.16,1,.3,1), filter .12s ease, opacity .12s ease;
        transform-box: fill-box;
    }
    .js-plotly-plot .barlayer path {transform-origin: 50% 100%;}
    .js-plotly-plot .barlayer path:hover {
        transform: scaleY(1.18) scaleX(1.22); filter: brightness(1.85) drop-shadow(0 0 6px rgba(79,140,255,.7));
    }
    .js-plotly-plot .scatterlayer path {transform-origin: 50% 50%;}
    .js-plotly-plot .scatterlayer path:hover {
        transform: scale(2.8); filter: brightness(1.6) drop-shadow(0 0 9px rgba(79,140,255,1));
    }
    .js-plotly-plot .funnellayer path {transform-origin: 50% 50%;}
    .js-plotly-plot .funnellayer path:hover {
        transform: scaleY(1.14) scaleX(1.1); filter: brightness(1.85) drop-shadow(0 0 6px rgba(79,140,255,.7));
    }
"""


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


@st.cache_data(show_spinner="Reading discovered jobs cache...")
def load_discovered_cached(path: str, mtime: float) -> list:
    return storage.load_discovered()


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

    if "fit_score" not in df.columns:
        df["fit_score"] = None
    df["fit_score"] = pd.to_numeric(df["fit_score"], errors="coerce")
    for col, default in (("matched_skills", []), ("missing_skills", []), ("actionable_improvements", [])):
        if col not in df.columns:
            df[col] = None
        df[col] = df[col].apply(_parse_json_list)
    return df


def ensure_fit_scores(df: pd.DataFrame):
    """Lazily score valid rows that have no stored fit score yet (persists to SQLite)."""
    try:
        pending = df[(df["is_valid"]) & (df["fit_score"].isna())]
        if pending.empty:
            return
        results = {}
        for _, row in pending.iterrows():
            analysis = matcher.analyze(row["role_title"], row["job_description_snippet"])
            results[row["thread_id"]] = analysis
            mask = df["thread_id"] == row["thread_id"]
            df.loc[mask, "fit_score"] = analysis["fit_score"]
            df.loc[mask, "matched_skills"] = [analysis["matched_skills"]]
            df.loc[mask, "missing_skills"] = [analysis["missing_skills"]]
            df.loc[mask, "actionable_improvements"] = [analysis["actionable_improvements"]]
        storage.save_fit_results(results)
    except Exception:
        pass


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

        scored = valid.dropna(subset=["fit_score"])
        pos = scored[scored["current_status"].isin(["Assessment / OA", "Interview", "Offer"])]["fit_score"]
        rej = scored[scored["current_status"] == "Rejected"]["fit_score"]
        if len(pos) and len(rej):
            tips.append(f"🧬 Roles you **progressed in average {pos.mean():.0f}% fit** vs "
                        f"**{rej.mean():.0f}%** for rejections — treat {max(70, pos.mean()):.0f}%+ as your apply threshold.")
        elif len(pos):
            tips.append(f"🧬 Progressed roles average **{pos.mean():.0f}% fit** — keep targeting that band.")

        cnt = Counter(s for row in valid["missing_skills"] for s in (row if isinstance(row, list) else []))
        if cnt:
            top = cnt.most_common(3)
            tips.append("🎯 **Skills to learn first**: " + ", ".join(
                f"**{s}** (missing in {n} role{'s' if n != 1 else ''})" for s, n in top))

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


def ai_want() -> bool:
    """True when the user enabled AI AND a free local model/endpoint is reachable."""
    try:
        return bool(st.session_state.get("ai_enabled", True)) and ai.available()
    except Exception:
        return False


def ai_model() -> str:
    try:
        return ai.active_model()
    except Exception:
        return ai.preferred_model()


def run_ai_test():
    """Send one tiny request so the user can see exactly whether the key+model works."""
    import time
    ai.clear_last_error()
    t0 = time.monotonic()
    text = ai.generate("You are a connectivity test.",
                       "Reply with exactly the word: OK", max_tokens=128, temperature=0.0)
    elapsed = time.monotonic() - t0
    if text and text.strip().upper() == "OK":
        st.session_state["ai_test_result"] = ("ok", f"replied in {elapsed:.1f}s",
                                              ai.provider_label())
    elif text:
        st.session_state["ai_test_result"] = ("ok", f"replied '{text.strip()[:40]}' in {elapsed:.1f}s",
                                              ai.provider_label())
    else:
        st.session_state["ai_test_result"] = ("fail",
                                              f"key {ai.key_preview()} · {ai.last_error() or 'no response'}",
                                              ai.provider_label())


def build_stats_digest(valid: pd.DataFrame, stale_count: int) -> str:
    """A compact, numbers-only digest of the pipeline that the AI turns into insights."""
    total = len(valid)
    counts = {str(k): int(v) for k, v in valid["current_status"].value_counts().items()}
    interviews = int(valid["current_status"].isin(["Interview", "Offer"]).sum())
    offers = int((valid["current_status"] == "Offer").sum())
    progressed = int(valid["current_status"].isin(
        ["Assessment / OA", "Interview", "Offer", "Rejected"]).sum())
    response_rate = 100 * progressed / total if total else 0.0
    scored = valid["fit_score"].dropna()
    avg_fit = float(scored.mean()) if len(scored) else None
    resp = valid[valid["current_status"].isin(
        ["Assessment / OA", "Interview", "Offer", "Rejected"])].dropna(
        subset=["application_date", "last_updated"]).copy()
    med_days = None
    if len(resp):
        d = (resp["last_updated"] - resp["application_date"]).dt.days
        d = d[(d >= 0) & (d < 120)]
        if len(d):
            med_days = float(d.median())
    cutoff = pd.Timestamp.today().normalize() - pd.Timedelta(days=30)
    last30 = int((valid["application_date"] >= cutoff).sum())
    prev30 = int(((valid["application_date"] >= cutoff - pd.Timedelta(days=30))
                  & (valid["application_date"] < cutoff)).sum())
    platforms = {str(k): int(v) for k, v in valid["source_platform"].value_counts().head(5).items()}
    companies = {str(k): int(v) for k, v in valid["company_name"].value_counts().head(5).items()}
    missing = Counter(s for row in valid["missing_skills"]
                      for s in (row if isinstance(row, list) else []))
    top_missing = [str(s) for s, _ in missing.most_common(5)]
    lines = [
        f"Total valid applications: {total}",
        f"Status counts: {counts}",
        f"Interview rate (interviews+offers/valid): {100 * interviews / total if total else 0:.1f}% ({interviews})",
        f"Offers: {offers}",
        f"Response rate (any employer reply): {response_rate:.1f}%",
        f"Median employer response days: {med_days if med_days is not None else 'n/a'}",
        f"Average CV fit score: {avg_fit:.1f}%" if avg_fit is not None else "Average CV fit score: n/a",
        f"Applications in the last 30 days: {last30} (previous 30: {prev30})",
        f"Stale / auto-ghosted (no update > {stale_days()} days): {stale_count}",
        f"Top platforms: {platforms}",
        f"Top employers applied to: {companies}",
        f"Most frequently missing skills: {top_missing}",
    ]
    return "\n".join(lines)


def ai_insights(valid: pd.DataFrame, stale_count: int) -> str | None:
    if not ai_want():
        return None
    try:
        system = ("You are a sharp, honest career coach for a maths & statistics graduate "
                  "targeting actuarial, risk and data-analytics roles. Be PRECISE and CONCISE. "
                  "Use only the numbers given. No preamble, no restating the stats, no filler, "
                  "no emojis.")
        prompt = ("My job-search stats:\n" + build_stats_digest(valid, stale_count) +
                  "\n\nOutput exactly:\n"
                  "- 4-6 insight bullets (1 sentence each, concrete, using the numbers)\n"
                  "- '**Next actions**' followed by 3 one-line steps\n"
                  "Plain markdown. Write at least 150 words — be specific.")
        text = ai.generate_cached(system, prompt, model=ai_model(), max_tokens=1200)
        if text and len(text) < 60:
            return None  # effectively empty — fall back to the deterministic insights
        return text
    except Exception:
        return None


def ai_drafts(queue) -> dict:
    """Batched AI follow-up emails for the shown queue rows -> {thread_id: email}."""
    if not queue or not ai_want():
        return {}
    try:
        items = []
        for row, tier, wait in queue:
            applied = row.get("application_date")
            items.append({
                "id": str(row["thread_id"]),
                "company": str(row.get("company_name", "") or ""),
                "role": str(row.get("role_title", "") or ""),
                "applied": f"{pd.Timestamp(applied):%d %b %Y}" if pd.notna(applied) else "",
                "waited_days": wait,
                "situation": ("applied but received no response yet" if tier == "nudge"
                              else "application went silent / likely ghosted"),
                "fit": (float(row.get("fit_score")) if pd.notna(row.get("fit_score")) else None),
                "matched": (row.get("matched_skills") or [])[:4],
                "missing": (row.get("missing_skills") or [])[:4],
            })
        system = ("You are a professional job-search email writer. Write short, tailored follow-up "
                  "emails — be PRECISE and CONCISE, each email under ~90 words, no fluff.")
        prompt = ("Return ONLY a JSON object mapping each id to the email text. Each email must "
                  "have a Subject line and a body (plain text, 3-4 sentences, no markdown, no "
                  "emojis), personally reference the company and role, and end with one clear, "
                  "polite ask (request an update / offer extra materials / ask for feedback). "
                  "The candidate is Faris Erhan, a fresh graduate in maths & statistics.\n\n" +
                  json.dumps(items, ensure_ascii=False))
        data = ai.batch_json(system, prompt, model=ai_model(), max_tokens=1200)
        return {k: str(v) for k, v in data.items() if isinstance(v, str) and v.strip()}
    except Exception:
        return {}


def ai_gap_narrative(report) -> str | None:
    if report is None or report.empty or not ai_want():
        return None
    try:
        top = report.head(6)[["Skill", "% of pipeline", "Live market", "Missing in", "On my CV"]]
        system = ("You are a career strategist for an actuarial/data-analyst job search. Be PRECISE "
                  "and CONCISE — no preamble, no emojis. Use only the numbers given.")
        prompt = ("Top skill gaps (skill, % of pipeline demanding it, live listings, times still "
                  "missing, on my CV?):\n" + json.dumps(top.to_dict("records"), ensure_ascii=False) +
                  "\n\nOutput: max 3 sentences of prioritized advice + one '**Best next move:**' line.")
        return ai.generate_cached(system, prompt, model=ai_model(), max_tokens=500)
    except Exception:
        return None


def ai_market_summary(jobs) -> str | None:
    if not jobs or not ai_want():
        return None
    try:
        sources = {}
        for j in jobs:
            sources[str(j.get("source", ""))] = sources.get(str(j.get("source", "")), 0) + 1
        top = sorted(jobs, key=lambda j: j.get("fit_score") or 0, reverse=True)[:8]
        rows = [{
            "title": j.get("title"), "company": j.get("company"), "source": j.get("source"),
            "fit": j.get("fit_score"),
            "top_missing": (_parse_json_list(j.get("missing_skills")) or [])[:2],
        } for j in top]
        system = ("You are a Malaysian job-market analyst. Be PRECISE and CONCISE — no preamble, no "
                  "emojis. Use only the data given.")
        prompt = (f"Sources: {sources}\nTop roles by fit score:\n" +
                  json.dumps(rows, ensure_ascii=False) +
                  "\n\nOutput: max 3 sentences — what's on offer, which roles fit best, and the "
                  "skills in demand.")
        return ai.generate_cached(system, prompt, model=ai_model(), max_tokens=450)
    except Exception:
        return None


def ai_company_strategy(valid: pd.DataFrame) -> str | None:
    if not ai_want():
        return None
    try:
        g = valid.groupby("company_name").agg(
            n=("thread_id", "count"),
            roles=("role_title", lambda s: "; ".join(sorted(
                {str(x) for x in s if str(x) not in ("", "nan")}))[:180]),
        ).reset_index()
        g = g[g["n"] > 1].sort_values("n", ascending=False).head(6)
        if g.empty:
            return None
        system = ("You are a hiring-strategy coach. Be PRECISE and CONCISE — 2-3 sentences, no "
                  "preamble, no emojis.")
        prompt = ("Employers I've applied to more than once (company, count, roles):\n" +
                  json.dumps(g.to_dict("records"), ensure_ascii=False) + "\n\nAdvice?")
        return ai.generate_cached(system, prompt, model=ai_model(), max_tokens=400)
    except Exception:
        return None


def ai_activity_summary(feed) -> str | None:
    if not feed or not ai_want():
        return None
    try:
        lines = []
        for ev in feed[:8]:
            lines.append(f"{ev.get('ts')}: {ev.get('company_name')} {ev.get('role_title')} -> "
                         f"{ev.get('to_status') or ev.get('current_status')}")
        system = ("You are a job-search momentum tracker. Be PRECISE and CONCISE — 1-2 sentences, no "
                  "preamble, no emojis.")
        prompt = "Recent activity:\n" + "\n".join(lines) + "\n\nSummary and next action?"
        return ai.generate_cached(system, prompt, model=ai_model(), max_tokens=300)
    except Exception:
        return None


def ai_fit_explanation(title: str, snippet: str, analysis: dict | None) -> str | None:
    if not analysis or not ai_want():
        return None
    try:
        bd = analysis.get("breakdown") or {}
        keys = [k for k in bd if not str(k).startswith("_")]
        parts = [f"{k}: {bd[k]}%" for k in keys if isinstance(bd.get(k), (int, float))]
        cap = bd.get("_cap")
        if isinstance(cap, (int, float)) and cap < 90:
            parts.append(f"capped at {cap:.0f}% (seniority/experience cap)")
        prompt = (f"Role: {title}\nFit score: {analysis.get('fit_score')}%\nBreakdown: "
                  + ", ".join(parts) +
                  f"\nMatched skills: {analysis.get('matched_skills') or []}"
                  f"\nMissing skills: {analysis.get('missing_skills') or []}")
        if snippet:
            prompt += f"\nJD text (first 400 chars): {snippet[:400]}"
        system = ("You are a CV-fit coach. Be PRECISE and CONCISE — 2-3 sentences, no preamble, "
                  "no emojis. Concrete and honest.")
        return ai.generate_cached(system, prompt, model=ai_model(), max_tokens=450)
    except Exception:
        return None


def platform_performance(valid: pd.DataFrame) -> pd.DataFrame:
    df = valid.copy()
    df["_prog"] = df["current_status"].isin(["Assessment / OA", "Interview", "Offer", "Rejected"])
    g = df.groupby("source_platform").agg(
        Applications=("thread_id", "count"),
        Progressed=("_prog", "sum"),
        Interviews=("current_status", lambda s: int(s.isin(["Interview", "Offer"]).sum())),
        Rejected=("current_status", lambda s: int((s == "Rejected").sum())),
        AvgFit=("fit_score", "mean"),
    ).reset_index()
    g["Response %"] = (100 * g["Progressed"] / g["Applications"]).round(1)
    g["Interview %"] = (100 * g["Interviews"] / g["Applications"]).round(1)
    g["AvgFit"] = g["AvgFit"].round(1)
    return g.sort_values("Applications", ascending=False)


def company_summary(valid: pd.DataFrame, limit: int = 20) -> pd.DataFrame:
    df = valid.sort_values("last_updated")
    latest_status = df.groupby("company_name")["current_status"].last()
    g = valid.groupby("company_name").agg(
        Applications=("thread_id", "count"),
        AvgFit=("fit_score", "mean"),
        LastUpdate=("last_updated", "max"),
    ).reset_index()
    g["Latest Status"] = g["company_name"].map(latest_status)
    g["AvgFit"] = g["AvgFit"].round(1)
    return g.sort_values(["Applications", "LastUpdate"], ascending=[False, False]).head(limit)


def aggregate_missing_skills(valid: pd.DataFrame) -> pd.DataFrame:
    """Demand-based gaps: signals in your applied roles AND the live market, not on your CV."""
    cv_text = matcher.get_cv_text().lower()
    pipe, market = Counter(), Counter()
    for _, row in valid.iterrows():
        text = f"{row['role_title'] or ''} {row['job_description_snippet'] or ''}"
        for label, pattern, _ in matcher.DEMAND_COMPILED:
            if pattern.search(text):
                pipe[label] += 1
    try:
        for job in storage.load_discovered():
            text = f"{job.get('title', '')} {job.get('description', '')}"
            for label, pattern, _ in matcher.DEMAND_COMPILED:
                if pattern.search(text):
                    market[label] += 1
    except Exception:
        pass
    gaps = Counter()
    for label in set(pipe) | set(market):
        if not re.search(matcher.owned_regex(label), cv_text, flags=re.IGNORECASE):
            gaps[label] = pipe.get(label, 0) + market.get(label, 0)
    if not gaps:
        return pd.DataFrame(columns=["skill", "count"])
    return pd.DataFrame(gaps.most_common(10), columns=["skill", "count"])


def render_pillar_bars(breakdown: dict):
    if not breakdown:
        return
    cap = breakdown.get("_cap")
    cols = st.columns([1.1, 2])
    labels = [k for k in breakdown if not k.startswith("_")]
    for label in labels:
        value = breakdown[label]
        with st.container(horizontal=True, vertical_alignment="center"):
            st.markdown(f"**{label}**")
            st.progress(min(value / 100, 1.0), text=f"{value}%" if isinstance(value, (int, float)) else "—")
    if isinstance(cap, (int, float)) and cap < 90:
        st.caption(f"⚠️ Score capped at {cap:.0f}% — seniority/experience ask exceeds a fresh-grad profile.")


# ------------------------------------------------------------------ sidebar

def _lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"
    finally:
        s.close()


def render_ipad_section():
    with st.expander("📱 Open on iPad / Phone", expanded=False):
        url = f"http://{_lan_ip()}:8501"
        st.markdown(f"On the same Wi-Fi, open Safari on your iPad and go to:  \n### `{url}`")
        try:
            import qrcode
            qr = qrcode.QRCode(box_size=5, border=2)
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="#0e1117", back_color="white")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            st.image(buf.getvalue(), width=180)
        except Exception:
            st.caption("Tip: `pip install qrcode[pil]` shows a scannable QR code here.")
        st.caption("Away from home? Run `cloudflared tunnel --url http://localhost:8501` "
                   "(free, no signup) and open the printed https URL from anywhere.")
        st.caption("⚠️ **Security:** this dashboard has no login by default and exposes your "
                   "application data. Before using a public tunnel, set an environment variable "
                   "`DASH_PASSWORD=your-passphrase` and restart Streamlit — the app will then "
                   "require it before rendering anything.")


def render_cv_editor():
    with st.expander("🧬 CV Profile (Fit Analyzer)", expanded=False):
        if "cv_text" not in st.session_state:
            st.session_state["cv_text"] = matcher.get_cv_text()
        upload = st.file_uploader("Upload CV (.txt / .md)", type=["txt", "md"], key="cv_upload")
        if upload is not None and upload.getvalue():
            st.session_state["cv_text"] = upload.getvalue().decode("utf-8", errors="replace")
            st.toast(f"Loaded CV: {upload.name}")
        cv_text = st.text_area("Editable CV / resume text", value=st.session_state["cv_text"],
                               height=220, key="cv_area")
        st.session_state["cv_text"] = cv_text
        c1, c2 = st.columns(2)
        if c1.button("💾 Save CV Profile", width="stretch"):
            matcher.save_cv_text(cv_text)
            st.toast("CV saved — click 🧬 Re-score all (Maintenance) to refresh fit scores; "
                     "AI scores recompute automatically because the CV signature changed")
        if c2.button("↩ Reset to curated default", width="stretch"):
            st.session_state["cv_text"] = matcher.DEFAULT_CV_TEXT
            st.rerun()
        st.caption("The curated CV drives the 5-pillar fit score: skills 40% · title 20% · "
                   "qualifications 15% · seniority 15% · location 10%.")


def run_sync(force_full: bool, zone=None):
    st.session_state["sync_running"] = True
    st.toast("⏳ Sync started — watch the progress below the header…", icon="⏳")
    summary = {}
    use_ai_fit = ai_want() and bool(st.session_state.get("ai_fit_ai", True))
    use_ai_classify = ai_want() and bool(st.session_state.get("ai_classify_ai", True))
    with (zone.container() if zone is not None else st.container()):
        with st.status("▶ Syncing Gmail...", expanded=True) as status:
            bar = st.progress(0.0, text=STAGE_LABELS["gmail"])
            log = st.empty()
            try:
                def notify(stage, message):
                    bar.progress(STAGE_PROGRESS.get(stage, 0), text=STAGE_LABELS.get(stage, stage))
                    log.markdown(f"`{datetime.now():%H:%M:%S}` {message}")

                summary = sync_engine.run(force_full=force_full, notify=notify,
                                          use_ai_fit=use_ai_fit,
                                          use_ai_classify=use_ai_classify)
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
                     "re-process the whole 2026 mailbox with AI classification & fit", icon="ℹ️")
        else:
            st.toast(f"Synced in {summary['elapsed']:.1f}s — {summary['inserted']} new, "
                     f"{summary['updated']} updated, {summary['garbage']} noise, "
                     f"{summary.get('ai_classified', 0)} AI-classified", icon="✅")
        if summary.get("csv_error"):
            st.warning(f"Sync succeeded, but the CSV export was skipped "
                       f"({summary['csv_error']}). The database is safe — close "
                       f"job_tracker.csv if it is open and re-run the sync.")
    st.rerun()


def run_market_scan():
    st.session_state["scan_running"] = True
    try:
        with st.status("🌐 Scanning live Malaysian job market...", expanded=True) as status:
            log = st.empty()

            def notify(stage, message):
                log.markdown(f"`{datetime.now():%H:%M:%S}` {message}")

            result = sync_engine.scan_market(notify=notify)
            status.update(label=f"✅ Found {len(result['jobs'])} live roles "
                                f"({result['elapsed']:.1f}s)", state="complete", expanded=False)
            for name, count in result["source_counts"].items():
                emoji = "🟢" if count else "⚪"
                st.caption(f"{emoji} {name}: {count} listings")
    except Exception as exc:
        st.error(f"Market scan failed: {type(exc).__name__}: {exc}")
    finally:
        st.session_state["scan_running"] = False
    st.cache_data.clear()
    st.rerun()


def run_rescore():
    """Re-run fit scores for every application (AI when enabled) + market roles (rules).
    AI scores use a CV-signature cache key, so editing the CV refreshes them."""
    use_ai_fit = ai_want() and bool(st.session_state.get("ai_fit_ai", True))
    with st.status("🧬 Re-scoring applications + market roles against your CV...",
                   expanded=True) as status:
        try:
            scored = sync_engine.run_fit_evaluation(notify=None, use_ai_fit=use_ai_fit)
            disc_results = {}
            try:
                for job in storage.load_discovered():
                    disc_results[job["job_key"]] = matcher.analyze(
                        job.get("title", "") or "", job.get("description") or "")
                storage.save_discovered_fits(disc_results)
            except Exception:
                pass
            status.update(label=f"✅ Re-scored {scored['scored']} application(s) + "
                                f"{len(disc_results)} market role(s)", state="complete")
            st.toast("Fit scores refreshed — the CV Gap Report & charts now use your latest CV")
        except Exception as exc:
            status.update(label=f"❌ Re-score failed: {exc}", state="error", expanded=True)
    st.cache_data.clear()
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

def fit_chip(score) -> str:
    if score is None or (isinstance(score, float) and pd.isna(score)):
        return "<span class='fit-chip fit-amber'>—</span>"
    if score >= 75:
        return f"<span class='fit-chip fit-green'>{score:.0f}% Fit</span>"
    if score >= 50:
        return f"<span class='fit-chip fit-amber'>{score:.0f}% Fit</span>"
    return f"<span class='fit-chip fit-red'>{score:.0f}% Fit</span>"


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


def render_gap_analysis(title: str, description: str, fallback_matched=None,
                        fallback_missing=None, fallback_tips=None):
    """Live CV gap analysis recomputed against the current CV profile."""
    try:
        analysis = matcher.analyze(title, description)
    except Exception:
        analysis = None
    matched = (analysis or {}).get("matched_skills") or (fallback_matched or [])
    missing = (analysis or {}).get("missing_skills") or (fallback_missing or [])
    tips = (analysis or {}).get("actionable_improvements") or (fallback_tips or [])
    score = (analysis or {}).get("fit_score")

    if score is not None:
        st.markdown(fit_chip(score), unsafe_allow_html=True)
    if matched:
        st.markdown("**✅ Matched:** " + " ".join(
            f"<span class='fit-chip fit-green'>{esc(m)}</span>" for m in matched),
            unsafe_allow_html=True)
    else:
        st.markdown("**✅ Matched:** _none detected_")
    if missing:
        st.markdown("**❌ Missing:** " + " ".join(
            f"<span class='fit-chip fit-red'>{esc(m)}</span>" for m in missing),
            unsafe_allow_html=True)
    if tips:
        st.markdown("**💡 How to improve your odds**")
        for tip in tips[:3]:
            st.markdown(f"- {tip}")
    if analysis and analysis.get("breakdown"):
        st.markdown("**📊 Score breakdown** (skills · title · quals · seniority · location)")
        render_pillar_bars(analysis["breakdown"])
    return analysis


# ------------------------------------------------------------------ tab 1

def render_applications_tab(valid: pd.DataFrame, view: pd.DataFrame, stale_count: int = 0):
    total_valid = len(valid)
    interviews = int(valid["current_status"].isin(["Interview", "Offer"]).sum())
    rejected = int((valid["current_status"] == "Rejected").sum())
    scored = valid["fit_score"].dropna()
    avg_fit = float(scored.mean()) if len(scored) else None

    interview_rate = (interviews / total_valid * 100) if total_valid else 0.0
    rejection_rate = (rejected / total_valid * 100) if total_valid else 0.0

    progressed = valid[valid["current_status"].isin(["Assessment / OA", "Interview", "Offer", "Rejected"])]
    response_rate = (len(progressed) / total_valid * 100) if total_valid else 0.0
    active = int(valid["current_status"].isin(["Applied", "Assessment / OA"]).sum())
    cutoff_30 = pd.Timestamp.today().normalize() - pd.Timedelta(days=30)
    recent_30 = int((valid["application_date"] >= cutoff_30).sum())
    prev_30 = int(((valid["application_date"] >= cutoff_30 - pd.Timedelta(days=30))
                   & (valid["application_date"] < cutoff_30)).sum())
    delta_30 = (recent_30 - prev_30) if (recent_30 or prev_30) else None
    resp_days = progressed.dropna(subset=["application_date", "last_updated"]).copy()
    med_days = None
    if len(resp_days):
        d = (resp_days["last_updated"] - resp_days["application_date"]).dt.days
        d = d[(d >= 0) & (d < 120)]
        med_days = float(d.median()) if len(d) else None

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("✅ Total Valid Applications", total_valid, help="Noise-filtered application emails")
    k2.metric("🎤 Interview Rate", f"{interview_rate:.1f}%", help="(Interviews + Offers) / Valid")
    k3.metric("❌ Rejection Rate", f"{rejection_rate:.1f}%", help="Rejected / Total Valid")
    k4.metric("👻 Auto-Ghosted", stale_count, delta_color="inverse",
              help=f"Applied > {stale_days()} days with no update")
    k5.metric("🧬 Avg CV Fit Score", f"{avg_fit:.0f}%" if avg_fit is not None else "—",
              help="Average 5-pillar fit across scored applications")

    k6, k7, k8, k9 = st.columns(4)
    k6.metric("🚀 Active Pipelines", active, help="Applied + Assessment/OA (still alive)")
    k7.metric("📬 Response Rate", f"{response_rate:.0f}%", help="Any employer response / valid")
    k8.metric("⏱️ Median Response", f"{med_days:.0f}d" if med_days is not None else "—",
              help="Application date → last employer update")
    k9.metric("🆕 Last 30 Days", recent_30,
              delta=(f"{delta_30:+d} vs prev" if delta_30 is not None else None),
              delta_color="normal" if (delta_30 or 0) >= 0 else "inverse",
              help=f"Applications submitted in the last 30 days (previous 30: {prev_30})")

    st.divider()

    with st.expander("🧠 Insights & Recommendations", expanded=False):
        if ai_want():
            if st.button("🤖 Generate AI insights", key="gen_insights_btn", width="stretch",
                         help="AI analysis of your pipeline — one free API call, cached after "
                              "the first time."):
                st.session_state["gen_insights"] = True
                st.rerun()
        ai_txt = ai_insights(valid, stale_count) if st.session_state.get("gen_insights") else None
        if ai_txt:
            st.caption(f"🤖 Generated by {ai.provider_label()} · numbers computed from your data")
            st.markdown(ai_txt)
        else:
            tips = generate_insights(valid, stale_count)[:6]
            if tips:
                for tip in tips:
                    st.markdown(f"- {tip}")
            if st.session_state.get("gen_insights"):
                err = ai.last_error()
                st.caption("AI didn't return usable content — showing deterministic insights."
                           + (f"  ({err})" if err else "  (no error recorded — the response "
                             "may have been too short to show)"))
            elif ai_want():
                st.caption("Click **Generate AI insights** above for an AI analysis (free).")

    render_followup_queue(valid)
    render_activity_feed()

    st.subheader("Pipeline & Fit")
    funnel_counts = [
        total_valid,
        int(valid["current_status"].isin(FUNNEL_STAGES[1:]).sum()),
        int(valid["current_status"].isin(FUNNEL_STAGES[2:]).sum()),
        int((valid["current_status"] == "Offer").sum()),
    ]
    left, right = st.columns([1, 1.2])
    with left:
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
    with right:
        st.markdown("**CV Fit Score Distribution**")
        scored_df = valid.dropna(subset=["fit_score"])
        if not scored_df.empty:
            fig = px.histogram(scored_df, x="fit_score", nbins=18,
                               color_discrete_sequence=["#4F8CFF"],
                               labels={"fit_score": "Fit Score %", "count": "Applications"})
            fig.update_traces(marker_line_color="#16181d", marker_line_width=1.5)
            style_fig(fig, 360)
            fig.add_vline(x=75, line_dash="dash", line_color="#00CC96",
                          annotation_text="apply threshold", annotation_font_color="#00CC96")
            avg = float(scored_df["fit_score"].mean())
            fig.add_vline(x=avg, line_dash="dot", line_color="#9db9ff",
                          annotation_text=f"your avg {avg:.0f}%", annotation_font_color="#9db9ff")
            ev = st.plotly_chart(fig, width="stretch", on_select="rerun",
                                 selection_mode="points", key="fit_hist")
            xval = consume_selection("fit_hist", ev)
            if xval is not None:
                try:
                    lo, hi = float(xval) - 5, float(xval) + 5
                except (TypeError, ValueError):
                    lo, hi = None, None
                if lo is not None:
                    band_rows = scored_df[(scored_df["fit_score"] >= lo) & (scored_df["fit_score"] <= hi)]
                    if not band_rows.empty:
                        inline_insights(f"Fit band {lo:.0f}-{hi:.0f}%", band_rows)

    left, right = st.columns([1.2, 1])
    with left:
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
    with right:
        st.markdown("**Fit vs Time** — hover for a preview, click a dot for the full JD & fit breakdown")
        splot = valid.dropna(subset=["fit_score", "application_date", "thread_id"])
        if not splot.empty:
            fig = px.scatter(splot, x="application_date", y="fit_score",
                             color="current_status", color_discrete_map=STATUS_COLORS,
                             custom_data=["thread_id"],
                             hover_data={"company_name": True, "fit_score": ":.0f",
                                         "application_date": "|%d %b %Y", "current_status": False,
                                         "role_title": False, "thread_id": False},
                             labels={"application_date": "Applied on", "fit_score": "Fit Score %"})
            fig.update_traces(marker=dict(size=9, line=dict(width=1, color="#16181d")), opacity=0.85)
            style_fig(fig, 360)
            best = splot.loc[splot["fit_score"].idxmax()]
            fig.add_annotation(
                x=best["application_date"], y=best["fit_score"],
                text=f"🏆 best fit: {best['company_name'][:16]} · {best['fit_score']:.0f}%",
                showarrow=True, arrowhead=2, arrowcolor="#9db9ff",
                font=dict(color="#9db9ff", size=11), bgcolor="rgba(26,29,36,.85)",
                bordercolor="#313947", borderwidth=1, borderpad=3)
            event = st.plotly_chart(fig, width="stretch", on_select="rerun",
                                    selection_mode="points", key="fit_time")
            clicked_tid = consume_selection("fit_time", event, labels=splot["thread_id"].tolist())
            if clicked_tid:
                clicked_rows = splot[splot["thread_id"] == str(clicked_tid)]
                if not clicked_rows.empty:
                    row0 = clicked_rows.iloc[0]
                    inline_insights(
                        f"{row0.get('company_name', 'Application')} "
                        f"({row0.get('fit_score', 0):.0f}% fit)", clicked_rows)
        else:
            st.info("No scored applications.")

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
                perf.rename(columns={"source_platform": "Platform", "AvgFit": "Avg Fit"}),
                width="stretch", hide_index=True,
                column_config={
                    "Avg Fit": st.column_config.ProgressColumn("Avg Fit", min_value=0, max_value=100,
                                                               format="%.0f%%"),
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

    missing_df = aggregate_missing_skills(valid)
    comp_df = company_summary(valid)
    left, right = st.columns(2)
    with left:
        st.markdown("**Skill Gaps to Close** (demanded in your pipeline + live market, not on your CV) — click a bar")
        if not missing_df.empty:
            asc = missing_df.sort_values("count", ascending=True)
            top_skill = asc["skill"].iloc[-1]
            colors = ["#EF553B" if s == top_skill else "#4F8CFF" for s in asc["skill"]]
            fig = px.bar(asc, x="count", y="skill", orientation="h",
                         custom_data=["skill"],
                         labels={"count": "Roles demanding it", "skill": ""})
            fig.update_traces(marker_color=colors, text=asc["count"],
                              textposition="outside", cliponaxis=False)
            style_fig(fig, 340)
            ev = st.plotly_chart(fig, width="stretch", on_select="rerun",
                                 selection_mode="points", key="gap_chart")
            skill = consume_selection("gap_chart", ev,
                                      labels=missing_df["skill"].tolist())
            if skill:
                demand_re = matcher.demand_patterns.get(str(skill), "")
                def _demands(r):
                    text = f"{r['role_title'] or ''} {r['job_description_snippet'] or ''}".lower()
                    return bool(demand_re) and re.search(demand_re, text) is not None
                skill_rows = valid[valid.apply(_demands, axis=1)]
                if skill_rows.empty:
                    skill_rows = valid[valid["missing_skills"].apply(
                        lambda ms: str(skill) in (ms if isinstance(ms, list) else []))]
                if not skill_rows.empty:
                    inline_insights(f"Skill: {skill} — where it is demanded", skill_rows)
        else:
            st.caption("No skill gaps detected yet — click **Scrape missing JDs** to enrich.")
    with right:
        st.markdown("**Company Pipeline Summary** (top 20)")
        if not comp_df.empty:
            st.dataframe(
                comp_df.rename(columns={"company_name": "Company", "AvgFit": "Avg Fit",
                                        "LastUpdate": "Last Update"}),
                width="stretch", hide_index=True,
                column_config={
                    "Avg Fit": st.column_config.ProgressColumn("Avg Fit", min_value=0, max_value=100,
                                                               format="%.0f%%"),
                    "Last Update": st.column_config.DateColumn(format="YYYY-MM-DD"),
                },
            )
        else:
            st.caption("No companies yet.")

    render_cv_gap_report(valid)
    render_company_dupes(valid)

    st.divider()
    head_col, btn_col, btn_col2 = st.columns([3, 1, 1])
    with head_col:
        st.subheader(f"📋 Interactive Tracker ({len(view)} shown of {total_valid} valid)")
    with btn_col:
        url_str = valid["job_url"].astype(str)
        thin = int(((url_str != "") & (url_str != "nan")
                    & (valid["job_description_snippet"].str.len() < 240)
                    & (url_str.apply(lambda u: not is_broken_job_url(u)))).sum())
        st.button(f"🧲 Scrape missing JDs ({thin})",
                  help="Re-scrapes known posting links (OG, 16 workers). Broken/logo links are "
                       "skipped — fix them with the button on the right.",
                  disabled=thin == 0, width="stretch", key="backfill_btn")
    with btn_col2:
        url_vals = valid["job_url"].astype(str)
        thin_any = int((valid["job_description_snippet"].str.len() < 240).sum())
        broken_n = int(url_vals.apply(lambda u: is_broken_job_url(u)).sum())
        look_n = thin_any + broken_n
        st.button(f"🔎 Look up JDs & fix broken links ({look_n})",
                  help="Searches live listings per role and fuzzy-matches your jobs to pull real "
                       "JDs AND replaces broken posting links (JobStreet tracking links that open a "
                       "logo) with the live posting. Missing descriptions are filled too.",
                  disabled=look_n == 0, width="stretch", key="lookup_btn")

    if st.session_state.get("backfill_btn"):
        run_jd_backfill(valid)
    if st.session_state.get("lookup_btn"):
        run_jd_lookup(valid)

    if broken_n:
        st.caption(f"⚠️ **{broken_n}** posting link(s) open a JobStreet logo instead of the job "
                   "page — click **🔎 Look up JDs & fix broken links** above to auto-replace "
                   "them with the live posting.")

    st.caption("👆 **Interactive Tracker** — click any row to open its details below.")
    render_tracker(view, valid)

    st.download_button(
        label="⬇️ Export filtered view to CSV",
        data=view.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"filtered_applications_{datetime.now():%Y%m%d_%H%M}.csv",
        mime="text/csv",
    )


def render_cv_gap_report(valid: pd.DataFrame):
    """Exhaustive gap analysis: every applied job's JD text × every market signal × the CV,
    plus live-market demand from the discovery cache."""
    if valid.empty:
        return
    counts_demand = Counter()
    counts_missing = Counter()
    counts_matched = Counter()
    for _, row in valid.iterrows():
        text = f"{row['role_title'] or ''} {row['job_description_snippet'] or ''}"
        for label, pattern, _ in matcher.DEMAND_COMPILED:
            if pattern.search(text):
                counts_demand[label] += 1
        for s in (row["missing_skills"] if isinstance(row["missing_skills"], list) else []):
            counts_missing[s] += 1
        for s in (row["matched_skills"] if isinstance(row["matched_skills"], list) else []):
            counts_matched[s] += 1

    # live-market demand from the discovery cache (richer JD text)
    market_demand = Counter()
    try:
        for job in storage.load_discovered():
            text = f"{job.get('title', '')} {job.get('description', '')}"
            for label, pattern, _ in matcher.DEMAND_COMPILED:
                if pattern.search(text):
                    market_demand[label] += 1
    except Exception:
        pass

    if not counts_demand and not market_demand:
        return

    cv_text = matcher.get_cv_text().lower()
    total = len(valid)
    rows = []
    for label in set(counts_demand) | set(market_demand):
        pattern = matcher.demand_patterns.get(label)
        demanded = counts_demand.get(label, 0)
        market = market_demand.get(label, 0)
        if demanded == 0 and market == 0 and counts_missing.get(label, 0) == 0:
            continue
        owned = bool(re.search(matcher.owned_regex(label), cv_text, flags=re.IGNORECASE))
        missing = counts_missing.get(label, 0)
        rows.append({
            "Skill": label,
            "Demanded in": demanded,
            "% of pipeline": round(100 * demanded / total, 1) if total else 0,
            "Live market": market,
            "Missing in": missing,
            "On my CV": "✅" if owned else "❌",
            "Priority": round((missing * 2 + demanded + market) / (2 if owned else 1), 1),
        })
    report = pd.DataFrame(rows)
    if report.empty:
        return
    report = report.sort_values("Priority", ascending=False)

    with st.expander("🧬 CV Gap Report — exhaustive analysis of your CV vs every applied job + the live market", expanded=False):
        st.caption(f"Mined from {total} applications (titles + scraped JDs) and the live-market "
                   f"cache, cross-referenced against your curated CV.")
        gap_ai = None
        if st.session_state.get("gen_gap"):
            gap_ai = ai_gap_narrative(report)
        elif ai_want():
            if st.button("🤖 Generate AI action plan", key="gen_gap_btn", width="stretch",
                         help="AI-prioritized next steps for closing your top gaps — one free "
                              "API call, cached after the first time."):
                st.session_state["gen_gap"] = True
                st.rerun()
        if gap_ai:
            st.markdown(gap_ai)
            st.caption("🤖 AI-prioritized action plan — the numbers above are from your data")
        top = report.head(6)
        for _, r in top.iterrows():
            if r["Missing in"] > 0:
                st.markdown(
                    f"- **{r['Skill']}** — demanded in {r['% of pipeline']}% of your pipeline "
                    f"({r['Demanded in']} roles), still missing in {r['Missing in']}. "
                    + ("Already on your CV — deepen the project evidence."
                       if r["On my CV"] == "✅" else
                       "**Not on your CV** — one targeted project/certification closes this gap."))
            else:
                st.markdown(f"- ✅ **{r['Skill']}** — demanded in {r['% of pipeline']}% of your "
                            f"pipeline ({r['Live market']} live listings) and covered by your CV. "
                            f"Keep leading with it.")
        st.dataframe(report, width="stretch", hide_index=True,
                     column_config={
                         "% of pipeline": st.column_config.NumberColumn(format="%.1f%%"),
                     })
        st.download_button(
            "⬇️ Export gap report CSV",
            data=report.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"cv_gap_report_{datetime.now():%Y%m%d}.csv",
            mime="text/csv",
        )
        chart_df = report[["Skill", "Demanded in", "Live market", "Missing in"]].set_index("Skill").head(10)
        fig = px.bar(chart_df, barmode="group",
                     color_discrete_map={"Demanded in": "#4F8CFF", "Live market": "#22D3EE",
                                         "Missing in": "#EF553B"},
                     labels={"value": "Roles", "index": ""})
        style_fig(fig, 340)
        fig.update_layout(legend_title_text="")
        st.plotly_chart(fig, width="stretch")


def render_company_dupes(valid: pd.DataFrame):
    """Where the same employer gets multiple applications — an awareness + consolidation view."""
    if valid.empty:
        return
    g = valid.groupby("company_name").agg(
        Applications=("thread_id", "count"),
        Roles=("role_title", lambda s: " · ".join(sorted({str(x) for x in s if str(x) not in ("", "nan")}))[:200]),
        AvgFit=("fit_score", "mean"),
    ).reset_index()
    g = g[g["Applications"] > 1].sort_values("Applications", ascending=False)
    if g.empty:
        return
    g["AvgFit"] = g["AvgFit"].round(1)
    with st.expander(f"🏢 Employers you've applied to multiple times ({len(g)})", expanded=False):
        st.caption("More applications ≠ better odds at the same firm. Pick the strongest fit, "
                   "tailor it hard, and chase a referral or recruiter ping for that one.")
        strat = None
        if st.session_state.get("gen_company"):
            strat = ai_company_strategy(valid)
        elif ai_want():
            if st.button("🤖 Generate AI strategy", key="gen_company_btn", width="stretch",
                         help="AI advice for handling multiple applications at the same employer — "
                              "one free API call, cached after the first time."):
                st.session_state["gen_company"] = True
                st.rerun()
        if strat:
            st.markdown(strat)
            st.caption("🤖 AI strategy — based on the employers and roles above")
        st.dataframe(
            g, width="stretch", hide_index=True,
            column_config={
                "AvgFit": st.column_config.ProgressColumn("Avg Fit", min_value=0, max_value=100,
                                                          format="%.0f%%"),
            },
        )


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
        act_ai = None
        if st.session_state.get("gen_activity"):
            act_ai = ai_activity_summary(feed)
        elif ai_want():
            if st.button("🤖 Generate AI momentum summary", key="gen_activity_btn", width="stretch",
                         help="AI summary of your recent activity + next best move — one free API "
                              "call, cached after the first time."):
                st.session_state["gen_activity"] = True
                st.rerun()
        if act_ai:
            st.markdown(act_ai)
            st.caption("🤖 AI momentum summary")
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
    # re-fit the enriched rows
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        updated_rows = conn.execute(
            "SELECT thread_id, role_title, job_description_snippet FROM applications WHERE is_valid=1"
        ).fetchall()
        conn.close()
        results = {r["thread_id"]: matcher.analyze(r["role_title"], r["job_description_snippet"] or "")
                   for r in updated_rows}
        storage.save_fit_results(results)
    except Exception:
        pass
    st.cache_data.clear()
    st.rerun()


def run_jd_lookup(valid: pd.DataFrame):
    """Find live postings matching tracked jobs, pull their JDs AND repair broken links.

    Targets = rows missing JD text OR carrying a broken posting link (e.g. JobStreet
    tracking links that resolve to a logo). Matched rows get the live posting URL
    (+ description when missing); broken links with no live match are cleared so the
    UI never offers a dead logo link again.
    """
    import jd_lookup

    url_vals = valid["job_url"].astype(str)
    thin = valid[valid["job_description_snippet"].str.len() < 240]
    broken = valid[url_vals.apply(lambda u: is_broken_job_url(u))]
    targets = pd.concat([thin, broken]).drop_duplicates(subset="thread_id")
    targets = targets[~targets["role_title"].isna()]
    if targets.empty:
        st.toast("Nothing to look up or repair — every tracked job has JD text and its posting "
                 "link opens a real page", icon="✅")
        return
    snippet_len = {r["thread_id"]: len(str(r["job_description_snippet"] if r["job_description_snippet"] is not None else ""))
                   for _, r in targets.iterrows()}
    tid_good_url = {tid: ("" if is_broken_job_url(u) else str(u))
                    for tid, u in zip(valid["thread_id"], url_vals)}
    broken_ids = set(broken["thread_id"])
    pairs = [(r["thread_id"], r["role_title"], r["company_name"]) for _, r in targets.iterrows()]
    with st.status(f"🔎 Searching live listings for {len(pairs)} job(s) and repairing links...",
                   expanded=True) as status:
        log = st.empty()
        results = jd_lookup.lookup_batch(
            pairs, max_workers=6,
            on_progress=lambda d, t: log.markdown(f"Fetched role pools `{d}/{t}`"))
        matched = {k: v for k, v in results.items() if v}
        fixed = 0
        snippets_filled = 0
        cleared = 0
        for tid, hit in matched.items():
            desc = (hit.get("description") or "").strip()
            if desc and snippet_len.get(tid, 0) < 240:
                storage.update_snippet(tid, desc)
                snippets_filled += 1
            # only repoint the URL when the stored one is broken/missing — never overwrite a
            # working link to the exact posting you applied to.
            if not tid_good_url.get(tid, ""):
                new_url = (hit.get("url") or "").strip()
                if new_url and not is_broken_job_url(new_url):
                    storage.set_job_url(tid, new_url)
                    fixed += 1
        # broken links we could not replace should not keep offering a logo page
        for tid in broken_ids:
            hit = matched.get(tid)
            replaced = bool(hit and (hit.get("url") or "").strip()
                            and not is_broken_job_url(hit.get("url")))
            if not replaced:
                if storage.set_job_url(tid, ""):
                    cleared += 1
        status.update(
            label=f"✅ Replaced {fixed} broken link(s) with live pages · filled "
                  f"{snippets_filled} JD(s) · cleared {cleared} dead logo link(s) · "
                  f"{len(targets) - fixed - cleared} unmatched (posting may be offline)",
            state="complete")
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT thread_id, role_title, job_description_snippet FROM applications WHERE is_valid=1"
        ).fetchall()
        conn.close()
        results_fit = {r["thread_id"]: matcher.analyze(r["role_title"], r["job_description_snippet"] or "")
                       for r in rows}
        storage.save_fit_results(results_fit)
    except Exception:
        pass
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
        avg = rows["fit_score"].dropna()
        progressed = int(rows["current_status"].isin(
            ["Assessment / OA", "Interview", "Offer"]).sum())
        rejected = int((rows["current_status"] == "Rejected").sum())
        stats = [f"**{len(rows)}** application(s)"]
        if len(avg):
            stats.append(f"avg fit **{avg.mean():.0f}%**")
        stats.append(f"**{progressed}** progressed · **{rejected}** rejected")
        st.caption(" · ".join(stats))
        shown = rows.sort_values("application_date", ascending=False).head(12)
        for _, row in shown.iterrows():
            fit = row.get("fit_score")
            chips = [fit_chip(fit) if pd.notna(fit) else
                     "<span class='fit-chip fit-amber'>—</span>",
                     f"<span class='compact-meta'>{esc(row['current_status'])} · "
                     f"{esc(row.get('job_type') or '—')}</span>"]
            missing = (row.get("missing_skills") or [])
            if missing:
                chips.append(f"<span class='fit-chip fit-red'>❌ {esc(missing[0])}</span>")
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
    """Full application detail: fit, gaps, JD text, requirements — for dialogs."""
    snippet = str(row.get("job_description_snippet", "") or "")
    snippet = snippet.replace("%str_to_replace_open_tracking%", "")
    snippet = "" if snippet.lower() in ("", "nan") else snippet.strip()
    missing = row.get("missing_skills") or []
    matched = row.get("matched_skills") or []

    bcol, _ = st.columns([0.16, 4.2], vertical_alignment="center")
    with bcol:
        if st.button("← Back", key=f"{key_prefix}_back", width="stretch",
                     help="Close this pop-up and deselect the row"):
            st.session_state["_detail_tids"] = None
            st.session_state["_table_seq"] = int(st.session_state.get("_table_seq", 0)) + 1
            st.rerun()

    st.markdown(f"### {esc(row['company_name'])} — {esc(role_display(row['role_title']))}")
    fit = row.get("fit_score")
    chips = [fit_chip(fit) if pd.notna(fit) else "<span class='fit-chip fit-amber'>—</span>"]
    chips += [f"<span class='fit-chip fit-green'>{esc(m)}</span>" for m in matched[:3]]
    chips += [f"<span class='fit-chip fit-red'>{esc(m)}</span>" for m in missing[:3]]
    st.markdown(" ".join(chips), unsafe_allow_html=True)
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
            reqs = matcher.extract_requirements(snippet)
            if reqs:
                st.markdown("**Role requirements (extracted)**")
                for r in reqs:
                    st.markdown(f"- {esc(r)}")
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
        gap_analysis = render_gap_analysis(
            row["role_title"], snippet,
            fallback_matched=matched, fallback_missing=missing,
            fallback_tips=row.get("actionable_improvements") or [],
        )
        if ai_want():
            if st.button("🤖 AI: why this score & how to improve",
                         key=f"{key_prefix}_ai_fit",
                         help="One free API call, cached after the first time."):
                st.session_state[f"gen_fit_{row['thread_id']}"] = True
        if st.session_state.get(f"gen_fit_{row['thread_id']}"):
            fit_ai = ai_fit_explanation(row["role_title"], snippet, gap_analysis)
            if fit_ai:
                st.markdown("**🤖 Why this score & how to improve**")
                st.markdown(fit_ai)
            else:
                st.caption("AI returned nothing usable — the deterministic tips above apply.")
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
    table["Fit Score"] = table["fit_score"].where(table["fit_score"].notna(), None)

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
            "Fit Score": st.column_config.ProgressColumn("Fit Score", min_value=0, max_value=100,
                                                         format="%.0f%%"),
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
        if shown and ai_want():
            if st.session_state.get("gen_drafts"):
                drafts = ai_drafts(shown)
            elif st.button("🤖 Generate AI drafts", key="gen_drafts_btn", width="stretch",
                           help="Writes tailored follow-up emails for the roles below — one free "
                                "API call, cached after the first time."):
                st.session_state["gen_drafts"] = True
                st.rerun()
        for row, tier, wait in shown:
            with st.container(border=True):
                meta, actions = st.columns([3, 1.05], vertical_alignment="center")
                with meta:
                    st.markdown(
                        f"**{esc(row['company_name'])}** — {esc(role_display(row['role_title']))}  \n"
                        + " ".join([
                            _status_pill("Nudge" if tier == "nudge" else "Ghosted"),
                            f"<span class='fit-chip fit-{ 'green' if (row.get('fit_score') or 0) >= 75 else ('amber' if (row.get('fit_score') or 0) >= 50 else 'red') }'>"
                            f"{(row.get('fit_score') or 0):.0f}% Fit</span>"
                            if pd.notna(row.get("fit_score")) else "",
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
                    if st.session_state.get("gen_drafts"):
                        if ai_want() and str(row["thread_id"]) not in drafts:
                            err = ai.last_error()
                            st.caption("AI draft unavailable for this row — showing the template."
                                       + (f"  ({err})" if err else ""))
                    elif ai_want():
                        st.caption("Click **Generate AI drafts** above for AI-tailored emails.")
        if len(queue) > 6:
            st.caption(f"+ {len(queue) - 6} more — select their rows in the tracker to update status.")


def render_saved_pipeline(saved: pd.DataFrame):
    """Saved / Planning rows live OUTSIDE the main KPIs; manage them here."""
    if saved.empty:
        return
    with st.expander(f"🗂 Saved / Planning — {len(saved)} role(s) from Live Matches", expanded=False):
        st.caption("These live outside the funnel/KPIs until you actually apply. Click "
                   "**Mark Applied** when you submit — it stamps today as the application "
                   "date and the row joins your tracker & analytics.")
        for _, row in saved.iterrows():
            with st.container(border=True):
                meta, actions = st.columns([3, 1.05], vertical_alignment="center")
                with meta:
                    chips = [f"<span class='compact-meta'>{esc(row.get('source_platform', ''))} · "
                             f"added {row['application_date']:%d %b %Y}</span>"]
                    if pd.notna(row.get("fit_score")):
                        chips.insert(0, fit_chip(row.get("fit_score")))
                    note = row.get("notes") or ""
                    if note:
                        chips.append(f"<span class='compact-meta'>🗒 {esc(note[:80])}</span>")
                    st.markdown(f"**{esc(row['company_name'])}** — {esc(role_display(row['role_title']))}  \n"
                                + " ".join(chips), unsafe_allow_html=True)
                    links = _row_links_html(row)
                    if links:
                        st.markdown(links)
                with actions:
                    if st.button("✅ Mark Applied", key=f"saved_app_{row['thread_id']}"):
                        res = storage.mark_applied(row["thread_id"])
                        st.cache_data.clear()
                        st.toast("Moved to Applied — recorded today as application date" if res == "applied"
                                 else "Marked as Applied")
                        st.rerun()
                    if st.button("🗑 Remove", key=f"saved_del_{row['thread_id']}",
                                 help="Delete this saved role from the tracker"):
                        storage.delete_application(row["thread_id"])
                        st.cache_data.clear()
                        st.toast("Removed from tracker")
                        st.rerun()
                with st.expander("🧬 Fit breakdown & notes"):
                    desc = row.get("job_description_snippet") or ""
                    render_gap_analysis(row["role_title"], desc,
                                        fallback_matched=row.get("matched_skills") or [],
                                        fallback_missing=row.get("missing_skills") or [],
                                        fallback_tips=row.get("actionable_improvements") or [])
                    render_notes_editor(row["thread_id"], note or "",
                                        key_prefix=f"saved_{row['thread_id'][-12:]}")


def render_discovery_tab():
    jobs = load_discovered_cached(DB_PATH, mtime_of(DB_PATH)) if os.path.exists(DB_PATH) else []
    tracked = set()
    if os.path.exists(DB_PATH):
        try:
            tracked = storage.get_tracked_discovery_keys()
        except Exception:
            tracked = set()

    top = st.columns([1.1, 3])
    with top[0]:
        scan_clicked = st.button("🔄 Scan Live Market", type="primary",
                                 disabled=st.session_state.get("scan_running", False),
                                 width="stretch")
    with top[1]:
        if jobs:
            last = jobs[0].get("discovered_at", "?")
            sources = {j.get("source") for j in jobs}
            st.caption(f"Cache: **{len(jobs)}** live roles (scanned {last}) · sources: "
                       f"{', '.join(sorted(sources))} — JobStreet/Hiredly may be empty from "
                       f"non-MY networks; rescan from a Malaysian connection for full coverage.")
            if ai_want():
                if st.button("🤖 Generate AI market read", key="gen_market_btn", width="stretch",
                             help="AI summary of the cached live roles — one free API call, "
                                  "cached after the first time."):
                    st.session_state["gen_market"] = True
                    st.rerun()
            market_ai = ai_market_summary(jobs) if st.session_state.get("gen_market") else None
            if market_ai:
                st.markdown(market_ai)
                st.caption("🤖 AI read on the live market — role cards below are your data")
        else:
            st.caption("No cached market data yet — click **Scan Live Market** to pull live "
                       "Malaysian roles (no API keys needed).")
    if scan_clicked:
        run_market_scan()

    with st.container():
        f1, f2, f3 = st.columns([1.4, 1, 1.2])
        roles = f1.multiselect("Target Role", options=DISCOVERY_ROLES,
                               default=DISCOVERY_ROLES, key="disc_roles")
        min_fit = f2.slider("Minimum Fit Score %", 0, 100, 40, step=5, key="disc_minfit")
        platforms_present = sorted({j.get("source", "") for j in jobs} - {""})
        chosen_platforms = f3.multiselect("Platform", options=platforms_present,
                                          default=platforms_present, key="disc_platforms")

    if not jobs:
        st.info("Run a scan to populate live listings. Results are cached in SQLite, "
                "so re-filtering is instant afterwards.")
        return

    rows = []
    for job in jobs:
        if roles and job.get("search_role") not in roles:
            continue
        if chosen_platforms and job.get("source") not in chosen_platforms:
            continue
        if job.get("fit_score") is None or (job.get("fit_score") or 0) < min_fit:
            continue
        rows.append(job)

    st.caption(f"**{len(rows)}** live roles match your filters (ranked by Fit Score %). "
               f"Expand **JD & Fit Breakdown** on any card for requirements and improvements.")
    if not rows:
        st.info("No live roles above the minimum fit score. Lower the slider or rescan.")
        return
    st.download_button(
        "⬇️ Export filtered matches CSV",
        data=pd.DataFrame(rows).to_csv(index=False).encode("utf-8-sig"),
        file_name=f"live_matches_{datetime.now():%Y%m%d_%H%M}.csv",
        mime="text/csv",
    )

    for job in rows[:60]:
        key = job["job_key"]
        with st.container(border=True):
            c1, c2, c3 = st.columns([0.9, 3, 1.5], vertical_alignment="center")
            with c1:
                st.markdown(fit_chip(job.get("fit_score")), unsafe_allow_html=True)
            with c2:
                st.markdown(f"**{esc(job.get('title', 'Untitled'))}**")
                st.markdown(
                    f"{esc(job.get('company', 'Unknown'))} · `{esc(job.get('source', ''))}` · "
                    f"{esc(job.get('location', '') or '—')}"
                    + (f" · posted {esc(job.get('posted_date', ''))}" if job.get("posted_date") else "")
                )
                missing = _parse_json_list(job.get("missing_skills"))
                if missing:
                    st.markdown("**Top missing:** " + " ".join(
                        f"<span class='fit-chip fit-red'>{esc(m)}</span>" for m in missing[:2]),
                        unsafe_allow_html=True)
                matched = _parse_json_list(job.get("matched_skills"))
                if matched:
                    st.markdown("**Matched:** " + " ".join(
                        f"<span class='fit-chip fit-green'>{esc(m)}</span>" for m in matched[:4]),
                        unsafe_allow_html=True)
            with c3:
                url = safe_url(job.get("url", ""))
                already = key in tracked
                if url and not already:
                    st.link_button("Apply on Platform ↗", url, width="stretch")
                elif url:
                    st.link_button("Apply on Platform ↗", url, width="stretch", type="secondary")
                elif not already:
                    st.button("Apply on Platform ↗", disabled=True, width="stretch",
                              key=f"apply_na_{key}")
                if already:
                    st.button("✓ Already in tracker", disabled=True, width="stretch",
                              help="In your Saved / Planning pipeline — open it under "
                                   "🗂 Saved / Planning on the Applications tab.")
                elif st.button("＋ Add to My Tracker", width="stretch", key=f"add_{key}"):
                    payload = dict(job)
                    payload["matched_skills"] = _parse_json_list(job.get("matched_skills"))
                    payload["missing_skills"] = _parse_json_list(job.get("missing_skills"))
                    result = storage.add_manual_application(payload)
                    if result == "exists":
                        st.toast("Already in your tracker", icon="ℹ️")
                    else:
                        st.toast(f"Added: {job.get('title', '')[:40]} (Saved / Planning)", icon="✅")
                        st.cache_data.clear()
                        st.rerun()

            with st.expander("📄 View JD & Fit Breakdown"):
                desc = job.get("description") or ""
                if desc:
                    st.markdown(f"<div class='jd-block'>{esc(desc[:1200])}</div>",
                                unsafe_allow_html=True)
                    reqs = matcher.extract_requirements(desc)
                    if reqs:
                        st.markdown("**📑 Role requirements (extracted)**")
                        for r in reqs:
                            st.markdown(f"- {esc(r)}")
                    st.markdown("### 🧬 Your fit for this role")
                    render_gap_analysis(job.get("title", ""), desc,
                                        fallback_matched=_parse_json_list(job.get("matched_skills")),
                                        fallback_missing=missing)
                elif url:
                    st.caption("No description cached for this listing — fetch it from the posting:")
                    if st.button("⬇ Fetch JD from posting (OG scrape, 4s)", key=f"fetch_{key}"):
                        with st.spinner("Fetching job page..."):
                            payload = enricher.enrich(url)
                        if payload.get("ok"):
                            fetched = f"{payload.get('og_title', '')} — {payload.get('og_description', '')}".strip(" —")
                            extra = " ".join(f"[{k}: {payload[k]}]" for k in
                                             ("company", "location", "salary", "employment_type")
                                             if payload.get(k))
                            if extra:
                                fetched = f"{fetched} {extra}".strip()
                            analysis = matcher.analyze(job.get("title", ""), fetched)
                            storage.update_discovered_description(key, fetched, analysis)
                            st.toast("JD fetched & re-scored against your CV")
                            st.cache_data.clear()
                            st.rerun()
                        else:
                            st.warning(f"Could not fetch ({payload.get('reason')}) — the platform "
                                       f"blocks scrapers. Use the Apply link to read the JD, then "
                                       f"paste key lines into your CV drawer to re-score.")
                else:
                    st.caption("No description or URL available for this listing.")


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
    for name in ("main", "storage", "parser", "matcher", "gmail_fetcher", "enricher",
                 "discovery", "jd_lookup", "ai", "repair"):
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
    # If a previous sync/scan was aborted (browser closed mid-run, crash, tunnel drop), the flag
    # can be left True in the session, which would disable the buttons forever. A fresh run never
    # legitimately starts with these True — so clear them.
    st.session_state["sync_running"] = False
    st.session_state["scan_running"] = False

    st.title("🎯 Job Application Suite")
    header_cap = st.caption("Personal analytics for your job hunt — loading data…")
    sync_zone = st.empty()  # live sync progress is rendered here (main column, always visible)

    with st.sidebar:
        # --- chart interaction mode (must run before the charts) ---
        anim = st.toggle("🎬 Chart hover animations", value=True,
                         help="ON: bars, dots and funnel slices enlarge + brighten under the "
                              "cursor (native tooltips are not available in this mode — the "
                              "numbers are printed on the charts).  \n"
                              "OFF: hover shows info tooltips and clicking a chart element "
                              "opens its inline insights below the chart. Donuts always have both.")
        st.markdown(
            "<style>" + (PE_CSS_ANIM if anim else "") + "</style>",
            unsafe_allow_html=True)

        st.header("⚡ Sync")
        running = st.session_state.get("sync_running", False)
        force_full = st.toggle("Force Full Re-sync", value=False,
                               help="Re-evaluates the whole mailbox with the current filter rules "
                                    "— every cached email is re-parsed offline and any missing "
                                    "body is re-downloaded once, so improved classification "
                                    "recovers older false negatives. Use after filter/CV changes.")
        if st.button("▶ Sync Gmail Now", type="primary", disabled=running,
                     width="stretch",
                     help="Fetches new emails, filters noise, scrapes JDs, re-scores CV fit"):
            run_sync(force_full, zone=sync_zone)
        summary = st.session_state.get("last_sync_summary")
        if summary:
            st.caption(f"Last sync: **{summary['elapsed']:.1f}s** · found **{summary.get('found', 0)}** · "
                       f"{summary['inserted']} new · {summary['updated']} updated · "
                       f"{summary['garbage']} noise · {summary['fit_scored']} fit · "
                       f"{summary.get('ai_classified', 0)} AI-classified")
            deep = int(summary.get("bodies_cached", 0)) + int(summary.get("bodies_refetched", 0))
            if deep:
                st.caption(f"🔎 Deep re-parse: {summary.get('bodies_cached', 0)} cached · "
                           f"{summary.get('bodies_refetched', 0)} re-downloaded")
            if summary.get("truncated"):
                st.warning("Search hit the message cap — some mailbox items may be missing. "
                           "Raise GMAIL_MAX_RESULTS and re-run the sync.")

        with st.expander("🛠 Maintenance", expanded=False):
            m1, m2 = st.columns(2)
            if m1.button("🧬 Re-score all", width="stretch",
                         help="Re-run the CV fit analyzer over every application + cached market "
                              "role — use after editing your CV in the drawer below"):
                run_rescore()
            if m2.button("👻 Ghost stale >{}d".format(stale_days()), width="stretch",
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
                              "profile + AI cache into backups/<timestamp>/ — safe before big "
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

        st.header("🤖 AI")
        providers_opts = ai.providers()
        cur_provider = st.session_state.get("ai_provider", ai.default_provider())
        prov_idx = providers_opts.index(cur_provider) if cur_provider in providers_opts else 0
        st.selectbox("Provider", options=providers_opts, index=prov_idx, key="ai_provider",
                     help="Free providers (no billing). Gemini is the most stable free tier; "
                          "Ollama runs fully locally & privately. 'Auto' uses OPENAI_* env vars "
                          "or a local Ollama.")
        provider = st.session_state["ai_provider"]
        # reset the model picker when the provider changes (models differ between providers)
        if st.session_state.get("ai_provider_prev") != provider:
            st.session_state.pop("ai_model", None)
        st.session_state["ai_provider_prev"] = provider

        if provider == "Auto (env or Ollama)":
            api_key = os.environ.get("OPENAI_API_KEY", "")
        elif provider == "Ollama (local)":
            preset_models = ai.provider_models(provider)
            cur_m = st.session_state.get("ai_model") or (preset_models[0] if preset_models else "")
            if preset_models and cur_m and cur_m not in preset_models:
                st.session_state.pop("ai_model", None)
                cur_m = preset_models[0]
            if preset_models:
                m_idx = preset_models.index(cur_m) if cur_m in preset_models else 0
                st.selectbox("Model", options=preset_models, index=m_idx, key="ai_model")
            else:
                st.caption("Ollama not detected — install it and run `ollama pull llama3.2:3b`, "
                           "then refresh.")
            api_key = ""
        else:
            api_key = st.text_input(f"{provider} API key", type="password", key="ai_api_key",
                                    help=ai.provider_key_hint(provider))
            loaded_key = api_key or os.environ.get("OPENAI_API_KEY", "")
            if loaded_key:
                st.caption(f"Loaded: {ai.key_preview(loaded_key)}")
            if not loaded_key:
                signup = ai.provider_signup(provider)
                st.caption(f"[get a free key here]({signup})")
            preset_models = ai.provider_models(provider, api_key or None)
            cur_m = st.session_state.get("ai_model") or (preset_models[0] if preset_models else "")
            if preset_models and cur_m and cur_m not in preset_models:
                st.session_state.pop("ai_model", None)
                cur_m = preset_models[0]
            elif preset_models and cur_m:
                # auto-upgrade away from retired/older generations (e.g. 2.5-flash -> 3.6-flash)
                cur_ver = ai.model_version(cur_m)
                best_ver = ai.model_version(preset_models[0])
                if cur_ver is not None and best_ver is not None and best_ver > cur_ver:
                    st.session_state.pop("ai_model", None)
                    cur_m = preset_models[0]
            if preset_models:
                m_idx = preset_models.index(cur_m) if cur_m in preset_models else 0
                st.selectbox("Model", options=preset_models, index=m_idx, key="ai_model")
            else:
                st.text_input("Model name", key="ai_model")
        st.toggle("Enable AI analysis", value=True, key="ai_enabled",
                  help="Replaces deterministic tips/drafts with AI-generated analysis. Free-tier "
                       "responses are cached on disk, so re-renders and restarts cost nothing.")
        with st.expander("⚙️ Sync automation", expanded=False):
            st.toggle("🤖 AI fit scores (sync)", value=True, key="ai_fit_ai",
                      help="During sync/backfill, score CV fit with the AI (batched, persisted "
                           "with fit_source='ai', never recomputed). Deterministic stays the "
                           "fallback.")
            st.toggle("🤖 AI classify emails (sync)", value=True, key="ai_classify_ai",
                      help="During sync, the AI decides if each email is a real application and "
                           "advances its status using thread memory — recovers applications the "
                           "deterministic filter missed. Persisted (ai_classified), never re-run "
                           "on incremental syncs.")
            st.caption("Cost-safe: batched, persisted to the DB, deterministic fallback.")
        ai.set_provider(provider, api_key or None, st.session_state.get("ai_model"))
        if ai.available():
            st.caption(f"🟢 {ai.provider_label()}")
        else:
            st.caption("⚠️ AI not ready — deterministic mode. Add a free key or install Ollama.")
        t1, t2 = st.columns(2)
        if t1.button("🧪 Test AI", width="stretch",
                     help="Send one tiny request to confirm the key + model actually work."):
            run_ai_test()
            st.rerun()
        if t2.button("🗑 Clear cache", width="stretch",
                     help="Forget cached AI responses — use after changing your CV/data."):
            n = ai.clear_cache()
            st.toast(f"Cleared {n} cached AI response(s)")
            st.rerun()
        test = st.session_state.get("ai_test_result")
        if test:
            status_r, detail, label = test
            st.caption(f"✅ AI OK — {label}" if status_r == "ok" else f"❌ AI failed — {detail[:120]}")

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
            ensure_fit_scores(df)
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
                       f"{'shown below' if show_noise else 'hidden'}"
                       + (f" · {int((df['current_status'] == 'Saved / Planning').sum())} saved from discovery"
                          if (df['current_status'] == 'Saved / Planning').any() else ""))

        if valid is not None and not valid.empty:
            search_all = st.text_input(
                "🔍 Search (company · role · subject · JD)", "",
                help="Filters the tracker to rows whose company, role, email subject or job "
                     "description contains the text (case-insensitive).")
            min_fit = st.slider("Minimum CV Fit %", 0, 100, 0, step=5,
                                help="Hide rows whose stored CV fit is below this. "
                                     "Rows not yet scored always stay visible.")

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
        render_cv_editor()

        st.divider()
        render_ipad_section()

    noise_view = None
    saved_df = None
    if raw is not None:
        df_all = normalize(raw)
        if show_noise:
            noise_view = df_all[~df_all["is_valid"]].copy()
        saved_df = df_all[(df_all["is_valid"]) & (df_all["current_status"] == "Saved / Planning")].copy()

    if raw is None or valid is None or valid.empty:
        if raw is None:
            header_cap.caption("Personal analytics for your job hunt — connect your Gmail to get "
                               "started. Everything runs from this browser.")
        elif saved_df is not None and not saved_df.empty:
            header_cap.caption(f"Personal analytics for your job hunt — {len(saved_df)} saved "
                               "role(s) from Live Matches, no applications sent yet.")
        else:
            header_cap.caption("Personal analytics for your job hunt — no valid applications yet. "
                               "Sync Gmail or add roles from the Live Matches tab.")
        tab1, tab2 = st.tabs(["📊 My Applications", "🌐 Live Job Matches"])
        with tab1:
            if show_noise and noise_view is not None and not noise_view.empty:
                render_noise_editor(noise_view)
            if raw is None:
                st.info("No data yet — hit **▶ Sync Gmail Now** in the sidebar.")
            else:
                st.info("No valid applications yet — hit **▶ Sync Gmail Now** (toggle "
                        "**Force Full Re-sync** to re-classify the full mailbox), or turn on "
                        "**Show Filtered Out Noise / Spam** to inspect and recover what was dropped.")
                render_saved_pipeline(saved_df)
        with tab2:
            render_discovery_tab()
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
    if min_fit > 0:
        view = view[(view["fit_score"].isna()) | (view["fit_score"] >= min_fit)]

    view = view.sort_values("application_date", ascending=False)

    header_cap.caption(f"Personal analytics for your job hunt — **{len(valid)}** valid "
                       f"applications · **{len(saved_df) if saved_df is not None else 0}** saved "
                       f"· **{len(view)}** shown. Live Malaysian market discovery + CV-tailored "
                       "fit scoring. Everything runs from this browser.")

    tab1, tab2 = st.tabs(["📊 My Applications", "🌐 Live Job Matches"])
    with tab1:
        if show_noise and noise_view is not None and not noise_view.empty:
            render_noise_editor(noise_view)
        render_applications_tab(valid, view, stale_count=int(valid.attrs.get("stale_count", 0) or 0))
        render_saved_pipeline(saved_df)
    with tab2:
        render_discovery_tab()


if __name__ == "__main__":
    main()
