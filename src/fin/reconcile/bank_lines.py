"""bank_vs_invoices: the lines of the bank receipts <-> sales invoices reconciliation (D5).

`load` reads the engagement's loaded bank statements (credits are receipts), sales register
lines and Ledger Vouchers receipts; `build` matches them (`fin.reconcile.bank_match`) and returns
each period's lines (`fin.reconcile.base` lines, with `match_lines`: the `recon_match_lines` rows):

- a matched group is `matched` (both sides equal in the receipt's currency) or `difference`;
- a foreign-currency group whose amounts agree is compared in rupees: the receipt's booked value
  (the Ledger Vouchers receipt on the customer's ledger within `fx_window_days` of the bank date)
  against the invoice's Amount in Rs. A difference is an `fx` line (gain positive, loss negative),
  never plugged; no booked receipt found is the note `no_booked_value`;
- an unmatched receipt is `left_only`, an unmatched invoice `right_only` (`not_yet_due` when it
  is not past its due days at the period's last day).

Receipts count in the period of their statement; an invoice in the period of its date, for periods
that have a bank statement; a matched group in its receipts' period.
"""

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import duckdb

from fin.diligence.stub import fiscal_start
from fin.reconcile.bank_match import Group, Invoice, Key, Receipt, match

TOKEN = re.compile(r"CUST-\d+")
SHOWN = 5
CENT = Decimal("0.01")
ZERO = Decimal("0.00")
QUERIED_KINDS = ("difference", "left_only", "right_only")


@dataclass(frozen=True)
class Booked:
    key: Key
    day: date
    party: str
    credit: Decimal


@dataclass
class Data:
    receipts: list[Receipt]
    invoices: list[Invoice]
    booked: list[Booked]
    periods: dict[str, tuple[date, date]]  # periods that have a bank statement
    receipt_period: dict[Key, str]
    names: dict[int, str]  # file_id -> file name
    confirmed: list[tuple[list[Key], list[Key]]]


def period_bounds(engagement: dict[str, Any]) -> dict[str, tuple[date, date]]:
    out = {}
    for period in engagement["periods"].split(","):
        start = fiscal_start(2000 + int(period[2:]), engagement["fiscal_start_month"])
        out[period] = (start, date(start.year + 1, start.month, 1) - timedelta(days=1))
    if engagement.get("stub"):
        out[engagement["stub"]] = (engagement["stub_start"], engagement["stub_end"])
    return out


def load(con: duckdb.DuckDBPyConnection, engagement: dict[str, Any]) -> Data:
    eid = engagement["engagement_id"]
    bounds = period_bounds(engagement)
    receipts, receipt_period = [], {}
    for fid, sheet, row, day, cur, ref, remarks, credit, period in con.execute(
        'SELECT b.file_id, b.sheet, b."row", b.txn_date, b.currency, b.reference, b.remarks, '
        "b.credit, "
        "s.period FROM bank_lines b JOIN source_files s ON s.file_id = b.file_id "
        "LEFT JOIN bank_statements st ON st.file_id = b.file_id WHERE s.status = 'loaded' "
        "AND (st.file_id IS NULL OR st.ties OR EXISTS (SELECT 1 FROM findings f WHERE "
        "f.file_id = b.file_id AND f.check_name = 'bank_tie' AND f.status = 'accepted')) "
        'AND s.engagement_id = ? AND b.credit > 0 ORDER BY b.file_id, b.sheet, b."row"',
        [eid],
    ).fetchall():
        text = f"{ref or ''} {remarks or ''}"
        found = TOKEN.search(text)
        key = (fid, sheet, row)
        receipts.append(Receipt(key, day, cur, credit, found[0] if found else None, text))
        receipt_period[key] = period
    invoices = []
    for fid, sheet, row, day, cur, amount, inr, party, number in con.execute(
        "SELECT r.file_id, r.sheet, r.\"row\", r.invoice_date, coalesce(r.currency, 'INR'), "
        "r.amount, "
        "r.amount_inr, r.customer, r.invoice_no FROM register_lines r JOIN source_files s "
        "USING (file_id) WHERE s.status = 'loaded' AND s.engagement_id = ? AND r.kind = 'sales' "
        'ORDER BY r.file_id, r.sheet, r."row"',
        [eid],
    ).fetchall():
        own = inr if cur == "INR" or amount is None else Decimal(amount).quantize(CENT)
        invoices.append(Invoice((fid, sheet, row), day, cur, own, inr, party, number))
    booked = [
        Booked((fid, sheet, row), day, ledger, credit)
        for fid, sheet, row, day, ledger, credit in con.execute(
            'SELECT e.file_id, e.sheet, e."row", e.entry_date, e.ledger, e.credit '
            "FROM ledger_entries e "
            "JOIN source_files s USING (file_id) WHERE s.status = 'loaded' AND s.engagement_id = ? "
            "AND e.credit > 0 AND lower(coalesce(e.voucher_type, '')) LIKE '%receipt%' "
            'ORDER BY e.file_id, e.sheet, e."row"',
            [eid],
        ).fetchall()
    ]
    with_bank = sorted({p for p in receipt_period.values()})
    statement_periods = {
        p
        for (p,) in con.execute(
            "SELECT DISTINCT period FROM source_files WHERE kind = 'bank' AND status = 'loaded' "
            "AND engagement_id = ?",
            [eid],
        ).fetchall()
    } | set(with_bank)
    names = dict(
        con.execute(
            "SELECT file_id, file_name FROM source_files WHERE engagement_id = ?", [eid]
        ).fetchall()
    )
    confirmed = [
        ([tuple(k) for k in json.loads(b)], [tuple(k) for k in json.loads(i)])
        for b, i in con.execute(
            "SELECT bank_lines, invoice_lines FROM match_proposals WHERE engagement_id = ? "
            "AND status = 'confirmed' ORDER BY proposal_id",
            [eid],
        ).fetchall()
    ]
    return Data(
        receipts, invoices, booked,
        {p: bounds[p] for p in sorted(statement_periods) if p in bounds},
        receipt_period, names, confirmed,
    )  # fmt: skip


def _text(data: Data, keys: list[Key]) -> str:
    keys = sorted(keys)
    shown = ", ".join(f"{data.names.get(f, f)} {s}!{r}" for f, s, r in keys[:SHOWN])
    return shown + (f" and {len(keys) - SHOWN} more" if len(keys) > SHOWN else "")


def _period_of(day: date, periods: dict[str, tuple[date, date]]) -> str | None:
    return next((p for p, (a, b) in periods.items() if a <= day <= b), None)


class _Booking:
    """Ledger Vouchers receipts, each used once, for the rupee value of foreign receipts."""

    def __init__(self, booked: list[Booked], window: int) -> None:
        self.free = list(booked)
        self.window = window

    def find(self, party: str | None, day: date, claim: bool) -> Booked | None:
        found = [
            b for b in self.free if b.party == party and abs((b.day - day).days) <= self.window
        ]
        if not found:
            return None
        best = min(found, key=lambda b: (abs((b.day - day).days), b.key))
        if claim:
            self.free.remove(best)
        return best


def _line(
    level: str, key: str, kind: str, left: Decimal | None, right: Decimal | None, **more: Any
) -> dict[str, Any]:
    return {
        "kind": kind, "level": level, "key": key, "note": None, "account": None, "ledger": None,
        "left_amount": left, "right_amount": right,
        "difference": (left or ZERO) - (right or ZERO), "left_source": None, "right_source": None,
        "match_pass": None, "currency": None, "amount_inr": None, "match_lines": [], **more,
    }  # fmt: skip


def _mline(
    side: str, item: Receipt | Invoice, party: str | None, inr: Decimal | None
) -> dict[str, Any]:
    is_bank = side == "bank"
    return {
        "side": side, "key": item.key, "party": party, "day": item.day,
        "reference": item.text.strip()[:80] if isinstance(item, Receipt) else item.number,
        "currency": item.currency, "amount": item.amount,
        "amount_inr": inr if is_bank or isinstance(item, Receipt) else item.amount_inr,
    }  # fmt: skip


def _group_lines(data: Data, group: Group, booking: _Booking) -> list[dict[str, Any]]:
    cur = group.receipts[0].currency if group.receipts else group.invoices[0].currency
    left = sum((r.amount for r in group.receipts), ZERO)
    right = sum((i.amount for i in group.invoices), ZERO)
    diff = left - right
    inv_inr = sum((i.amount_inr for i in group.invoices), ZERO)
    party = next((i.party for i in group.invoices if i.party), None)
    amount_inr = diff if cur == "INR" or not right else (diff * inv_inr / right).quantize(CENT)
    mlines = [_mline("invoice", i, i.party, None) for i in group.invoices]
    booked_total, missing = ZERO, False
    for r in group.receipts:
        value = r.amount if cur == "INR" else None
        if cur != "INR" and diff == 0:
            hit = booking.find(party or r.party, r.day, True)
            value = hit.credit if hit else None
        missing = missing or value is None
        booked_total += value or ZERO
        mlines.append(_mline("bank", r, r.party or party, value))
    base = _line(
        "match", ", ".join(i.number for i in group.invoices),
        "matched" if diff == 0 else "difference", left, right, ledger=party,
        left_source=_text(data, [r.key for r in group.receipts]),
        right_source=_text(data, [i.key for i in group.invoices]), match_pass=group.pass_name,
        currency=cur, amount_inr=amount_inr, match_lines=mlines,
    )  # fmt: skip
    out = [base]
    if cur != "INR" and diff == 0:
        if missing:
            base["note"] = (
                "no booked value: no Receipt voucher on the customer's ledger near the bank date"
            )
        elif booked_total != inv_inr:
            out.append(
                base | {
                    "kind": "fx", "left_amount": booked_total, "right_amount": inv_inr,
                    "difference": booked_total - inv_inr, "currency": "INR",
                    "amount_inr": booked_total - inv_inr,
                    "note": "FX " + ("gain" if booked_total > inv_inr else "loss")
                    + ": receipt as booked less the invoice's Amount in Rs",
                }
            )  # fmt: skip
    return out


def _rate(data: Data, cur: str, party: str | None, day: date) -> Decimal | None:
    """Rupees per unit of `cur` from the latest invoice of the party on or before the day."""
    rows = [
        i for i in data.invoices
        if i.currency == cur and i.amount and i.party == party and i.day <= day
    ]  # fmt: skip
    if not rows:
        return None
    last = max(rows, key=lambda i: (i.day, i.key))
    return last.amount_inr / last.amount


def _wants_query(line: dict[str, Any], threshold: Decimal) -> bool:
    """A difference, receipt or invoice line at or above the threshold in rupees (a foreign line
    with no rupee value too); not an invoice that is not yet due, never an fx line."""
    if line["kind"] not in QUERIED_KINDS or line["note"] == "not yet due":
        return False
    return line["amount_inr"] is None or abs(line["amount_inr"]) >= threshold


def build(data: Data, engagement: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """The lines of every period that has a bank statement: {period: [line, ...]}, each with
    `needs_query` set."""
    window = engagement["match_window_days"]
    due, threshold = engagement["invoice_due_days"], engagement["query_threshold"]
    groups = match(data.receipts, data.invoices, window, data.confirmed)
    booking = _Booking(data.booked, engagement["fx_window_days"])
    out: dict[str, list[dict[str, Any]]] = {p: [] for p in data.periods}
    matched_r = {r.key for g in groups for r in g.receipts}
    matched_i = {i.key for g in groups for i in g.invoices}
    for group in groups:
        period = min(data.receipt_period[r.key] for r in group.receipts) if group.receipts else None
        if period in out:
            out[period] += _group_lines(data, group, booking)
    for r in data.receipts:
        period = data.receipt_period[r.key]
        if r.key in matched_r or period not in out:
            continue
        value = r.amount if r.currency == "INR" else None
        if r.currency != "INR":
            hit = booking.find(r.party, r.day, True)
            rate = _rate(data, r.currency, r.party, r.day)
            value = hit.credit if hit else (r.amount * rate).quantize(CENT) if rate else None
        out[period].append(
            _line("receipt", f"{r.key[1]}!{r.key[2]} {r.day}", "left_only", r.amount, None,
                  ledger=r.party, left_source=_text(data, [r.key]), currency=r.currency,
                  amount_inr=value, match_lines=[_mline("bank", r, r.party, value)])
        )  # fmt: skip
    for i in data.invoices:
        period = _period_of(i.day, data.periods)
        if i.key in matched_i or period is None:
            continue
        overdue = (data.periods[period][1] - i.day).days > due
        out[period].append(
            _line("invoice", i.number, "right_only", None, i.amount, ledger=i.party,
                  right_source=_text(data, [i.key]), currency=i.currency, amount_inr=-i.amount_inr,
                  note=None if overdue else "not yet due",
                  match_lines=[_mline("invoice", i, i.party, None)])
        )  # fmt: skip
    for lines in out.values():
        for line in lines:
            line["needs_query"] = _wants_query(line, threshold)
    return out
