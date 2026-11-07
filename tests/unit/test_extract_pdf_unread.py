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


def test_a_titled_statement_with_no_full_row_is_reported(tmp_path: Path) -> None:
    # Every row of page 2 has a figure under one column only: nothing can be read.
    half = [(label, fy25, "") for label, fy25, _ in ROWS]
    path = write_pdf(
        tmp_path / "half.pdf",
        statement("Statement of Profit and Loss for the year ended 31 March 2025"),
        statement("Balance Sheet as at 31 March 2025", half),
    )

    assert unread(path) == [
        {
            "page": 2,
            "statement": "balance_sheet",
            "code": "E201",
            "reason": "page 2: titled as a statement, but no line has a figure under every "
            "period column",
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
