"""payroll_vs_gl (DILIGENCE_PLAN.md D4): the payroll snapshot vs employee expense in the books.

Left is the payroll snapshot's annual cost to company (CTC) added up over its employees (gross
salary when no row has a CTC); right is the Ledger Vouchers' movement (debit - credit) of the
ledgers confirmed to employee expense, for the annual period the snapshot date falls in.
difference = payroll - books. One line, level 'payroll', key the period. The snapshot lists who
is on the payroll at its date, so a leaver earlier in the year or a mid-year joiner is a real
difference: the line is a question for the accountant, not an error of fin's. The stub has no
annual payroll comparison. The user must have confirmed the file's unit is annual (E840 otherwise).
"""

from decimal import Decimal
from typing import Any

import duckdb

from fin.audit.errors import ReconCannotRun
from fin.reconcile.base import (
    ZERO,
    Computed,
    books_entries,
    loaded_file,
    make_line,
    require_file,
    rows_text,
    unconfirmed_blocks,
)

NAME = "payroll_vs_gl"
ACCOUNTS = ("employee_expense",)


def applies(con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], period: str) -> bool:
    eid = engagement["engagement_id"]
    return (
        period in engagement["periods"].split(",")
        and loaded_file(con, eid, "payroll", period) is not None
        and loaded_file(con, eid, "ledger_vouchers", period) is not None
    )


def payroll_vs_gl(
    con: duckdb.DuckDBPyConnection, company: str, engagement: dict[str, Any], period: str
) -> Computed:
    """The payroll snapshot vs employee expense for the annual period. E840 when the period is the
    stub, a file is not loaded, the unit is not confirmed annual or no ledger is confirmed to
    employee expense."""
    eid = engagement["engagement_id"]
    if period not in engagement["periods"].split(","):
        raise ReconCannotRun(
            f"{period} is not an annual period of {company}'s open engagement; payroll_vs_gl is "
            "annual. Nothing recorded"
        )
    payroll = require_file(con, company, eid, "payroll", period, "payroll snapshot")
    voucher = require_file(con, company, eid, "ledger_vouchers", period, "Ledger Vouchers")
    unit = con.execute("SELECT unit FROM payroll_units WHERE file_id = ?", [payroll[0]]).fetchone()
    if unit is None or unit[0] != "annual":
        raise ReconCannotRun(
            f"{company} {period}: the payroll file {payroll[1]} is not confirmed annual "
            f"(`fin payroll confirm-unit {company} {payroll[1]} --annual`, when the user says "
            "so); nothing recorded"
        )
    rows = con.execute(
        'SELECT sheet, "row", gross_salary, ctc FROM payroll_lines WHERE file_id = ? '
        'ORDER BY sheet, "row"',
        [payroll[0]],
    ).fetchall()
    use_ctc = any(row[3] is not None for row in rows)
    total = sum((Decimal(r[3 if use_ctc else 2] or 0) for r in rows), ZERO)
    entries = books_entries(con, company, period, voucher[0], ACCOUNTS, "employee expense")
    books = sum((e[5] - e[6] for e in entries), ZERO)
    measure = "CTC" if use_ctc else "gross salary"
    return Computed(
        [
            make_line(
                "payroll",
                period,
                total,
                books,
                rows_text([(payroll[1], r[0], r[1]) for r in rows]),
                rows_text([(voucher[1], e[0], e[1]) for e in entries]),
                note=f"the payroll snapshot's annual {measure} for {len(rows)} employees "
                "(a snapshot at its date: it omits earlier leavers) vs the debit less credit of "
                "the employee expense ledgers",
            )
        ],
        payroll[0],
        voucher[0],
        unconfirmed_blocks(con, voucher[0]),
    )
