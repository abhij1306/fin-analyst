"""The report's SVG line chart: round ticks from zero, a gap for a missing value, a legend."""

import re

from fin.report.chart import line_chart


def test_ticks_legend_and_a_gap_for_a_missing_value() -> None:
    svg = line_chart(
        ["FY21", "FY22", "FY23", "FY24", "FY25"],
        [
            ("EBITDA margin", [20.0, 19.0, None, 18.5, 18.0]),
            ("PAT margin", [10.0, 9.5, 9.0, 8.0, 7.5]),
        ],
        lambda value: f"{value:g}%",
    )

    ticks = re.findall(r'text-anchor="end" fill="#898781">([^<]+)<', svg)
    assert ticks == ["0%", "5%", "10%", "15%", "20%"]
    assert re.findall(r'fill="#52514e">([^<]+)<', svg) == ["EBITDA margin", "PAT margin"]
    # EBITDA margin: two segments around the missing FY23; PAT margin: one line.
    assert svg.count("<polyline") == 3
    assert svg.count("<circle") == 9
