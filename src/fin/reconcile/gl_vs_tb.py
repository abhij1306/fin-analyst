"""gl_vs_tb (docs/diligence.md D4): Ledger Vouchers movements vs the trial balance, per ledger.

Left is the Ledger Vouchers' movement of the ledger in the period (debit - credit), right is what
the trial balance says it must be, difference = movement - trial balance:

- a profit and loss ledger: its trial balance closing (the year's amounts; Tally starts them at
  zero). The stub's trial balance is year to date, so the same;
- a balance sheet ledger: its closing less the closing in the prior period's trial balance (the
  previous annual period; the last annual period for the stub). With no prior trial balance the
  ledger is listed as not_reconciled (an opening inferred from the same movements would make the
  check circular);
- Tally closes the year's profit into the Profit & Loss A/c at year end without a voucher, so the
  ledgers confirmed to other equity are compared together, with the prior period's profit and loss
  ledgers' net added to their movement (the note shows it).

A ledger not confirmed to an account is listed as unmapped (not compared: fin cannot tell its
side). A ledger in the Ledger Vouchers but not the trial balance is left_only; one in the trial
balance with no ledger block is right_only. A line at or above the threshold needs a query.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import duckdb

from fin.diligence.engagement import rupees
from fin.reconcile.base import (
    ZERO,
    Computed,
    loaded_file,
    make_line,
    require_file,
    rows_text,
    unconfirmed_blocks,
)

NAME = "gl_vs_tb"
RETAINED = "other_equity"


@dataclass
class Side:
    amount: Decimal = ZERO
    refs: list[tuple[str, str, int]] = field(default_factory=list)
    present: bool = False


def _tb(con: duckdb.DuckDBPyConnection, file_id: int, file_name: str) -> dict[str, Side]:
    out: dict[str, Side] = {}
    for sheet, row, ledger, net in con.execute(
        'SELECT sheet, "row", ledger, closing_debit - closing_credit FROM trial_balance '
        "WHERE file_id = ? AND row_type = 'ledger' ORDER BY sheet, \"row\"",
        [file_id],
    ).fetchall():
        side = out.setdefault(ledger, Side(present=True))
        side.amount += net
        side.refs.append((file_name, sheet, row))
    return out


def _movements(con: duckdb.DuckDBPyConnection, file_id: int, file_name: str) -> dict[str, Side]:
    out: dict[str, Side] = {}
    for sheet, row, ledger in con.execute(
        'SELECT sheet, "row", ledger FROM ledger_blocks WHERE file_id = ? ORDER BY sheet, "row"',
        [file_id],
    ).fetchall():
        side = out.setdefault(ledger, Side(present=True))
        if not side.refs:
            side.refs.append((file_name, sheet, row))
    entered: set[str] = set()
    for sheet, row, ledger, net in con.execute(
        'SELECT sheet, "row", ledger, debit - credit FROM ledger_entries WHERE file_id = ? '
        'ORDER BY sheet, "row"',
        [file_id],
    ).fetchall():
        side = out.setdefault(ledger, Side(present=True))
        if ledger not in entered:
            side.refs = []  # entries replace the block row as the lineage
            entered.add(ledger)
        side.amount += net
        side.refs.append((file_name, sheet, row))
    return out


def _prior_period(engagement: dict[str, Any], period: str) -> str | None:
    annual = engagement["periods"].split(",")
    if period == engagement.get("stub"):
        return annual[-1]
    index = annual.index(period)
    return annual[index - 1] if index else None


def applies(con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], period: str) -> bool:
    eid = engagement["engagement_id"]
    return (
        loaded_file(con, eid, "trial_balance", period) is not None
        and loaded_file(con, eid, "ledger_vouchers", period) is not None
    )


def _prior_tb(
    con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], period: str
) -> dict[str, Side] | None:
    """The prior period's trial balance, None when there is none loaded."""
    prior_name = _prior_period(engagement, period)
    if not prior_name:
        return None
    prior_file = loaded_file(con, engagement["engagement_id"], "trial_balance", prior_name)
    return _tb(con, prior_file[0], prior_file[1]) if prior_file else None


def _confirmed(con: duckdb.DuckDBPyConnection) -> dict[str, tuple[Any, Any]]:
    """ledger -> (account, statement) of the confirmed ledger mappings."""
    rows = con.execute(
        "SELECT ledger, account, statement FROM ledger_accounts WHERE status = 'confirmed'"
    ).fetchall()
    return {ledger: (account, statement) for ledger, account, statement in rows}


def gl_vs_tb(
    con: duckdb.DuckDBPyConnection, company: str, engagement: dict[str, Any], period: str
) -> Computed:
    """Ledger Vouchers movements vs the trial balance for the period. E840 without its trial
    balance or Ledger Vouchers."""
    eid = engagement["engagement_id"]
    tb = require_file(con, company, eid, "trial_balance", period, "trial balance")
    lv = require_file(con, company, eid, "ledger_vouchers", period, "Ledger Vouchers")
    closing, moved = _tb(con, tb[0], tb[1]), _movements(con, lv[0], lv[1])
    before = _prior_tb(con, engagement, period)
    mapping = _confirmed(con)
    lines: list[dict[str, Any]] = []
    retained: list[str] = []
    ledgers: set[str] = {*closing, *moved, *(before or {})}
    for ledger in sorted(ledgers):
        account, statement = mapping.get(ledger, (None, None))
        if ledger not in closing and ledger not in moved and statement != "balance_sheet":
            continue  # only in the prior trial balance: a closed profit and loss ledger
        if statement is None:
            lines.append(
                make_line(
                    "ledger", ledger, None, None, None, None, kind="unmapped",
                    note="not confirmed to an account: fin cannot tell its side, so it is not "
                    "compared",
                )
            )  # fmt: skip
            continue
        if account == RETAINED and statement == "balance_sheet":
            retained.append(ledger)
            continue
        if statement == "balance_sheet" and before is None:
            lines.append(_not_reconciled(ledger, account, moved.get(ledger)))
            continue
        lines.append(_line(ledger, account, statement, moved.get(ledger), closing, before))
    if retained:
        lines.append(_retained(retained, moved, closing, before, mapping))
    return Computed(lines, lv[0], tb[0], unconfirmed_blocks(con, lv[0]))


def _not_reconciled(ledger: str, account: str | None, moved: Side | None) -> dict[str, Any]:
    return make_line(
        "ledger", ledger, moved.amount if moved else None, None,
        rows_text(moved.refs) if moved else None, None, kind="not_reconciled", account=account,
        note="balance sheet ledger with no prior trial balance to start from",
    )  # fmt: skip


def _line(
    ledger: str,
    account: str | None,
    statement: str,
    moved: Side | None,
    closing: dict[str, Side],
    before: dict[str, Side] | None,
) -> dict[str, Any]:
    now, then = closing.get(ledger), (before or {}).get(ledger)
    expected: Decimal | None = None
    refs: list[tuple[str, str, int]] = []
    if now or then:
        expected = (now.amount if now else ZERO) - (
            then.amount if then and statement == "balance_sheet" else ZERO
        )
        refs = (now.refs if now else []) + (
            then.refs if then and statement == "balance_sheet" else []
        )
    return make_line(
        "ledger", ledger, moved.amount if moved else None, expected,
        rows_text(moved.refs) if moved else None, rows_text(refs), account=account,
    )  # fmt: skip


def _retained(
    ledgers: list[str],
    moved: dict[str, Side],
    closing: dict[str, Side],
    before: dict[str, Side] | None,
    mapping: dict[str, tuple[Any, Any]],
) -> dict[str, Any]:
    """One line for the ledgers confirmed to other equity (the prior profit rolls into them)."""
    key = ", ".join(ledgers)
    movement = [moved[ledger] for ledger in ledgers if ledger in moved]
    refs_left = [ref for side in movement for ref in side.refs]
    if before is None:
        return make_line(
            "ledger", key, sum((s.amount for s in movement), ZERO) if movement else None, None,
            rows_text(refs_left), None, kind="not_reconciled", account=RETAINED,
            note="balance sheet ledger with no prior trial balance to start from",
        )  # fmt: skip
    profit = sum(
        (
            side.amount
            for ledger, side in before.items()
            if mapping.get(ledger, ("", ""))[1] == "profit_and_loss"
        ),
        ZERO,
    )
    now = [closing[ledger] for ledger in ledgers if ledger in closing]
    then = [before[ledger] for ledger in ledgers if ledger in before]
    expected = sum((s.amount for s in now), ZERO) - sum((s.amount for s in then), ZERO)
    refs_right = [ref for side in [*now, *then] for ref in side.refs]
    return make_line(
        "ledger", key,
        (sum((s.amount for s in movement), ZERO) if movement else ZERO) + profit,
        expected if now or then else None,
        rows_text(refs_left), rows_text(refs_right), account=RETAINED,
        note=f"Ledger Vouchers movement plus {rupees(profit)}, the prior period's profit and "
        "loss ledgers' net (debit - credit) that Tally closes into these ledgers at year end",
    )  # fmt: skip
