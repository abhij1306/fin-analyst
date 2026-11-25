"""Golden checks for the GST return summaries (docs/fdd.md F6): `gst_return`.

A figure of one return month as filed, in rupees: period is the file's period (FY25), ledger is the
return, the month and the field, e.g. `gstr1 2024-05 taxable_value` or `gstr3b 2024-05 itc`
(fields: taxable_value, igst, cgst, sgst, cess; GSTR-3B also itc, rcm_liability, paid_cash,
paid_credit). The source a person checks is the return summary's row (file + sheet!row). The GST
reconciliations' lines are scored as reconciliation kinds (`fin.evals.golden.RECON_KINDS`).
"""

from collections.abc import Callable
from typing import Any

import duckdb

from fin.fdd.gst import AMOUNTS, EXTRA_3B

KINDS = ("gst_return",)
_LATEST = "(SELECT max(engagement_id) FROM engagement)"
_CASE = (
    "CASE split_part(?, ' ', 3) "
    + " ".join(f"WHEN '{f}' THEN g.{f}" for f in (*AMOUNTS, *EXTRA_3B))
    + " END"
)
FIN_VALUE = {
    "gst_return": (
        f"SELECT {_CASE} FROM gst_returns g JOIN source_files s USING (file_id) "
        f"WHERE s.status = 'loaded' AND s.engagement_id = {_LATEST} AND s.period = ? "
        "AND g.return_type = split_part(?, ' ', 1) AND strftime(g.month, '%Y-%m') = "
        "split_part(?, ' ', 2)",
        ("ledger", "period", "ledger", "ledger"),
    ),
}
_ROWS = f"""
SELECT s.period, g.return_type, strftime(g.month, '%Y-%m'), s.file_name, g.sheet, g."row"
FROM gst_returns g JOIN source_files s USING (file_id)
WHERE s.status = 'loaded' AND s.engagement_id = {_LATEST}
ORDER BY g.return_type, g.month
"""
SCAFFOLDED = {"gstr1": ("taxable_value",), "gstr3b": ("taxable_value", "itc", "rcm_liability")}


def candidates(
    con: duckdb.DuckDBPyConnection, row: Callable[..., dict[str, str]]
) -> list[dict[str, Any]]:
    """The return figures a person can check: taxable value per month, and the GSTR-3B ITC and
    RCM liability."""
    out = []
    for period, kind, month, file_name, sheet, number in con.execute(_ROWS).fetchall():
        for field in SCAFFOLDED[kind]:
            out.append(
                row(
                    "gst_return",
                    period,
                    f"{file_name} {sheet}!{number}",
                    ledger=f"{kind} {month} {field}",
                    unit="rupees",
                    note=f"{kind.upper()} {field} for the month, as filed",
                )
            )
    return out
