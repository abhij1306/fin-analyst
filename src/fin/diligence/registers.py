"""What the rows of a sales and purchase registers workbook mean (DILIGENCE_PLAN.md D3).

The rows come from `fin.extract.registers.read_registers`. Three steps, called by
`fin.diligence.sources.ingest_source`:

1. `parse`: each row becomes an invoice line (E813 for a row that is not one: no invoice number,
   date, party or Amount in Rs, or a date in another format; E204 for a bad number). Rows named in
   the profile's `skip` (totals) are not lines.
2. `file_period`: the register has no period header, so the file's period comes from its invoice
   dates: first to last date, the fiscal years they fall in, and basis 'unaudited' when any date is
   in the engagement's stub. Lines outside the engagement are loaded and counted in the summary.
3. `load`: the lines are stored in `register_lines` with sheet + row, customer and vendor names
   masked (`customer` and `vendor` kinds), handler and submitted-by as `user` tokens, known masked
   names inside the free-text columns replaced. The same invoice number twice is a finding
   (`duplicate_findings`), not a failure: both rows stay.

Amount in Rs is stored as filed (before GST); fin does not recompute it from amount and rate.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import duckdb

from fin.audit.errors import RegisterLineInvalid
from fin.audit.logger import Run
from fin.diligence.mask import Masker
from fin.extract.excel import parse_number
from fin.extract.ledger import SheetRow, amount, parse_date
from fin.extract.periods import fiscal_year_of
from fin.extract.profiles import Profile, norm
from fin.extract.registers import RegisterTable
from fin.findings import record

CHECK = "register_duplicate_invoice"
MASKED = {"customer": "customer", "vendor": "vendor", "handler": "user", "submitted_by": "user"}
FREE_TEXT = (
    "location",
    "order_type",
    "product",
    "service_type",
)  # known names in them are replaced
IDS = ("bill_id", "purchase_id", "customer_id", "vendor_id", "invoice_no", "currency")
DATES = ("contract_start", "contract_end", "service_start", "service_end")
NUMBERS = ("quantity", "rate", "amount", "currency_rate")
COLUMNS = (
    "bill_id, purchase_id, customer_id, customer, vendor_id, vendor, location, invoice_no, "
    "contract_start, contract_end, invoice_date, order_type, product, service_start, service_end, "
    "quantity, rate, amount, currency, currency_rate, amount_inr, service_type, handler, "
    "submitted_by, period"
)


@dataclass(frozen=True)
class Line:
    kind: str  # 'sales' or 'purchase'
    sheet: str
    row: int
    invoice_date: date
    amount_inr: Decimal
    values: dict[str, Any]  # the other fields, as filed (names not yet masked)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return " ".join(str(value).split()) or None


def _is_skipped(r: SheetRow, profile: Profile) -> bool:
    """A total row: no invoice date, and a cell named in the profile's `skip`. A dated row is
    always an invoice line, even when a product or party is called "Total"."""
    if _text(r.cells.get("invoice_date")) is not None:
        return False
    return any(norm(v) in profile.skip for v in r.cells.values() if v is not None)


def _line(kind: str, r: SheetRow, profile: Profile, file_name: str) -> Line:
    where = f"{file_name} {r.sheet}!{r.row}"
    cells = r.cells
    party = "customer" if kind == "sales" else "vendor"
    for field in ("invoice_no", party):
        if _text(cells.get(field)) is None:
            raise RegisterLineInvalid(
                f"{where}: no {field.replace('_', ' ')} on this {kind} register row. If it is not "
                f"an invoice line, add its name to [rows] skip in the profile ({profile.name})"
            )
    invoice_date = parse_date(cells.get("invoice_date"), profile)
    if invoice_date is None:
        raise RegisterLineInvalid(
            f"{where}: invoice date {cells.get('invoice_date')!r} is not a date in the profile's "
            f"date format ({profile.date_format}, profile {profile.name})"
        )
    if _text(cells.get("amount_inr")) is None:
        raise RegisterLineInvalid(f"{where}: no Amount in Rs")
    values: dict[str, Any] = {}
    for field, value in cells.items():
        if field in DATES:
            values[field] = parse_date(value, profile)
            if _text(value) is not None and values[field] is None:
                raise RegisterLineInvalid(
                    f"{where}: {field.replace('_', ' ')} {value!r} is not a date in the profile's "
                    f"date format ({profile.date_format}, profile {profile.name})"
                )
        elif field in NUMBERS:
            values[field] = parse_number(value, where)[1] if _text(value) is not None else None
        elif field not in ("invoice_date", "amount_inr"):
            values[field] = _text(value)
    return Line(
        kind, r.sheet, r.row, invoice_date, amount(cells["amount_inr"], profile, where), values
    )


def parse(table: RegisterTable, file_name: str) -> list[Line]:
    """The invoice lines, sales sheet first, in row order. E813, E204."""
    lines = []
    for kind, rows in table.rows.items():
        for r in rows:
            if not _is_skipped(r, table.profile):
                lines.append(_line(kind, r, table.profile, file_name))
    if not lines:
        raise RegisterLineInvalid(f"{file_name}: no invoice line below the column headers")
    return lines


def period_label(day: date, engagement: dict[str, Any], start_month: int) -> str | None:
    """The engagement period a date is in: the stub, or an annual period in scope; else None."""
    stub = engagement.get("stub")
    if stub and engagement["stub_start"] <= day <= engagement["stub_end"]:
        return stub
    label = fiscal_year_of(day, start_month)
    return label if label in engagement["periods"].split(",") else None


def file_period(
    lines: list[Line], engagement: dict[str, Any], start_month: int
) -> tuple[str, str, date, date]:
    """(period name, basis, first invoice date, last invoice date) of the lines."""
    first = min(line.invoice_date for line in lines)
    last = max(line.invoice_date for line in lines)
    names = sorted({fiscal_year_of(line.invoice_date, start_month) for line in lines})
    period = names[0] if len(names) == 1 else f"{names[0]}-{names[-1]}"
    stub = engagement.get("stub")
    in_stub = bool(stub) and any(
        period_label(line.invoice_date, engagement, start_month) == stub for line in lines
    )
    return period, "unaudited" if in_stub else "audited", first, last


def duplicate_findings(
    lines: list[Line], keys: list[str], file_name: str, engagement: dict[str, Any], month: int
) -> list[dict[str, Any]]:
    """A finding per line whose invoice number was already used: sales by invoice number, purchases
    by vendor and invoice number (vendors number their own invoices). `keys`: each line's party as
    stored (token). The finding's figure is the repeated line's Amount in Rs (what is at stake)."""
    first: dict[tuple[str, str, str], Line] = {}
    out = []
    for line, party in zip(lines, keys, strict=True):
        invoice_no = line.values["invoice_no"]
        key = (line.kind, party if line.kind == "purchase" else "", norm(invoice_no))
        if key not in first:
            first[key] = line
            continue
        was = first[key]
        out.append(
            {
                "file_name": file_name,
                "check_name": CHECK,
                "finding": "duplicate_invoice_no",
                "statement": f"{line.kind}_register",
                "period": period_label(line.invoice_date, engagement, month),
                "label": (f"{party} {invoice_no}" if line.kind == "purchase" else invoice_no)
                + f" ({line.sheet}!{line.row})",  # the row, so each repeat is its own finding
                "fin_value": line.amount_inr,
                "difference": line.amount_inr,
                "fin_source": f"{line.sheet}!{line.row}",
                "other_value": was.amount_inr,
                "other_source": f"{was.sheet}!{was.row}",
                "message": (
                    f"{line.kind} register: invoice number {invoice_no!r} is on "
                    f"{line.sheet}!{was.row} and again on {line.sheet}!{line.row}; both rows kept "
                    "as filed"
                ),
            }
        )
    return out


def load(
    run: Run,
    company: str,
    con: duckdb.DuckDBPyConnection,
    lines: list[Line],
    file: dict[str, Any],
    masker: Masker,
    engagement: dict[str, Any],
    start_month: int,
) -> list[str]:
    """Insert the lines (names masked) and record the duplicate-invoice findings; the lines to
    show."""
    from fin.diligence.sources import insert_rows  # sources calls this module

    values = [dict(line.values) for line in lines]
    for v in values:  # every name is tokenised before any free text is searched for names
        for field, kind in MASKED.items():
            if v.get(field) is not None:
                v[field] = masker.token(kind, v[field])
    rows, parties = [], []
    for line, v in zip(lines, values, strict=True):
        for field in FREE_TEXT:
            if v.get(field) is not None:
                v[field] = masker.text(v[field])
        parties.append(v.get("customer") or v.get("vendor") or "")
        v["invoice_date"], v["amount_inr"] = line.invoice_date, line.amount_inr
        v["period"] = period_label(line.invoice_date, engagement, start_month)
        rows.append(
            [file["file_id"], line.sheet, line.row, line.kind]
            + [v.get(field) for field in COLUMNS.split(", ")]
            + [run.run_id]
        )
    insert_rows(
        con,
        "register_lines",
        f'file_id, sheet, "row", kind, {COLUMNS}, run_id',
        rows,
    )
    name, file_id = file["file_name"], file["file_id"]
    findings = duplicate_findings(lines, parties, name, engagement, start_month)
    record(
        run, company, con, findings, f"file_id = ? AND check_name = '{CHECK}'", [file_id], file_id
    )
    count = {k: sum(1 for x in lines if x.kind == k) for k in ("sales", "purchase")}
    outside = sum(1 for x in lines if period_label(x.invoice_date, engagement, start_month) is None)
    out = [
        f"registers {file['period']} ({file['period_start']} to {file['period_end']}): "
        f"{count['sales']} sales lines, {count['purchase']} purchase lines, "
        f"{masker.added} names newly masked, {len(findings)} findings"
    ]
    if outside:
        out.append(
            f"{outside} lines have an invoice date outside the engagement's periods (loaded; "
            "filter on invoice_date)"
        )
    if findings:
        out.append(
            f"{len(findings)} invoice numbers appear more than once (rows kept as filed; "
            f"findings, not failures): `fin findings list {company}`"
        )
    return out
