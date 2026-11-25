"""Gate G3 Recast's checks (docs/diligence.md §5), for `fin.diligence.gates`.

Once a sales register is loaded, for every annual period of the engagement the checklist says:

- the recast ran on the current data (its latest run's input hash equals what it compares now);
- every sales invoice at or above the threshold that has no service dates is in a query;
- the period's recast difference has a proposed or decided recast adjustment
  (`fin.reconcile.recast_run.covered`).

Contracts are optional evidence and not checked. Without a loaded sales register there is nothing
to recast: one passing line says so. The inputs (each period's run and current hash, the undated
lines and their queries, the recast adjustments) go into G3's hash, so new data, a new query or a
decided adjustment reopens it.
"""

from decimal import Decimal
from typing import Any

from fin.diligence.engagement import rupees
from fin.reconcile import recast, store
from fin.reconcile.recast import adjustment_rows, covered

Line = tuple[bool, str]


def _periods(ctx: Any) -> list[str]:
    if not recast.has_lines(ctx.con, ctx.engagement["engagement_id"]):
        return []
    return ctx.engagement["periods"].split(",")


def _difference(ctx: Any, recon_id: int) -> Decimal:
    row = ctx.con.execute(
        "SELECT difference FROM recon_items WHERE recon_id = ? AND level = 'period'", [recon_id]
    ).fetchone()
    assert row is not None
    return Decimal(row[0])


def _period_lines(ctx: Any, period: str) -> list[Line]:
    engagement = ctx.engagement
    latest = store.latest_run(ctx.con, engagement["engagement_id"], recast.NAME, period)
    now = recast.current_hash(ctx.con, ctx.company, engagement, period)
    command = f"`fin recast run {ctx.company} --period {period}`"
    if latest is None:
        return [(False, f"{period}: the recast has not run ({command})")]
    if latest[1] != now:
        return [(False, f"{period}: the recast ran on data that has changed since ({command})")]
    problems: list[Line] = []
    if open_lines := store.open_lines(ctx.con, latest[0]):
        problems.append(
            (
                False,
                f"{period}: {open_lines} sales invoices at or above the threshold have no service "
                f"dates and are not in a query (`fin query raise {ctx.company} --items <ids>`)",
            )
        )
    difference = _difference(ctx, latest[0])
    if not covered(adjustment_rows(ctx.con, engagement["engagement_id"], period), difference):
        problems.append(
            (
                False,
                f"{period}: the recast difference {rupees(difference)} has no proposed or decided "
                f"recast adjustment ({command})",
            )
        )
    return problems or [
        (
            True,
            f"{period}: the recast ran; every undated invoice at or above the threshold is in a "
            f"query; the difference {rupees(difference)} has a recast adjustment",
        )
    ]


def checklist(ctx: Any) -> list[Line]:
    """G3's lines (`ctx`: a `fin.diligence.gates.Context`)."""
    periods = _periods(ctx)
    if not periods:
        return [(True, "no sales register loaded: nothing to recast")]
    return [line for period in periods for line in _period_lines(ctx, period)]


def inputs(ctx: Any) -> dict[str, Any]:
    """G3's input: per period the latest run, the hash now, the lines that need a query with their
    queries, and the recast adjustments."""
    engagement = ctx.engagement
    runs = []
    for period in _periods(ctx):
        latest = store.latest_run(ctx.con, engagement["engagement_id"], recast.NAME, period)
        runs.append(
            [
                period,
                latest,
                recast.current_hash(ctx.con, ctx.company, engagement, period),
                store.query_rows(ctx.con, None if latest is None else latest[0]),
                adjustment_rows(ctx.con, engagement["engagement_id"], period),
            ]
        )
    return {"recast": runs} if runs else {}
