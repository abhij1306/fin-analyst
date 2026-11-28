"""The company's statements, the adviser's databook and walk sheet, as sheets; and the PDF pages.

Statements print rupees in lakhs to two decimals (every figure is a multiple of 1,000 rupees).
"""

from datetime import date

from mock.generator import books as b
from mock.generator import data as d
from mock.generator import statements as st
from mock.generator.sources import Sheet

UNIT = "(₹ in Lakhs)"


def lakhs(rupees: int) -> float:
    return round(rupees / 100000, 2)


def year_end(period: str) -> date:
    return d.PERIODS[period][1]


def _previous(period: str) -> str:
    return d.ANNUAL[d.ANNUAL.index(period) - 1]


def statement_sheets(books: b.Books, period: str, audited: bool, with_prior: bool) -> list[Sheet]:
    """The three statements of a year, with the year before beside it when `with_prior`."""
    years = [period, _previous(period)] if with_prior else [period]
    figures = [st.printed(books, y, audited or y != period) for y in years]
    out = []
    for title, layout in st.LAYOUTS.items():
        s = Sheet(title)
        end = year_end(period)
        when = "as at" if title == "Balance Sheet" else "for the year ended"
        s.add([d.COMPANY.upper()], bold=True)
        s.add([d.BANNER])
        name = "Statement of Profit and Loss" if title == "Profit and Loss" else title
        name = "Cash Flow Statement" if title == "Cash Flow" else name
        s.add([f"{name} {when} {end.day}-{end:%b-%Y}"], bold=True)
        s.add([None, *([None] * (len(years) - 1)), UNIT])
        prefix = "As at " if title == "Balance Sheet" else "Year ended "
        s.add(
            ["Particulars", *(f"{prefix}{year_end(y).day}-{year_end(y):%b-%Y}" for y in years)],
            bold=True,
        )
        for label, account, kind in layout:
            if account is None:
                s.add([label], bold=True)
            else:
                s.add([label, *(lakhs(f[account]) for f in figures)], bold=kind == "t")
        out.append(s)
    return out


DATABOOK_SHEET = "QoE P&L"


def databook(books: b.Books) -> list[Sheet]:
    """The adviser's databook: the P&L for the three years, FY25 taken from the provisional
    statements (the adviser worked before the audit closed)."""
    s = Sheet(DATABOOK_SHEET)
    s.add(["Saltmarsh Advisory LLP - Financial due diligence databook"], bold=True)
    s.add([f"{d.COMPANY}: Statement of Profit and Loss (reported)"], bold=True)
    s.add([None, None, None, UNIT])
    s.add(
        ["Particulars", *(f"Year ended {year_end(y).day}-{year_end(y):%b-%Y}" for y in d.ANNUAL)],
        bold=True,
    )
    figures = [st.printed(books, y, y != "FY25") for y in d.ANNUAL]
    for label, account, kind in st.PROFIT_AND_LOSS:
        if account is None:
            s.add([label], bold=True)
        else:
            s.add([label, *(lakhs(f[account]) for f in figures)], bold=kind == "t")
    return [s]


WALK_HEADER = [
    "Period",
    "Scope",
    "Category",
    "Description",
    "Revenue Effect",
    "Cost Effect",
    "Account",
    "Source Reference",
]


def walk_sheet() -> list[Sheet]:
    """The adviser's EBITDA walk: one normalisation per one-off cost in `data.ONE_OFFS`."""
    s = Sheet("Adjustments")
    s.add(WALK_HEADER)
    for when, ledger, amount, what in d.ONE_OFFS:
        s.add(
            [
                b.period_of(when),
                "standalone",
                "normalisation",
                f"One-off: {what}",
                0,
                -amount,
                "other_expenses",
                f"Ledger Vouchers {b.period_of(when)}, {ledger}, {when:%d-%b-%Y}",
            ]
        )
    return [s]


# --- PDF pages: (x, y from the top, text) ---
Page = list[tuple[float, float, str]]
FY_X, PRIOR_X = 570, 660


def _statement_page(
    title: str, layout: list, figures: list[dict[str, int]], ends: list[date]
) -> Page:
    page: Page = [(50, 50, title), (50, 65, "(Rs. in Lakhs)")]
    for x, end in zip((FY_X, PRIOR_X), ends, strict=False):
        page.append((x - 10, 85, f"{end.day} {end:%B %Y}"))
    for n, (label, account, _) in enumerate(layout):
        y = 105 + 15 * n
        page.append((50, y, label))
        if account:
            for x, f in zip((FY_X, PRIOR_X), figures, strict=False):
                page.append((x, y, f"{lakhs(f[account]):.2f}"))
    return page


def _text_page(lines: list[str]) -> Page:
    return [(50, 50 + 16 * n, line) for n, line in enumerate(lines)]


def annual_report(books: b.Books) -> list[Page]:
    """The audited FY25 annual report: the auditor's opinion, the three statements (FY25 and FY24)
    and the notes the diligence reads (related parties, contingent liabilities, commitments)."""
    years = ["FY25", "FY24"]
    figures = [st.printed(books, y) for y in years]
    ends = [year_end(y) for y in years]
    cover = _text_page(
        [
            d.COMPANY.upper(),
            d.BANNER,
            "Annual report for the year ended 31 March 2025",
            "Independent auditor's report (Kesar & Rao, Chartered Accountants, fictitious firm)",
            "In our opinion the financial statements give a true and fair view of the state of",
            "affairs of the Company as at 31 March 2025, and its profit and cash flows for the"
            " year.",
            "Amounts are in Rs. lakhs unless stated otherwise.",
        ]
    )
    pages = [cover]
    titles = {
        "Balance Sheet": "BALANCE SHEET",
        "Profit and Loss": "STATEMENT OF PROFIT AND LOSS",
        "Cash Flow": "CASH FLOW STATEMENT",
    }
    for key, layout in st.LAYOUTS.items():
        pages.append(_statement_page(titles[key], layout, figures, ends))
    sales = {
        code: sum(
            i.amount_inr
            for i in books.invoices
            if i.kind == "sales" and i.party == code and b.period_of(i.when) == "FY25"
        )
        for code in d.CUSTOMERS
    }
    notes = [
        "Notes to the financial statements (extract)",
        "",
        "Note 28 Related party transactions (Rs.)",
        "Party | Relationship | Nature of transaction | Year ended 31 March 2025",
    ]
    for party, (nature, _, disclosed) in d.RPT_DISCLOSED.items():
        code = next((k for k, c in d.CUSTOMERS.items() if c.ledger == party), None)
        amount = disclosed if disclosed is not None else sales[code or ""]
        relation = (
            "Entity with a common director"
            if code
            else "Firm in which a relative of a director is a partner"
        )
        notes.append(f"{party} | {relation} | {nature} | {amount:,}")
    notes += ["", "Note 29 Contingent liabilities not provided for (Rs.)"]
    notes += [f"{what} | {amount:,}" for what, amount in d.CONTINGENT]
    notes += ["", "Note 30 Commitments (Rs.)", f"{d.COMMITMENT[0]} | {d.COMMITMENT[1]:,}"]
    pages.append(_text_page(notes))
    return pages


def contract() -> list[Page]:
    """The Bellwether annual renewal: what Claude reads for the recast evidence (F1 terms)."""
    a = d.BILLED_AHEAD
    c = d.CUSTOMERS[a["customer"]]
    start, end = a["service"]
    return [
        _text_page(
            [
                "SUBSCRIPTION RENEWAL AGREEMENT",
                d.BANNER,
                "",
                f"Between {d.COMPANY} (the Provider) and {c.ledger} (the Customer)",
                f"Dated {a['date']:%d %B %Y}. Order reference SO-2025-009. Invoice {a['number']}.",
                "",
                f"1. Service: {a['product']} for all clinics of the Customer.",
                f"2. Term: {start:%d %B %Y} to {end:%d %B %Y} (twelve months).",
                f"3. Fee: Rs. {a['amount']:,} plus GST, invoiced in full in advance on signing.",
                "4. Lock-in: twelve months. Either party may end the agreement at the end of"
                " the term",
                "   on sixty days' written notice.",
                "5. Service levels: 99.5% monthly availability; a service credit of 5% of the"
                " monthly",
                "   fee for each full percentage point below it.",
                "6. Renewal: by a new order for a further twelve months at a fee agreed then.",
                "",
                "Signed for the Provider and for the Customer.",
            ]
        )
    ]
