"""Write statements.xls, the synthetic .xls fixture (SPEC-04). Run it once with
`uv run --no-project --with xlwt python tests/fixtures/xls/make_statements_xls.py`
(xlwt is not a dependency of fin: it only writes this file). A balance sheet whose period cells
are Excel dates under a "Standalone" heading merged over both value columns, and a profit and
loss sheet."""

from datetime import date
from pathlib import Path

import xlwt

book = xlwt.Workbook(encoding="utf-8")
dated = xlwt.easyxf(num_format_str="dd-mmm-yyyy")

balance = book.add_sheet("Balance Sheet")
balance.write(0, 0, "Demo Industries Limited")
balance.write(1, 0, "Balance Sheet")
balance.write(2, 0, "(₹ in Lakhs)")
balance.write_merge(3, 3, 2, 3, "Standalone")
balance.write(4, 0, "Particulars")
balance.write(4, 1, "Note")
balance.write(4, 2, date(2025, 3, 31), dated)
balance.write(4, 3, date(2024, 3, 31), dated)
balance.write(5, 0, "Current liabilities")
balance.write(6, 0, "Borrowings")
balance.write(6, 1, "5")
balance.write(6, 2, 1200.5)
balance.write(6, 3, "(3.25)")
balance.write(7, 0, "Trade payables")
balance.write(7, 2, 10)
balance.write(7, 3, "-")

profit = book.add_sheet("Profit and Loss")
profit.write(0, 0, "Statement of Profit and Loss")
profit.write(1, 0, "(₹ in Lakhs)")
profit.write(2, 0, "Particulars")
profit.write(2, 1, "Year ended 31.03.2025")
profit.write(2, 2, "Year ended 31.03.2024")
profit.write(3, 0, "Revenue from operations")
profit.write(3, 1, 500)
profit.write(3, 2, 450.75)

book.save(str(Path(__file__).with_name("statements.xls")))
