"""A synthetic orderbook workbook (made-up customers), written with openpyxl in a tmp_path.

`write_orderbook`: a title line ("Orderbook as at 31-Mar-2025"), one header row (Customer |
Order No |
Order Date | Order Value | Currency | Start Date | End Date | Billed To Date), the orders of
`ORDERS` and a Total row. Customers 0 and 1 are those of `write_registers`; `MAPLE` is only on the
orderbook. `omit_second` leaves out the second register customer's orders; `bad_value` blanks one
order value (the planted E960).
"""

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from tests.tally_fixtures import CUSTOMERS

MAPLE = "Maple Retail Pvt Ltd"
HEADER = [
    "Customer", "Order No", "Order Date", "Order Value", "Currency", "Start Date", "End Date",
    "Billed To Date",
]  # fmt: skip
# (customer, order no, order date, value, currency, start, end, billed); rupees unless a currency
ORDERS = [
    (CUSTOMERS[0], "O-1", (2024, 4, 1), 1200000, "INR", (2024, 4, 1), (2026, 3, 31), 300000),
    (MAPLE, "O-2", (2025, 1, 15), 500000, "INR", (2025, 1, 1), (2026, 9, 30), 0),
    (CUSTOMERS[1], "O-3", (2024, 6, 1), 50000, "USD", (2024, 6, 1), (2025, 12, 31), 10000),
    (CUSTOMERS[1], "O-4", (2024, 7, 1), 200000, "INR", (2024, 7, 1), None, 50000),
]  # fmt: skip


def write_orderbook(
    path: Path, *, as_at: str = "31-Mar-2025", omit_second: bool = False, bad_value: bool = False
) -> Path:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Orderbook"
    sheet.append(["Acme Synthetic Private Limited"])
    sheet.append([f"Orderbook as at {as_at}"])
    sheet.append(HEADER)
    for customer, number, ordered, value, currency, start, end, billed in ORDERS:
        if omit_second and customer == CUSTOMERS[1]:
            continue
        sheet.append(
            [
                customer,
                number,
                datetime(*ordered),
                None if bad_value and number == "O-1" else value,
                currency,
                datetime(*start),
                datetime(*end) if end else None,
                billed,
            ]
        )
    sheet.append(["Total", None, None, 1, None, None, None, 1])
    book.save(path)
    return path
