"""The D5 reconciliations (DILIGENCE_PLAN.md D5): bank receipts and invoices, and bank and books.

bank_vs_invoices: the loaded bank statements' credits (receipts) against sales register invoices,
matched in fixed passes (`bank_match.py`; lines in `bank_lines.py`). A line is one matched group
(level `match`: matched, difference, or an fx line for a foreign-currency receipt), an unmatched
receipt (`receipt`, left_only) or an unmatched invoice (`invoice`, right_only). The lines at or
above the threshold in rupees need a query (`fin query raise --items`); fx lines and invoices not
yet due are listed, never queried.

bank_vs_tb: the INR statements ending on a period's last day (the latest per account) against the
trial balance ledgers confirmed to cash and bank accounts, one line (the stub's cross-check
against bank). A foreign-currency account is not compared (no rate in the trial balance).
Nothing here changes company data to make a line tie.
"""

from decimal import Decimal
from typing import Any

import duckdb

from fin.audit.errors import ReconCannotRun
from fin.diligence.engagement import rupees
from fin.reconcile import bank_lines
from fin.reconcile.base import ZERO, Computed, make_line

INVOICES = "bank_vs_invoices"
TB = "bank_vs_tb"
NAMES = (INVOICES, TB)
BANK_ACCOUNTS = ("cash", "other_bank_balances", "cash_and_bank_balances")


def _bank_files(
    con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], period: str
) -> list[int]:
    return [
        f
        for (f,) in con.execute(
            "SELECT file_id FROM source_files WHERE engagement_id = ? AND period = ? "
            "AND kind = 'bank' AND status = 'loaded' ORDER BY file_id",
            [engagement["engagement_id"], period],
        ).fetchall()
    ]


def applies_invoices(
    con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], period: str
) -> bool:
    """Required once the period has a loaded bank statement (it then needs a sales register)."""
    return bool(_bank_files(con, engagement, period))


def bank_vs_invoices(
    con: duckdb.DuckDBPyConnection, company: str, engagement: dict[str, Any], period: str
) -> Computed:
    """The period's lines. E840 when there is no bank statement or sales register."""
    data = bank_lines.load(con, engagement)
    if period not in data.periods:
        raise ReconCannotRun(
            f"{company} {period}: no loaded bank statement with receipts (`fin ingest {company} "
            "<file> --kind bank`); nothing recorded"
        )
    if not data.invoices:
        raise ReconCannotRun(
            f"{company} {period}: no loaded sales register (`fin ingest {company} <file> "
            "--kind registers`); nothing recorded"
        )
    lines = bank_lines.build(data, engagement)[period]
    register = min(i.key[0] for i in data.invoices)
    return Computed(
        lines,
        min(f for f in {k[0] for k, p in data.receipt_period.items() if p == period} or {0}),
        register,
        0,
        as_of=data.periods[period][1],
        due_days=engagement["invoice_due_days"],
    )


def _statements(
    con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], end: Any
) -> tuple[list[Any], int]:
    """(the INR statements ending on `end`, latest per account; how many foreign ones)."""
    rows = con.execute(
        "SELECT b.file_id, s.file_name, b.bank, b.account, b.currency, b.closing_balance "
        "FROM bank_statements b JOIN source_files s USING (file_id) WHERE s.status = 'loaded' "
        "AND s.engagement_id = ? AND b.period_end = ? AND (b.ties OR EXISTS (SELECT 1 FROM "
        "findings f WHERE f.file_id = b.file_id AND f.check_name = 'bank_tie' "
        "AND f.status = 'accepted')) ORDER BY b.file_id",
        [engagement["engagement_id"], end],
    ).fetchall()
    latest = {(r[2], r[3], r[4]): r for r in rows}
    inr = [r for r in latest.values() if r[4] == "INR"]
    return inr, len(latest) - len(inr)


def _books(con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], period: str) -> Any:
    tb = con.execute(
        "SELECT file_id, file_name FROM source_files WHERE engagement_id = ? AND period = ? "
        "AND kind = 'trial_balance' AND status = 'loaded'",
        [engagement["engagement_id"], period],
    ).fetchone()
    if tb is None:
        return None
    rows = con.execute(
        'SELECT t.sheet, t."row", t.ledger, t.closing_debit - t.closing_credit, m.account '
        "FROM trial_balance t LEFT JOIN ledger_accounts m ON m.ledger = t.ledger "
        "AND m.status = 'confirmed' WHERE t.file_id = ? AND t.row_type = 'ledger' "
        'ORDER BY t.sheet, t."row"',
        [tb[0]],
    ).fetchall()
    return tb, rows


def _end(engagement: dict[str, Any], period: str) -> Any:
    return bank_lines.period_bounds(engagement)[period][1]


def applies_tb(con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], period: str) -> bool:
    """A trial balance for the period and INR statements ending on its last day."""
    return (
        _books(con, engagement, period) is not None
        and period in bank_lines.period_bounds(engagement)
        and bool(_statements(con, engagement, _end(engagement, period))[0])
    )


def bank_vs_tb(
    con: duckdb.DuckDBPyConnection, company: str, engagement: dict[str, Any], period: str
) -> Computed:
    """One line: the statements' closing balances against the cash and bank ledgers. E840 when
    there is no trial balance or no INR statement ending on the period's last day."""
    end = _end(engagement, period)
    books = _books(con, engagement, period)
    statements, foreign = _statements(con, engagement, end)
    if books is None or not statements:
        raise ReconCannotRun(
            f"{company} {period}: needs a loaded trial balance and an INR bank statement ending "
            f"{end}; nothing recorded"
        )
    (tb_id, tb_name), ledgers = books
    mine = [x for x in ledgers if x[4] in BANK_ACCOUNTS]
    unmapped = sum(1 for x in ledgers if x[4] is None)
    left = sum((Decimal(r[5]) for r in statements), ZERO)
    right = sum((Decimal(x[3]) for x in mine), ZERO)
    notes = []
    if foreign:
        notes.append(f"{foreign} foreign-currency accounts not compared")
    if unmapped:
        notes.append(f"{unmapped} trial balance ledgers not mapped yet: no query until they are")
    rows = ", ".join(str(x[1]) for x in mine[:5]) + (
        f" and {len(mine) - 5} more" if len(mine) > 5 else ""
    )
    line = make_line(
        "balance", "cash and bank", left, right,
        "; ".join(f"{r[1]} closing balance" for r in statements),
        f"{tb_name} {mine[0][0]}!{rows}" if mine else tb_name,
        note="; ".join(notes) or None,
    )  # fmt: skip
    line["needs_query"] = (
        line["kind"] == "difference"
        and not unmapped
        and abs(line["difference"]) >= engagement["query_threshold"]
    )
    return Computed([line], statements[0][0], tb_id, unmapped)


def format_result(result: dict[str, Any]) -> str:
    """The text of a bank reconciliation result."""
    lines, threshold = result["lines"], result["threshold"]
    head = (
        f"{result['name']} {result['period']} (recon #{result['recon_id']}, threshold "
        f"{rupees(threshold)})"
    )
    out = []
    if result["name"] == TB:
        x = lines[0]
        note = f"; {x['note']}" if x["note"] else ""
        return (
            f"{head}: bank {rupees(x['left_amount'])}, trial balance {rupees(x['right_amount'])}, "
            f"difference {rupees(x['difference'])} ({x['kind']}, {_mark(x)}){note}"
        )
    count = {k: sum(1 for x in lines if x["kind"] == k) for k in ("matched", "difference", "fx")}
    only = {k: sum(1 for x in lines if x["kind"] == k) for k in ("left_only", "right_only")}
    passes: dict[str, int] = {}
    for x in lines:
        if x["match_pass"] and x["kind"] != "fx":
            passes[x["match_pass"]] = passes.get(x["match_pass"], 0) + 1
    out.append(
        f"{head}: {count['matched']} matched, {count['difference']} differ, {count['fx']} fx, "
        f"{only['left_only']} receipts without invoice, {only['right_only']} invoices without "
        "receipt"
    )
    if passes:
        out.append("matched by pass: " + ", ".join(f"{k} {v}" for k, v in passes.items()))
    for x in lines:
        if x["kind"] == "matched" and not x["note"]:
            continue
        who = x["ledger"] or "-"
        out.append(
            f"  [item #{x['item_id']}] {x['kind']} {x['level']} {x['key']} {who} "
            f"[{x['match_pass'] or '-'}] {x['currency']}: receipts {x['left_amount']}, invoices "
            f"{x['right_amount']}, difference {x['difference']} ({_mark(x)})"
            + (f"\n      {x['note']}" if x["note"] else "")
        )
    return "\n".join(out)


def _mark(line: dict[str, Any]) -> str:
    if line["query_id"]:
        return f"query #{line['query_id']}"
    if line["needs_query"]:
        return "needs a query: `fin query raise --items`"
    return "listed, not queried"
