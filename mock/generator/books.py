"""The books of the mock company, built from `data`: vouchers, then balances per period.

A voucher is double entry (debits = credits). Every other document is read off the vouchers: the
Ledger Vouchers and trial balances (Tally), the bank statements (the vouchers on the bank ledger, on
the day the bank cleared them), the billing registers (the sales and licence purchase invoices) and
the GST returns.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from mock.generator import data as d
from mock.generator import data_counterparty as cp


@dataclass
class Voucher:
    when: date
    kind: str  # Tally voucher type: Sales, Purchase, Receipt, Payment, Journal, Credit Note
    number: str
    entries: list[tuple[str, int, int]]  # (ledger, debit, credit); the party first
    remarks: str = ""  # the bank's narration, for a voucher on the bank ledger
    cleared: date | None = None  # the bank's date when it differs from the books'
    audit: bool = False  # the auditor's adjustment (not in the provisional books)


@dataclass
class Invoice:
    """A billing-system register line (sales or licence purchase)."""

    kind: str  # sales or purchase
    number: str
    when: date
    party: str  # C1.. for sales, the vendor ledger for purchases
    product: str
    amount: float  # own currency, before GST
    currency: str
    rate: float
    amount_inr: int
    service: tuple[date, date]
    service_type: str = ""


@dataclass
class Books:
    vouchers: list[Voucher] = field(default_factory=list)
    invoices: list[Invoice] = field(default_factory=list)

    def add(self, voucher: Voucher) -> Voucher:
        debit = sum(e[1] for e in voucher.entries)
        if debit != sum(e[2] for e in voucher.entries):
            raise ValueError(f"voucher {voucher.number} does not balance")
        self.vouchers.append(voucher)
        return voucher


def period_of(when: date) -> str:
    return next(p for p, (a, b) in d.PERIODS.items() if a <= when <= b)


def months(start: date, end: date) -> list[date]:
    out, m = [], date(start.year, start.month, 1)
    while m <= end:
        out.append(m)
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return out


def month_end(m: date) -> date:
    nxt = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def _all_months() -> list[date]:
    return months(d.PERIODS["FY23"][0], d.PERIODS[d.STUB][1])


def _invoice_dates(plan: d.Plan) -> list[tuple[date, tuple[date, date]]]:
    out = []
    for m in _all_months():
        if plan.amounts.get(period_of(m)) is None or (plan.first and m < plan.first):
            continue
        quarter_start = m.month in (4, 7, 10, 1)
        if plan.cycle == "monthly":
            out.append((m.replace(day=plan.day), (m, month_end(m))))
        elif plan.cycle == "quarterly" and quarter_start:
            out.append((m.replace(day=plan.day), (m, month_end(months(m, m + timedelta(62))[2]))))
        elif plan.cycle == "quarter_end" and m.month in (6, 9, 12, 3):
            out.append((month_end(m), (months(m - timedelta(61), m)[0], month_end(m))))
    return out


def _sale(
    books: Books,
    plan: d.Plan,
    n: int,
    when: date,
    service: tuple[date, date],
    amount: float,
    number: str | None = None,
    product: str | None = None,
) -> None:
    customer = d.CUSTOMERS[plan.customer]
    rate = plan.rates.get(period_of(when), 1.0)
    inr = round(amount * rate)
    number = number or f"INV-{when:%y%m}-{n:02d}"
    gst = 0 if plan.currency != "INR" else inr * d.GST_RATE // 100
    entries = [(customer.ledger, inr + 2 * gst, 0), (plan.ledger, 0, inr)]
    if gst:
        entries += [("Output CGST", 0, gst), ("Output SGST", 0, gst)]
    books.add(Voucher(when, "Sales", number, entries))
    books.invoices.append(
        Invoice(
            "sales",
            number,
            when,
            plan.customer,
            product or plan.product,
            amount,
            plan.currency,
            rate,
            inr,
            service,
            plan.service_type,
        )
    )
    paid = when + timedelta(days=customer.pay_days)
    if paid > d.PERIODS[d.STUB][1]:
        return
    if plan.currency == "INR":
        remarks = f"NEFT-{customer.ledger}-{number}"
        books.add(
            Voucher(
                paid,
                "Receipt",
                f"R-{number}",
                [(d.BANK, inr + 2 * gst, 0), (customer.ledger, 0, inr + 2 * gst)],
                remarks,
            )
        )
        return
    got = round(amount * (rate + d.FX_RECEIPT_PREMIUM))
    remarks = f"SWIFT INWARD-{customer.ledger}-{number}-USD {amount:,.2f}"
    books.add(
        Voucher(
            paid,
            "Receipt",
            f"R-{number}",
            [(d.BANK, got, 0), (customer.ledger, 0, inr), ("Foreign Exchange Gain", 0, got - inr)],
            remarks,
        )
    )


def _sales(books: Books) -> None:
    serial: dict[str, int] = {}
    entries = [(when, service, plan) for plan in d.PLANS for when, service in _invoice_dates(plan)]
    for when, service, plan in sorted(entries, key=lambda e: (e[0], e[2].customer)):
        key = f"{when:%y%m}"
        serial[key] = serial.get(key, 0) + 1
        _sale(books, plan, serial[key], when, service, plan.amounts[period_of(when)])
    ahead = d.BILLED_AHEAD
    plan = next(p for p in d.PLANS if p.customer == ahead["customer"])
    _sale(
        books,
        plan,
        0,
        ahead["date"],
        ahead["service"],
        ahead["amount"],
        ahead["number"],
        ahead["product"],
    )
    note = d.CREDIT_NOTE
    ledger = d.CUSTOMERS[note["customer"]].ledger
    books.add(
        Voucher(
            note["date"],
            "Credit Note",
            note["number"],
            [(ledger, 0, note["amount"]), ("Subscription Revenue", note["amount"], 0)],
        )
    )
    books.add(
        Voucher(
            note["refund"],
            "Payment",
            f"P-{note['number']}",
            [(ledger, note["amount"], 0), (d.BANK, 0, note["amount"])],
            f"NEFT-REFUND-{ledger}-{note['number']}",
        )
    )


def _purchases(books: Books) -> None:
    n = 0
    for m in _all_months():
        p = period_of(m)
        if m.month in (4, 7, 10, 1):
            n += 1
            amount = d.LICENCE_PURCHASE[p]
            when, number = m.replace(day=10), f"QSD/{m:%y}/{n:03d}"
            gst = amount * d.GST_RATE // 100
            quarter_end = month_end(months(m, m + timedelta(62))[2])
            books.add(
                Voucher(
                    when,
                    "Purchase",
                    number,
                    [
                        (d.LICENCE_VENDOR, 0, amount + 2 * gst),
                        ("Purchase of Licences", amount, 0),
                        ("Input CGST", gst, 0),
                        ("Input SGST", gst, 0),
                    ],
                )
            )
            books.invoices.append(
                Invoice(
                    "purchase",
                    number,
                    when,
                    d.LICENCE_VENDOR,
                    "Analytics engine licences",
                    amount,
                    "INR",
                    1.0,
                    amount,
                    (m, quarter_end),
                )
            )
            paid, cleared = when + timedelta(days=d.LICENCE_PAY_DAYS), None
            cheque = d.UNCLEARED_CHEQUE
            if p == "FY25" and m.month == 1:  # paid late, by cheque
                paid, cleared = cheque["booked"], cheque["cleared"]
            if paid <= d.PERIODS[d.STUB][1]:
                remarks = (
                    f"CHQ {cheque['cheque']}-{d.LICENCE_VENDOR}"
                    if cleared
                    else (f"NEFT-{d.LICENCE_VENDOR}-{number}")
                )
                books.add(
                    Voucher(
                        paid,
                        "Payment",
                        f"P-{number}",
                        [(d.LICENCE_VENDOR, amount + 2 * gst, 0), (d.BANK, 0, amount + 2 * gst)],
                        remarks,
                        cleared,
                    )
                )
        gst = d.RETAINER * d.GST_RATE // 100
        number = f"SAL/{m:%y%m}"
        books.add(
            Voucher(
                m.replace(day=5),
                "Purchase",
                number,
                [
                    (d.ADVISER_VENDOR, 0, d.RETAINER + 2 * gst),
                    ("Consultancy Fees", d.RETAINER, 0),
                    ("Input CGST", gst, 0),
                    ("Input SGST", gst, 0),
                ],
            )
        )
        books.add(
            Voucher(
                m.replace(day=20),
                "Payment",
                f"P-{number}",
                [(d.ADVISER_VENDOR, d.RETAINER + 2 * gst, 0), (d.BANK, 0, d.RETAINER + 2 * gst)],
                f"NEFT-{d.ADVISER_VENDOR}-{number}",
            )
        )


def _monthly_costs(books: Books) -> None:
    for m in _all_months():
        p = period_of(m)
        books.add(
            Voucher(
                m,
                "Payment",
                f"RENT/{m:%y%m}",
                [("Rent", d.RENT[p], 0), (d.BANK, 0, d.RENT[p])],
                "Rent-Meridian Estates Pvt Ltd",
            )
        )
        books.add(
            Voucher(
                m.replace(day=15),
                "Payment",
                f"HOST/{m:%y%m}",
                [("Cloud Hosting", d.HOSTING[p], 0), (d.BANK, 0, d.HOSTING[p])],
                "CARD-Nimbus Cloud Services",
            )
        )
        paid = [e for e in d.EMPLOYEES if e.joined <= m]
        for n, employee in enumerate(paid, 1):  # one transfer per employee
            salary = employee.ctc // 12
            books.add(
                Voucher(
                    month_end(m),
                    "Payment",
                    f"SALARY/{m:%y%m}/{n:02d}",
                    [("Salaries", salary, 0), (d.BANK, 0, salary)],
                    f"SALARY-{m:%b-%Y}",
                )
            )


def _gst(books: Books) -> None:
    """Each month end: set the month's input tax off the output tax; pay the rest on the 20th of
    the next month (the opening liability on 20-Apr-2022)."""
    books.add(
        Voucher(
            date(2022, 4, 20),
            "Payment",
            "GST/2203",
            [("Output CGST", 27000, 0), ("Output SGST", 27000, 0), (d.BANK, 0, 54000)],
            "GST PAYMENT-GSTIN 27AACCC1234F1Z5",
        )
    )
    for m in _all_months():
        end = month_end(m)
        out = {h: 0 for h in ("CGST", "SGST")}
        inp = dict(out)
        for v in books.vouchers:
            if m <= v.when <= end:
                for ledger, debit, credit in v.entries:
                    if ledger.startswith("Output") and v.kind == "Sales":
                        out[ledger[-4:]] += credit
                    elif ledger.startswith("Input") and v.kind == "Purchase":
                        inp[ledger[-4:]] += debit
        for h in out:
            books.add(
                Voucher(
                    end,
                    "Journal",
                    f"GST-SETOFF/{m:%y%m}",
                    [(f"Output {h}", inp[h], 0), (f"Input {h}", 0, inp[h])],
                )
            )
        net = sum(out.values()) - sum(inp.values())
        pay = end + timedelta(days=20)
        if pay <= d.PERIODS[d.STUB][1]:
            books.add(
                Voucher(
                    pay,
                    "Payment",
                    f"GST/{m:%y%m}",
                    [
                        ("Output CGST", out["CGST"] - inp["CGST"], 0),
                        ("Output SGST", out["SGST"] - inp["SGST"], 0),
                        (d.BANK, 0, net),
                    ],
                    "GST PAYMENT-GSTIN 27AACCC1234F1Z5",
                )
            )


def _loan(books: Books) -> None:
    balance = -d.OPENING["Term Loan - Axis Bank"]
    for m in _all_months():
        if m.month not in (6, 9, 12, 3):
            continue
        end = month_end(m)
        interest = int(round(balance * d.LOAN_RATE / 4, -3))
        books.add(
            Voucher(
                end,
                "Payment",
                f"LOAN/{end:%y%m}",
                [
                    ("Interest on Term Loan", interest, 0),
                    ("Term Loan - Axis Bank", d.LOAN_REPAYMENT, 0),
                    (d.BANK, 0, interest + d.LOAN_REPAYMENT),
                ],
                "EMI-AXIS TERM LOAN 921060012345",
            )
        )
        balance -= d.LOAN_REPAYMENT


def _year_end(books: Books) -> None:
    block = d.OPENING["Computer Equipment"]
    for p, (_, end) in d.PERIODS.items():
        when, cost = d.CAPEX[p]
        books.add(
            Voucher(
                when,
                "Payment",
                f"CAPEX/{when:%y%m}",
                [("Computer Equipment", cost, 0), (d.BANK, 0, cost)],
                "NEFT-Arcadia Systems Pvt Ltd",
            )
        )
        share = d.DEPRECIATION_RATE / (2 if p == d.STUB else 1)
        dep = int(round((block + cost) * share, -3))
        block += cost - dep
        books.add(
            Voucher(
                end,
                "Journal",
                f"DEP/{p}",
                [("Depreciation", dep, 0), ("Computer Equipment", 0, dep)],
            )
        )
        books.add(
            Voucher(
                end,
                "Journal",
                f"GRAT/{p}",
                [
                    ("Gratuity Expense", d.GRATUITY[p], 0),
                    ("Provision for Gratuity", 0, d.GRATUITY[p]),
                ],
            )
        )
    for when, ledger, amount, what in d.ONE_OFFS:
        books.add(
            Voucher(
                when,
                "Payment",
                f"OO/{when:%y%m%d}",
                [(ledger, amount, 0), (d.BANK, 0, amount)],
                what,
            )
        )
    adj = d.AUDIT_ADJUSTMENT
    books.add(
        Voucher(
            adj["date"],
            "Journal",
            adj["number"],
            [(adj["debit"], adj["amount"], 0), (adj["credit"], 0, adj["amount"])],
            audit=True,
        )
    )


def _forensic(books: Books) -> None:
    """The journals planted for the forensic tests (SPEC-18, `data.FORENSIC`)."""
    for p in d.FORENSIC:
        books.add(
            Voucher(p.when, "Journal", p.number, [(p.debit, p.amount, 0), (p.credit, 0, p.amount)])
        )


def _counterparty(books: Books) -> None:
    """The entries planted for the counterparty tests (SPEC-20, `data_counterparty`)."""
    for vendor, when, number, amount, _ in cp.PURCHASES:
        gst = amount * d.GST_RATE // 100
        entries = [
            (vendor, 0, amount + 2 * gst),
            ("Purchase of Licences", amount, 0),
            ("Input CGST", gst, 0),
            ("Input SGST", gst, 0),
        ]
        books.add(Voucher(when, "Purchase", number, entries))
        service = (when, month_end(when))
        books.invoices.append(
            Invoice("purchase", number, when, vendor, "Analytics engine licences", amount, "INR",
                    1.0, amount, service)
        )  # fmt: skip
        paid = when + timedelta(days=cp.PAY_DAYS)
        if paid <= d.PERIODS[d.STUB][1]:
            total = amount + 2 * gst
            books.add(
                Voucher(paid, "Payment", f"P-{number}", [(vendor, total, 0), (d.BANK, 0, total)],
                        f"NEFT-{vendor}-{number}")
            )  # fmt: skip
    for when, number, amount, _ in cp.UNDER_LIMIT:
        entries = [(d.LICENCE_VENDOR, 0, amount), ("Purchase of Licences", amount, 0)]
        books.add(Voucher(when, "Purchase", number, entries))
        books.invoices.append(
            Invoice("purchase", number, when, d.LICENCE_VENDOR, "Analytics engine licences",
                    amount, "INR", 1.0, amount, (when, month_end(when)))
        )  # fmt: skip
    customer = d.CUSTOMERS["C3"].ledger
    for when, number, amount, _ in cp.CREDIT_NOTES:
        entries = [(customer, 0, amount), (cp.CREDIT_LEDGER, amount, 0)]
        books.add(Voucher(when, "Credit Note", number, entries))
    for when, number, amount, _ in cp.LOANS:
        entries = [(cp.LOAN, amount, 0), (d.BANK, 0, amount)]
        books.add(Voucher(when, "Payment", number, entries, "NEFT-Vireo Ventures Pvt Ltd"))
    for number, when, credit, amount, _ in cp.CAPITALISED:
        entries = [("Computer Equipment", amount, 0), (credit, 0, amount)]
        books.add(Voucher(when, "Journal", number, entries))


def _tax(books: Books) -> None:
    """Tax at TAX_RATE on each year's profit before tax as provisionally closed (the auditor's
    adjustment is not deductible), paid on 31 July of the next year."""
    owed = -d.OPENING["Provision for Income Tax"]
    for p in d.ANNUAL:
        start, end = d.PERIODS[p]
        pay = date(start.year, 7, 31)
        books.add(
            Voucher(
                pay,
                "Payment",
                f"TAX/{p}",
                [("Provision for Income Tax", owed, 0), (d.BANK, 0, owed)],
                "CBDT-SELF ASSESSMENT TAX",
            )
        )
        pl = profit_and_loss(books, p, audited=False)
        owed = int(round(sum(pl.values()) * -d.TAX_RATE, -3))
        books.add(
            Voucher(
                end,
                "Journal",
                f"TAXPROV/{p}",
                [("Income Tax Expense", owed, 0), ("Provision for Income Tax", 0, owed)],
            )
        )
    books.add(
        Voucher(
            date(2025, 7, 31),
            "Payment",
            "TAX/FY26",
            [("Provision for Income Tax", owed, 0), (d.BANK, 0, owed)],
            "CBDT-SELF ASSESSMENT TAX",
        )
    )


def profit_and_loss(books: Books, period: str, audited: bool = True) -> dict[str, int]:
    """P&L ledger -> debit - credit in the period."""
    start, end = d.PERIODS[period]
    out: dict[str, int] = {}
    for v in books.vouchers:
        if start <= v.when <= end and (audited or not v.audit):
            for ledger, debit, credit in v.entries:
                if d.LEDGERS[ledger][0] in d.PL_GROUPS:
                    out[ledger] = out.get(ledger, 0) + debit - credit
    return out


def build() -> Books:
    books = Books()
    _sales(books)
    _purchases(books)
    _monthly_costs(books)
    _loan(books)
    _year_end(books)
    _counterparty(books)
    _gst(books)
    _forensic(books)
    _tax(books)
    books.vouchers.sort(key=lambda v: (v.when, v.kind != "Sales", v.number))
    return books


def balances(books: Books, period: str, audited: bool = True) -> dict[str, int]:
    """The trial balance at the period end, ledger -> debit - credit, as Tally shows it: balance
    sheet ledgers closing, profit and loss ledgers for the period only, and the Profit & Loss A/c
    with every earlier period's profit closed into it."""
    start, end = d.PERIODS[period]
    out = {ledger: 0 for ledger in d.LEDGERS}
    for ledger, value in d.OPENING.items():
        out[ledger] += value
    out[d.RETAINED] -= sum(d.OPENING.values())
    for v in books.vouchers:
        if v.when > end or (v.audit and not audited):
            continue
        for ledger, debit, credit in v.entries:
            if d.LEDGERS[ledger][0] in d.PL_GROUPS and v.when < start:
                ledger = d.RETAINED
            out[ledger] += debit - credit
    if sum(out.values()):
        raise ValueError(f"the {period} trial balance does not balance")
    return out


def bank_balance(books: Books, upto: date) -> int:
    """The bank's balance at the end of `upto`: the bank ledger's vouchers on their cleared date."""
    total = d.OPENING[d.BANK]
    for v in books.vouchers:
        if (v.cleared or v.when) <= upto:
            total += sum(e[1] - e[2] for e in v.entries if e[0] == d.BANK)
    return total
