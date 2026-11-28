"""service_recast (docs/diligence.md D6): the sales register's service dates drive the recast.

Each sales line's Amount in Rs (before GST) with a usable service period (start and end dates, end
not before start) is spread straight-line over it: each month gets the share of the service days
that fall in it (rounded on the running total, so any run of months, and the whole, add up exactly).
A line with no usable service dates is recognised on its invoice date and listed. The recast of a
period is compared with what was booked in it: the register's amount by invoice date. Left is the
recast, right is booked; difference = recast - booked is the period's revenue effect, proposed as
a `recast` adjustment (`fin.reconcile.recast_run`). Only the annual periods of the engagement are
recast; the stub is not. Lines, per period:

- period: the recast against booked for the period (the revenue effect); a roll-up, listed. With
  no register line dated in the period, booked is missing (None), not zero;
- month: the same per month of the period; a roll-up, listed;
- schedule: per invoice number whose recast and booked amounts in the period differ; a roll-up,
  listed. The differences of these lines add up to the period's;
- each period and schedule side names the register rows (file sheet!row) it adds up;
- invoice, kind undated: a sales invoice with no usable service dates. Its amount is recognised on
  the invoice date. At or above the threshold it needs a query (`fin query raise --items`); below
  it, it is listed. The note names a contract Claude read for the customer or invoice, as evidence
  only.

Contracts are never the source of the schedule: fin only shows them beside a line.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

import duckdb

from fin.audit.errors import RecastCannotRun
from fin.reconcile.base import (
    ZERO,
    Computed,
    count_text,
    input_hash,
    make_line,
    month_of,
    rows_text,
)

NAME = "service_recast"
CATEGORY = "recast"  # the category of the adjustments it proposes
SUPERSEDED = "superseded by recast run"  # the reason on a proposal fin replaces
CENT = Decimal("0.01")
Ref = tuple[str, str, int]  # a register row: file name, sheet, row


@dataclass
class Sale:
    """One invoice number's lines of the register (the undated ones are kept apart)."""

    key: str
    customer: str | None
    refs: list[tuple[str, str, int]] = field(default_factory=list)
    amount: Decimal = ZERO
    note: str = ""
    first: date | None = None
    last: date | None = None


def months_of(period: str, start_month: int) -> list[str]:
    """The twelve months ('2024-04') of an annual period ('FY25' with an April start)."""
    end_year = 2000 + int(period[2:])
    year = end_year - 1 if start_month > 1 else end_year
    return [
        f"{year + (start_month - 1 + n) // 12}-{(start_month - 1 + n) % 12 + 1:02d}"
        for n in range(12)
    ]


def spread(amount: Decimal, start: date, end: date) -> dict[str, Decimal]:
    """The amount over the service days, by month: each month the share of days up to its end,
    rounded to paise once, less the months before. Any run of months then adds up to its own
    days' share rounded once (a year's recast is not off by a paisa per month), and the months add
    up to the amount."""
    days: dict[str, int] = {}
    day = start
    while day <= end:
        days[month_of(day)] = days.get(month_of(day), 0) + 1
        day += timedelta(days=1)
    total = (end - start).days + 1
    shares: dict[str, Decimal] = {}
    done, before = 0, ZERO  # days and amount (rounded once) up to the month before
    for key, count in days.items():
        done += count
        upto = (amount * done / total).quantize(CENT, ROUND_HALF_EVEN)
        shares[key] = upto - before
        before = upto
    return shares


def usable(start: date | None, end: date | None) -> bool:
    return start is not None and end is not None and end >= start


def _lines(con: duckdb.DuckDBPyConnection, engagement_id: int) -> list[Any]:
    return con.execute(
        'SELECT f.file_id, f.file_name, r.sheet, r."row", r.invoice_no, r.invoice_date, '
        "r.service_start, r.service_end, r.amount_inr, r.customer "
        "FROM register_lines r JOIN source_files f USING (file_id) WHERE f.engagement_id = ? "
        "AND f.kind = 'registers' AND f.status = 'loaded' AND r.kind = 'sales' "
        'ORDER BY f.file_id, r.sheet, r."row"',
        [engagement_id],
    ).fetchall()


def has_lines(con: duckdb.DuckDBPyConnection, engagement_id: int) -> bool:
    return bool(_lines(con, engagement_id))


def contract_note(con: duckdb.DuckDBPyConnection, engagement_id: int, sale: Sale) -> str:
    """The contracts Claude read for the invoice or its customer, as text (evidence only)."""
    rows = con.execute(
        "SELECT contract_id, start_date, end_date, page, file_name FROM contracts "
        "WHERE engagement_id = ? AND party_kind = 'customer' "
        "AND (invoice_no = ? OR (invoice_no IS NULL AND counterparty = ?)) "
        "ORDER BY contract_id",
        [engagement_id, sale.key, sale.customer],
    ).fetchall()
    return "; ".join(
        f"contract #{cid} ({file} p.{page}) says {start} to {end}"
        for cid, start, end, page, file in rows
    )


@dataclass
class _Tally:
    """What the register's lines add up to inside the period's months."""

    inside: set[str]
    recast: dict[str, Decimal] = field(default_factory=dict)
    booked: dict[str, Decimal] = field(default_factory=dict)
    counts: dict[str, list[int]] = field(default_factory=dict)  # month -> [recast, booked lines]
    schedule: dict[str, list[Decimal]] = field(default_factory=dict)  # invoice -> [recast, booked]
    # invoice -> its register rows (file, sheet, row) in the recast and in booked
    refs: dict[str, tuple[list[Ref], list[Ref]]] = field(default_factory=dict)
    notes: dict[str, set[str]] = field(default_factory=dict)
    undated: dict[str, Sale] = field(default_factory=dict)


def _add_undated(tally: _Tally, number: str, row: tuple[Any, ...]) -> None:
    """Add a line with no usable service dates to its invoice's undated sale."""
    _, file_name, sheet, row_no, _, invoice_day, start, end, amount, customer = row
    sale = tally.undated.setdefault(number, Sale(number, customer))
    sale.amount += amount
    sale.refs.append((file_name, sheet, row_no))
    sale.first = min(sale.first or invoice_day, invoice_day)
    sale.last = max(sale.last or invoice_day, invoice_day)
    sale.note = "service end is before its start" if start and end else "no service dates"


def _tally_line(tally: _Tally, row: tuple[Any, ...]) -> None:
    """Add one register line to the booked amounts, the recast and the schedule."""
    number = str(row[4]).strip()
    invoice_day, start, end, amount = row[5], row[6], row[7], row[8]
    month = month_of(invoice_day)
    ok = usable(start, end)
    ref = (row[1], row[2], row[3])
    recast_refs, booked_refs = tally.refs.setdefault(number, ([], []))
    if month in tally.inside:
        booked_refs.append(ref)
        if not ok:
            _add_undated(tally, number, row)
        tally.booked[month] = tally.booked.get(month, ZERO) + amount
        tally.counts.setdefault(month, [0, 0])[1] += 1
        tally.schedule.setdefault(number, [ZERO, ZERO])[1] += amount
    shares = spread(amount, start, end) if ok else {month: amount}
    if tally.inside.intersection(shares):
        recast_refs.append(ref)
    for key, share in shares.items():
        if key not in tally.inside:
            continue
        tally.recast[key] = tally.recast.get(key, ZERO) + share
        tally.counts.setdefault(key, [0, 0])[0] += 1
        tally.schedule.setdefault(number, [ZERO, ZERO])[0] += share
        if ok:
            tally.notes.setdefault(number, set()).add(f"service {start} to {end}")


def _month_lines(
    months: list[str], tally: _Tally, sources: tuple[Any, Any]
) -> list[dict[str, Any]]:
    return [
        make_line(
            "month",
            month,
            tally.recast.get(month, ZERO),
            tally.booked.get(month, ZERO),
            count_text(*sources, tally.counts[month][0]),
            count_text(*sources, tally.counts[month][1]),
        )
        for month in months
        if month in tally.recast or month in tally.booked
    ]


def _schedule_lines(tally: _Tally) -> list[dict[str, Any]]:
    lines = []
    for number in sorted(tally.schedule):
        left, right = tally.schedule[number]
        if left != right:
            note = "; ".join(sorted(tally.notes.get(number, ()))) or None
            recast_refs, booked_refs = tally.refs[number]
            lines.append(
                make_line(
                    "schedule",
                    number,
                    left,
                    right,
                    rows_text(recast_refs),
                    rows_text(booked_refs),
                    note=note,
                )
            )
    return lines


def _undated_lines(
    con: duckdb.DuckDBPyConnection, engagement_id: int, tally: _Tally
) -> list[dict[str, Any]]:
    lines = []
    for number in sorted(tally.undated):
        sale = tally.undated[number]
        text = f"{sale.note}: recognised on the invoice date ({sale.first})"
        if contract := contract_note(con, engagement_id, sale):
            text += f"; {contract}"
        if sale.customer:
            text += f"; customer {sale.customer}"
        lines.append(
            make_line(
                "invoice",
                number,
                sale.amount,
                None,
                rows_text(sale.refs),
                None,
                kind="undated",
                note=text,
            )  # fmt: skip
        )
    return lines


def compute(
    con: duckdb.DuckDBPyConnection, company: str, engagement: dict[str, Any], period: str
) -> Computed:
    """The recast of the period. E863 for a period that is not an annual period of the engagement
    or when no sales register line is loaded."""
    engagement_id = engagement["engagement_id"]
    if period not in engagement["periods"].split(","):
        raise RecastCannotRun(
            f"{period} is not an annual period of {company}'s open engagement "
            f"({engagement['periods']}); the stub is not recast; nothing recorded"
        )
    rows = _lines(con, engagement_id)
    if not rows:
        raise RecastCannotRun(
            f"{company}: no loaded sales register line to recast (`fin ingest {company} <file> "
            "--kind registers`); nothing recorded"
        )
    months = months_of(period, engagement.get("fiscal_start_month", 4))
    tally = _Tally(set(months))
    for row in rows:
        _tally_line(tally, row)
    first_file, sources = min(r[0] for r in rows), (rows[0][1], rows[0][2])
    recast_refs = [ref for pair in tally.refs.values() for ref in pair[0]]
    booked_refs = [ref for pair in tally.refs.values() for ref in pair[1]]
    lines = [
        make_line(
            "period",
            period,
            sum(tally.recast.values(), ZERO),
            # no register line dated in the period: the books are missing, not zero
            sum(tally.booked.values(), ZERO) if booked_refs else None,
            rows_text(recast_refs),
            rows_text(booked_refs),
        )
    ]
    lines += _month_lines(months, tally, sources)
    lines += _schedule_lines(tally)
    lines += _undated_lines(con, engagement_id, tally)
    return Computed(lines, first_file, None)


def current_hash(
    con: duckdb.DuckDBPyConnection, company: str, engagement: dict[str, Any], period: str
) -> str | None:
    """The hash of what the recast compares now (None when it cannot run now)."""
    try:
        return input_hash(compute(con, company, engagement, period), engagement["query_threshold"])
    except RecastCannotRun:
        return None


# (adj_id, status, amount, reason, made by `fin recast run`)
AdjustmentRow = tuple[int, str, Decimal, str | None, bool]


def adjustment_rows(
    con: duckdb.DuckDBPyConnection, engagement_id: int, period: str
) -> list[AdjustmentRow]:
    """The period's standalone recast adjustments (the scope `fin recast run` proposes in). The
    last field says whether `fin recast run` made it (its source reference starts
    'service_recast'): only those may be replaced by a later run."""
    return [
        (a, s, Decimal(m), r, (ref or "").startswith("service_recast "))
        for a, s, m, r, ref in con.execute(
            "SELECT adj_id, status, amount, reason, source_ref FROM adjustments "
            "WHERE engagement_id = ? AND period = ? AND category = ? AND scope = 'standalone' "
            "ORDER BY adj_id",
            [engagement_id, period, CATEGORY],
        ).fetchall()
    ]


def accepted_sum(rows: list[AdjustmentRow]) -> Decimal:
    return sum((m for _, s, m, _, _ in rows if s == "accepted"), ZERO)


def covered(rows: list[AdjustmentRow], difference: Decimal) -> bool:
    """The period's difference has a proposed or decided recast adjustment: proposed and accepted
    amounts add up to it, or the user rejected an adjustment for the whole difference or for what
    accepted ones leave of it (a rejection that fin replaced as stale does not count)."""
    accepted = accepted_sum(rows)
    live = accepted + sum((m for _, s, m, _, _ in rows if s == "proposed"), ZERO)
    if live == difference:
        return True
    return any(
        s == "rejected"
        and m in (difference, difference - accepted)
        and not (r or "").startswith(SUPERSEDED)
        for _, s, m, r, _ in rows
    )
