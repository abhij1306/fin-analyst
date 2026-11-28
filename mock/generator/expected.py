"""mock/EXPECTED.md, the answer key, and the texts under mock/extra/: every figure is computed here
from the books, never typed."""

from collections.abc import Iterable
from datetime import date

from mock.generator import books as b
from mock.generator import data as d
from mock.generator import generate as g
from mock.generator import sources as s
from mock.generator import statements as st

HEADLINE = [
    ("Revenue from operations", "revenue"),
    ("Other income", "other_income"),
    ("EBITDA (PBT + finance costs + depreciation)", "ebitda"),
    ("Profit before tax", "pbt"),
    ("Profit for the year", "pat"),
    ("Trade receivables", "trade_receivables"),
    ("Cash and cash equivalents", "cash"),
    ("Borrowings", "lt_borrowings"),
    ("Total assets", "total_assets"),
]


def rs(n: float) -> str:
    return f"{n:,.0f}" if n >= 0 else f"({-n:,.0f})"


def _table(header: list[str], rows: Iterable[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return out + ["| " + " | ".join(row) + " |" for row in rows]


def _figures(books: b.Books, period: str, audited: bool = True) -> dict[str, int]:
    f = st.printed(books, period, audited)
    return {**f, "ebitda": f["pbt"] + f["finance_cost"] + f["depreciation"]}


def _loads(doc: g.Document, books: b.Books) -> str:
    """What fin should store for the document, counted off the grid it was written from."""
    if doc.copy_of:
        return f"nothing new: the same bytes as `{doc.copy_of}` (one plan row, a duplicate)"
    if doc.kind == "statements":
        lines = sum(1 for layout in st.LAYOUTS.values() for row in layout if row[1])
        years = 2 if doc.fmt == "pdf" or "fy23" not in doc.path else 1
        return f"3 statements, {lines} lines per year, {years} year(s), unit lakhs"
    sheets = doc.build(books)
    if doc.kind == "trial_balance":
        tb = sheets[0]
        return f"{len(tb.indent)} ledgers, grand total {rs(tb.rows[-1][1])}"
    if doc.kind == "ledger_vouchers":
        rows = [x for x in sheets[0].rows if len(x) > 2 and x[2]][1:]
        return f"{len(rows)} entries (opening and closing rows not loaded)"
    if doc.kind == "bank":
        rows = sheets[0].rows
        return f"{sum(1 for x in rows if isinstance(x[0], int))} transactions, account masked"
    if doc.kind == "registers":
        return ", ".join(f"{x.title}: {len(x.rows) - 2} invoices" for x in sheets)
    if doc.kind == "reference_databook":
        return f"reference figures: {len(st.PROFIT_AND_LOSS) - 1} P&L lines x {len(d.ANNUAL)} years"
    if doc.fmt == "pdf":
        return "nothing: Claude reads it when asked (`fin ingest --reading`, contract terms)"
    return f"{len(sheets[0].rows) - (4 if 'gst' in doc.kind else 3) - (doc.kind != 'gst')} rows"


def _diff(a: dict[str, int], z: dict[str, int]) -> list[tuple[str, int, int]]:
    return [
        (k, a.get(k, 0), z.get(k, 0)) for k in sorted(set(a) | set(z)) if a.get(k, 0) != z.get(k, 0)
    ]


def _documents(books: b.Books) -> list[str]:
    out = [
        "## Documents and what fin should store",
        "",
        "Paths are under `mock/dataroom/` (the same tree is in "
        "`mock/zipped/corvane_dataroom.zip`). Kind is what the "
        "data-room plan should say (`fin dataroom scan`).",
        "",
    ]
    rows = [
        [f"`{doc.path}`", doc.source, doc.kind, _loads(doc, books), doc.note] for doc in g.DOCUMENTS
    ]
    return out + _table(["File", "Source", "Kind", "fin stores", "Version / note"], rows) + [""]


def _versions(books: b.Books) -> list[str]:
    prov, aud = _figures(books, "FY25", False), _figures(books, "FY25")
    v1, v2 = b.balances(books, "FY25", False), b.balances(books, "FY25")
    a = d.AUDIT_ADJUSTMENT
    out = [
        "## Versions, sources and storage",
        "",
        "- FY25 statements: the provisional workbook (company, 30-Apr-2025) is version 1; "
        "the audited "
        f"annual report PDF (auditor) is version 2 and **current**. They differ by the auditor's "
        f"adjustment {a['number']} of {rs(a['amount'])} ({a['debit']} / {a['credit']}):",
    ]
    out += [f"  - {k}: provisional {rs(x)}, audited {rs(y)}" for k, x, y in _diff(prov, aud)]
    held = len([k for k, _, _ in _diff(prov, aud) if k != "ebitda"])
    out += [
        f"  Ingested after the provisional one, the audited PDF is held with {held} open findings "
        "(E404 cross_document, each naming the PDF page and the workbook cell). `fin findings "
        "accept` on them (the user's call) loads it, and it becomes current: the latest document "
        "for the period. FY24 in both FY25 documents agrees with the FY24 audited workbook.",
        "- FY25 trial balance: v1 (15-Apr-2025) then v2 (20-Jun-2025, **current**, supersedes v1). "
        "They differ in:",
    ]
    out += [f"  - {k}: v1 {rs(x)}, v2 {rs(y)}" for k, x, y in _diff(v1, v2)]
    dup = next(doc for doc in g.DOCUMENTS if doc.copy_of)
    out += [
        f"- `{dup.path}` is byte-identical to `{dup.copy_of}`: one plan row with a duplicate; "
        "ingesting it again is E105.",
        "- The databook is the adviser's: stored as reference figures, never as the company's.",
        "- Every other file is the only version of its kind and period, and is current.",
        "- Storage checks: `fin document delete` removes every version of a document; ingesting "
        "it again stores it again. `--force` on the same file makes a new version that "
        "supersedes the old. Every figure traces to file and sheet!cell (workbooks) or page "
        "(the PDF): e.g. FY25 revenue to the annual report page 3, FY24 revenue to "
        "`corvane_fy24_audited_statements.xlsx` Profit and Loss!B6.",
        "",
    ]
    return out


def _headline(books: b.Books) -> list[str]:
    periods = [*d.ANNUAL, d.STUB]
    figs = [_figures(books, p) for p in periods]
    prov = _figures(books, "FY25", False)
    rows = [[label, *(rs(f[k]) for f in figs), rs(prov[k])] for label, k in HEADLINE]
    return [
        "## Headline figures (rupees, current version)",
        "",
        *_table(["Line", *periods, "FY25 provisional"], rows),
        "",
        "FY24 is as printed (after the statement reclassification below). The stub is the "
        "management trial balance; no statements are filed for it.",
        "",
    ]


def _year_tax(books: b.Books, ledger: str, period: str, side: int) -> int:
    start, end = d.PERIODS[period]
    return sum(
        e[side]
        for v in books.vouchers
        if start <= v.when <= end
        for e in v.entries
        if e[0] == ledger
    )


def _items(books: b.Books) -> list[str]:
    ahead, cn, a = d.BILLED_AHEAD, d.CREDIT_NOTE, d.AUDIT_ADJUSTMENT
    ahead_tax = ahead["amount"] * d.GST_RATE // 100
    books_fy24, printed_fy24 = st.books_view(books, "FY24"), st.printed(books, "FY24")
    reclass = {k: books_fy24[k] - printed_fy24[k] for k in ("other_expenses", "finance_cost")}
    fy25_end = d.PERIODS["FY25"][1]
    tb_bank, bank = b.balances(books, "FY25")[d.BANK], b.bank_balance(books, fy25_end)
    cheque = next(v for v in books.vouchers if v.cleared and v.when == d.UNCLEARED_CHEQUE["booked"])
    ctc = sum(e.ctc for e in d.EMPLOYEES)
    employee = st.books_view(books, "FY25")["employee_expense"]
    consult = _year_tax(books, "Consultancy Fees", "FY25", 1)
    orrery = sum(
        i.amount_inr for i in books.invoices if i.party == "C3" and b.period_of(i.when) == "FY25"
    )
    orders = {o[0]: o for o in s.orders(books)}
    usd = orders["C4"][3] * d.USD_RATE_AS_AT
    t = d.QUERY_THRESHOLD
    rows = [
        [
            "Billed ahead",
            f"Sales register, {ahead['number']} ({ahead['date']:%d-%b-%Y}), service "
            f"{ahead['service'][0]:%d-%b-%Y} to {ahead['service'][1]:%d-%b-%Y}",
            rs(ahead["amount"]),
            "sales_cutoff FY25 (invoice, left_only); service_recast; gstr1_vs_sales_register "
            f"2025-03 taxable_value ({rs(-ahead['amount'])}); gstr3b_vs_books 2025-03 output tax "
            f"({rs(-2 * ahead_tax)}); the contract PDF is its evidence",
        ],
        [
            "Credit note",
            f"Ledger Vouchers FY25 {cn['number']} ({cn['date']:%d-%b-%Y}), not in the "
            "register; refunded through the bank",
            rs(cn["amount"]),
            "sales_register_vs_gl FY25 (invoice, right_only; month 2024-12)",
        ],
        [
            "Auditor's adjustment",
            f"{a['number']} {a['date']:%d-%b-%Y}: in TB v2 and the audited "
            "PDF, not in TB v1, the provisional statements or the databook",
            rs(a["amount"]),
            "versions (above); the databook against the statements (reference difference, FY25)",
        ],
        [
            "Statement reclassification",
            "FY24 statements: lease interest shown in finance costs, booked in Rent",
            rs(d.STATEMENT_RECLASS["amount"]),
            f"tb_vs_statements FY24: other_expenses {rs(reclass['other_expenses'])}, "
            f"finance_cost {rs(reclass['finance_cost'])} (books - statements)",
        ],
        [
            "Uncleared cheque",
            f"Payment {cheque.number}, booked {cheque.when:%d-%b-%Y}, cleared "
            f"{cheque.cleared:%d-%b-%Y} (cheque {d.UNCLEARED_CHEQUE['cheque']})",
            rs(abs(tb_bank - bank)),
            f"bank_vs_tb FY25 (balance): TB {rs(tb_bank)}, bank {rs(bank)}",
        ],
        *(
            [
                f"One-off: {what}",
                f"Ledger Vouchers {b.period_of(when)}, {ledger}, {when:%d-%b-%Y}",
                rs(amount),
                "walk sheet row: `fin adjust import` (normalisation, EBITDA effect "
                f"{rs(amount)}); bridge {b.period_of(when)}",
            ]
            for when, ledger, amount, what in d.ONE_OFFS
        ),
        [
            "Mid-year joiner",
            "Employee master: CA009 joined 01-Jan-2025; master CTC vs books employee expense FY25",
            rs(ctc - employee),
            f"payroll_vs_gl FY25 (payroll): CTC {rs(ctc)}, books {rs(employee)}",
        ],
        [
            "Related party",
            f"Annual report page 5, Note 28: {d.ADVISER_VENDOR} consultancy fees "
            f"disclosed {rs(d.RPT_DISCLOSED[d.ADVISER_VENDOR][2])}",
            rs(consult - d.RPT_DISCLOSED[d.ADVISER_VENDOR][2]),
            f"rpt_vs_books FY25 (books {rs(consult)}); {d.CUSTOMERS['C3'].ledger} sales "
            f"{rs(orrery)} tie",
        ],
        [
            "Order with no billing",
            f"Orderbook {d.NEW_ORDER['number']} ({d.CUSTOMERS['C5'].ledger}), "
            f"starts {d.NEW_ORDER['start']:%d-%b-%Y}",
            rs(d.NEW_ORDER["value"]),
            "orderbook_vs_register FY25 (order, left_only)",
        ],
        [
            "Billing with no order",
            f"{d.CUSTOMERS['C3'].ledger}: FY25 billing, no order on the orderbook",
            rs(orrery),
            "orderbook_vs_register FY25 (order, right_only)",
        ],
        [
            "USD order",
            f"Orderbook {orders['C4'][1]}: USD {orders['C4'][3]:,.0f} at the stated "
            f"rate {d.USD_RATE_AS_AT} as at {d.ORDERS_AS_AT:%d-%b-%Y}",
            rs(usd),
            "`fin engagement rate`; with no rate the order has no rupee amount",
        ],
        *(
            [
                "Contingent liability",
                f"Annual report page 5, Note 29: {what}",
                rs(amount),
                "disclosures reading (F5), net debt and FDD findings",
            ]
            for what, amount in d.CONTINGENT
        ),
    ]
    return [
        "## Deliberate diligence items",
        "",
        f"Query threshold {rs(t)}: a line at or above it needs a query.",
        "",
        *_table(["Item", "Where", "Amount", "Surfaces in"], rows),
        "",
    ]


def _quiet(books: b.Books) -> list[str]:
    return [
        "## What should tie, and what fin shows that is not planted",
        "",
        "- gl_vs_tb FY24, FY25 and the stub: no differences. FY23 balance sheet ledgers are "
        "not_reconciled (no prior trial balance in the set).",
        "- purchase_register_vs_gl, gstr3b_vs_gstr1, rcm and tb_vs_statements FY23 and FY25 "
        "(against "
        "the current version): no differences. bank_vs_tb FY24 and the stub tie.",
        "- purchase_cutoff: coverage lines only (the purchase register has no service dates).",
        "- bank_vs_invoices: every domestic receipt includes GST and the invoice side is "
        "Amount in Rs "
        f"before GST, so each matched group is a difference of {d.GST_RATE * 2}% of the invoice "
        "(fin's design; not a planted item). USD receipts are fx lines.",
        "- Gates: G1 Ingestion is ready once every ledger is mapped (`fin map propose`, user "
        "confirms) and tb_vs_statements has run; G2 Reconciliation needs a query on each line at "
        "or above the threshold; G3 Recast and G4 Adjustments follow. Only the user approves.",
        "",
    ]


def _setup() -> list[str]:
    start, end = d.PERIODS[d.STUB]
    maps = sorted({(ledger, account) for ledger, (_, account) in d.LEDGERS.items()})
    out = [
        "## Engagement and mappings",
        "",
        f"Company code `{d.CODE}`; periods {', '.join(d.ANNUAL)} (fiscal year starts April); stub "
        f"`{d.STUB}` ({start:%d-%b-%Y} to {end:%d-%b-%Y}); "
        f"query threshold {rs(d.QUERY_THRESHOLD)}; "
        f"USD rate {d.USD_RATE_AS_AT} as at {d.ORDERS_AS_AT:%d-%b-%Y}. The walk sheet and the "
        "management reply are in `mock/extra/`.",
        "",
        "Order: ingest the statements first (they create the company), then `fin engagement open`, "
        "then the diligence files (`--kind`, or `fin dataroom scan corvane mock/zipped`). A "
        "ledger can be mapped once a loaded trial balance lists it; the trial balances list every "
        "ledger with an entry in the period, at a nil balance too.",
        "",
        "Intended ledger mappings:",
        "",
    ]
    out += _table(["Ledger", "Account"], ([ledger, account] for ledger, account in maps))
    out += ["", "Tax classes: " + ", ".join(f"{k} = {v}" for k, v in d.TAX_CLASSES.items()), ""]
    return out


def render(books: b.Books) -> str:
    head = [
        "# Mock document set: answer key",
        "",
        f"Generated by `mock/generator` from `mock/generator/data.py`; do not edit. {d.COMPANY} "
        "is fictitious. Amounts are rupees.",
        "",
    ]
    parts = [
        head,
        _documents(books),
        _versions(books),
        _headline(books),
        _items(books),
        _quiet(books),
        _setup(),
    ]
    return "\n".join(line for part in parts for line in part).rstrip() + "\n"


def extra_texts() -> dict[str, str]:
    """The management's reply to the query on the legal settlement (an evidence file)."""
    when, ledger, amount, what = d.ONE_OFFS[-1]
    reply = [
        f"Reply from the management of {d.COMPANY} (synthetic)",
        "",
        f"Query: the {ledger} ledger has an entry of Rs. {amount:,} on {when:%d-%b-%Y}.",
        "",
        f"Reply: this is the {what.lower()}. The dispute was about commissions for 2021-22; "
        "it is settled in full and there are no other claims. We do not expect a similar cost.",
        "",
        f"Dated {date(2025, 7, 10):%d-%b-%Y}.",
    ]
    return {"management_reply_legal_settlement.md": "\n".join(reply) + "\n"}
