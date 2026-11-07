import pytest

from fin.map.normalize import normalize_label


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("(a) Equity share capital", "equity share capital"),
        ("V. Profit before tax (III - IV)", "profit before tax"),
        ("III. Total income (I + II)", "total income"),
        (
            "Net increase/(decrease) in cash and cash equivalents (A+B+C)",
            "net increase decrease in cash and cash equivalents",
        ),
        ("(Increase)/decrease in inventories", "increase decrease in inventories"),
        ("Fixed assets (net block)", "fixed assets net block"),
        ("Total shareholders' funds", "total shareholders funds"),
        ("Shareholder’s funds", "shareholders funds"),
        ("Cash & bank balances", "cash and bank balances"),
        ("Trade receivables (Note 10)", "trade receivables"),
        ("Trade receivables Note No. 10", "trade receivables"),
        ("Revenue (refer note 13 and 14)", "revenue"),
        ("Revenue in ₹", "revenue in"),
        ("Rs. Revenue", "revenue"),
        ("  Sundry   DEBTORS  ", "sundry debtors"),
        ("(2) Non-current liabilities", "non current liabilities"),
        ("A. CASH FLOW FROM OPERATING ACTIVITIES", "cash flow from operating activities"),
        ("Civil works (iv)", "civil works"),
        ("Total expenses (IV)", "total expenses"),
        ("III Total income (I+II)", "total income"),
        ("IX Profit/(Loss) for the year (VII-VIII)", "profit loss for the year"),
        ("Various items", "various items"),
        ("5 Profit after tax (3-4)", "profit after tax"),
        ("7. Profit / (loss) for the period/ year (5-6)", "profit loss for the period year"),
        ("5. Profit / (Loss) before tax (3 ± 4 )", "profit loss before tax"),
        ("5 |PROFIT BEFORE TAX", "profit before tax"),
        ("c. |Employee benefit expenses", "employee benefit expenses"),
        ("Total equity and liabilities [A+B+C)", "total equity and liabilities"),
        ("(ia) Lease liabilities", "lease liabilities"),
        ("(j) Other non-current assets", "other non current assets"),
        ("(a) Basic (In Rs.)", "basic"),
        ("Diluted (in ₹)", "diluted"),
        (
            "Profit before tax from continuing operations (iv) = (i-ii+iii)",
            "profit before tax from continuing operations",
        ),
        ("(i) Borrowings 13 (a)", "borrowings"),
        ("(ii) Lease liability 10 B", "lease liability"),
        ("(b) Provisions 15", "provisions"),
        ("Ind AS 116 adjustments", "ind as 116 adjustments"),
        ("Total income 2025", "total income 2025"),
    ],
)
def test_normalize_label(label: str, expected: str) -> None:
    assert normalize_label(label) == expected
