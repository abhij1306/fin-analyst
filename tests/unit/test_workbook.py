"""An exported statement sheet is the databook layout: a caption with the unit, the headings, frozen
panes, values as SQL converted them with the display's number format, bold subtotals, and each
figure's source in a comment (`fin statements --export`, on tests/statements_fixtures.py)."""

from pathlib import Path

import pytest
from openpyxl import load_workbook

from fin.workbook import export_workbook
from tests.statements_fixtures import make_statements_root


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_statements_root(tmp_path_factory.mktemp("workbook"))


def test_a_sheet_is_the_databook_layout(root: Path) -> None:
    sheet = load_workbook(export_workbook(root, "acme"))["P&L standalone"]
    heads = [c.value for c in sheet[2]]
    fy24, fy25 = heads.index("FY24"), heads.index("FY25")
    lines = {row[0].value: row for row in sheet.iter_rows(min_row=3) if row[0].value}
    revenue = lines["Revenue from operations"]
    [profit] = [row for label, row in lines.items() if label.startswith("Profit for the year")]
    eps = lines["Basic earnings per share"]

    assert sheet["A1"].value == "acme: Profit and loss, standalone (INR Mn)"
    assert heads[0] == "Line"
    assert sheet.freeze_panes == "B3"
    assert (revenue[fy24].value, revenue[fy25].value) == (2, 2.5)
    assert revenue[fy25].number_format == '#,##0.0;(#,##0.0);"–"'
    assert eps[fy25].value == 8
    assert eps[fy25].number_format == '#,##0.00;(#,##0.00);"–"'
    assert revenue[fy25].comment.text.startswith("Source: fs_FY25.xlsx")
    assert profit[0].font.bold
    assert not revenue[0].font.bold
