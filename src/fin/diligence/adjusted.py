"""The adjusted P&L (SPEC-09): each period and scope of the latest engagement, before and after
accepted adjustments, down to profit before tax, with EBITDA beside it.

The figures are the views `adjusted_pl` (db/schema/diligence_views.sql: where an adjustment lands)
and `adjusted_ebitda`; this module only reads them in statement order (`fin.statements.add_view`).
Tax is not computed for an adjustment, so the lines after profit before tax are not shown. The
balance sheet and cash flow are as reported: no adjustment kind changes them yet.
"""

from typing import Any

from fin.audit.logger import Run
from fin.db.rows import fetch_dicts
from fin.diligence.bridge import latest_engagement
from fin.diligence.engagement import company_database
from fin.statements import add_view

# The P&L lines down to profit before tax, in statement order; depth 0 for a subtotal.
LINES_SQL = """
SELECT
    p.period, p.scope, p.account, s.label, p.reported, p.adjustment, p.adjusted,
    CASE WHEN EXISTS (SELECT 1 FROM statement_paths t WHERE t.ancestor = p.account)
        THEN 0 ELSE 1 END AS depth
FROM adjusted_pl p
JOIN statement_accounts s USING (account)
WHERE s.statement = 'profit_and_loss'
  AND s.position <= (SELECT position FROM statement_accounts WHERE account = 'pbt')
ORDER BY p.period, p.scope, s.position
"""
EBITDA_SQL = """
SELECT period, scope, reported_ebitda, accepted_adjustments, adjusted_ebitda, proposed_adjustments,
    proposed_count
FROM adjusted_ebitda ORDER BY period, scope
"""


def adjusted_pl(run: Run, company: str) -> dict[str, Any]:
    """The latest engagement, the P&L lines (`LINES_SQL`) and the EBITDA rows
    of `adjusted_ebitda`, by period and scope. E803 when the company has no engagement."""
    with company_database(run, company) as con:
        engagement = latest_engagement(con, company)
        add_view(con, run.root)
        return {
            "company": company,
            "engagement": engagement,
            "lines": fetch_dicts(con, LINES_SQL),
            "ebitda": fetch_dicts(con, EBITDA_SQL),
        }
