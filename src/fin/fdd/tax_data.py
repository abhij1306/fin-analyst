"""What the FDD report's tax section (F8) reads for F6 (docs/fdd.md): one call, `collect(con)`, on a
company database. Every figure comes from the tables and views; nothing is computed in Python but
counts. A part with no data is an empty list, so the section can say what is not loaded.
"""

from typing import Any

import duckdb

from fin.db.rows import fetch_dicts
from fin.reconcile.gst import NAMES

LATEST = "(SELECT max(engagement_id) FROM engagement)"
_RETURNS = f"""
SELECT s.period, g.return_type, s.file_name, count(*) AS months,
       sum(g.taxable_value) AS taxable_value, sum(g.igst + g.cgst + g.sgst + g.cess) AS output_tax,
       sum(g.itc) AS itc, sum(g.rcm_liability) AS rcm_liability,
       sum(g.paid_cash) AS paid_cash, sum(g.paid_credit) AS paid_credit
FROM gst_returns g JOIN source_files s USING (file_id)
WHERE s.status = 'loaded' AND s.engagement_id = {LATEST}
GROUP BY ALL ORDER BY s.period, g.return_type
"""
_RECON = f"""
SELECT r.name, r.period, r.recon_id, r.threshold,
       count(*) FILTER (WHERE i.kind <> 'matched') AS differences,
       count(*) FILTER (WHERE i.needs_query) AS at_or_above_threshold,
       count(*) FILTER (WHERE i.needs_query AND i.query_id IS NOT NULL) AS in_a_query,
       coalesce(sum(abs(i.difference)), 0) AS total_difference
FROM recon_runs r LEFT JOIN recon_items i USING (recon_id)
WHERE r.engagement_id = {LATEST} AND list_contains(?::VARCHAR[], r.name) AND r.recon_id IN (
    SELECT max(recon_id) FROM recon_runs WHERE engagement_id = {LATEST} GROUP BY name, period)
GROUP BY ALL ORDER BY r.period, r.name
"""


def collect(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """The F6 data for a report: `returns` (per period and return: months, totals as filed),
    `reconciliations` (each GST reconciliation's latest run per period: lines that differ, at or
    above the threshold, in a query), `tax_ledgers` (confirmed classes and their trial balance
    balances), `tax_ledgers_waiting` (proposals the user has not decided) and `tax_charge`."""
    waiting = con.execute("SELECT count(*) FROM tax_ledgers WHERE status = 'proposed'").fetchone()
    return {
        "returns": fetch_dicts(con, _RETURNS),
        "reconciliations": fetch_dicts(con, _RECON, [list(NAMES)]),
        "tax_ledgers": fetch_dicts(
            con, "SELECT * FROM tax_ledger_balances ORDER BY period, tax_class, ledger"
        ),
        "tax_ledgers_waiting": int(waiting[0]) if waiting else 0,
        "tax_charge": fetch_dicts(con, "SELECT * FROM tax_charge"),
    }
