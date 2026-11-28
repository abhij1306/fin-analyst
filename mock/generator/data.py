"""The data definition of the mock set: one fictitious company, Corvane Analytics Private Limited.

Everything in mock/ is computed from this module: the books (vouchers, trial balances, Ledger
Vouchers), the statements, the source files and the answer key (mock/EXPECTED.md). Amounts are
rupees; every voucher is a multiple of 1,000, so a figure shows exactly in lakhs to two decimals.
To extend the set, add a plan, a party or a planted item here and regenerate.
"""

from dataclasses import dataclass, field
from datetime import date

from mock.generator import data_counterparty as counterparty

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
    "Donations": ("Indirect Expenses", "other_expenses"),  # SPEC-18: a rare ledger, planted
    "Staff Welfare": ("Indirect Expenses", "other_expenses"),  # SPEC-18: a near miss
    "Income Tax Expense": ("Indirect Expenses", "tax_expense"),
    **counterparty.LEDGERS,  # SPEC-20: two new vendors and a loan
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


@dataclass(frozen=True)
class Planted:
    """A journal planted for a forensic test (SPEC-18): debit one ledger, credit another. flags:
    the rows the test flags (ledgers, or the missing voucher number for je_gap); none for a near
    miss just outside the default (docs/decisions/forensic.md)."""

    number: str
    when: date
    debit: str
    credit: str
    amount: int
    test: str
    why: str
    flags: tuple[str, ...] = ()


R, H = "Rent", "Cloud Hosting"  # most planted journals move between two expense ledgers
FORENSIC = [
    Planted(
        "JV/25/001", date(2024, 7, 10), R, H, 1000000, "je_round", "a multiple of 10,00,000", (R, H)
    ),
    Planted(
        "JV/25/002",
        date(2024, 7, 11),
        R,
        H,
        900000,
        "je_round",
        "near miss: 9,00,000 is not a multiple",
    ),
    Planted("JV/25/003", date(2024, 6, 9), R, H, 210000, "je_weekend", "a Sunday", (R, H)),
    Planted(
        "JV/25/004",
        date(2024, 6, 8),
        R,
        H,
        220000,
        "je_weekend",
        "near miss: a Saturday (not a weekend day by default)",
    ),
    Planted(
        "JV/25/005", date(2024, 8, 15), R, H, 230000, "je_weekend", "a holiday (15 August)", (R, H)
    ),
    Planted(
        "JV/25/006",
        date(2024, 9, 12),
        R,
        H,
        120000,
        "je_duplicate",
        "the same lines as JV/25/007",
        (R, H),
    ),
    Planted(
        "JV/25/007",
        date(2024, 9, 12),
        R,
        H,
        120000,
        "je_duplicate",
        "the same lines as JV/25/006",
        (R, H),
    ),
    Planted(
        "JV/25/008",
        date(2024, 9, 18),
        R,
        H,
        130000,
        "je_duplicate",
        "near miss: as JV/25/009, a day apart",
    ),
    Planted(
        "JV/25/009",
        date(2024, 9, 19),
        R,
        H,
        130000,
        "je_duplicate",
        "near miss: as JV/25/008, a day apart",
    ),
    Planted(
        "JV/25/010",
        date(2024, 10, 8),
        R,
        H,
        495000,
        "je_under_limit",
        "1% below the stated limit of 5,00,000",
        (R, H),
    ),
    Planted(
        "JV/25/011",
        date(2024, 10, 9),
        R,
        H,
        489000,
        "je_under_limit",
        "near miss: 2.2% below the limit",
    ),
    Planted(
        "JV/25/012",
        date(2025, 3, 29),
        R,
        H,
        140000,
        "je_period_end",
        "in the last 5 days; none on these ledgers in FY24's",
        (R, H),
    ),
    Planted(
        "JV/25/014",
        date(2025, 3, 26),
        R,
        H,
        145000,
        "je_period_end",
        "near miss: 6 days before the year end",
    ),
    Planted(
        "JV/25/015",
        date(2025, 3, 28),
        "Depreciation",
        "Computer Equipment",
        450000,
        "je_reversed",
        "reversed by JV/26/001 on 4 April",
        ("Depreciation", "Computer Equipment"),
    ),
    Planted(
        "JV/25/016",
        date(2025, 3, 28),
        "Depreciation",
        "Computer Equipment",
        350000,
        "je_reversed",
        "near miss: reversed by JV/26/002 on 16 April, day 16",
    ),
    Planted(
        "JV/26/001",
        date(2025, 4, 4),
        "Computer Equipment",
        "Depreciation",
        450000,
        "je_reversed",
        "the reversal of JV/25/015",
    ),
    Planted(
        "JV/26/002",
        date(2025, 4, 16),
        "Computer Equipment",
        "Depreciation",
        350000,
        "je_reversed",
        "the reversal of JV/25/016",
    ),
    Planted(
        "JV/25/017",
        date(2024, 11, 12),
        "Bellwether Clinics LLP",
        "Halcyon Freight Systems Pvt Ltd",
        250000,
        "je_sensitive",
        "a journal between two customers",
        ("Bellwether Clinics LLP", "Halcyon Freight Systems Pvt Ltd"),
    ),
    Planted(
        "JV/25/018",
        date(2024, 11, 13),
        "Bellwether Clinics LLP",
        "Halcyon Freight Systems Pvt Ltd",
        90000,
        "je_sensitive",
        "near miss: below the query threshold",
    ),
    Planted(
        "JV/25/019",
        date(2024, 12, 10),
        "Interest on Term Loan",
        R,
        150000,
        "je_unusual_pair",
        "finance cost debited against other expenses, once",
        ("Interest on Term Loan",),
    ),
    Planted(
        "JV/25/020",
        date(2025, 1, 14),
        R,
        "Interest on Term Loan",
        60000,
        "je_unusual_pair",
        "near miss: the reverse pair, in two vouchers",
    ),
    Planted(
        "JV/25/021",
        date(2025, 2, 11),
        R,
        "Interest on Term Loan",
        61000,
        "je_unusual_pair",
        "near miss: the reverse pair, in two vouchers",
    ),
    Planted(
        "JV/25/022",
        date(2025, 1, 21),
        "Donations",
        R,
        110000,
        "je_rare_ledger",
        "a new ledger with one line",
        ("Donations",),
    ),
    Planted(
        "JV/25/023",
        date(2025, 2, 18),
        R,
        H,
        40000,
        "je_gap",
        "JV/25/013 is missing from JV/25/001 to JV/25/023",
        ("JV/25/013",),
    ),
]
# FY24: Staff Welfare has three lines (one more than a rare ledger), and the JV/24 series has 19
# numbers with JV/24/009 missing: one short of a series the gap test reads.
FORENSIC += [
    Planted(
        f"JV/24/{n:03d}",
        date(2023 + (3 + (n - 1) // 2) // 12, (3 + (n - 1) // 2) % 12 + 1, 10 + 10 * (n % 2)),
        "Staff Welfare" if n <= 3 else R,
        H,
        20000 + 1000 * n,
        "je_rare_ledger" if n <= 3 else "je_gap",
        "near miss: Staff Welfare has 3 lines"
        if n <= 3
        else "near miss: JV/24/009 is missing from a series of 19 numbers"
        if n == 10
        else "part of the JV/24 series",
    )
    for n in range(1, 21)
    if n != 9
]
# Rows of the books a test rightly flags: (test, period, voucher number, ledger, why).
AJ, CN = AUDIT_ADJUSTMENT["number"], CREDIT_NOTE["number"]
AJ_LEDGERS = (AUDIT_ADJUSTMENT["debit"], AUDIT_ADJUSTMENT["credit"])
FORENSIC_EXISTING: list[tuple[str, str, str, str, str]] = [
    *(
        ("je_period_end", "FY25", AJ, ledger, "the auditor's adjustment on the year's last day; no "
         "entry on the ledger in FY24's last days")
        for ledger in AJ_LEDGERS
    ),
    ("je_sensitive", "FY25", AJ, AUDIT_ADJUSTMENT["credit"], "the auditor's journal credits the "
     "doubtful debts provision, mapped to trade receivables"),
    ("je_unusual_pair", "FY25", AJ, AUDIT_ADJUSTMENT["debit"], "the only voucher debiting other "
     "expenses and crediting trade receivables"),
    ("je_unusual_pair", "FY25", f"P-{CN}", CUSTOMERS[CREDIT_NOTE["customer"]].ledger, "the only "
     "refund to a customer: trade receivables debited, cash credited"),
    *(
        ("je_rare_ledger", "FY25", AJ, ledger, "a ledger used once, by the auditor's adjustment")
        for ledger in AJ_LEDGERS
    ),
    ("je_rare_ledger", "FY25", f"OO/{ONE_OFFS[1][0]:%y%m%d}", ONE_OFFS[1][1], "a one-off cost, "
     "the legal settlement"),
    ("je_rare_ledger", "FY23", f"OO/{ONE_OFFS[0][0]:%y%m%d}", ONE_OFFS[0][1], "a one-off cost, "
     "the office relocation"),
    # SPEC-20 removed CN-2412-01's je_unusual_pair row (data_counterparty.CREDIT_NOTES book the pair
    # twice more) and adds the under-limit purchase's je_under_limit rows
    *counterparty.JE_EXISTING,
]  # fmt: skip
APPROVAL_LIMIT = 500000  # the limit the user states for the mock company (je_under_limit)
