"""Contracts (`fin recast contracts <company> <file> --reading <csv>`): optional evidence for the
recast, never the source of its schedule, and the contract terms of the FDD extension.

Claude reads a contract document and writes a reading CSV, one row per contract (a UTF-8 file with a
header row; columns in any order):

- `counterparty` (required; `customer` is read as the same column): the party's token (CUST-0001,
  VEND-0001) or name as printed; a name is matched to its register token (`mask_values`, whichever
  kind first masked it); one no loaded register has (sales for a customer, purchase for a vendor) is
  E865;
- `party_kind`: customer (default) or vendor;
- `invoice_no`: the sales (customer) or purchase (vendor) invoice the contract supports; one no
  register has is E865;
- `start`, `end` (required): the contract's dates, YYYY-MM-DD;
- `value`: the contract value as printed (`12,00,000.00`), in rupees; `billing_terms`: as read;
- the terms in `contract_terms.py` (lock-in, notice, SLA, penalty, escalation, renewal and the
  yes/no clauses), each optional;
- `page` (required): the page of the document it was read from, from 1.

Every row is stored in `contracts` with the file name, its sha256 and the page, read_by 'claude'.
Whether a Claude-read contract is trusted is the user's decision. Loading the same file again
replaces its rows. E864 for a reading that cannot be used, naming each CSV line at fault.
"""

import csv
import hashlib
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import duckdb

from fin.audit.errors import ContractNotOnRegister, ContractReadingInvalid
from fin.audit.logger import Run
from fin.db.conn import transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database, require_open, rupees
from fin.extract.profiles import norm
from fin.extract.units import parse_figure
from fin.reconcile.contract_terms import (
    OPTIONAL,
    TERM_COLUMNS,
    parse_terms,
    terms_text,
)

REQUIRED = ("counterparty", "start", "end", "page")
REGISTER = {"customer": ("sales", "customer"), "vendor": ("purchase", "vendor")}  # kind, column


def _date(text: str) -> date:
    return date.fromisoformat(text.strip())


def _value(text: str | None) -> Decimal | None:
    if not text:
        return None
    figure = parse_figure(text)
    if figure is None:
        raise ValueError(f"value {text!r} is not a figure")
    return Decimal(figure)


def _parse_row(number: int, row: dict[str, str]) -> dict[str, Any]:
    """One validated reading row; ValueError or InvalidOperation names what is wrong."""
    start, end = _date(row["start"]), _date(row["end"])
    if end < start:
        raise ValueError("end is before start")
    page = int(row["page"])
    if page < 1:
        raise ValueError("page starts at 1")
    if not row["counterparty"]:
        raise ValueError("counterparty is empty")
    return {
        "line": number,
        "counterparty": row["counterparty"],
        "invoice_no": row.get("invoice_no") or None,
        "start_date": start,
        "end_date": end,
        "value": _value(row.get("value")),
        "page": page,
        **parse_terms(row),
    }


def read_rows(path: Path) -> list[dict[str, Any]]:
    """The reading's rows (validated, line numbers kept). E864 listing every line at fault."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        names = set(reader.fieldnames or [])
        if "counterparty" not in names and "customer" in names:
            names.add("counterparty")
            reader.fieldnames = [
                "counterparty" if name == "customer" else name for name in reader.fieldnames or []
            ]
        table = list(reader)
    missing = [name for name in REQUIRED if name not in names]
    if missing:
        raise ContractReadingInvalid(
            f"{path.name}: missing column(s) {', '.join(missing)} (needs {', '.join(REQUIRED)}; "
            f"optional {', '.join(OPTIONAL)}); nothing recorded"
        )
    if not table:
        raise ContractReadingInvalid(f"{path.name}: no rows; nothing recorded")
    rows, problems = [], []
    for number, raw in enumerate(table, start=2):
        try:
            rows.append(_parse_row(number, {k: (v or "").strip() for k, v in raw.items() if k}))
        except (ValueError, InvalidOperation) as exc:
            problems.append(f"line {number}: {exc}")
    if problems:
        raise ContractReadingInvalid(f"{path.name}: {'; '.join(problems)}; nothing recorded")
    return rows


def _token(con: duckdb.DuckDBPyConnection, party: str) -> str | None:
    """The token the reading's counterparty is (a token as given, or a masked name's token)."""
    found = con.execute(
        "SELECT token FROM mask_values WHERE token = ? OR match_key = ?",
        [party, norm(party)],
    ).fetchone()
    return found[0] if found else None


def _on_register(con: duckdb.DuckDBPyConnection, engagement_id: int) -> dict[str, set[str]]:
    """Per party kind, the tokens and the invoice numbers of its loaded register ('customer',
    'vendor', 'customer_invoices', 'vendor_invoices')."""
    found: dict[str, set[str]] = {}
    for party_kind, (kind, column) in REGISTER.items():
        rows = con.execute(
            f"SELECT r.{column}, trim(r.invoice_no) FROM register_lines r JOIN source_files f "
            "USING (file_id) WHERE f.engagement_id = ? AND f.status = 'loaded' AND r.kind = ?",
            [engagement_id, kind],
        ).fetchall()
        found[party_kind] = {party for party, _ in rows}
        found[f"{party_kind}_invoices"] = {number for _, number in rows}
    return found


def load(run: Run, company: str, path: Path, reading: Path) -> list[dict[str, Any]]:
    """Store the reading's contracts for the open engagement. E864, E865 (nothing recorded)."""
    rows = read_rows(reading)
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        engagement_id = engagement["engagement_id"]
        on_register = _on_register(con, engagement_id)
        unknown = []
        for row in rows:
            row["counterparty"] = _token(con, row["counterparty"])
            kind = row["party_kind"]
            if row["counterparty"] not in on_register[kind] or (
                row["invoice_no"] and row["invoice_no"] not in on_register[f"{kind}_invoices"]
            ):
                unknown.append(f"line {row['line']}")
        if unknown:
            raise ContractNotOnRegister(
                f"{reading.name}: {', '.join(unknown)} name a counterparty or invoice number that "
                "no loaded register of its party kind has; nothing recorded"
            )
        con.execute(
            "DELETE FROM contracts WHERE engagement_id = ? AND sha256 = ?", [engagement_id, sha256]
        )
        now = utcnow()
        names = (
            "party_kind", "counterparty", "invoice_no", "start_date", "end_date", "value",
            *TERM_COLUMNS, "page",
        )  # fmt: skip
        for row in rows:
            con.execute(
                f"INSERT INTO contracts (engagement_id, {', '.join(names)}, file_name, sha256, "
                f"read_by, run_id, created_at) VALUES (?, {', '.join('?' for _ in names)}, ?, ?, "
                "'claude', ?, ?)",
                [
                    engagement_id, *(row[name] for name in names), path.name, sha256, run.run_id,
                    now,
                ],
            )  # fmt: skip
        return list_contracts_in(con, engagement_id)


def list_contracts_in(con: duckdb.DuckDBPyConnection, engagement_id: int) -> list[dict[str, Any]]:
    return fetch_dicts(
        con, "SELECT * FROM contracts WHERE engagement_id = ? ORDER BY contract_id", [engagement_id]
    )


def list_contracts(run: Run, company: str) -> list[dict[str, Any]]:
    with company_database(run, company) as con:
        return list_contracts_in(con, require_open(con, company)["engagement_id"])


def format_contract(row: dict[str, Any]) -> str:
    invoice = f", invoice {row['invoice_no']}" if row["invoice_no"] else ""
    value = f", value {rupees(row['value'])}" if row["value"] is not None else ""
    terms = f", billing {row['billing_terms']}" if row["billing_terms"] else ""
    return (
        f"#{row['contract_id']} {row['party_kind']} {row['counterparty']}{invoice}: "
        f"{row['start_date']} to {row['end_date']}{value}{terms}{terms_text(row)} "
        f"({row['file_name']} p.{row['page']}, read by {row['read_by']}, not verified)"
    )
