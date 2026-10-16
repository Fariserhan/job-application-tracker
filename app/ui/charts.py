"""The registered Plotly template and every chart in the app, graphics-desk style.

Grammar, enforced here so pages only choose *what* to show:

* the chart title lives in HTML above the figure (it is the takeaway sentence); figures
  carry no title and no legend; series are labelled directly at their ends
* grey for context, the accent for the thing that matters, green/brick only for outcome
* one value axis (never dual-axis), light value gridlines, no axis lines, muted ticks,
  generous margins and ``cliponaxis=False`` so labels never clip
* every rate shown with its sample size; intervals are Wilson 95%

Builders take a metric frame exactly as the SQL layer returns it, reshaping only.
"""
from __future__ import annotations

import math

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

from jobtracker import benchmarks as bm

from . import theme

TEMPLATE = "jobtracker-paper"
# Charts are read, hovered and clicked, never zoomed or dragged: a stray drag used to zoom
# into a corner with no visible way back.
CONFIG = {"displayModeBar": False, "responsive": True, "scrollZoom": False,
          "doubleClick": False, "showAxisDragHandles": False,
          "showAxisRangeEntryBoxes": False, "editable": False, "showTips": False}
LABEL = dict(family=theme.SANS, size=11.5, color=theme.INK_2)
LABEL_INK = dict(family=theme.SANS, size=12, color=theme.INK)


def _axis(**extra) -> dict:
    return dict(gridcolor=theme.RULE, gridwidth=1, zeroline=False, showline=False, ticks="",
                tickfont=dict(family=theme.SANS, size=11, color=theme.INK_3),
                title=dict(font=dict(family=theme.SANS, size=11.5, color=theme.INK_3),
                           standoff=12),
                automargin=True, fixedrange=True, **extra)


def _register() -> None:
    pio.templates[TEMPLATE] = go.layout.Template(layout=go.Layout(
        font=dict(family=theme.SANS, size=12, color=theme.INK_2),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        colorway=[theme.ACCENT, theme.GREY_DARK, theme.POSITIVE, theme.NEGATIVE],
        margin=dict(l=4, r=24, t=16, b=8), showlegend=False,
        xaxis=_axis(showgrid=False), yaxis=_axis(showgrid=True),
        hoverlabel=dict(bgcolor=theme.SURFACE, bordercolor=theme.RULE,
                        font=dict(family=theme.SANS, size=12, color=theme.INK), align="left"),
        hovermode="closest", bargap=0.42, separators=".,", dragmode=False,
    ))


_register()


def base(height: int = 320, **layout) -> go.Figure:
    """New figure on the paper template. Backgrounds/legend are also set on the layout:
    Streamlit's renderer paints its theme over template-level values."""
    fig = go.Figure()
    settings = dict(template=TEMPLATE, height=height, dragmode=False,
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)", showlegend=False,
                    font=dict(family=theme.SANS, size=12, color=theme.INK_2))
    settings.update(layout)
    fig.update_layout(**settings)
    return fig


def lock(fig: go.Figure) -> go.Figure:
    """No zoom, pan or drag on any axis, whatever a builder set."""
    fig.update_layout(dragmode=False)
    fig.update_xaxes(fixedrange=True)
    fig.update_yaxes(fixedrange=True)
    return fig


def show(fig: go.Figure, key: str, clickable: bool = False) -> dict | None:
    """Render with the app config. Keys containing ``h-`` mark horizontal bars (the motion
    layer grows those from the left instead of from the baseline).

    ``clickable`` charts return the point the reader just clicked (once), so the page can
    open the applications behind it. The chart is re-keyed after a click, which clears
    Plotly's selection dimming on the next run instead of leaving the chart greyed out."""
    lock(fig)
    if not clickable:
        st.plotly_chart(fig, theme=None, config=CONFIG, key=key)
        return None
    gen_key = f"_jt_gen_{key}"
    gen = int(st.session_state.get(gen_key, 0))
    event = st.plotly_chart(fig, theme=None, config=CONFIG, key=f"pick-{key}-v{gen}",
                            on_select="rerun", selection_mode="points")
    points = list(getattr(getattr(event, "selection", None), "points", None) or [])
    if not points and isinstance(event, dict):
        points = list((event.get("selection") or {}).get("points") or [])
    if points:
        st.session_state[gen_key] = gen + 1
        return dict(points[0])
    return None


def _num(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(out) else out


def fmt_days(value) -> str:
    v = _num(value)
    if v is None:
        return "-"
    return f"{v:.0f} d" if v.is_integer() else f"{v:.1f} d"


def _end_label(fig: go.Figure, x, y, text: str, color: str = theme.INK, shift: int = 8) -> None:
    fig.add_annotation(x=x, y=y, text=text, showarrow=False, xanchor="left", xshift=shift,
                       align="left", font=dict(family=theme.SANS, size=11.5, color=color))


# ============================================================================ flow
SANKEY_ORDER = ["Applied", "Assessment", "Interview", "Offer", "In progress", "Awaiting reply",
                "Rejected", "Ghosted", "Withdrawn"]
_STORY = {"Assessment", "Interview", "Offer", "In progress"}


def sankey(frame: pd.DataFrame, height: int = 440) -> go.Figure:
    """Where applications went: grey flows for context, accent for the path to interview and
    offer, brick for rejection. Node labels carry counts, so colour never stands alone."""
    if frame.empty:
        return base(height)
    names = [n for n in SANKEY_ORDER if n in set(frame["source"]) | set(frame["target"])]
    names += sorted((set(frame["source"]) | set(frame["target"])) - set(names))
    idx = {n: i for i, n in enumerate(names)}
    inflow = frame.groupby("target")["applications"].sum()
    outflow = frame.groupby("source")["applications"].sum()
    totals = {n: int(max(inflow.get(n, 0), outflow.get(n, 0))) for n in names}

    def link_color(target: str) -> str:
        if target == "Offer":
            return theme.rgba(theme.POSITIVE, 0.55)
        if target == "Rejected":
            return theme.rgba(theme.NEGATIVE, 0.28)
        if target in _STORY:
            return theme.rgba(theme.ACCENT, 0.36)
        return theme.rgba(theme.GREY, 0.42)

    fig = base(height, margin=dict(l=2, r=2, t=6, b=6))
    fig.add_trace(go.Sankey(
        arrangement="fixed", valueformat=",d",
        node=dict(label=[f"{n}  {totals[n]:,}" for n in names],
                  color=[theme.FLOW_COLORS.get(n, theme.INK_3) for n in names],
                  pad=26, thickness=6, line=dict(width=0),
                  hovertemplate="<b>%{label}</b><extra></extra>"),
        link=dict(source=[idx[s] for s in frame["source"]],
                  target=[idx[t] for t in frame["target"]],
                  value=frame["applications"],
                  color=[link_color(t) for t in frame["target"]],
                  hovertemplate="%{source.label} → %{target.label}<br>"
                                "<b>%{value:,} applications</b><extra></extra>"),
        textfont=dict(family=theme.SANS, size=12.5, color=theme.INK),
    ))
    return fig


def funnel(frame: pd.DataFrame, height: int = 320) -> go.Figure:
    """Applications per 100 sent (accent bar) with the published per-100 range on its own
    thin grey row directly beneath each benchmarked stage, labelled, so the two never overlap."""
    data = frame.sort_values("stage_order").reset_index(drop=True)
    n = len(data)
    ys = [n - 1 - i for i in range(n)]
    fig = base(height, margin=dict(l=4, r=60, t=8, b=8))
    fig.add_bar(
        y=[y + 0.12 for y in ys], x=data["pct_of_applied"], orientation="h", width=0.3,
        marker=dict(color=theme.ACCENT, cornerradius=2),
        text=[f"{p:.1f}" if pd.notna(p) else "" for p in data["pct_of_applied"]],
        textposition="outside", cliponaxis=False, textfont=LABEL_INK,
        customdata=data[["stage", "applications", "step_conversion_pct", "vs_benchmark"]].to_numpy(),
        hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]:,} applications · "
                      "%{x:.1f} per 100<br>%{customdata[2]:.1f}% of the stage before"
                      "<br><i>Click to see these applications</i><extra></extra>",
    )
    for y, row in zip(ys, data.itertuples(index=False), strict=False):
        if pd.isna(row.benchmark_per_100_lo):
            continue
        lo, hi = float(row.benchmark_per_100_lo), float(row.benchmark_per_100_hi)
        fig.add_shape(type="rect", x0=lo, x1=max(hi, lo + 0.6), y0=y - 0.3, y1=y - 0.16,
                      fillcolor=theme.GREY_DARK, line=dict(width=0))
        fig.add_annotation(x=max(hi, lo + 0.6), y=y - 0.23, text=f"published {lo:g} to {hi:g}",
                           showarrow=False, xanchor="left", xshift=6,
                           font=dict(size=10.5, color=theme.INK_3))
    fig.update_yaxes(tickmode="array", tickvals=ys,
                     ticktext=[f"{s}  " for s in data["stage"]], showgrid=False,
                     tickfont=dict(size=12, color=theme.INK), range=[-0.6, n - 0.4])
    fig.update_xaxes(range=[0, 105], showgrid=True, tickvals=[0, 25, 50, 75, 100],
                     title_text="Applications per 100 sent")
    return fig


# ============================================================================ velocity
def velocity(frame: pd.DataFrame, height: int = 300) -> go.Figure:
    fig = base(height, hovermode="x unified", margin=dict(l=4, r=120, t=16, b=8))
    fig.add_bar(x=frame["week_start"], y=frame["applications"], name="Sent",
                marker=dict(color=theme.GREY, cornerradius=1),
                hovertemplate="%{y} sent<extra></extra>")
    fig.add_scatter(x=frame["week_start"], y=frame["rolling_4w_avg"], name="4-week average",
                    mode="lines", line=dict(color=theme.ACCENT, width=2.2, shape="spline",
                                            smoothing=0.5),
                    hovertemplate="%{y:.1f} 4-week avg<extra></extra>")
    if not frame.empty:
        last = frame.iloc[-1]
        _end_label(fig, last["week_start"], last["rolling_4w_avg"],
                   f"<b>{last['rolling_4w_avg']:.1f}</b> a week<br>4-week average", theme.ACCENT, 14)
    fig.update_yaxes(title_text="Applications per week", rangemode="tozero")
    fig.update_xaxes(tickformat="%b", dtick="M1", ticklabelmode="period")
    return fig


# ============================================================================ rates with CIs
def rate_ci(frame: pd.DataFrame, label_col: str, reference: float | None = None,
            reference_label: str = "", highlight: str | None = None,
            confidence_col: str | None = None, height: int | None = None) -> go.Figure:
    """Dot-and-whisker: response rate with its Wilson 95% interval, sorted, n at the right.
    The highlighted row is the accent; small samples are hollow."""
    data = frame.dropna(subset=["response_rate_pct"]).sort_values("response_rate_pct")
    height = height or max(230, 42 * len(data) + 80)
    fig = base(height, margin=dict(l=4, r=130, t=34, b=8))
    if data.empty:
        return fig
    small = ((data[confidence_col] == "Too small") if confidence_col
             else (data["applications"] < bm.SAMPLE_INDICATIVE)).tolist()
    labels = data[label_col].astype(str).tolist()
    colors = [theme.ACCENT if lbl == highlight else theme.INK_2 for lbl in labels]
    fig.add_scatter(
        x=data["response_rate_pct"], y=labels, mode="markers",
        error_x=dict(type="data", symmetric=False,
                     array=data["response_rate_hi"] - data["response_rate_pct"],
                     arrayminus=data["response_rate_pct"] - data["response_rate_lo"],
                     color=theme.GREY, thickness=2.5, width=0),
        marker=dict(size=11, color=[theme.PAPER if s else c for s, c in zip(small, colors, strict=False)],
                    line=dict(color=colors, width=2)),
        customdata=data[["applications", "responses", "response_rate_lo",
                         "response_rate_hi"]].to_numpy(),
        hovertemplate="<b>%{y}</b><br>%{x:.1f}% replied (%{customdata[1]} of %{customdata[0]})"
                      "<br>Likely range %{customdata[2]:.1f} to %{customdata[3]:.1f}%"
                      "<br><i>Click to see these applications</i><extra></extra>",
    )
    conf = data[confidence_col].tolist() if confidence_col else [""] * len(data)
    for lbl, n, c in zip(labels, data["applications"], conf, strict=False):
        tag = f"{int(n)} sent" + (", too few" if c == "Too small" else
                                  ", early" if c == "Indicative" else "")
        fig.add_annotation(x=1.0, xref="paper", y=lbl, text=tag, showarrow=False, xanchor="left",
                           xshift=14, font=dict(size=11, color=theme.INK_3))
    if reference is not None:
        fig.add_vline(x=reference, line=dict(color=theme.INK, width=1))
        fig.add_annotation(x=reference, y=1.0, yref="paper", yanchor="bottom", showarrow=False,
                           text=reference_label, font=dict(size=11, color=theme.INK),
                           xanchor="left", xshift=5)
    fig.update_xaxes(range=[0, 101], ticksuffix="%", showgrid=True,
                     title_text="Share that got a reply (grey bar: likely range)")
    fig.update_yaxes(showgrid=False, tickfont=dict(size=12, color=theme.INK))
    return fig


def interview_rates(frame: pd.DataFrame, height: int | None = None) -> go.Figure:
    """Interview rate per platform; direct-to-employer channels in the accent, against the
    two published channel benchmarks."""
    data = frame.sort_values("interview_rate_pct")
    height = height or max(250, 40 * len(data) + 80)
    fig = base(height, margin=dict(l=4, r=60, t=52, b=8))
    colors = [theme.rgba(theme.ACCENT if d else theme.GREY_DARK, 0.35)
              if c == "Too small" else (theme.ACCENT if d else theme.GREY_DARK)
              for d, c in zip(data["is_direct"], data["sample_confidence"], strict=False)]
    fig.add_bar(y=data["platform"], x=data["interview_rate_pct"], orientation="h", width=0.5,
                marker=dict(color=colors, cornerradius=2),
                text=[f"{v:.1f}%  ({n} sent)" for v, n in zip(data["interview_rate_pct"],
                                                              data["applications"], strict=False)],
                textposition="outside", cliponaxis=False, textfont=LABEL,
                customdata=data[["platform"]].to_numpy(),
                hovertemplate="<b>%{y}</b><br>%{x:.1f}% reached an interview"
                              "<br><i>Click to see these applications</i><extra></extra>")
    top = min(105.0, max(25.0, float(data["interview_rate_pct"].max() or 0) * 1.3))
    for value, text, lift in ((bm.DIRECT_INTERVIEW_RATE_PCT,
                               f"Direct benchmark {bm.DIRECT_INTERVIEW_RATE_PCT}%", 0),
                              (bm.JOB_BOARD_INTERVIEW_RATE_PCT,
                               f"Easy Apply {bm.JOB_BOARD_INTERVIEW_RATE_PCT}%", 18)):
        fig.add_vline(x=value, line=dict(color=theme.INK, width=1))
        fig.add_annotation(x=value, y=1.0, yref="paper", yanchor="bottom", showarrow=False,
                           text=text, font=dict(size=11, color=theme.INK), xanchor="left",
                           xshift=-2, yshift=lift)
    fig.update_yaxes(categoryorder="array", categoryarray=list(data["platform"]), showgrid=False,
                     tickfont=dict(size=12, color=theme.INK))
    fig.update_xaxes(ticksuffix="%", range=[0, top], showgrid=True,
                     title_text="Reached interview, % of applications")
    return fig


def volume_vs_rate(frame: pd.DataFrame, height: int = 360) -> go.Figure:
    """Where effort goes vs where replies come from. Direct channels in the accent."""
    fig = base(height, margin=dict(l=4, r=24, t=16, b=8))
    frame = frame.sort_values(["applications", "response_rate_pct"]).reset_index(drop=True)
    colors = [theme.ACCENT if d else theme.INK_2 for d in frame["is_direct"]]
    hollow = [c == "Too small" for c in frame["sample_confidence"]]
    span = float(frame["applications"].max() or 1)
    positions: list[str] = []
    placed: list[tuple[float, float, str]] = []
    for x, y in zip(frame["applications"], frame["response_rate_pct"], strict=False):
        taken = {pos for px, py, pos in placed
                 if abs(x - px) < span * 0.08 and abs(y - py) < 9}
        choice = next(p for p in ("middle right", "bottom right", "top right", "middle left")
                      if p not in taken or p == "middle left")
        positions.append(choice)
        placed.append((x, y, choice))
    fig.add_scatter(
        x=frame["applications"], y=frame["response_rate_pct"], mode="markers+text",
        text=frame["platform"], textposition=positions, textfont=LABEL_INK,
        error_y=dict(type="data", symmetric=False,
                     array=frame["response_rate_hi"] - frame["response_rate_pct"],
                     arrayminus=frame["response_rate_pct"] - frame["response_rate_lo"],
                     color=theme.GREY, thickness=2, width=0),
        marker=dict(size=11, color=[theme.PAPER if h else c for h, c in zip(hollow, colors, strict=False)],
                    line=dict(color=colors, width=2)),
        customdata=frame[["sample_confidence", "response_rate_lo", "response_rate_hi",
                          "platform"]].to_numpy(),
        hovertemplate="<b>%{text}</b><br>%{x} applications · %{y:.1f}% replied"
                      "<br>Likely range %{customdata[1]:.1f} to %{customdata[2]:.1f}%"
                      "<br><i>Click to see these applications</i><extra></extra>",
    )
    top = float(frame["applications"].max() or 1)
    fig.update_xaxes(title_text="Applications sent", range=[-top * 0.03, top * 1.22],
                     showgrid=True)
    fig.update_yaxes(title_text="Response rate", ticksuffix="%", range=[0, 104])
    return fig


# ============================================================================ timing
def survival(frame: pd.DataFrame, median_reply: float | None = None,
             height: int = 360) -> tuple[go.Figure, dict]:
    """Kaplan–Meier 'still waiting' step curve with censoring ticks and the median marked
    (or, when fewer than half ever reply, the 50% line it never crosses)."""
    fig = base(height, hovermode="closest", margin=dict(l=4, r=40, t=16, b=8))
    marks: dict = {}
    if frame.empty:
        return fig, marks
    days = [0, *frame["day"].tolist()]
    waiting = [100.0, *frame["still_waiting_pct"].tolist()]
    fig.add_scatter(x=days, y=waiting, mode="lines", line=dict(shape="hv", color=theme.ACCENT,
                                                                width=2.2),
                    hovertemplate="Day %{x}: %{y:.1f}% still waiting<extra></extra>")
    cens = frame[frame["censored"] > 0]
    fig.add_scatter(x=cens["day"], y=cens["still_waiting_pct"], mode="markers",
                    marker=dict(symbol="line-ns-open", size=6, color=theme.GREY,
                                line=dict(width=1, color=theme.GREY)),
                    customdata=cens["censored"],
                    hovertemplate="Day %{x}: %{customdata} still waiting at this age"
                                  "<extra></extra>")
    for horizon in (7, 14, 21, 30):
        upto = frame[frame["day"] <= horizon]
        if not upto.empty:
            marks[horizon] = float(upto["replied_pct"].iloc[-1])
    below = frame[frame["still_waiting_pct"] <= 50]
    if not below.empty:
        median_day = int(below["day"].iloc[0])
        marks["median_day"] = median_day
        fig.add_shape(type="line", x0=median_day, x1=median_day, y0=0, y1=50,
                      line=dict(color=theme.INK, width=1))
        fig.add_annotation(x=median_day, y=50, text=f"<b>Half have heard by day {median_day}</b>",
                           showarrow=False, xanchor="left", xshift=6, yanchor="bottom",
                           font=dict(color=theme.INK, size=12))
    else:
        fig.add_hline(y=50, line=dict(color=theme.INK_3, width=1, dash="dot"))
        fig.add_annotation(x=1, xref="paper", y=50, yanchor="bottom", xanchor="right",
                           showarrow=False, text="Most never hear back, so the line<br>"
                                                 "never drops below half",
                           align="right", font=dict(color=theme.INK_2, size=11))
    if 21 in marks:
        y21 = 100 - marks[21]
        fig.add_scatter(x=[21], y=[y21], mode="markers", hoverinfo="skip",
                        marker=dict(size=10, color=theme.ACCENT,
                                    line=dict(color=theme.PAPER, width=2)))
        fig.add_annotation(x=21, y=y21, ax=46, ay=-52, text=f"Day 21: <b>{marks[21]:.0f}%</b> "
                           "have heard back", showarrow=True, arrowhead=0, arrowwidth=1,
                           arrowcolor=theme.INK_3, xanchor="left",
                           font=dict(color=theme.INK, size=12))
    if median_reply is not None:
        fig.add_shape(type="line", x0=median_reply, x1=median_reply, y0=0, y1=100,
                      line=dict(color=theme.GREY_DARK, width=1, dash="dot"))
        fig.add_annotation(x=median_reply, y=10, text=f"Typical reply:<br>{median_reply:g} days",
                           showarrow=False, xanchor="left", xshift=6,
                           font=dict(color=theme.INK_2, size=11), align="left")
    fig.update_yaxes(title_text="Still waiting", range=[0, 103], ticksuffix="%")
    fig.update_xaxes(title_text="Days since applying", rangemode="tozero", showgrid=False)
    return fig, marks


def reply_buckets(frame: pd.DataFrame, height: int = 280) -> go.Figure:
    fig = base(height, margin=dict(l=4, r=16, t=28, b=8))
    if frame.empty:
        return fig
    peak = frame["responses"].idxmax()
    fig.add_bar(x=frame["bucket"], y=frame["responses"], width=0.5,
                marker=dict(color=[theme.ACCENT if i == peak else theme.GREY for i in frame.index],
                            cornerradius=2),
                text=[f"{n}  ·  {s:.0f}%" for n, s in zip(frame["responses"], frame["share_pct"],
                                                          strict=False)],
                textposition="outside", cliponaxis=False, textfont=LABEL_INK,
                hovertemplate="%{x}: %{y} replies<extra></extra>")
    fig.update_yaxes(title_text="Replies with a known date", rangemode="tozero")
    return fig


def weekday(frame: pd.DataFrame, height: int = 280) -> go.Figure:
    fig = base(height, margin=dict(l=4, r=16, t=40, b=8))
    ok = frame["applications"] >= bm.SAMPLE_INDICATIVE
    best = frame[ok]["response_rate_pct"].idxmax() if ok.any() else None
    fig.add_bar(x=frame["weekday"], y=frame["response_rate_pct"], width=0.5,
                marker=dict(color=[theme.ACCENT if i == best else
                                   (theme.GREY if ok[i] else theme.rgba(theme.GREY, 0.45))
                                   for i in frame.index], cornerradius=2),
                text=[f"{r:.0f}%<br>n={n}" for r, n in zip(frame["response_rate_pct"],
                                                           frame["applications"], strict=False)],
                textposition="outside", cliponaxis=False, textfont=LABEL,
                customdata=frame[["applications", "responses"]].to_numpy(),
                hovertemplate="%{x}: %{y:.1f}% replied (%{customdata[1]} of %{customdata[0]})"
                              "<extra></extra>")
    fig.update_yaxes(title_text="Response rate", ticksuffix="%", rangemode="tozero")
    return fig


def stage_timing(frame: pd.DataFrame, height: int = 240) -> go.Figure:
    """Dumbbell: interquartile range (grey) with the median (accent) labelled."""
    fig = base(height, margin=dict(l=4, r=40, t=24, b=8))
    data = frame.dropna(subset=["median_days"])
    for row in data.itertuples(index=False):
        fig.add_scatter(x=[row.p25_days, row.p75_days], y=[row.milestone] * 2, mode="lines",
                        line=dict(color=theme.GREY, width=8), hoverinfo="skip")
    fig.add_scatter(x=data["median_days"], y=data["milestone"], mode="markers+text",
                    marker=dict(size=12, color=theme.ACCENT, line=dict(color=theme.PAPER, width=2)),
                    text=[f"median {fmt_days(v)}" for v in data["median_days"]],
                    textposition="top center", textfont=LABEL_INK,
                    customdata=data[["p25_days", "p75_days", "applications"]].to_numpy(),
                    hovertemplate="<b>%{y}</b><br>median %{x:.1f} days<br>middle half "
                                  "%{customdata[0]:.1f} to %{customdata[1]:.1f} days<br>"
                                  "n=%{customdata[2]}<extra></extra>")
    fig.update_xaxes(title_text="Days since applying · grey bar = middle half of cases",
                     rangemode="tozero", showgrid=True)
    fig.update_yaxes(showgrid=False, categoryorder="array",
                     categoryarray=list(data["milestone"])[::-1],
                     tickfont=dict(size=12, color=theme.INK), range=[-0.5, len(data) - 0.2])
    return fig


# ============================================================================ flow matrix
def transitions(frame: pd.DataFrame, height: int = 330) -> go.Figure:
    order = ["(new)", "Saved / Planning", "Applied", "Assessment / OA", "Interview", "Offer",
             "Rejected", "Ghosted", "Withdrawn"]
    pivot = frame.pivot_table(index="from_status", columns="to_status", values="transitions",
                              aggfunc="sum")
    rows = [s for s in order if s in pivot.index]
    cols = [s for s in order if s in pivot.columns]
    pivot = pivot.reindex(index=rows, columns=cols)
    short = {s: theme.SHORT_STATUS.get(s, s) for s in order}
    z = pivot.to_numpy()
    peak = float(pd.DataFrame(z).max().max() or 1)
    text = [["" if pd.isna(v) else f"{int(v)}" for v in r] for r in z]
    fig = base(height, margin=dict(l=4, r=8, t=8, b=8))
    fig.add_trace(go.Heatmap(
        z=z, x=[short[c] for c in cols], y=[short[r] for r in rows], text=text,
        texttemplate="%{text}", colorscale=theme.SEQUENTIAL, showscale=False, xgap=2, ygap=2,
        hoverongaps=False, zmin=0, zmax=peak,
        textfont=dict(family=theme.SANS, size=12),
        hovertemplate="%{y} → %{x}: <b>%{z}</b> moves<extra></extra>",
    ))
    fig.update_xaxes(title_text="to", side="top", showgrid=False,
                     tickfont=dict(size=11.5, color=theme.INK))
    fig.update_yaxes(title_text="from", autorange="reversed", showgrid=False,
                     tickfont=dict(size=11.5, color=theme.INK))
    return fig


# ============================================================================ cohorts
def month_labels(frame: pd.DataFrame) -> list[str]:
    """'2026-08' -> 'Aug' (with the year only when the cohorts span more than one)."""
    stamps = pd.to_datetime(frame["cohort_month"] + "-01")
    fmt = "%b %y" if stamps.dt.year.nunique() > 1 else "%b"
    return [d.strftime(fmt) for d in stamps]


COHORT_PARTS = [("offers", "Offer"), ("still_open", "In progress"), ("ghosted", "Ghosted"),
                ("rejected", "Rejected")]


def cohort_outcomes(frame: pd.DataFrame, height: int = 340) -> go.Figure:
    """Stacked outcomes per application month. Offer and Rejected are never adjacent (their
    hues are the CVD-weakest pair). Segment labels would collide on short bars, so the page
    shows a small key instead (``COHORT_PARTS`` order)."""
    fig = base(height, barmode="stack", hovermode="x unified",
               margin=dict(l=4, r=16, t=24, b=8), bargap=0.5)
    months = month_labels(frame)
    for col, name in COHORT_PARTS:
        fig.add_bar(x=months, y=frame[col], name=name,
                    customdata=frame[["cohort_month"]].to_numpy(),
                    marker=dict(color=theme.FLOW_COLORS[name],
                                line=dict(color=theme.PAPER, width=1.5)),
                    hovertemplate=f"{name}: %{{y}}<extra></extra>")
    for label, row in zip(months, frame.itertuples(index=False), strict=False):
        if row.is_maturing:
            fig.add_annotation(x=label, y=row.applications, text="maturing", showarrow=False,
                               yanchor="bottom", yshift=4, font=dict(size=10.5, color=theme.INK_3))
    fig.update_yaxes(title_text="Applications sent that month", rangemode="tozero")
    fig.update_xaxes(type="category")
    return fig


def cohort_rates(frame: pd.DataFrame, height: int = 300) -> go.Figure:
    fig = base(height, hovermode="x unified", margin=dict(l=4, r=120, t=24, b=8))
    months = month_labels(frame)
    for col, name, color in (("response_rate_pct", "Response rate", theme.ACCENT),
                             ("interview_rate_pct", "Interview rate", theme.INK_2)):
        fig.add_scatter(x=months, y=frame[col], name=name, mode="lines+markers",
                        customdata=frame[["cohort_month"]].to_numpy(),
                        line=dict(color=color, width=2),
                        marker=dict(size=8, color=[theme.PAPER if m else color
                                                   for m in frame["is_maturing"]],
                                    line=dict(color=color, width=2)),
                        hovertemplate=f"{name}: %{{y:.1f}}%<extra></extra>")
        if months:
            _end_label(fig, months[-1], frame[col].iloc[-1], name, color, 12)
    fig.add_hline(y=bm.DIRECT_INTERVIEW_RATE_PCT, line=dict(color=theme.INK_3, width=1, dash="dot"))
    fig.add_annotation(x=0, xref="paper", y=bm.DIRECT_INTERVIEW_RATE_PCT, yanchor="top",
                       xanchor="left", showarrow=False, yshift=-2,
                       text=f"{bm.DIRECT_INTERVIEW_RATE_PCT}% published direct interview rate",
                       font=dict(size=10.5, color=theme.INK_3))
    fig.update_yaxes(title_text="% of the month's applications", ticksuffix="%",
                     rangemode="tozero")
    fig.update_xaxes(type="category")
    return fig


# ============================================================================ data model
def parser_verdicts(frame: pd.DataFrame, height: int | None = None) -> go.Figure:
    data = frame.sort_values("messages")
    labels = [r if len(r) <= 48 else r[:46] + "…" for r in data["filter_reason"]]
    height = height or max(230, 32 * len(data) + 60)
    fig = base(height, margin=dict(l=4, r=40, t=8, b=8))
    colors = [theme.ACCENT if keep else theme.GREY for keep in data["is_application_mail"]]
    fig.add_bar(y=labels, x=data["messages"], orientation="h", width=0.55,
                marker=dict(color=colors, cornerradius=2),
                text=data["messages"], textposition="outside", cliponaxis=False,
                textfont=LABEL_INK,
                customdata=data[["filter_reason", "avg_confidence", "parsed_by"]].to_numpy(),
                hovertemplate="<b>%{customdata[0]}</b><br>%{x} emails · avg confidence "
                              "%{customdata[1]:.2f} · %{customdata[2]}<extra></extra>")
    fig.update_yaxes(showgrid=False, categoryorder="array", categoryarray=labels,
                     tickfont=dict(size=11.5, color=theme.INK))
    fig.update_xaxes(title_text="Emails inspected", showgrid=True)
    return fig
