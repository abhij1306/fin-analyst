import pytest

from fin.map.normalize import normalize_label


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("(a) Equity share capital", "equity share capital"),
        ("V. Profit before tax (III - IV)", "profit before tax"),
        ("III. Total income (I + II)", "total income"),
        ("Net increase/(decrease) in cash and cash equivalents (A+B+C)", "net increase decrease in cash and cash equivalents"),  # noqa: E501
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
    ],
)
def test_normalize_label(label: str, expected: str) -> None:
    assert normalize_label(label) == expected
