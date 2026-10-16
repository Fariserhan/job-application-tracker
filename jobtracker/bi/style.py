"""One palette for every BI surface: the app, the Excel workbook and the Power BI theme.

Light editorial direction (FT / Economist-like): warm paper, ink text, hairline rules and a
single accent blue used sparingly - headline KPIs and the "you" series. Benchmarks and other
context series are drawn in warm greys so the reader's eye goes to their own numbers first.
"""
from __future__ import annotations

# Canvas and text
PAPER = "F7F5F0"        # page / canvas
SURFACE = "FFFFFF"      # cards, tiles, charts
INK = "191919"          # primary text
SECONDARY = "5F5B54"    # secondary text, axis labels
MUTED = "8A857C"        # notes, captions
RULE = "E3DED5"         # hairline borders, gridlines

# Accent and semantics
ACCENT = "1D3FBB"       # headline KPIs, the "you" series
ACCENT_SOFT = "DCE3FA"  # accent tint (input cells, soft highlights)
POSITIVE = "2E7D4F"
NEGATIVE = "C04A32"
CAUTION = "B7791F"
CONTEXT = "C9C3B8"      # context / benchmark series (light)
CONTEXT_DARK = "A39E94"  # context / benchmark series (dark)

# Pipeline statuses (shared with the app).
STATUS: dict[str, str] = {
    "Applied": "A39E94",
    "Assessment / OA": "6B7FD7",
    "Interview": "1D3FBB",
    "Offer": "2E7D4F",
    "Rejected": "C04A32",
    "Ghosted": "C9C3B8",
    "Withdrawn": "8A857C",
}

# Categorical order for charts: the accent first, then the status palette.
CATEGORICAL = [ACCENT, "6B7FD7", POSITIVE, NEGATIVE, CAUTION, CONTEXT_DARK, CONTEXT, MUTED,
               SECONDARY]

HEADING_FONT = "Georgia"   # Newsreader in the app; Georgia is the Office-safe fallback
BODY_FONT = "Calibri"      # Inter in the app; Calibri / Segoe UI in Office


def tint(hex_colour: str, amount: float) -> str:
    """Mix a colour with white: amount 0 = unchanged, 1 = white."""
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (0, 2, 4))
    mix = [round(c + (255 - c) * amount) for c in (r, g, b)]
    return "".join(f"{c:02X}" for c in mix)


def _luminance(hex_colour: str) -> float:
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def chip(hex_colour: str) -> tuple[str, str]:
    """(fill, text) for a status / label chip: a pale tint with readable text."""
    text = hex_colour if _luminance(hex_colour) < 0.55 else SECONDARY
    return tint(hex_colour, 0.82), text


STATUS_CHIPS: dict[str, tuple[str, str]] = {k: chip(v) for k, v in STATUS.items()}

CONFIDENCE_CHIPS: dict[str, tuple[str, str]] = {
    "Adequate": chip(POSITIVE),
    "Indicative": chip(CAUTION),
    "Too small": chip(NEGATIVE),
}


def hex_rgb(value: str) -> str:
    return "#" + value.upper()
