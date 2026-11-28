"""How money is shown (SPEC-06): the `display_value` SQL macro and the text and Excel formats.

Values are stored in rupees. Every conversion to the display unit (config.toml [display], or a
company's [display.companies.<name>] table, or a unit a picker chose) is the one DuckDB macro
`display_value(v, rupees_per_unit, decimals)`: exact integer arithmetic, rounded half away from
zero, a DECIMAL(38, 6). Python and the templates never divide a stored value (a test searches for
it); they only format what SQL returned. Each figure is rounded on its own, so a total is the
rounded total, not the sum of its rounded lines.

Earnings per share are rupees per share: shown as printed, two decimals, never scaled.
"""

import threading
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

import duckdb

from fin.audit.errors import DependencyMissing
from fin.config import DISPLAY_UNITS, Display, load_config

PER_SHARE = "rupees_per_share"
PER_SHARE_DECIMALS = 2
NIL = "–"  # a zero, with negatives in brackets (as Excel's "#,##0.0;(#,##0.0);"–"")

# round(v / per_unit, decimals), half away from zero, exactly: on integers (v in millionths of a
# rupee), as div6 in company_views.sql, since DuckDB divides DECIMALs as DOUBLE.
DISPLAY_MACRO = """
CREATE OR REPLACE TEMP MACRO display_value(v, per_unit, decimals) AS
    CASE WHEN v IS NOT NULL THEN CAST(
        CAST(
            sign(CAST(v * 1000000 AS HUGEINT))
            * ((2 * abs(CAST(v * 1000000 AS HUGEINT)) * CAST(10 ** decimals AS HUGEINT)
                + CAST(per_unit AS HUGEINT) * 1000000)
               // (2 * CAST(per_unit AS HUGEINT) * 1000000))
            * CAST(10 ** (6 - decimals) AS HUGEINT)
        AS DECIMAL(38, 0)) * CAST(0.000001 AS DECIMAL(7, 6))
    AS DECIMAL(38, 6)) END
"""

_LOCAL = threading.local()


def add_macro(con: duckdb.DuckDBPyConnection) -> None:
    """Create `display_value` on a connection (temporary: nothing is written)."""
    con.execute(DISPLAY_MACRO)


def display_settings(root: Path, company: str | None, unit: str | None = None) -> Display:
    """The company's display settings (config.toml), in `unit` when a picker chose one. E001 for
    a unit that is not one of DISPLAY_UNITS; E001/E002 for a missing or invalid config.toml."""
    if unit is not None and unit not in DISPLAY_UNITS:
        raise DependencyMissing(
            f"no display unit {unit!r}; use one of {', '.join(DISPLAY_UNITS)}"
        ).with_context(unit=unit)
    return load_config(root).display_for(company).in_unit(unit)


def sql_params(display: Display) -> dict[str, object]:
    """The named parameters a query that calls display_value takes: $per_unit and $decimals."""
    return {"per_unit": display.rupees_per_unit, "decimals": display.decimals}


def _scratch() -> duckdb.DuckDBPyConnection:
    """This thread's in-memory connection (no file) with the macro, for values read elsewhere."""
    con = getattr(_LOCAL, "con", None)
    if con is None:
        con = duckdb.connect(":memory:")
        add_macro(con)
        _LOCAL.con = con
    return con


def convert(
    values: Sequence[Decimal | None], display: Display, per_share: bool = False
) -> list[Decimal | None]:
    """Rupee values in the display unit, by `display_value` (per-share values: two decimals, as
    they are). For values a report or page already read; a query adds the macro itself."""
    if not values:
        return []
    per_unit, decimals = (
        (1, PER_SHARE_DECIMALS) if per_share else (display.rupees_per_unit, display.decimals)
    )
    texts = [None if v is None else str(v) for v in values]
    row = (
        _scratch()
        .execute(
            "SELECT list_transform(?::VARCHAR[], "
            "lambda x: display_value(CAST(x AS DECIMAL(38, 6)), ?, ?))",
            [texts, per_unit, decimals],
        )
        .fetchone()
    )
    assert row is not None
    return list(row[0])


def shown(value: Decimal | None, display: Display, per_share: bool = False) -> Decimal | None:
    """One rupee value in the display unit (`convert`)."""
    return convert([value], display, per_share)[0]


def number(
    value: Decimal | None, display: Display, per_share: bool = False, blank: str = ""
) -> str:
    """A value SQL already converted, as text: thousands separators, the display's decimals (two
    for per-share), a negative as -1,234.5 or (1,234.5), and with brackets a zero as a dash."""
    if value is None:
        return blank
    decimals = PER_SHARE_DECIMALS if per_share else display.decimals
    text = f"{abs(value):,.{decimals}f}"
    if display.negatives == "brackets":
        if value == 0:
            return NIL
        return f"({text})" if value < 0 else text
    return f"-{text}" if value < 0 else text


def money(value: Decimal | None, display: Display, blank: str = "n/a") -> str:
    """A rupee value converted (SQL) and written with its unit: "1,340.9 INR Mn"."""
    if value is None:
        return blank
    return f"{number(shown(value, display), display)} {display.unit_label}"


def percent(value: Decimal | None, blank: str = "") -> str:
    """A percentage SQL computed (45.3 for 45.3%) as text: "45.3%"."""
    return blank if value is None else f"{value:,.1f}%"


def excel_format(display: Display, per_share: bool = False) -> str:
    """The Excel number format of a converted value: #,##0.0;(#,##0.0);"–" with brackets,
    #,##0.0;-#,##0.0 with minus."""
    decimals = PER_SHARE_DECIMALS if per_share else display.decimals
    body = "#,##0" + ("." + "0" * decimals if decimals else "")
    if display.negatives == "brackets":
        return f'{body};({body});"{NIL}"'
    return f"{body};-{body}"
