"""Everything the FDD report (templates/report/fdd.qmd, docs/fdd.md F8) shows: the diligence
report's data (`fin.report.dd_data.collect`: adjusted EBITDA, working capital, reconciliations,
queries, gates, sources) and the data function of each FDD workstream, read on one read-only
connection. A part with no data is empty (or None for revenue visibility), so its section says it is
not loaded. Python only gathers: every figure was computed in SQL.
"""

from pathlib import Path
from typing import Any

from fin.audit.errors import VisibilityUnavailable
from fin.config import load_config
from fin.db.conn import connect
from fin.diligence import inquiries, netdebt, provisions
from fin.disclosures.data import section_data
from fin.fdd import findings, tax_data
from fin.paths import company_db_path
from fin.reconcile.contract_coverage import coverage
from fin.reconcile.visibility import visibility
from fin.report.dd_data import collect as diligence_data
from fin.sampling.walkthrough import summaries

# The figures the FDD narrative may reference besides the diligence report's (fin.report.dd_data):
# name -> (label, unit, view). Net debt per period end of the engagement and its stub.
FDD_FIGURES: dict[str, tuple[str, str, str]] = {
    "net_debt": ("Net debt", "rupees", "net_debt"),
    "net_debt_cash": ("Cash in net debt", "rupees", "net_debt"),
    "net_debt_debt": ("Debt", "rupees", "net_debt"),
    "net_debt_debt_like": ("Debt-like items", "rupees", "net_debt"),
    "effective_tax_rate": ("Effective tax rate", "ratio", "tax_charge"),
}
_NET_DEBT = {"net_debt": "net_debt", "net_debt_cash": "cash", "net_debt_debt": "debt",
             "net_debt_debt_like": "debt_like"}  # fmt: skip


def collect(root: Path, company: str, scope: str, drafts: bool = False) -> dict[str, Any]:
    """The diligence report's data plus the FDD parts. Only final findings unless `drafts`. E873
    when the company has no engagement."""
    data = diligence_data(root, company, scope)
    fdd = load_config(root).fdd
    engagement_id = data["engagement"]["engagement_id"]
    with connect(company_db_path(root, company)) as con:
        data["drafts"] = drafts
        data["fdd_findings"] = findings.collect(con, engagement_id, drafts)
        data["net_debt"] = netdebt.collect(con)
        data["provisions"] = provisions.collect(con, fdd)
        data["tax"] = tax_data.collect(con)
        data["disclosures"] = section_data(con, engagement_id)
        data["inquiries"] = inquiries.collect(con, engagement_id, tuple(fdd["inquiry_topics"]))
        data["walkthroughs"] = summaries(con, engagement_id)
        data["coverage"] = coverage(con, engagement_id, fdd["coverage_top_n"])
        try:
            data["visibility"] = visibility(con, engagement_id)
        except VisibilityUnavailable:
            data["visibility"] = None
    figures = data["figures"]
    for name, column in _NET_DEBT.items():
        figures[name] = {r["period"]: r[column] for r in data["net_debt"]["net_debt"]}
    figures["effective_tax_rate"] = {
        r["period"]: r["effective_tax_rate"]
        for r in data["tax"]["tax_charge"]
        if r["scope"] == scope
    }
    return data
