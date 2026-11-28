"""The source exports as sheets (`Sheet`: rows of cell values), read off the books.

Each function returns the sheets of one file, laid out as the column profile in
`templates/profiles/` reads it: Tally trial balance and Ledger Vouchers, the billing registers, the
payroll snapshot, the orderbook, the GSTR-1 and GSTR-3B summaries and the ICICI bank statement.
"""

from dataclasses import dataclass, field
from datetime import date, datetime

from mock.generator import books as b
from mock.generator import data as d

Cell = str | int | float | datetime | None


@dataclass
class Sheet:
    title: str
    rows: list[list[Cell]] = field(default_factory=list)
    bold: set[int] = field(default_factory=set)  # 1-based rows
    indent: dict[int, int] = field(default_factory=dict)  # 1-based row -> indent of column A
    merges: list[str] = field(default_factory=list)

    def add(self, row: list[Cell], *, bold: bool = False, indent: int = 0) -> None:
        self.rows.append(row)
        if bold:
            self.bold.add(len(self.rows))
        if indent:
            self.indent[len(self.rows)] = indent


def at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day)


def period_line(period: str) -> str:
    start, end = d.PERIODS[period]
    return f"{start.day}-{start:%b-%Y} to {end.day}-{end:%b-%Y}"


def trial_balance(books: b.Books, period: str, audited: bool = True) -> list[Sheet]:
    tb = b.balances(books, period, audited)
    s = Sheet("Trial Balance")
    for line in (d.COMPANY, "Trial Balance", period_line(period)):
        s.add([line])
    s.add(["Particulars", "Closing Balance", None])
    s.add([None, "Debit", "Credit"])
    s.merges += ["A4:A5", "B4:C4"]
    start, end = d.PERIODS[period]
    moved = {
        e[0]
        for v in books.vouchers
        if start <= v.when <= end and (audited or not v.audit)
        for e in v.entries
    }
    groups: dict[str, list[str]] = {}
    for ledger, (group, _) in d.LEDGERS.items():
        if tb[ledger] or ledger in moved:  # Tally lists a ledger with entries at a nil balance
            groups.setdefault(group, []).append(ledger)
    for group, ledgers in groups.items():
        net = sum(tb[ledger] for ledger in ledgers)
        s.add([group, net if net > 0 else None, -net if net < 0 else None], bold=True)
        for ledger in ledgers:
            v = tb[ledger]
            s.add([ledger, v if v >= 0 else None, -v if v < 0 else None], indent=1)
    debit = sum(v for v in tb.values() if v > 0)
    s.add(["Grand Total", debit, debit], bold=True)
    return [s]


def _particulars(v: b.Voucher, ledger: str) -> str:
    return next(e[0] for e in v.entries if e[0] != ledger)


def ledger_vouchers(books: b.Books, period: str) -> list[Sheet]:
    """Every ledger of the trial balance or with an entry in the period, one block each."""
    start, end = d.PERIODS[period]
    tb = b.balances(books, period)
    s = Sheet("Ledger Vouchers")
    for line in (d.COMPANY, "Ledger Vouchers", period_line(period)):
        s.add([line])
    s.add(["Date", "Particulars", "Vch Type", "Vch No.", "Debit", "Credit"])
    entries: dict[str, list[list[Cell]]] = {ledger: [] for ledger in d.LEDGERS}
    for v in books.vouchers:
        if start <= v.when <= end:
            for ledger, debit, credit in v.entries:
                entries[ledger].append(
                    [
                        at(v.when),
                        _particulars(v, ledger),
                        v.kind,
                        v.number,
                        debit or None,
                        credit or None,
                    ]
                )
    for ledger, rows in entries.items():
        if not rows and not tb[ledger]:
            continue
        moved = sum((r[4] or 0) - (r[5] or 0) for r in rows)  # type: ignore[operator]
        pl = d.LEDGERS[ledger][0] in d.PL_GROUPS
        opening = 0 if pl else tb[ledger] - moved
        s.add([f"Ledger: {ledger}"], bold=True)
        s.add(
            [None, "Opening Balance", None, None, max(opening, 0) or None, max(-opening, 0) or None]
        )
        for row in rows:
            s.add(row)
        closing = opening + moved
        s.add(
            [None, "Closing Balance", None, None, max(closing, 0) or None, max(-closing, 0) or None]
        )
    return [s]


SALES_HEADERS = [
    "Bill Id",
    "Purchase Id",
    "Customer Id",
    "Customer Name",
    "Location",
    "Invoice No",
    "Contract Start Date",
    "Contract End Date",
    "Invoice Date",
    "Order Type",
    "Product",
    "Service Start Date",
    "Service End Date",
    "Quantity",
    "Rate",
    "Amount",
    "Currency",
    "Currency Rate",
    "Amount in Rs",
    "Service Type",
    "Handler",
    "Submitted By",
]
PURCHASE_HEADERS = [
    "Purchase Id",
    "Vendor Id",
    "Vendor Name",
    "Location",
    "Invoice No",
    "Invoice Date",
    "Product",
    "Quantity",
    "Rate",
    "Amount",
    "Currency",
    "Currency Rate",
    "Amount in Rs",
    "Handler",
    "Submitted By",
]


def registers(books: b.Books) -> list[Sheet]:
    sales, purchase = Sheet("Sales Register"), Sheet("Purchase Register")
    sales.add(SALES_HEADERS)
    purchase.add(PURCHASE_HEADERS)
    for n, inv in enumerate(books.invoices, start=1):
        if inv.kind == "sales":
            c = d.CUSTOMERS[inv.party]
            order = "Renewal" if inv.number == d.BILLED_AHEAD["number"] else "Recurring"
            sales.add(
                [
                    f"B{n:04d}",
                    None,
                    c.customer_id,
                    c.ledger,
                    c.location,
                    inv.number,
                    at(inv.service[0]),
                    at(inv.service[1]),
                    at(inv.when),
                    order,
                    inv.product,
                    at(inv.service[0]),
                    at(inv.service[1]),
                    1,
                    inv.amount,
                    inv.amount,
                    inv.currency,
                    inv.rate,
                    inv.amount_inr,
                    inv.service_type,
                    d.HANDLERS[0],
                    d.HANDLERS[1],
                ]
            )
        else:
            purchase.add(
                [
                    f"P{n:04d}",
                    2101,
                    inv.party,
                    "Pune",
                    inv.number,
                    at(inv.when),
                    inv.product,
                    1,
                    inv.amount,
                    inv.amount,
                    "INR",
                    1,
                    inv.amount_inr,
                    d.HANDLERS[0],
                    d.HANDLERS[1],
                ]
            )
    for s, width in ((sales, len(SALES_HEADERS)), (purchase, len(PURCHASE_HEADERS))):
        s.add(["Total", *([None] * (width - 1))], bold=True)
    return [sales, purchase]


def payroll() -> list[Sheet]:
    s = Sheet("Employee Master")
    s.add([d.COMPANY])
    s.add([f"Employee Master as on {d.PAYROLL_AS_ON.day}-{d.PAYROLL_AS_ON:%b-%Y}"])
    s.add(
        [
            "Employee Code",
            "Employee Name",
            "Designation",
            "Department",
            "Vertical",
            "Sub Vertical",
            "Date of Joining",
            "Date of Resignation",
            "Gross Salary",
            "CTC",
        ]
    )
    for e in d.EMPLOYEES:
        s.add(
            [
                e.code,
                e.name,
                e.designation,
                e.department,
                e.vertical,
                e.sub_vertical,
                at(e.joined),
                None,
                e.ctc * 9 // 10,
                e.ctc,
            ]
        )
    s.add(
        [
            "Total",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            sum(e.ctc * 9 // 10 for e in d.EMPLOYEES),
            sum(e.ctc for e in d.EMPLOYEES),
        ],
        bold=True,
    )
    return [s]


def orders(books: b.Books) -> list[tuple[str, str, date, float, str, date, date, float]]:
    """The open orders on 31-Mar-2025: (customer, order no, date, value, currency, start, end,
    billed to date), in the order's currency."""
    asat = d.ORDERS_AS_AT

    def billed(code: str, start: date, end: date) -> float:
        return sum(
            i.amount
            for i in books.invoices
            if i.kind == "sales"
            and i.party == code
            and start <= i.when <= min(end, asat)
            and i.number != d.BILLED_AHEAD["number"]
        )

    c1 = next(p for p in d.PLANS if p.customer == "C1")
    c4 = next(p for p in d.PLANS if p.customer == "C4")
    c1_value = 12 * (c1.amounts["FY24"] + c1.amounts["FY25"] + c1.amounts[d.STUB])
    c4_value = 8 * c4.amounts["FY25"]
    ahead, new = d.BILLED_AHEAD, d.NEW_ORDER
    return [
        (
            "C1",
            "SO-2023-001",
            date(2023, 3, 20),
            c1_value,
            "INR",
            date(2023, 4, 1),
            date(2026, 3, 31),
            billed("C1", date(2023, 4, 1), date(2026, 3, 31)),
        ),
        (
            "C2",
            "SO-2025-009",
            date(2025, 3, 20),
            ahead["amount"],
            "INR",
            *ahead["service"],
            ahead["amount"],
        ),
        (
            "C4",
            "SO-2024-004",
            date(2024, 3, 25),
            c4_value,
            "USD",
            date(2024, 4, 1),
            date(2026, 3, 31),
            billed("C4", date(2024, 4, 1), date(2026, 3, 31)),
        ),
        (
            new["customer"],
            new["number"],
            new["date"],
            new["value"],
            "INR",
            new["start"],
            new["end"],
            0,
        ),
    ]


def orderbook(books: b.Books) -> list[Sheet]:
    s = Sheet("Orderbook")
    s.add([d.COMPANY])
    s.add([f"Orderbook as at {d.ORDERS_AS_AT.day}-{d.ORDERS_AS_AT:%b-%Y}"])
    s.add(
        [
            "Customer",
            "Order No",
            "Order Date",
            "Order Value",
            "Currency",
            "Start Date",
            "End Date",
            "Billed To Date",
        ]
    )
    for code, number, when, value, currency, start, end, billed in orders(books):
        s.add(
            [
                d.CUSTOMERS[code].ledger,
                number,
                at(when),
                value,
                currency,
                at(start),
                at(end),
                billed,
            ]
        )
    s.add(["Total", None, None, None, None, None, None, None], bold=True)
    return [s]


def gst_months(books: b.Books, period: str = "FY25") -> list[dict[str, int | str]]:
    """Per month of the period: the returns' taxable value and tax (the late invoice left out),
    and the input tax credit booked."""
    out = []
    for m in b.months(*d.PERIODS[period]):
        end = b.month_end(m)
        taxable = sum(
            i.amount_inr
            for i in books.invoices
            if i.kind == "sales" and m <= i.when <= end and i.number != d.GST_LATE_INVOICE
        )
        tax = sum(
            i.amount_inr * d.GST_RATE // 100
            for i in books.invoices
            if i.kind == "sales"
            and i.currency == "INR"
            and m <= i.when <= end
            and i.number != d.GST_LATE_INVOICE
        )
        itc = sum(
            e[1]
            for v in books.vouchers
            if m <= v.when <= end and v.kind == "Purchase"
            for e in v.entries
            if e[0].startswith("Input")
        )
        out.append({"month": f"{m:%b-%Y}", "taxable": taxable, "tax": tax, "itc": itc})
    return out


def gst_return(books: b.Books, kind: str) -> list[Sheet]:
    s = Sheet("Summary")
    start, end = d.PERIODS["FY25"]
    title = "GSTR-1 summary" if kind == "gstr1" else "GSTR-3B summary"
    for line in (d.COMPANY, title, f"Return period {start:%d-%b-%Y} to {end:%d-%b-%Y}"):
        s.add([line])
    header = ["Month", "Taxable Value", "IGST", "CGST", "SGST", "Cess"]
    if kind == "gstr3b":
        header += ["ITC Claimed", "RCM Liability", "Paid in Cash", "Paid through Credit"]
    s.add(header)
    rows = []
    for m in gst_months(books):
        row: list[Cell] = [m["month"], m["taxable"], 0, m["tax"], m["tax"], 0]
        if kind == "gstr3b":
            row += [m["itc"], 0, 2 * int(m["tax"]) - int(m["itc"]), m["itc"]]
        rows.append(row)
        s.add(row)
    s.add(["Total", *(sum(r[i] for r in rows) for i in range(1, len(header)))], bold=True)  # type: ignore[misc]
    return [s]


def bank_statement(books: b.Books, period: str) -> list[Sheet]:
    """The ICICI statement for the period: the bank ledger's vouchers on their cleared date."""
    start, end = d.PERIODS[period]
    opening = b.bank_balance(books, date.fromordinal(start.toordinal() - 1))
    closing = b.bank_balance(books, end)
    s = Sheet("Statement")
    for line in (
        "ICICI Bank - Statement of Account",
        f"Account Number : {d.BANK_ACCOUNT}",
        "Account Currency : INR",
        f"Transactions Period: {start:%d/%m/%Y} to {end:%d/%m/%Y}",
        f"Opening Balance : {opening:,.2f}",
        f"Closing Balance : {closing:,.2f}",
    ):
        s.add([line])
    s.add(
        [
            "S No.",
            "Value Date",
            "Transaction Date",
            "Cheque Number",
            "Transaction Remarks",
            "Withdrawal Amount (INR)",
            "Deposit Amount (INR)",
            "Balance (INR)",
        ]
    )
    lines = [
        v
        for v in books.vouchers
        if start <= (v.cleared or v.when) <= end and any(e[0] == d.BANK for e in v.entries)
    ]
    balance = opening
    for n, v in enumerate(sorted(lines, key=lambda v: v.cleared or v.when), start=1):
        when = (v.cleared or v.when).strftime("%d/%m/%Y")
        net = sum(e[1] - e[2] for e in v.entries if e[0] == d.BANK)
        balance += net
        cheque = d.UNCLEARED_CHEQUE["cheque"] if v.cleared else None
        s.add(
            [
                n,
                when,
                when,
                cheque,
                v.remarks,
                -net if net < 0 else None,
                net if net > 0 else None,
                balance,
            ]
        )
    return [s]
