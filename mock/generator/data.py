"""The data definition of the mock set: one fictitious company, Corvane Analytics Private Limited.

Everything in mock/ is computed from this module: the books (vouchers, trial balances, Ledger
Vouchers), the statements, the source files and the answer key (mock/EXPECTED.md). Amounts are
rupees; every voucher is a multiple of 1,000, so a figure shows exactly in lakhs to two decimals.
To extend the set, add a plan, a party or a planted item here and regenerate.
"""

from dataclasses import dataclass, field
from datetime import date

COMPANY = "Corvane Analytics Private Limited"
CODE = "corvane"
BANNER = "SYNTHETIC TEST DATA - fictitious company, generated for testing fin"
# period -> (first day, last day). The stub is the first half of FY26: unaudited management
# accounts.
PERIODS = {
    "FY23": (date(2022, 4, 1), date(2023, 3, 31)),
    "FY24": (date(2023, 4, 1), date(2024, 3, 31)),
    "FY25": (date(2024, 4, 1), date(2025, 3, 31)),
    "FY26-YTD-SEP": (date(2025, 4, 1), date(2025, 9, 30)),
}
ANNUAL = ("FY23", "FY24", "FY25")
STUB = "FY26-YTD-SEP"
OPENING_DATE = date(2022, 3, 31)

BANK = "ICICI Bank Current A/c"
BANK_ACCOUNT = "004105012345"
RETAINED = "Profit & Loss A/c"
# ledger -> (Tally group, the account the ledger should be mapped to)
LEDGERS = {
    "Share Capital": ("Capital Account", "share_capital"),
    RETAINED: ("Reserves & Surplus", "other_equity"),
    "Term Loan - Axis Bank": ("Secured Loans", "lt_borrowings"),
    "Quillfeather Software Distribution Pvt Ltd": ("Sundry Creditors", "trade_payables"),
    "Saranga Advisory LLP": ("Sundry Creditors", "trade_payables"),
    "Output CGST": ("Duties & Taxes", "other_current_liabilities"),
    "Output SGST": ("Duties & Taxes", "other_current_liabilities"),
    "Input CGST": ("Duties & Taxes", "other_current_liabilities"),
    "Input SGST": ("Duties & Taxes", "other_current_liabilities"),
    "Provision for Gratuity": ("Provisions", "st_provisions"),
    "Provision for Income Tax": ("Provisions", "current_tax_liabilities"),
    "Provision for Doubtful Debts": ("Provisions", "trade_receivables"),
    "Computer Equipment": ("Fixed Assets", "ppe_net"),
    "Halcyon Freight Systems Pvt Ltd": ("Sundry Debtors", "trade_receivables"),
    "Bellwether Clinics LLP": ("Sundry Debtors", "trade_receivables"),
    "Orrery Retail Pvt Ltd": ("Sundry Debtors", "trade_receivables"),
    "Peregrine Labs Inc": ("Sundry Debtors", "trade_receivables"),
    "Kinfolk Hotels Pvt Ltd": ("Sundry Debtors", "trade_receivables"),
    BANK: ("Bank Accounts", "cash"),
    "Security Deposit - Office": ("Loans & Advances (Asset)", "other_current_assets"),
    "Subscription Revenue": ("Sales Accounts", "revenue"),
    "Implementation Services": ("Sales Accounts", "revenue"),
    "Purchase of Licences": ("Purchase Accounts", "purchases_stock_in_trade"),
    "Foreign Exchange Gain": ("Indirect Incomes", "other_income"),
    "Salaries": ("Indirect Expenses", "employee_expense"),
    "Gratuity Expense": ("Indirect Expenses", "employee_expense"),
    "Rent": ("Indirect Expenses", "other_expenses"),
    "Cloud Hosting": ("Indirect Expenses", "other_expenses"),
    "Consultancy Fees": ("Indirect Expenses", "other_expenses"),
    "Legal and Professional Fees": ("Indirect Expenses", "other_expenses"),
    "Office Relocation Expenses": ("Indirect Expenses", "other_expenses"),
    "Bad and Doubtful Debts": ("Indirect Expenses", "other_expenses"),
    "Interest on Term Loan": ("Indirect Expenses", "finance_cost"),
    "Depreciation": ("Indirect Expenses", "depreciation"),
    "Income Tax Expense": ("Indirect Expenses", "tax_expense"),
}
PL_GROUPS = ("Sales Accounts", "Purchase Accounts", "Indirect Incomes", "Indirect Expenses")
# Debit (+) or credit (-) balances on 31-Mar-2022; the Profit & Loss A/c balances the rest.
OPENING = {
    "Share Capital": -1000000,
    "Term Loan - Axis Bank": -6000000,
    "Output CGST": -27000,
    "Output SGST": -27000,
    "Provision for Gratuity": -600000,
    "Provision for Income Tax": -500000,
    "Computer Equipment": 1400000,
    "Halcyon Freight Systems Pvt Ltd": 354000,
    BANK: 5000000,
    "Security Deposit - Office": 500000,
}
# ledger -> the tax class the user should confirm (fin map tax), docs/fdd.md F6
TAX_CLASSES = {
    "Output CGST": "gst_output",
    "Output SGST": "gst_output",
    "Input CGST": "gst_input",
    "Input SGST": "gst_input",
    "Provision for Income Tax": "income_tax",
}


@dataclass(frozen=True)
class Customer:
    ledger: str
    customer_id: int
    location: str
    pay_days: int  # days from invoice to receipt
    related: bool = False


CUSTOMERS = {
    "C1": Customer("Halcyon Freight Systems Pvt Ltd", 1101, "Mumbai", 30),
    "C2": Customer("Bellwether Clinics LLP", 1102, "Pune", 30),
    "C3": Customer("Orrery Retail Pvt Ltd", 1103, "Bengaluru", 60, related=True),
    "C4": Customer("Peregrine Labs Inc", 1104, "Singapore", 30),
    "C5": Customer("Kinfolk Hotels Pvt Ltd", 1105, "Goa", 30),
}
LICENCE_VENDOR = "Quillfeather Software Distribution Pvt Ltd"
ADVISER_VENDOR = "Saranga Advisory LLP"  # a director's relative is its partner: a related party
HANDLERS = ("Ira Fernandes", "Sameer Gokhale")  # the billing team (masked on load)


@dataclass(frozen=True)
class Plan:
    """A customer's billing: one invoice per cycle, amount per invoice by period (own currency)."""

    customer: str
    product: str
    service_type: str
    ledger: str
    cycle: str  # monthly (1st), quarterly (1st of the quarter) or quarter_end (last day)
    amounts: dict[str, int]
    currency: str = "INR"
    rates: dict[str, float] = field(default_factory=dict)  # USD rate per period (booked)
    day: int = 1
    first: date | None = None  # no invoice before this date


PLANS = [
    Plan(
        "C1",
        "Fleet analytics subscription",
        "Subscription",
        "Subscription Revenue",
        "monthly",
        {"FY23": 800000, "FY24": 1000000, "FY25": 1200000, STUB: 1300000},
    ),
    Plan(
        "C2",
        "Clinic analytics subscription",
        "Subscription",
        "Subscription Revenue",
        "quarterly",
        {"FY23": 1500000, "FY24": 1800000, "FY25": 2000000},
    ),
    Plan(
        "C3",
        "Store analytics implementation",
        "Services",
        "Implementation Services",
        "quarter_end",
        {"FY23": 1200000, "FY24": 1500000, "FY25": 1800000, STUB: 1800000},
    ),
    Plan(
        "C4",
        "Analytics platform licence",
        "Subscription",
        "Subscription Revenue",
        "quarterly",
        {"FY23": 30000, "FY24": 36000, "FY25": 40000, STUB: 40000},
        currency="USD",
        rates={"FY23": 82.0, "FY24": 83.0, "FY25": 84.0, STUB: 85.5},
        day=15,
    ),
    Plan(
        "C5",
        "Hotel analytics subscription",
        "Subscription",
        "Subscription Revenue",
        "monthly",
        {STUB: 300000},
        first=date(2025, 5, 1),
    ),
]
FX_RECEIPT_PREMIUM = 0.5  # a USD receipt converts at the booked rate plus this: an fx gain
GST_RATE = 9  # per cent each of CGST and SGST on domestic invoices; exports under LUT carry none
LICENCE_PURCHASE = {"FY23": 600000, "FY24": 800000, "FY25": 1000000, STUB: 1000000}  # quarterly
LICENCE_PAY_DAYS = 45
RETAINER = 50000  # Saranga Advisory LLP, monthly on the 5th, paid on the 20th
RENT = {"FY23": 150000, "FY24": 160000, "FY25": 170000, STUB: 180000}  # monthly, 1st
HOSTING = {"FY23": 200000, "FY24": 250000, "FY25": 300000, STUB: 320000}  # monthly, 15th
CAPEX = {
    "FY23": (date(2022, 5, 15), 800000),
    "FY24": (date(2023, 6, 12), 600000),
    "FY25": (date(2024, 5, 20), 1000000),
    STUB: (date(2025, 7, 15), 400000),
}
DEPRECIATION_RATE = 0.25  # on the opening block plus additions, rounded to 1,000; the stub half
GRATUITY = {"FY23": 240000, "FY24": 264000, "FY25": 300000, STUB: 156000}
LOAN_REPAYMENT = 300000  # each quarter end, with interest at LOAN_RATE a year on the balance
LOAN_RATE = 0.10
TAX_RATE = 0.25  # on the year's profit before tax (the stub has no tax charge)


@dataclass(frozen=True)
class Employee:
    code: str
    name: str
    designation: str
    department: str
    vertical: str
    sub_vertical: str
    joined: date
    ctc: int  # annual rupees; gross salary is 90% of it


EMPLOYEES = [
    Employee(
        "CA001",
        "Anaya Deshpande",
        "Chief Technology Officer",
        "Technology",
        "Products",
        "Platform",
        date(2019, 6, 1),
        3600000,
    ),
    Employee(
        "CA002",
        "Kabir Sethi",
        "Engineering Manager",
        "Technology",
        "Products",
        "Platform",
        date(2020, 2, 10),
        2400000,
    ),
    Employee(
        "CA003",
        "Ritika Nair",
        "Senior Engineer",
        "Technology",
        "Products",
        "Data",
        date(2021, 7, 1),
        1800000,
    ),
    Employee(
        "CA004",
        "Farhan Qureshi",
        "Engineer",
        "Technology",
        "Products",
        "Data",
        date(2022, 1, 3),
        1200000,
    ),
    Employee(
        "CA005",
        "Tanvi Joshi",
        "Customer Success Lead",
        "Services",
        "Delivery",
        "Onboarding",
        date(2021, 4, 1),
        1440000,
    ),
    Employee(
        "CA006",
        "Arjun Menon",
        "Sales Manager",
        "Sales",
        "Growth",
        "Enterprise",
        date(2020, 9, 15),
        2160000,
    ),
    Employee(
        "CA007",
        "Leela Iyer",
        "Finance Manager",
        "Finance",
        "Corporate",
        "Accounts",
        date(2019, 4, 1),
        1680000,
    ),
    Employee(
        "CA008",
        "Vikram Bose",
        "Engineer",
        "Technology",
        "Products",
        "Mobile",
        date(2023, 7, 1),
        1320000,
    ),
    Employee(
        "CA009",
        "Neha Kapoor",
        "Senior Engineer",
        "Technology",
        "Products",
        "Mobile",
        date(2025, 1, 1),
        1920000,
    ),
]
PAYROLL_AS_ON = date(2025, 3, 31)

# The deliberate diligence items (mock/EXPECTED.md lists where each surfaces).
BILLED_AHEAD = {  # an annual renewal invoiced before the year end for next year's service
    "customer": "C2",
    "number": "INV-2503-90",
    "date": date(2025, 3, 25),
    "amount": 7200000,
    "service": (date(2025, 4, 1), date(2026, 3, 31)),
    "product": "Clinic analytics annual renewal",
}
CREDIT_NOTE = {  # a service credit booked in the books only, refunded through the bank
    "customer": "C1",
    "number": "CN-2412-01",
    "date": date(2024, 12, 20),
    "amount": 200000,
    "refund": date(2025, 1, 15),
}
ONE_OFFS = [  # (date, ledger, amount, what it was): one-off costs for EBITDA adjustments
    (date(2022, 8, 10), "Office Relocation Expenses", 200000, "Office relocation to Baner"),
    (
        date(2024, 11, 18),
        "Legal and Professional Fees",
        600000,
        "Settlement of a contract dispute with a former reseller",
    ),
]
# The FY25 auditor's adjustment: in the audited statements and the v2 trial balance.
AUDIT_ADJUSTMENT = {
    "date": date(2025, 3, 31),
    "number": "AJ-1",
    "amount": 300000,
    "debit": "Bad and Doubtful Debts",
    "credit": "Provision for Doubtful Debts",
}
STATEMENT_RECLASS = {  # FY24 statements show lease interest in finance costs; the books in rent
    "period": "FY24",
    "amount": 150000,
    "from": "other_expenses",
    "to": "finance_cost",
}
UNCLEARED_CHEQUE = {  # paid by cheque on the last day of FY25, cleared by the bank in April
    "booked": date(2025, 3, 31),
    "cleared": date(2025, 4, 3),
    "cheque": "004512",
}
GST_LATE_INVOICE = BILLED_AHEAD["number"]  # left out of the FY25 returns (filed in April 2025)
RPT_DISCLOSED = {  # the FY25 notes' related party transactions, rupees
    "Orrery Retail Pvt Ltd": ("Sale of services", "sales", None),  # disclosed = the register
    ADVISER_VENDOR: ("Consultancy fees", "debit", 540000),  # the books say 6,00,000
}
CONTINGENT = [
    ("GST show cause notice for FY 2020-21 on export of services, under appeal", 1850000),
    ("Income tax demand for AY 2022-23 on disallowance of expenses, under appeal", 640000),
]
COMMITMENT = ("Capital commitment for laptops ordered, not delivered", 350000)
ORDERS_AS_AT = date(2025, 3, 31)
NEW_ORDER = {
    "customer": "C5",
    "number": "SO-2025-014",
    "date": date(2025, 3, 15),
    "value": 3600000,
    "start": date(2025, 5, 1),
    "end": date(2026, 4, 30),
}
USD_RATE_AS_AT = 85.40  # the rate the user states for the orderbook (fin engagement rate)
QUERY_THRESHOLD = 100000
