"""Contracts (`fin recast contracts <company> <file> --reading <csv>`): optional evidence for the
recast, never the source of its schedule.

Claude reads a contract document and writes a reading CSV, one row per contract (a UTF-8 file with a
header row; columns in any order):

- `customer` (required): the customer's token (CUST-0001) or name as printed; a name is masked to
  its token (`mask_values`, whichever kind first masked it); one no loaded sales register has is
  E865;
- `invoice_no`: the sales invoice the contract supports; one no register has is E865;
- `start`, `end` (required): the contract's dates, YYYY-MM-DD;
- `value`: the contract value as printed (`12,00,000.00`), in rupees; `billing_terms`: as read,
  with known names replaced by their tokens;
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
from fin.diligence.mask import Masker
from fin.extract.profiles import norm
from fin.extract.units import parse_figure

REQUIRED = ("customer", "start", "end", "page")
OPTIONAL = ("invoice_no", "value", "billing_terms")


def _date(text: str) -> date:
    return date.fromisoformat(text.strip())


def read_rows(path: Path) -> list[dict[str, Any]]:
    """The reading's rows (validated, line numbers kept). E864 listing every line at fault."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        names = set(reader.fieldnames or [])
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
        row = {key: (value or "").strip() for key, value in raw.items() if key}
        try:
            start, end = _date(row["start"]), _date(row["end"])
            if end < start:
                raise ValueError("end is before start")
            page = int(row["page"])
            if page < 1:
                raise ValueError("page starts at 1")
            if not row["customer"]:
                raise ValueError("customer is empty")
            value = None
            if row.get("value"):
                figure = parse_figure(row["value"])
                if figure is None:
                    raise ValueError(f"value {row['value']!r} is not a figure")
                value = Decimal(figure)
        except (ValueError, InvalidOperation) as exc:
            problems.append(f"line {number}: {exc}")
            continue
        rows.append(
            {
                "line": number,
                "customer": row["customer"],
                "invoice_no": row.get("invoice_no") or None,
                "start_date": start,
                "end_date": end,
                "value": value,
                "billing_terms": row.get("billing_terms") or None,
                "page": page,
            }
        )
    if problems:
        raise ContractReadingInvalid(f"{path.name}: {'; '.join(problems)}; nothing recorded")
    return rows


def _token(con: duckdb.DuckDBPyConnection, customer: str) -> str | None:
    """The customer token the reading's customer is (a token as given, or a name masked to one)."""
    found = con.execute(
        "SELECT token FROM mask_values WHERE token = ? OR match_key = ?",
        [customer, norm(customer)],
    ).fetchone()
    return found[0] if found else None


def load(run: Run, company: str, path: Path, reading: Path) -> list[dict[str, Any]]:
    """Store the reading's contracts for the open engagement. E864, E865 (nothing recorded)."""
    rows = read_rows(reading)
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        engagement_id = engagement["engagement_id"]
        on_register = {
            customer
            for (customer,) in con.execute(
                "SELECT DISTINCT r.customer FROM register_lines r JOIN source_files f USING "
                "(file_id) WHERE f.engagement_id = ? AND f.status = 'loaded' AND r.kind = 'sales'",
                [engagement_id],
            ).fetchall()
        }
        invoices = {
            number
            for (number,) in con.execute(
                "SELECT DISTINCT trim(r.invoice_no) FROM register_lines r JOIN source_files f "
                "USING (file_id) WHERE f.engagement_id = ? AND f.status = 'loaded' "
                "AND r.kind = 'sales'",
                [engagement_id],
            ).fetchall()
        }
        masker = Masker(con, run.run_id)
        unknown = []
        for row in rows:
            row["billing_terms"] = masker.text(row["billing_terms"])
            row["customer"] = _token(con, row["customer"])
            if row["customer"] not in on_register or (
                row["invoice_no"] and row["invoice_no"] not in invoices
            ):
                unknown.append(f"line {row['line']}")
        if unknown:
            raise ContractNotOnRegister(
                f"{reading.name}: {', '.join(unknown)} name a customer or invoice number that no "
                "loaded sales register has; nothing recorded"
            )
        con.execute(
            "DELETE FROM contracts WHERE engagement_id = ? AND sha256 = ?", [engagement_id, sha256]
        )
        now = utcnow()
        for row in rows:
            con.execute(
                "INSERT INTO contracts (engagement_id, customer, invoice_no, start_date, end_date, "
                "value, billing_terms, file_name, sha256, page, read_by, run_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'claude', ?, ?)",
                [
                    engagement_id, row["customer"], row["invoice_no"], row["start_date"],
                    row["end_date"], row["value"], row["billing_terms"], path.name, sha256,
                    row["page"], run.run_id, now,
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
        f"#{row['contract_id']} {row['customer']}{invoice}: {row['start_date']} to "
        f"{row['end_date']}{value}{terms} ({row['file_name']} p.{row['page']}, read by "
        f"{row['read_by']}, not verified)"
    )
