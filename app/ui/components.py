"""HTML components in the editorial grammar: lede, KPI strip, sections, notes, ledgers.

Every builder renders an HTML string through ``st.html`` (not iframed, so the one
stylesheet applies). All dynamic text is escaped here: company names and role titles come
from email subjects and must never be trusted as markup. ``st.html`` sanitises with
DOMPurify's HTML-only profile, which strips inline ``<svg>``; the one glyph that needs SVG
(the sparkline) is therefore emitted as a data-URI image.
"""
from __future__ import annotations

import base64
import html
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd
import streamlit as st

from . import theme

esc = html.escape


def render(markup: str) -> None:
    st.html(markup)


# ============================================================================ formatting
def _isnull(value) -> bool:
    try:
        return value is None or bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def fmt_int(value) -> str:
    if _isnull(value):
        return "-"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "-"


def fmt_pct(value, digits: int = 1) -> str:
    if _isnull(value):
        return "-"
    try:
        return f"{float(value):.{digits}f}%"
    except (TypeError, ValueError):
        return "-"


def fmt_num(value, digits: int = 1) -> str:
    if _isnull(value):
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{number:.0f}" if number.is_integer() else f"{number:.{digits}f}"


def fmt_date(value, pattern: str = "%d %b %Y") -> str:
    if _isnull(value):
        return "-"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value[:10])
        except ValueError:
            return value
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.strftime(pattern).lstrip("0")
    return str(value)


def svg_img(svg: str, width: int, height: int, cls: str = "", alt: str = "") -> str:
    if "xmlns" not in svg:
        svg = svg.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
    data = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    role = f' alt="{esc(alt)}"' if alt else ' alt="" aria-hidden="true"'
    klass = f' class="{cls}"' if cls else ""
    return (f'<img{klass} src="data:image/svg+xml;base64,{data}" width="{width}" '
            f'height="{height}"{role}>')


# ============================================================================ text atoms
TONE_CLASS = {"good": "t-pos", "bad": "t-neg", "warn": "t-warn", "accent": "t-acc", "": ""}
BAND_TONE = {"Strong": "good", "Good": "good", "Average": "warn", "Below average": "bad"}


def toned(text: str, tone: str = "") -> str:
    return f'<span class="{TONE_CLASS.get(tone, "")}">{esc(text)}</span>'


def status_mark(status: str) -> str:
    name = theme.SHORT_STATUS.get(status, status)
    return (f'<span class="jt-st"><i style="background:{theme.status_color(status)}"></i>'
            f'{esc(name)}</span>')


# ============================================================================ page head
Segment = tuple[str, str]  # (text, kind) with kind in {"", "em", "muted"}


def lede(kicker: str, segments: Sequence[Segment] | str, dek: str = "") -> None:
    """Kicker + one big serif sentence, revealed word by word, + optional standfirst."""
    if isinstance(segments, str):
        segments = [(segments, "")]
    # Words are inline-block spans (so each can rise on its own); the spaces between them
    # stay plain text nodes, outside any span, so wrapping and underlines behave normally.
    parts, i = [], 0
    for text, kind in segments:
        words = []
        for token in text.split():
            words.append(f'<span class="w" style="--i:{i}">{esc(token)}</span>')
            i += 1
        joined = " ".join(words)
        if kind == "em":
            joined = f"<em>{joined}</em>"
        elif kind == "muted":
            joined = f'<span class="m">{joined}</span>'
        lead = " " if text[:1].isspace() else ""
        tail = " " if text[-1:].isspace() else ""
        parts.append(f"{lead}{joined}{tail}")
    dek_html = f'<p class="jt-dek">{dek}</p>' if dek else ""
    render(f'<div class="jt-head"><div class="jt-kicker">{esc(kicker)}</div>'
           f'<p class="jt-lede">{"".join(parts)}</p>{dek_html}</div>')


def empty_state(title: str, body: str) -> None:
    render(f'<div class="jt-empty"><b>{esc(title)}</b>{esc(body)}</div>')


# ============================================================================ KPI strip
@dataclass
class Stat:
    label: str
    value: float | int | None
    decimals: int = 0
    unit: str = ""
    context: str = ""        # plain text
    verdict: str = ""        # plain text
    tone: str = ""
    spark: Sequence[float] = field(default_factory=tuple)
    help: str = ""           # plain-language meaning, shown on hover of the (i)


def counter(value: float | int | None, decimals: int = 0, index: int = 0) -> str:
    """Count-up numeral. CSS animates an integer custom property and prints it with a
    counter; the real value sits in a screen-reader span, and without animation (reduced
    motion, or no ``@property`` support) the counter simply shows the final number."""
    if _isnull(value):
        return "-"
    text = f"{float(value):,.{decimals}f}"
    scaled = round(float(value) * (10 ** decimals))
    if decimals > 1 or abs(scaled) >= 10000 or scaled < 0:
        return esc(text)  # counters cannot print separators or long decimals: stay static
    klass = "jt-count d1" if decimals == 1 else "jt-count"
    return (f'<span class="jt-sr">{esc(text)}</span><span class="{klass}" aria-hidden="true" '
            f'style="--n:{scaled};--i:{index}"></span>')


def _stat_html(s: Stat, i: int) -> str:
    unit = f"<small>{esc(s.unit)}</small>" if s.unit else ""
    verdict = (f'<div class="jt-stat-verdict {TONE_CLASS.get(s.tone, "")}">{esc(s.verdict)}</div>'
               if s.verdict else "")
    spark = sparkline(s.spark) if s.spark else ""
    tip = (f' <span class="jt-i" title="{esc(s.help)}" aria-label="{esc(s.help)}">i</span>'
           if s.help else "")
    return (f'<div class="jt-stat" style="--i:{i}"><div class="jt-stat-label">{esc(s.label)}{tip}'
            f'</div><div class="jt-stat-value">{counter(s.value, s.decimals, i)}{unit}</div>'
            f'<div class="jt-stat-ctx">{esc(s.context)}</div>{verdict}{spark}</div>')


def kpi_strip(stats: Sequence[Stat]) -> None:
    render(f'<div class="jt-strip">{"".join(_stat_html(s, i) for i, s in enumerate(stats))}</div>')


def kpi_tile(stat: Stat, index: int) -> None:
    """One KPI on its own, for strips laid out in Streamlit columns (so each tile can carry
    its own "See them" button underneath)."""
    render(_stat_html(stat, index))


def sparkline(values: Sequence[float], color: str = theme.ACCENT, width: int = 120,
              height: int = 26) -> str:
    vals = [float(v) for v in values if not _isnull(v)]
    if len(vals) < 2:
        return ""
    pad = 3.0
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    step = (width - 2 * pad) / (len(vals) - 1)
    pts = [(pad + i * step, height - pad - (v - lo) / span * (height - 2 * pad))
           for i, v in enumerate(vals)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    ex, ey = pts[-1]
    svg = (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
           f'<polyline points="{line}" fill="none" stroke="{theme.GREY_DARK}" stroke-width="1.5" '
           f'stroke-linejoin="round" stroke-linecap="round"/>'
           f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="2.6" fill="{color}"/></svg>')
    return svg_img(svg, width, height, "jt-spark", "weekly trend")


# ============================================================================ sections
def section(n: int, kicker: str, title: str, dek: str = ""):
    """A numbered story section: ink rule, kicker, takeaway title, dek. Returns its container."""
    box = st.container(key=f"jt-sec-{n}")
    dek_html = f'<p class="jt-sec-dek">{dek}</p>' if dek else ""
    box.html(f'<div class="jt-sec-head"><div class="jt-sec-kicker"><span>{n:02d}</span>'
             f'{esc(kicker)}</div><h2 class="jt-sec-title">{esc(title)}</h2>{dek_html}</div>')
    return box


def subhead(title: str, dek: str = "") -> None:
    render(f'<div class="jt-sub">{esc(title)}</div>'
           + (f'<p class="jt-subdek">{dek}</p>' if dek else ""))


def explain(how: str, means: str = "") -> None:
    """The two plain-English lines every chart carries: how to read it, and what it means
    for the reader (the takeaway, with an action where there is one). Folded away behind a
    small "How to read this" toggle so the page stays clean; one click opens it. HTML
    allowed."""
    means_html = f'<p><b>What it means for you.</b> {means}</p>' if means else ""
    render(f'<details class="jt-howto"><summary>How to read this</summary>'
           f'<div><p><b>How to read it.</b> {how}</p>{means_html}</div></details>')


def aside(text_html: str) -> None:
    """The same quiet box as ``explain`` for plain guidance that is not about a chart."""
    render(f'<div class="jt-explain"><p>{text_html}</p></div>')


def term(word: str, meaning: str) -> str:
    """A word with a dotted underline that explains itself on hover."""
    return f'<abbr class="jt-term" title="{esc(meaning)}">{esc(word)}</abbr>'


def source(text_html: str) -> None:
    render(f'<p class="jt-source">{text_html}</p>')


def label(text: str) -> None:
    render(f'<div class="jt-label">{esc(text)}</div>')


@dataclass
class Note:
    kicker: str
    title: str
    body: str           # pre-escaped HTML
    tone: str = ""


def notes(items: Iterable[Note]) -> None:
    parts = [f'<div class="jt-note"><div class="jt-note-k {TONE_CLASS.get(n.tone, "")}">'
             f'{esc(n.kicker)}</div><h4>{esc(n.title)}</h4><p>{n.body}</p></div>'
             for n in items]
    render(f'<div class="jt-aside">{"".join(parts)}</div>')


def compare_table(rows: Sequence[tuple[str, str, str, str, str]],
                  head: Sequence[str] = ("Per 100 sent", "You", "Published", "Verdict")) -> None:
    """(label, highlighted value, context value, verdict, tone) rows, e.g. the funnel
    against the published benchmark."""
    body = "".join(f'<tr><td>{esc(a)}</td><td class="you">{esc(b)}</td><td>{esc(c)}</td>'
                   f'<td>{toned(d, t)}</td></tr>' for a, b, c, d, t in rows)
    th = "".join(f"<th>{esc(h)}</th>" for h in head)
    render(f'<table class="jt-compare"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>')


def mini_stats(items: Iterable[tuple[str, str]]) -> None:
    render('<div class="jt-stats">' + "".join(
        f'<div class="jt-mini" style="--i:{i}"><span>{esc(k)}</span><b>{esc(v)}</b></div>'
        for i, (k, v) in enumerate(items)) + "</div>")


# ============================================================================ ledger
def link(url, text: str) -> str:
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return f'<a href="{esc(url, quote=True)}" target="_blank" rel="noopener">{esc(text)}</a>'
    return ""


def ledger(head: Sequence[str], rows: Iterable[tuple[str, str, str, str]]) -> None:
    """Four-column list: (title + sub HTML, col2 HTML, col3 HTML, number HTML)."""
    head_html = "".join(f"<div>{esc(h)}</div>" for h in head)
    body = "".join(f'<div class="jt-lrow" style="--i:{i}"><div>{a}</div><div class="c">{b}</div>'
                   f'<div class="c">{c}</div><div class="n">{d}</div></div>'
                   for i, (a, b, c, d) in enumerate(rows))
    render(f'<div class="jt-ledger"><div class="jt-lrow head">{head_html}</div>{body}</div>')


# ============================================================================ kanban
def kanban(lanes: Sequence[tuple[str, pd.DataFrame]], follow_up_days: int,
           per_lane: int = 8) -> None:
    cols = []
    k = 0
    for ci, (status, frame) in enumerate(lanes):
        cards = []
        for row in frame.head(per_lane).itertuples(index=False):
            quiet = int(row.days_since_activity) if not _isnull(row.days_since_activity) else None
            stale = quiet is not None and quiet >= follow_up_days and status in (
                "Applied", "Assessment / OA", "Interview")
            age = (f'<span class="jt-card-age{" stale" if stale else ""}">{quiet} d quiet</span>'
                   if quiet is not None else "")
            links = " ".join(filter(None, [link(row.job_url, "Posting"),
                                           link(row.gmail_link, "Thread")]))
            cards.append(
                f'<div class="jt-card" style="--i:{k}"><div class="jt-card-co">{esc(str(row.company_name))}</div>'
                f'<div class="jt-card-role">{esc(str(row.role_title))}</div>'
                f'<div class="jt-card-meta"><span>{esc(str(row.platform))}</span>'
                f'<span>{fmt_date(row.applied_date, "%d %b")}</span>'
                f'{f"<span>{links}</span>" if links else ""}{age}</div></div>')
            k += 1
        more = len(frame) - per_lane
        if more > 0:
            cards.append(f'<div class="jt-col-more">+ {more} more in the table view</div>')
        if not cards:
            cards.append('<div class="jt-col-empty">Nothing here right now.</div>')
        name = theme.SHORT_STATUS.get(status, status)
        cols.append(f'<div class="jt-col" style="--lane:{theme.status_color(status)};--i:{ci}">'
                    f'<div class="jt-col-head"><strong>{esc(name)}</strong>'
                    f'<span>{len(frame)}</span></div>{"".join(cards)}</div>')
    render(f'<div class="jt-board">{"".join(cols)}</div>')


# ============================================================================ timeline
SOURCE_LABEL = {"gmail": "from Gmail", "manual": "manual", "system": "rule", "repair": "repair",
                "legacy": "imported", "inferred": "inferred", "llm": "Claude review"}


def timeline(events: Sequence[dict]) -> None:
    if not events:
        empty_state("No status history", "This application has no recorded status events yet.")
        return
    parts = []
    for i, ev in enumerate(events):
        to_status = str(ev.get("to_status") or "")
        from_status = ev.get("from_status")
        title = f"{from_status} → {to_status}" if from_status else to_status
        src = SOURCE_LABEL.get(str(ev.get("source") or ""), str(ev.get("source") or ""))
        note_text = str(ev.get("note") or "").strip()
        note_html = f'<div class="jt-tl-note">{esc(note_text)}</div>' if note_text else ""
        parts.append(
            f'<div class="jt-tl-item" style="--i:{i}"><span class="jt-tl-dot" '
            f'style="--c:{theme.status_color(to_status)}"></span>'
            f'<div class="jt-tl-date">{fmt_date(ev.get("occurred_on"))} · {esc(src)}</div>'
            f'<div class="jt-tl-title">{esc(title)}</div>{note_html}</div>')
    render(f'<div class="jt-timeline">{"".join(parts)}</div>')


def facts(items: Iterable[tuple[str, str]]) -> None:
    render('<div class="jt-facts">' + "".join(
        f'<div class="jt-fact"><span>{esc(k)}</span><b>{esc(v)}</b></div>' for k, v in items)
        + "</div>")


# ============================================================================ meters & tests
def meters(items: Iterable[tuple[str, int, int]]) -> None:
    """Coverage meters: (label, passing, total). Fill colour carries severity, with the %."""
    parts = []
    for i, (name, passing, total) in enumerate(items):
        pct = 100.0 * passing / total if total else 100.0
        tone = "" if pct >= 90 else ("warn" if pct >= 70 else "bad")
        parts.append(
            f'<div><div class="jt-meter-top"><span>{esc(name)}</span><b>{pct:.0f}%'
            f'<em>{fmt_int(passing)} / {fmt_int(total)}</em></b></div><div class="jt-meter-track">'
            f'<div class="jt-meter-fill {tone}" style="width:{pct:.1f}%;--i:{i}"></div></div></div>')
    render(f'<div class="jt-meters">{"".join(parts)}</div>')


def test_results(tests: dict[str, int], descriptions: dict[str, str]) -> None:
    parts = []
    for i, (name, failing) in enumerate(tests.items()):
        ok = int(failing or 0) == 0
        mark = '<span class="mark t-pos">✓</span>' if ok else '<span class="mark t-neg">✗</span>'
        verdict = ('<span class="v t-pos">Pass</span>' if ok
                   else f'<span class="v t-neg">{int(failing)} failing rows</span>')
        desc = descriptions.get(name, "")
        parts.append(f'<div class="jt-test" style="--i:{i}">{mark}<div><code>{esc(name)}</code>'
                     f'{f"<span class=d>{esc(desc)}</span>" if desc else ""}</div>{verdict}</div>')
    render(f'<div class="jt-tests">{"".join(parts)}</div>')


def flow(steps: Sequence[tuple[str, str]]) -> None:
    """Architecture strip: numbered (title, caption) steps."""
    parts = [f'<div class="jt-flow-step" style="--i:{i}"><i>{i + 1}</i><b>{esc(t)}</b>'
             f'<span>{esc(c)}</span></div>' for i, (t, c) in enumerate(steps)]
    render(f'<div class="jt-flow">{"".join(parts)}</div>')


# ============================================================================ calendar
def activity_calendar(frame: pd.DataFrame) -> None:
    """GitHub-style grid: weeks as columns, Mon–Sun rows, one-hue sequential scale.

    A CSS grid of elements (not SVG) so each day keeps a native ``title`` tooltip and can
    ripple in by week (``--w``).
    """
    if frame.empty:
        empty_state("No activity yet", "Applications will appear here by day.")
        return
    data = frame.copy()
    data["week_start"] = pd.to_datetime(data["week_start"])
    data["date_day"] = pd.to_datetime(data["date_day"])
    weeks = sorted(data["week_start"].unique())
    week_index = {w: i for i, w in enumerate(weeks)}
    peak = max(1, int(data["applications"].max()))

    def level(n: int) -> int:
        if n <= 0:
            return 0
        return min(4, 1 + int((n - 1) * 4 / peak)) if peak > 1 else 3

    cells, last_month = [], None
    for w in weeks:
        stamp = pd.Timestamp(w)
        if stamp.month != last_month:
            cells.append(f'<span class="jt-cal-m" style="grid-column:{week_index[w] + 2}">'
                         f'{stamp:%b}</span>')
            last_month = stamp.month
    for i, d in ((0, "Mon"), (2, "Wed"), (4, "Fri"), (6, "Sun")):
        cells.append(f'<span class="jt-cal-d" style="grid-row:{i + 2}">{d}</span>')
    for row in data.itertuples(index=False):
        n = int(row.applications)
        tip = f"{row.date_day:%a %d %b %Y}: {n} application{'s' if n != 1 else ''}"
        wi = week_index[row.week_start]
        cells.append(f'<i class="jt-cal-c l{level(n)}" title="{esc(tip)}" '
                     f'style="grid-column:{wi + 2};grid-row:{int(row.day_of_week_num) + 1};--w:{wi}"></i>')
    legend = "".join(f'<i class="jt-cal-c l{i}"></i>' for i in range(5))
    render(f'<div class="jt-cal" role="img" aria-label="Applications per day, last 26 weeks" '
           f'style="grid-template-columns:30px repeat({len(weeks)}, minmax(6px, 17px))">'
           f'{"".join(cells)}</div><div class="jt-cal-legend"><span>None</span>{legend}'
           f'<span>{peak} a day</span></div>')
