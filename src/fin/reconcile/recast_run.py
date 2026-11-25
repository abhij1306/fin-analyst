"""`fin recast run`: recast the sales register by service period and propose the adjustment.

For each annual period (default: every one of the open engagement) it computes the recast
(`fin.reconcile.recast`), saves it as a reconciliation run (`recon_runs`, name service_recast) and
proposes a `recast` adjustment for the period's difference (revenue effect; no cost effect, so the
EBITDA effect is the same): fin proposes, the user accepts or rejects. Running again on changed
data:

- a proposed recast adjustment of fin's own (its source reference starts "service_recast") that no
  longer matches is rejected with the reason "superseded by recast run #n" (fin's own proposal
  replaced, not a decision) and a new one is proposed for what is not yet accepted or proposed;
  one a person proposed by hand is never touched;
- an accepted one stays; the difference left over is proposed as another adjustment;
- a recast adjustment the user rejected for this very difference stands: nothing is proposed again.
"""

from decimal import Decimal
from typing import Any

import duckdb

from fin.audit.errors import RecastCannotRun
from fin.audit.logger import Run
from fin.db.conn import transaction, utcnow
from fin.diligence.adjustments import Evidence, propose_in
from fin.diligence.engagement import company_database, record_event, require_open, rupees
from fin.reconcile import recast, store
from fin.reconcile.base import DETAIL_LEVELS, ZERO
from fin.reconcile.recast import (
    CATEGORY,
    SUPERSEDED,
    accepted_sum,
    adjustment_rows,
    covered,
)


def difference_of(result: dict[str, Any]) -> Decimal:
    [line] = [x for x in result["lines"] if x["level"] == "period"]
    return Decimal(line["difference"])


def _propose(
    run: Run,
    con: duckdb.DuckDBPyConnection,
    company: str,
    engagement: dict[str, Any],
    result: dict[str, Any],
) -> str:
    """Bring the period's recast adjustments up to date; the sentence that says what happened."""
    period, difference = result["period"], difference_of(result)
    engagement_id = engagement["engagement_id"]
    rows = adjustment_rows(con, engagement_id, period)
    live = accepted_sum(rows) + sum((m for _, s, m, _, _ in rows if s == "proposed"), ZERO)
    if live == difference:
        return (
            "no adjustment needed: no difference"
            if difference == 0 and not rows
            else "adjustments already cover the difference"
        )
    for adj_id, status, _, _, ours in rows:
        if status == "proposed" and ours:
            con.execute(
                "UPDATE adjustments SET status = 'rejected', reason = ?, status_run_id = ?, "
                "status_at = ? WHERE adj_id = ?",
                [f"{SUPERSEDED} #{result['recon_id']}", run.run_id, utcnow(), adj_id],
            )
            record_event(run, company, con, "adjustment", adj_id)
    rows = adjustment_rows(con, engagement_id, period)
    if covered(rows, difference):
        return "adjustments already cover the difference"
    delta = (
        difference
        - accepted_sum(rows)
        - sum((m for _, s, m, _, _ in rows if s == "proposed"), ZERO)
    )
    [recon_line] = [x for x in result["lines"] if x["level"] == "period"]
    adjustment = propose_in(
        run,
        con,
        company,
        engagement,
        (delta, delta, ZERO),
        period=period,
        scope="standalone",
        category=CATEGORY,
        account="revenue",
        description=(
            f"Service-period recast {period}: revenue recognised over service periods "
            f"{rupees(recon_line['left_amount'])} against {rupees(recon_line['right_amount'])} "
            "booked by invoice date"
        ),
        evidence=Evidence(source_ref=f"service_recast {period} (recon #{result['recon_id']})"),
    )
    return f"proposed adjustment #{adjustment['adj_id']} ({rupees(delta)})"


def run_recast(run: Run, company: str, period: str | None) -> list[dict[str, Any]]:
    """Recast the period (default: every annual period) in one transaction and propose its
    adjustment. E803 without an open engagement, E863 for a period that is not annual in it or
    without a loaded sales register."""
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        periods = engagement["periods"].split(",")
        if period is not None and period not in periods:
            raise RecastCannotRun(
                f"{period} is not an annual period of {company}'s open engagement "
                f"({engagement['periods']}); the stub is not recast; nothing recorded"
            )
        results = []
        for chosen in [period] if period else periods:
            result = store.save(
                run,
                con,
                engagement,
                recast.NAME,
                chosen,
                recast.compute(con, company, engagement, chosen),
            )
            result["adjustment"] = _propose(run, con, company, engagement, result)
            results.append(result)
    for result in results:
        run.event(
            "recast",
            format_result(result).splitlines()[0],
            context={"company": company, "recon_id": result["recon_id"]},
        )
    return results


def _mark(line: dict[str, Any]) -> str:
    if line["query_id"]:
        return f"query #{line['query_id']}"
    return (
        "needs a query: `fin query raise --items`"
        if line["needs_query"]
        else "below the threshold, listed"
    )


def format_result(result: dict[str, Any]) -> str:
    lines, threshold = result["lines"], result["threshold"]
    [head] = [x for x in lines if x["level"] == "period"]
    undated = [x for x in lines if x["kind"] == "undated"]
    out = [
        f"service_recast {result['period']} (recon #{result['recon_id']}, threshold "
        f"{rupees(threshold)}): recast {rupees(head['left_amount'])}, booked "
        f"{rupees(head['right_amount'])}, difference {rupees(head['difference'])} (revenue "
        "effect); "
        f"{len(undated)} sales invoices without service dates; {result['adjustment']}"
    ]
    for line in lines:
        if line["level"] in ("month", "schedule") and line["kind"] != "matched":
            note = f" ({line['note']})" if line["note"] else ""
            out.append(
                f"  {line['level']} {line['key']}: recast {rupees(line['left_amount'])}, booked "
                f"{rupees(line['right_amount'])}, difference {rupees(line['difference'])}{note}"
            )
    for line in undated:
        item = f"[item #{line['item_id']}]" if line["level"] in DETAIL_LEVELS else ""
        out.append(
            f"  {item} undated invoice {line['key']}: {rupees(line['left_amount'])} "
            f"({_mark(line)})\n      {line['left_source']}\n      {line['note']}"
        )
    return "\n".join(out)
