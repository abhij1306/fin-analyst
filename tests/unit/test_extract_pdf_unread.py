"""A primary statement printed in a PDF but not read is reported with its page and reason, never
passed over in silence."""

from pathlib import Path

import pytest

from fin.audit.errors import NoTablesFound
from fin.extract.pdf import extract_pdf
from tests.synthetic_pdf import Page, write_pdf

UNIT = (50, 65, "(Rs. in Lakhs)")
HEADER = [(380, 85, "31 March 2025"), (470, 85, "31 March 2024")]
ROWS = [
    ("Revenue from operations", "900.00", "800.00"),
    ("Other income", "10.00", "8.00"),
    ("Total income", "910.00", "808.00"),
    ("Total expenses", "700.00", "650.00"),
    ("Profit before tax", "210.00", "158.00"),
    ("Profit for the year", "160.00", "120.00"),
]


def statement(title: str, rows: list[tuple[str, str, str]] = ROWS) -> Page:
    page: Page = [(50, 50, title), UNIT, *HEADER]
    for n, (label, fy25, fy24) in enumerate(rows):
        y = 105 + 15 * n
        page += [(50, y, label), (390, y, fy25), (480, y, fy24)]
    return page


def unread(path: Path) -> list[dict[str, object]]:
    return [s.as_dict() for s in extract_pdf(path).skipped]


def test_a_statement_under_a_title_fin_does_not_recognise_is_reported(tmp_path: Path) -> None:
    path = write_pdf(
        tmp_path / "titles.pdf",
        statement("Statement of Profit and Loss for the year ended 31 March 2025"),
        statement("Abridged Balance Sheet as at 31 March 2025"),
        statement("Summary of Financial Results for the year ended 31 March 2025"),
    )

    # Pages 2 and 3 are not taken as page 1's profit and loss carried on: they name a balance
    # sheet and results of their own.
    assert {(v.page, v.statement) for v in extract_pdf(path).values} == {(1, "profit_and_loss")}
    assert unread(path) == [
        {
            "page": 2,
            "statement": "balance_sheet",
            "code": "E201",
            "reason": "page 2: 'Abridged Balance Sheet as at 31 March 2025' names a statement in "
            "a title fin does not recognise; the page was not read",
        },
        {
            "page": 3,
            "statement": "profit_and_loss",
            "code": "E201",
            "reason": "page 3: 'Summary of Financial Results for the year ended 31 March 2025' "
            "names a statement in a title fin does not recognise; the page was not read",
        },
    ]


def test_titles_under_a_letterhead_or_garbled_are_reported(tmp_path: Path) -> None:
    letterhead: Page = [(50, 20 + 8 * n, f"Company letterhead line {n}") for n in range(8)]
    under_letterhead = [
        (x, y + 60 if y >= 50 else y, text)
        for x, y, text in statement("STATEMENT OF STANDALONE UNAUDITED FINANCIAL RESULTS FOR")
    ]
    garbled = statement("Consolidated tah rid Sebt for the Halt ybar")
    garbled.insert(1, (50, 98, "A CASH FLOW FROM OPERATING ACTIVITIES"))
    path = write_pdf(
        tmp_path / "letterhead.pdf",
        statement("Balance Sheet as at 31 March 2025"),
        letterhead + under_letterhead,
        statement("Unaudited Consolidated Statement of Assets & Liabilities as on 30-Sep-25"),
        garbled,
    )

    assert [(p["page"], p["statement"], p["code"]) for p in unread(path)] == [
        (2, "profit_and_loss", "E201"),
        (3, "balance_sheet", "E201"),
        (4, "cash_flow", "E201"),
    ]


def test_a_page_that_only_mentions_a_statement_is_not_reported(tmp_path: Path) -> None:
    path = write_pdf(
        tmp_path / "narrative.pdf",
        statement("Statement of Profit and Loss for the year ended 31 March 2025"),
        # Fewer than five figure lines.
        statement("Highlights of the Balance Sheet", ROWS[:2]),
        # Note pages: lines that are sentences or lead-ins, not titles.
        statement("statement of profit and loss as they accrue"),
        statement("Amounts recognised in the statement of profit and loss are as follows:"),
        statement("Market risk is the risk that the future cash flows of an instrument change"),
        statement("Financial assets and liabilities measured at fair value"),
    )

    assert unread(path) == []


def test_a_titled_statement_with_no_row_of_figures_is_reported(tmp_path: Path) -> None:
    labels_only = [(label, "", "") for label, _, _ in ROWS]
    path = write_pdf(
        tmp_path / "labels.pdf",
        statement("Statement of Profit and Loss for the year ended 31 March 2025"),
        statement("Balance Sheet as at 31 March 2025", labels_only),
    )

    assert unread(path) == [
        {
            "page": 2,
            "statement": "balance_sheet",
            "code": "E201",
            "reason": "page 2: titled as a statement, but no line has a figure under a period "
            "column",
        }
    ]


def test_a_row_with_no_comparative_loads_the_figure_printed(tmp_path: Path) -> None:
    page: Page = [
        (50, 50, "Balance Sheet as at 31 March 2025"),
        UNIT,
        *HEADER,
        (50, 105, "Current assets"),
        (50, 120, "Right-of-use assets"),
        (390, 120, "25.00"),  # new this year: nothing printed for 31 March 2024
        (50, 135, "Inventories"),
        (390, 135, "178.54"),
        (480, 135, "150.94"),
        (50, 150, "Annual Report 2024-25"),
        (500, 150, "12"),  # a page number, not a figure
    ]
    path = write_pdf(tmp_path / "bs.pdf", page)

    result = extract_pdf(path)

    assert [(v.label, v.period, v.raw, v.section) for v in result.values] == [
        ("Right-of-use assets", "FY25", "25.00", "Current assets"),
        ("Inventories", "FY25", "178.54", "Current assets"),
        ("Inventories", "FY24", "150.94", "Current assets"),
    ]
    assert result.skipped == []


def test_an_integer_row_with_no_comparative_loads_inside_the_table_only(tmp_path: Path) -> None:
    page: Page = [
        (50, 50, "Statement of Profit and Loss for the year ended 31 March 2025"),
        (50, 65, "(Rs. in Crores)"),
        *HEADER,
        (50, 105, "Income"),
        (50, 120, "Other income"),
        (390, 120, "45"),  # nothing printed for the year before
        (50, 135, "Revenue from operations"),
        (390, 135, "900"),
        (480, 135, "800"),
        (50, 165, "Annual Report 2024-25"),
        (480, 165, "12"),  # a page number below the table, under the last column
    ]
    path = write_pdf(tmp_path / "pl.pdf", page)

    result = extract_pdf(path)

    assert [(v.label, v.period, v.raw, v.section) for v in result.values] == [
        ("Other income", "FY25", "45", "Income"),
        ("Revenue from operations", "FY25", "900", "Income"),
        ("Revenue from operations", "FY24", "800", "Income"),
    ]
    assert result.skipped == []


def test_a_figure_that_cannot_be_placed_under_a_column_makes_the_page_unread(
    tmp_path: Path,
) -> None:
    # Page 2's first figure has no label of its own: fin cannot tell what it is, nor take it as
    # a heading over the rows below.
    rows = [("", "5.00", ""), *ROWS]
    path = write_pdf(
        tmp_path / "bs.pdf",
        statement("Statement of Profit and Loss for the year ended 31 March 2025"),
        statement("Balance Sheet as at 31 March 2025", rows),
    )

    assert unread(path) == [
        {
            "page": 2,
            "statement": "balance_sheet",
            "code": "E204",
            "reason": "page 2: the figures of '5.00' cannot be placed under the period columns",
        }
    ]


def test_when_no_statement_can_be_read_the_error_lists_every_page(tmp_path: Path) -> None:
    path = write_pdf(
        tmp_path / "none.pdf",
        statement("Abridged Balance Sheet as at 31 March 2025"),
        statement("Summary of Financial Results for the year ended 31 March 2025"),
    )

    with pytest.raises(NoTablesFound) as raised:
        extract_pdf(path)

    assert [(p["page"], p["statement"], p["code"]) for p in raised.value.pages] == [
        (1, "balance_sheet", "E201"),
        (2, "profit_and_loss", "E201"),
    ]


def test_a_page_whose_figures_look_misread_is_reported_not_loaded(tmp_path: Path) -> None:
    # Page 2: a figure sits in a label, so the figures do not line up with their labels (a skewed
    # scan). Page 3: "(31" has no closing bracket, so its sign is unknown.
    skewed = [*ROWS[:2], ("Trade receivables 1,234.00", "910.00", "808.00")]
    unpaired = [*ROWS[:2], ("Net cash from operating activities", "(31", "120.00")]
    path = write_pdf(
        tmp_path / "misread.pdf",
        statement("Statement of Profit and Loss for the year ended 31 March 2025"),
        statement("Balance Sheet as at 31 March 2025", skewed),
        statement("Statement of Cash Flows for the year ended 31 March 2025", unpaired),
    )

    assert {(v.page, v.statement) for v in extract_pdf(path).values} == {(1, "profit_and_loss")}
    assert unread(path) == [
        {
            "page": 2,
            "statement": "balance_sheet",
            "code": "E204",
            "reason": "page 2: the label 'Trade receivables 1,234.00' holds a figure; the "
            "figures do not line up with their labels",
        },
        {
            "page": 3,
            "statement": "cash_flow",
            "code": "E204",
            "reason": "page 3: figure '(31' has an unpaired bracket",
        },
    ]


def test_a_page_carrying_on_a_page_not_read_is_reported_too(tmp_path: Path) -> None:
    unpaired = [*ROWS[:2], ("Trade receivables", "(31", "120.00")]
    # Page 3 has no title: five lines of figures carrying on page 2's balance sheet.
    rest: Page = [
        item
        for n, (label, fy25, fy24) in enumerate(ROWS[1:])
        for item in ((50, 50 + 15 * n, label), (390, 50 + 15 * n, fy25), (480, 50 + 15 * n, fy24))
    ]
    path = write_pdf(
        tmp_path / "carried.pdf",
        statement("Statement of Profit and Loss for the year ended 31 March 2025"),
        statement("Balance Sheet as at 31 March 2025", unpaired),
        rest,
    )

    assert [(s["page"], s["statement"], s["code"], s["reason"]) for s in unread(path)] == [
        (2, "balance_sheet", "E204", "page 2: figure '(31' has an unpaired bracket"),
        (3, "balance_sheet", "E204", "page 3: carries on page 2, which was not read"),
    ]
