"""SPEC-20's plants: the entries the counterparty tests flag, and the GSTR-2B file's differences.

Each is placed so one test flags it and a twin falls just outside a threshold (a near miss, logged,
not flagged). Amounts are multiples of 1,000; a purchase with GST is a multiple of 1,00,000 so its
9% tax is too. The vouchers are built by `books._counterparty`; what each test should flag is in
`expected.counterparty` (mock/EXPECTED.md).
"""

from datetime import date

TESSELLATE = "Tessellate Data Labs Pvt Ltd"
CORBEL = "Corbel Systems Pvt Ltd"
QUILLFEATHER = "Quillfeather Software Distribution Pvt Ltd"  # data.LICENCE_VENDOR
LOAN = "Loan to Vireo Ventures Pvt Ltd"
LEDGERS = {
    TESSELLATE: ("Sundry Creditors", "trade_payables"),
    CORBEL: ("Sundry Creditors", "trade_payables"),
    LOAN: ("Loans & Advances (Asset)", "loans_current"),
}
PAY_DAYS = 45

# Licence purchases with GST, in the purchase register: (vendor, date, number, amount, why).
PURCHASES = [
    (TESSELLATE, date(2024, 6, 12), "TDL/24/0611", 600000, "cp_new_vendor: new in FY25"),
    (TESSELLATE, date(2024, 9, 12), "TDL/24/0907", 600000, "cp_new_vendor: new in FY25"),
    (QUILLFEATHER, date(2024, 10, 18), "QSD/24/X01", 1000000, "cp_vendor_spike near miss"),
    (CORBEL, date(2024, 12, 12), "CSP/24-25/118", 400000, "cp_new_vendor near miss"),
    (QUILLFEATHER, date(2025, 1, 17), "QSD/25/X02", 1500000, "cp_vendor_spike: January"),
    (CORBEL, date(2025, 3, 12), "CSP/24-25/164", 400000, "not in GSTR-2B"),
]
# Licence purchases without GST in the FY26 stub, not yet paid: just under the approval limit
# (data.APPROVAL_LIMIT, 5,00,000; limit_band 2%) and a near miss within 2.4%.
UNDER_LIMIT = [
    (date(2025, 6, 12), "QSD/25/U01", 495000, "cp_under_limit: 1% under the limit"),
    (date(2025, 8, 14), "QSD/25/U02", 489000, "cp_under_limit near miss: 2.2% under"),
]
# Credit notes early in the next period on the last invoice of a year (customer C3): (date,
# number, amount, why). CN-2504-01 is 10 days into FY26; CN-2406-01 is 66 days into FY25.
CREDIT_NOTES = [
    (date(2025, 4, 10), "CN-2504-01", 300000, "cp_credit_note: FY25's 31-Mar invoice"),
    (date(2024, 6, 5), "CN-2406-01", 200000, "cp_credit_note near miss: day 66 of FY25"),
]
CREDIT_LEDGER = "Implementation Services"
# A loan paid out to a party with no sales or purchase (FY26 stub): (date, number, amount, why).
LOANS = [
    (date(2025, 7, 18), "LN/26/001", 1500000, "cp_loan_no_trade"),
    (date(2025, 8, 22), "LN/26/002", 90000, "cp_loan_no_trade near miss: below the threshold"),
]
# Computer equipment debited against an expense, not cash or a party: (number, date, credit
# ledger, amount, why).
CAPITALISED = [
    ("CP/26/001", date(2025, 8, 12), "Salaries", 700000, "cp_capitalised: salaries capitalised"),
    ("CP/26/002", date(2025, 9, 9), "Depreciation", 85000, "cp_capitalised near miss"),
]
# The FY25 GSTR-2B: the register's FY25 purchase invoices with GST (the adviser's retainer is not in
# the register), as the suppliers reported them.
TWO_B_PERIOD = "FY25"
TWO_B_NAMES = {TESSELLATE: "Tessellate Data Labs Private Limited"}  # a pass-2 match
TWO_B_MISSING = ("CSP/24-25/164",)  # the vendor has not reported it: right_only
TWO_B_TAXABLE = {"QSD/25/X02": 1300000}  # reported at less than the register: a difference
STUB = "FY26-YTD-SEP"  # data.STUB
# What `fin forensic run` flags with the registers loaded: (test, period, voucher or invoice number,
# ledger (None for a register line), why).
FLAGS = [
    ("cp_new_vendor", "FY25", "TDL/24/0611", None, "Tessellate: new in FY25, its first invoice"),
    ("cp_vendor_spike", "FY25", "QSD/25/X02", None, "Quillfeather's January, the month's largest"),
    ("cp_under_limit", STUB, "QSD/25/U01", None, "1% under the approval limit"),
    ("cp_credit_note", "FY25", "INV-2503-02", None, "C3's 31-Mar-2025 invoice, then CN-2504-01"),
    (
        "cp_setoff",
        "FY25",
        "JV/25/017",
        "Bellwether Clinics LLP",
        "the customer-to-customer journal",
    ),
    ("cp_setoff", "FY25", "JV/25/017", "Halcyon Freight Systems Pvt Ltd", "its other side"),
    ("cp_loan_no_trade", STUB, "LN/26/001", LOAN, "a loan to a party that never traded"),
    ("cp_capitalised", STUB, "CP/26/001", "Computer Equipment", "salaries capitalised"),
    ("cp_capitalised", STUB, "JV/26/001", "Computer Equipment", "SPEC-18's reversal: depreciation"),
    ("cp_capitalised", STUB, "JV/26/002", "Computer Equipment", "SPEC-18's reversal: depreciation"),
]
# The near misses each test logs (not flagged): (test, period, voucher or invoice number, why).
NEAR = [
    ("cp_new_vendor", "FY25", "CSP/24-25/118", "Corbel: 9.4% of FY25's purchases"),
    ("cp_vendor_spike", "FY25", "QSD/24/X01", "Quillfeather's October: 2.5 times its median"),
    ("cp_under_limit", STUB, "QSD/25/U02", "2.2% under the approval limit"),
    ("cp_credit_note", "FY24", "CN-2406-01", "C3's 31-Mar-2024 invoice credited on day 66"),
    ("cp_setoff", "FY25", "JV/25/018", "the customer-to-customer journal below the threshold"),
    ("cp_loan_no_trade", STUB, "LN/26/002", "below the threshold"),
    ("cp_capitalised", STUB, "CP/26/002", "below the threshold"),
]
# Rows of SPEC-18's tests the plants rightly add (data.FORENSIC_EXISTING): (test, period, voucher,
# ledger, why).
JE_EXISTING = [
    ("je_under_limit", STUB, "QSD/25/U01", ledger, "the purchase 1% under the approval limit")
    for ledger in (QUILLFEATHER, "Purchase of Licences")
]
# purchase_register_vs_gstr2b FY25 lines that are not plain matches: (invoice number, kind, why).
TWO_B_LINES = [
    ("CSP/24-25/164", "right_only", "in the register, not reported by the vendor"),
    ("QSD/25/X02", "difference", "reported at a lower taxable value"),
    ("TDL/24/0611", "matched", "vendor names differ"),
    ("TDL/24/0907", "matched", "vendor names differ"),
]
GSTIN = {
    QUILLFEATHER: "27AABCQ4821K1Z3",
    TESSELLATE: "27AAECT7315M1Z8",
    CORBEL: "27AAHCC6402P1Z1",
}
