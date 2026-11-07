"""Write tiny text PDFs for extractor tests: Helvetica text placed at exact positions.

`statement` lays out a two-year Schedule III statement page; BALANCE_SHEET and PROFIT_AND_LOSS
are a complete pair that maps fully and passes every check."""

from pathlib import Path

# (x, y from the top, text), or (x, y, text, "F2") for Helvetica-Bold instead of Helvetica.
Page = list[tuple[float, float, str] | tuple[float, float, str, str]]


def write_pdf(path: Path, *pages: Page, width: float = 595, height: float = 842) -> Path:
    """Write a PDF with one page per `pages` entry, 10 pt text at (x, y-from-top)."""
    count = len(pages)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids ["
        + b" ".join(b"%d 0 R" % (4 + 2 * i) for i in range(count))
        + b"] /Count %d >>" % count,
        b"<< /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
        b" /F2 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >> >> >>",
    ]
    for i, lines in enumerate(pages):
        ops = []
        for x, y, text, *font in lines:
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            name = font[0] if font else "F1"
            ops.append(f"BT /{name} 10 Tf 1 0 0 1 {x} {height - y} Tm ({escaped}) Tj ET")
        stream = "\n".join(ops).encode("latin-1")
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R"
            b" /Resources 3 0 R >>" % (width, height, 5 + 2 * i)
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    body = b"%PDF-1.4\n"
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += b"%d 0 obj\n" % number + obj + b"\nendobj\n"
    xref = len(body)
    body += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    body += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    body += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    path.write_bytes(body)
    return path


WIDTH = 760  # wide enough for the longest Schedule III labels left of the figures
FY25_X, FY24_X = 570, 660
Line = tuple[str, str, str] | str  # (label, FY25, FY24), or a heading


def statement(title: str, *lines: Line) -> Page:
    page: Page = [
        (50, 50, title),
        (50, 65, "(Rs. in Lakhs)"),
        (FY25_X - 10, 85, "31 March 2025"),
        (FY24_X - 10, 85, "31 March 2024"),
    ]
    for n, line in enumerate(lines):
        y = 105 + 15 * n
        if isinstance(line, str):
            page.append((50, y, line))
        else:
            label, fy25, fy24 = line
            page += [(50, y, label), (FY25_X, y, fy25), (FY24_X, y, fy24)]
    return page


BALANCE_SHEET = statement(
    "BALANCE SHEET",
    "Non-current assets",
    ("Property, plant and equipment", "60.00", "50.00"),
    "Financial Assets",
    ("Loans", "5.00", "5.00"),
    ("Security deposits", "-", "-"),  # unmapped but nil: does not stop the subtotal check
    ("Total non-current assets", "65.00", "55.00"),
    "Current assets",
    ("Inventories", "10.00", "9.00"),
    "Financial assets",
    ("Cash and cash equivalents", "20.00", "16.00"),
    ("Loans", "5.00", "10.00"),
    ("Total current assets", "35.00", "35.00"),
    ("TOTAL ASSETS", "100.00", "90.00"),
    "EQUITY AND LIABILITIES",
    ("Equity share capital", "10.00", "10.00"),
    ("Other equity", "50.00", "40.00"),
    ("Total equity", "60.00", "50.00"),
    "Financial liabilities",  # no "Non-current liabilities" heading: the total equity line says so
    ("Borrowings", "15.00", "20.00"),
    ("Total non-current liabilities", "15.00", "20.00"),
    "Financial liabilities",
    ("Borrowings", "10.00", "8.00"),
    "Trade payables",
    ("Total outstanding dues of micro enterprises and small enterprises", "5.00", "4.00"),
    (
        "Total outstanding dues of creditors other than micro enterprises and small enterprises",
        "10.00",
        "8.00",
    ),
    ("Total current liabilities", "25.00", "20.00"),
    ("Total liabilities", "40.00", "40.00"),
    ("TOTAL EQUITY AND LIABILITIES", "100.00", "90.00"),
)
PROFIT_AND_LOSS = statement(
    "STATEMENT OF PROFIT AND LOSS",
    ("Revenue from operations", "100.00", "90.00"),
    ("Other income", "2.00", "1.00"),
    ("Total income", "102.00", "91.00"),
    ("Cost of materials consumed", "60.00", "50.00"),
    ("Employee benefits expense", "20.00", "18.00"),
    ("Total expenses", "80.00", "68.00"),
    ("Profit before exceptional items and tax", "22.00", "23.00"),
    ("Exceptional items", "3.00", "-"),  # a gain, added: 22 + 3 = 25
    ("Profit before tax", "25.00", "23.00"),
    ("Current tax", "5.00", "5.00"),
    ("Deferred tax", "1.00", "-"),
    ("Total tax expense", "6.00", "5.00"),
    ("Profit for the year", "19.00", "18.00"),
    "Items that will not be reclassified to profit or loss",
    ("Remeasurement of defined benefit plans", "(1.00)", "0.50"),
    ("Income tax relating to items that will not be reclassified to profit or loss", "0.25", "-"),
    ("Other comprehensive income for the year, net of tax", "(0.75)", "0.50"),
    ("Total comprehensive income for the year", "18.25", "18.50"),
    "Earnings per equity share",
    ("Nominal value per share", "10.00", "10.00"),  # unmapped, per share: not part of a subtotal
    ("Basic (in Rs.)", "12.34", "11.00"),
    ("Diluted (in Rs.)", "12.30", "11.00"),
)
