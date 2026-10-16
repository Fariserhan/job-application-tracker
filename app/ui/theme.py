"""Design tokens shared by the stylesheet, the Plotly template and the HTML components.

Editorial "paper" palette, pinned so the app, the Excel workbook and the Power BI theme
match. One accent (ink blue) marks *you / the story*; warm greys carry context; a sober
green and a brick red carry outcome. Validated with the data-viz palette checker on the
paper surface. One deliberate deviation from the pinned list: negative is ``#C04A32``
rather than ``#B3412E``: the original pair against positive ``#2E7D4F`` failed deutan
separation (ΔE 5.4); the shifted brick reaches ΔE 6.8, which the method allows *with*
secondary encoding, and every offer/rejection mark in the app is direct-labelled.
Context greys sit under 3:1 on paper by design, so they too are always labelled.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from jobtracker import domain

ASSETS = Path(__file__).resolve().parent.parent / "assets"
CSS_FILE = ASSETS / "style.css"
CSS_VERSION = "3.1.0"

# ---------------------------------------------------------------- paper & ink
PAPER = "#F7F5F0"
SURFACE = "#FFFFFF"
INK = "#191919"
INK_2 = "#5F5B54"
INK_3 = "#8A857C"
RULE = "#E3DED5"
GREY = "#C9C3B8"
GREY_DARK = "#A39E94"

ACCENT = "#1D3FBB"
ACCENT_SOFT = "#DCE3FA"
POSITIVE = "#2E7D4F"
NEGATIVE = "#C04A32"
CAUTION = "#B7791F"

SERIF = "Newsreader, Georgia, 'Times New Roman', serif"
SANS = "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"

# Sequential (one hue, light → dark) for heatmaps and the activity calendar.
SEQUENTIAL = [[0.0, "#EFEBE3"], [0.0001, ACCENT_SOFT], [0.35, "#9AABEB"],
              [0.7, "#5C77D6"], [1.0, ACCENT]]
CAL_STEPS = ["#ECE8E0", ACCENT_SOFT, "#9AABEB", "#5C77D6", ACCENT]

# ---------------------------------------------------------------- status semantics
STATUS_COLORS: dict[str, str] = {
    domain.SAVED: "#D8D2C7",
    domain.APPLIED: GREY_DARK,
    domain.ASSESSMENT: "#6B7FD7",
    domain.INTERVIEW: ACCENT,
    domain.OFFER: POSITIVE,
    domain.REJECTED: NEGATIVE,
    domain.GHOSTED: GREY,
    domain.WITHDRAWN: INK_3,
}
# Flow-chart labels that are not statuses themselves.
FLOW_COLORS: dict[str, str] = {
    "Applied": GREY_DARK,
    "Assessment": STATUS_COLORS[domain.ASSESSMENT],
    "Interview": ACCENT,
    "Offer": POSITIVE,
    "Rejected": NEGATIVE,
    "Ghosted": GREY,
    "Withdrawn": INK_3,
    "Awaiting reply": "#D8D2C7",
    "In progress": "#9AABEB",
}
SHORT_STATUS = {domain.ASSESSMENT: "Assessment", domain.SAVED: "Saved"}


def status_color(status: str | None) -> str:
    return STATUS_COLORS.get(domain.normalise_status(status), INK_3)


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def load_css() -> None:
    """Inject the one versioned stylesheet, once per run, from the entry point.

    ``st.html`` with only a ``<style>`` tag goes to Streamlit's event container, so the
    stylesheet takes no vertical space in the page.
    """
    css = CSS_FILE.read_text(encoding="utf-8")
    # DOMPurify (SAFE_FOR_XML) drops a style block containing anything tag-like, so the
    # stylesheet must never contain "<"; use CSS escapes (\3C) instead.
    if "<" in css:
        raise ValueError("style.css must not contain '<' (use the CSS escape \\3C)")
    st.html(f'<style data-jt-css="{CSS_VERSION}">{css}</style>')
