"""A synthetic billing-system registers workbook (made-up names), written with openpyxl.

`write_registers`: a "Sales Register" and a "Purchase Register" sheet with the headers of
`templates/profiles/billing_registers.toml`; dates as Excel dates and as text
(dd-mm-yyyy); a Total row under each. Options plant what the loader must catch:
`duplicate` repeats the sales invoice INV-1002 and the purchase invoice PINV-71 of the same
vendor, and `same_number_other_vendor` gives another vendor the number PINV-71 (not a
duplicate).
"""

from datetime import date
from pathlib import Path

from openpyxl import Workbook

from tests.tally_fixtures import CUSTOMERS, VENDORS

HANDLERS = ("Meera Rao", "Dev Anand")
SALES_HEADERS = [
    "Bill Id", "Purchase Id", "Customer Id", "Customer Name", "Location", "Invoice No",
    "Contract Start Date", "Contract End Date", "Invoice Date", "Order Type", "Product",
    "Service Start Date", "Service End Date", "Quantity", "Rate", "Amount", "Currency",
    "Currency Rate", "Amount in Rs", "Service Type", "Handler", "Submitted By",
]  # fmt: skip
PURCHASE_HEADERS = [
    "Purchase Id", "Vendor Id", "Vendor Name", "Location", "Invoice No", "Invoice Date",
    "Product", "Quantity", "Rate", "Amount", "Currency", "Currency Rate", "Amount in Rs",
    "Handler", "Submitted By",
]  # fmt: skip


def sales_line(n: int, customer: str, amount_inr: float, **over: object) -> list[object]:
    usd = over.pop("usd", False)
    fx = 80 if usd else 1
    cells: dict[str, object] = {
        "Bill Id": f"B{n}", "Purchase Id": "P1" if n == 1 else None, "Customer Id": 100 + n,
        "Customer Name": customer, "Location": "Pune", "Invoice No": f"INV-{1000 + n}",
        "Contract Start Date": date(2024, 4, 1), "Contract End Date": date(2025, 3, 31),
        "Invoice Date": f"{10 + n:02d}-05-2024", "Order Type": "New",
        "Product": f"Licence for {customer}" if n == 1 else "Support",
        "Service Start Date": date(2024, 5, 1), "Service End Date": date(2025, 4, 30),
        "Quantity": 2, "Rate": amount_inr / 2 / fx,
        "Amount": amount_inr / fx,
        "Currency": "USD" if usd else "INR", "Currency Rate": 80 if usd else 1,
        "Amount in Rs": amount_inr, "Service Type": "Subscription", "Handler": HANDLERS[0],
        "Submitted By": HANDLERS[1],
    }  # fmt: skip
    cells.update(over)
    return [cells[h] for h in SALES_HEADERS]


def purchase_line(
    n: int, vendor: str, amount_inr: float, invoice_no: str | None = None
) -> list[object]:
    cells: dict[str, object] = {
        "Purchase Id": f"P{n}", "Vendor Id": 200 + n, "Vendor Name": vendor, "Location": "Pune",
        "Invoice No": invoice_no or f"PINV-{70 + n}", "Invoice Date": date(2024, 6, n),
        "Product": "Hardware", "Quantity": 1, "Rate": amount_inr, "Amount": amount_inr,
        "Currency": "INR", "Currency Rate": 1, "Amount in Rs": amount_inr,
        "Handler": HANDLERS[0], "Submitted By": HANDLERS[1],
    }  # fmt: skip
    return [cells[h] for h in PURCHASE_HEADERS]


def write_registers(
    path: Path,
    *,
    duplicate: bool = False,
    same_number_other_vendor: bool = False,
    sales_extra: list[list[object]] | None = None,
    sheets: tuple[str, ...] = ("sales", "purchase"),
) -> Path:
    """Sales rows 2-4 (INV-1001..1003; a USD line is row 4), purchase rows 2-3 (PINV-71, PINV-72);
    row 1 is the header."""
    book = Workbook()
    first = book.active
    assert first is not None
    sales = [
        sales_line(1, CUSTOMERS[0], 120000),
        sales_line(2, CUSTOMERS[1], 80000),
        sales_line(3, CUSTOMERS[0], 160000, usd=True),
    ]
    purchases = [purchase_line(1, VENDORS[0], 90000), purchase_line(2, VENDORS[1], 40000)]
    if duplicate:
        sales.append(sales_line(2, CUSTOMERS[1], 80000))  # INV-1002 again
        purchases.append(purchase_line(3, VENDORS[0], 90000, invoice_no="PINV-71"))
    if same_number_other_vendor:
        purchases.append(purchase_line(4, VENDORS[1], 5000, invoice_no="PINV-71"))
    sales += sales_extra or []
    if "sales" in sheets:
        first.title = "Sales Register"
        first.append(SALES_HEADERS)
        for line in sales:
            first.append(line)
        first.append(["Total"] + [None] * (len(SALES_HEADERS) - 1))
    if "purchase" in sheets:
        sheet = book.create_sheet("Purchase Register") if "sales" in sheets else first
        sheet.title = "Purchase Register"
        sheet.append(PURCHASE_HEADERS)
        for line in purchases:
            sheet.append(line)
        sheet.append(["Total"] + [None] * (len(PURCHASE_HEADERS) - 1))
    book.save(path)
    return path
