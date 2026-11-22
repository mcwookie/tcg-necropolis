"""Deck simulation: the game-agnostic part.

A game plugin that can simulate decks returns settings from
`plugin.sim_settings(deck, pool)` and builds a SimReport in
`plugin.simulate(deck, pool, values)`. This module holds the shared types,
reads the settings form, and draws the report's charts, so each game only
writes its rules. Arkham's simulator (games/ahlcg/simulate.py) is the first;
docs/SIMULATOR.md explains how it works.

A report is a list of sections, each made of blocks (tables, band charts,
notes). The page template renders blocks by their `kind`.
"""

import math
from dataclasses import dataclass, field
from html import escape

from markupsafe import Markup

# ----- settings ----------------------------------------------------------------


@dataclass
class SimSetting:
    """One field on the simulation form.

    type: "number", "select", "multiselect", "toggle" or "text". `default`
    is a number, an option value, a list of option values, a bool or a string
    to match. Numbers are clamped to min..max. `show_if` = (key, value) hides
    the field unless another setting has that value (e.g. the custom chaos
    bag box only shows when the bag is "custom").
    """

    key: str
    label: str
    type: str
    default: object = None
    options: list[tuple[str, str]] = field(default_factory=list)
    min: float | None = None
    max: float | None = None
    step: float | None = None
    help: str | None = None
    show_if: tuple[str, str] | None = None


def parse_settings(settings: list[SimSetting], params) -> dict:
    """Setting values from a query string or form (anything with .get and
    .getlist). Missing or bad values fall back to the defaults. A form that
    was submitted carries `run=1`; until then toggles and multiselects use
    their defaults (an unticked box sends nothing)."""
    submitted = bool(params.get("run"))
    out = {}
    for s in settings:
        raw = params.get(s.key)
        if s.type == "number":
            try:
                value = float(raw) if raw not in (None, "") else None
            except ValueError:
                value = None
            if value is None or math.isnan(value):
                value = s.default
            if value is not None:
                if s.min is not None:
                    value = max(s.min, value)
                if s.max is not None:
                    value = min(s.max, value)
                if float(value).is_integer() and (s.step is None or float(s.step).is_integer()):
                    value = int(value)
            out[s.key] = value
        elif s.type == "select":
            allowed = {v for v, _ in s.options}
            out[s.key] = raw if raw in allowed else s.default
        elif s.type == "multiselect":
            allowed = [v for v, _ in s.options]
            chosen = params.getlist(s.key) if submitted else list(s.default or [])
            out[s.key] = [v for v in allowed if v in chosen]
        elif s.type == "toggle":
            out[s.key] = bool(raw) if submitted else bool(s.default)
        else:
            out[s.key] = str(raw) if raw is not None else (s.default or "")
    return out


# ----- report ----------------------------------------------------------------------


@dataclass
class Cell:
    """A table cell. `shade` (0..1) tints the cell, for percentages."""

    text: str
    shade: float | None = None
    title: str | None = None


def pct(value: float | None, digits: int = 0) -> Cell:
    """A shaded percentage cell from a 0..1 fraction (None: a dash)."""
    if value is None:
        return Cell("–")
    return Cell(f"{value * 100:.{digits}f}%", shade=max(0.0, min(1.0, value)))


@dataclass
class SimTable:
    headers: list[str]
    rows: list[list[Cell]]
    caption: str | None = None
    kind: str = "table"


@dataclass
class Band:
    """One series on a band chart: a middle line and a shaded low..high band,
    one value per x label (e.g. median and 10th..90th percentiles by round)."""

    label: str
    mid: list[float]
    low: list[float]
    high: list[float]


@dataclass
class SimChart:
    title: str
    x_labels: list[str]
    bands: list[Band]
    y_label: str = ""
    caption: str | None = None
    kind: str = "chart"

    def svg(self) -> Markup:
        return render_band_chart(self)


@dataclass
class SimNotes:
    """Plain lines of text (a bulleted list when there's more than one)."""

    lines: list[str]
    kind: str = "notes"


@dataclass
class SimStats:
    """A row of headline figures: (label, value, tooltip)."""

    items: list[tuple[str, str, str | None]]
    kind: str = "stats"


@dataclass
class SimSection:
    title: str
    blocks: list = field(default_factory=list)
    intro: str | None = None


@dataclass
class SimReport:
    sections: list[SimSection]
    summary: str = ""
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0


# ----- statistics helpers --------------------------------------------------------------


def percentile(sorted_values: list[float], q: float) -> float:
    """The q-th percentile (0..100) of an already sorted list, interpolated."""
    if not sorted_values:
        return 0.0
    pos = (len(sorted_values) - 1) * q / 100
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def band(label: str, per_x: list[list[float]], low: float = 10, high: float = 90) -> Band:
    """A Band from raw samples per x position (median and low/high percentiles)."""
    mids, lows, highs = [], [], []
    for samples in per_x:
        s = sorted(samples)
        mids.append(percentile(s, 50))
        lows.append(percentile(s, low))
        highs.append(percentile(s, high))
    return Band(label, mids, lows, highs)


# ----- charts --------------------------------------------------------------------------

_W, _H = 600, 220            # SVG coordinate box; it scales to the page width
_L, _R, _T, _B = 40, 12, 12, 30


def _nice_max(value: float) -> float:
    if value <= 0:
        return 1
    exp = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * exp:
            return step * exp
    return 10 * exp


def render_band_chart(chart: SimChart) -> Markup:
    """An inline SVG band chart. Colours come from CSS classes (app.css), so
    it follows the light/dark theme."""
    n = len(chart.x_labels)
    if n == 0 or not chart.bands:
        return Markup("")
    top = _nice_max(max(max(b.high) for b in chart.bands))
    pw, ph = _W - _L - _R, _H - _T - _B

    def x(i: int) -> float:
        return _L + (pw * i / (n - 1) if n > 1 else pw / 2)

    def y(v: float) -> float:
        return _T + ph - ph * v / top

    parts = [f'<svg class="band-chart" viewBox="0 0 {_W} {_H}" role="img" '
             f'aria-label="{escape(chart.title)}" preserveAspectRatio="xMidYMid meet">']
    for k in range(5):   # gridlines and y labels
        v = top * k / 4
        parts.append(f'<line class="grid" x1="{_L}" x2="{_W - _R}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>')
        label = f"{v:g}" if v == int(v) else f"{v:.1f}"
        parts.append(f'<text class="axis" x="{_L - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{label}</text>')
    every = max(1, math.ceil(n / 12))
    for i, lab in enumerate(chart.x_labels):
        if i % every == 0 or i == n - 1:
            parts.append(f'<text class="axis" x="{x(i):.1f}" y="{_H - 10}" text-anchor="middle">{escape(lab)}</text>')
    for idx, b in enumerate(chart.bands):
        series = f"s{idx % 4}"
        upper = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(b.high))
        lower = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in reversed(list(enumerate(b.low))))
        parts.append(f'<polygon class="band {series}" points="{upper} {lower}"/>')
        line = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(b.mid))
        parts.append(f'<polyline class="mid {series}" points="{line}"/>')
        for i, v in enumerate(b.mid):
            tip = f"{b.label} · {chart.x_labels[i]}: {v:.1f} (range {b.low[i]:.1f}–{b.high[i]:.1f})"
            parts.append(f'<circle class="dot {series}" cx="{x(i):.1f}" cy="{y(v):.1f}" r="3">'
                         f'<title>{escape(tip)}</title></circle>')
    parts.append("</svg>")
    return Markup("".join(parts))
