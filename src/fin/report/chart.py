"""A small SVG line chart for the health report: one line per metric over the fiscal years.

Plain SVG, so it needs no library and renders the same in HTML, Word and PDF. Values come from
the report data; the chart only draws them. A missing value (n/a) leaves a gap in its line.
Colours are the first categorical slots of a colour-blind-checked palette, in fixed order; the
legend names every line, and the table above the chart has every value.
"""

import math
from collections.abc import Callable
from html import escape

COLOURS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")  # fixed order, never cycled
GRID, AXIS, MUTED, INK, SURFACE = "#e1e0d9", "#c3c2b7", "#898781", "#52514e", "#ffffff"
WIDTH, HEIGHT = 640, 280
LEFT, RIGHT, TOP, BOTTOM = 70, 20, 44, 34


def _ticks(low: float, high: float) -> list[float]:
    """About four evenly spaced round ticks covering [low, high]."""
    if high == low:
        high = low + 1
    raw = (high - low) / 4
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    start = math.floor(low / step) * step
    count = math.ceil((high - start) / step - 1e-9)
    return [start + i * step for i in range(count + 1)]


def line_chart(
    labels: list[str],
    series: list[tuple[str, list[float | None]]],
    tick_text: Callable[[float], str],
) -> str:
    """An SVG document: x = `labels` (periods), one line per (name, values) in `series` (at most
    four), y-axis labels by `tick_text`. The y range always includes zero."""
    values = [v for _, line in series for v in line if v is not None]
    ticks = _ticks(min(0.0, *values), max(0.0, *values))
    low, high = ticks[0], ticks[-1]
    plot_w, plot_h = WIDTH - LEFT - RIGHT, HEIGHT - TOP - BOTTOM

    def x(i: int) -> float:
        return LEFT + (plot_w * (i + 0.5) / len(labels))

    def y(v: float) -> float:
        return TOP + plot_h * (high - v) / (high - low)

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" font-family="Helvetica, Arial, sans-serif" '
        'font-size="12">',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{SURFACE}"/>',
    ]
    for t in ticks:
        colour = AXIS if t == 0 else GRID
        out.append(
            f'<line x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{y(t):.1f}" y2="{y(t):.1f}" '
            f'stroke="{colour}" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{LEFT - 8}" y="{y(t) + 4:.1f}" text-anchor="end" fill="{MUTED}">'
            f"{escape(tick_text(t))}</text>"
        )
    for i, label in enumerate(labels):
        out.append(
            f'<text x="{x(i):.1f}" y="{HEIGHT - 12}" text-anchor="middle" fill="{MUTED}">'
            f"{escape(label)}</text>"
        )
    legend_x = LEFT
    for (name, line), colour in zip(series, COLOURS, strict=False):
        out.append(
            f'<line x1="{legend_x}" x2="{legend_x + 16}" y1="16" y2="16" stroke="{colour}" '
            'stroke-width="2"/>'
        )
        out.append(f'<text x="{legend_x + 22}" y="20" fill="{INK}">{escape(name)}</text>')
        legend_x += 30 + 7 * len(name)
        points = [None if v is None else (x(i), y(v)) for i, v in enumerate(line)]
        out += _series(points, colour)
    out.append("</svg>")
    return "\n".join(out) + "\n"


def _series(points: list[tuple[float, float] | None], colour: str) -> list[str]:
    """One series as SVG: a polyline per run of two or more points (a missing value, None, leaves
    a gap), then a dot on every point."""
    out = []
    segment: list[str] = []
    for point in [*points, None]:
        if point is not None:
            segment.append(f"{point[0]:.1f},{point[1]:.1f}")
            continue
        if len(segment) > 1:
            out.append(
                f'<polyline points="{" ".join(segment)}" fill="none" stroke="{colour}" '
                'stroke-width="2" stroke-linejoin="round"/>'
            )
        segment = []
    for point in points:
        if point is not None:
            out.append(
                f'<circle cx="{point[0]:.1f}" cy="{point[1]:.1f}" r="4" fill="{colour}" '
                f'stroke="{SURFACE}" stroke-width="2"/>'
            )
    return out
