"""The bank forensic part of the mock set's data definition (SPEC-19), beside `data.py` (kept apart
so each stays under the file size limit).

A second current account of the company at the same bank, with two statements in `mock/extra/bank/`
(the last quarter of FY25 and the first of the stub). It is not in the books and not in the data
room: load it only for the bank forensic tests (it would change bank_vs_tb FY25, which sums every
statement ending on the period's last day). Its lines carry a planted row and a near miss for each
bank test; the main account's statements (`03_bank/`) flag nothing, and the uncleared cheque (paid
31-Mar-2025, cleared 03-Apr-2025) is not a period-end reversal: no credit precedes it.
"""

from dataclasses import dataclass
from datetime import date

from mock.generator import data as d

ACCOUNT = "004105067890"


@dataclass(frozen=True)
class Statement:
    name: str  # under mock/extra/bank/
    period: str
    start: date
    end: date


Q4 = Statement(f"icici_{ACCOUNT}_fy25_q4.xlsx", "FY25", date(2025, 1, 1), date(2025, 3, 31))
Q1 = Statement(f"icici_{ACCOUNT}_fy26_q1.xlsx", d.STUB, date(2025, 4, 1), date(2025, 6, 30))
STATEMENTS = (Q4, Q1)
OPENING = 2000000  # Q4's opening balance; Q1 opens at Q4's closing


@dataclass(frozen=True)
class Row:
    """One statement row, in file order. test and why: the bank test it is planted for (flagged)
    or a near miss of (why starts "near miss"). off: added to the printed balance of this row only;
    blank: the row prints no balance."""

    statement: Statement
    when: date
    remarks: str
    debit: int = 0
    credit: int = 0
    reference: str | None = None
    test: str = ""
    why: str = ""
    off: int = 0
    blank: bool = False

    @property
    def flagged(self) -> bool:
        return bool(self.test) and not self.why.startswith("near miss")


MAIN = f"TRF TO {d.BANK_ACCOUNT}"
FROM_MAIN = f"TRF FROM {d.BANK_ACCOUNT}"
ROUND, TRIP, OWN, CASH = "bank_round", "bank_round_trip", "bank_own_transfer", "bank_cash"
ORDER, DUP, END, CHAIN = (
    "bank_date_order",
    "bank_duplicate",
    "bank_period_end_credit",
    "bank_balance_chain",
)
GAP = "bank_date_gap"
BELLWETHER, SARANGA, ORRERY = "Bellwether Clinics LLP", d.ADVISER_VENDOR, "Orrery Retail Pvt Ltd"


def _q4(day: int, month: int, *args: object, **kw: object) -> Row:
    return Row(Q4, date(2025, month, day), *args, **kw)  # type: ignore[arg-type]


def _q1(day: int, month: int, *args: object, **kw: object) -> Row:
    return Row(Q1, date(2025, month, day), *args, **kw)  # type: ignore[arg-type]


ROWS = [
    _q4(
        2,
        1,
        "FD MATURITY-0001",
        credit=1000000,
        test=ROUND,
        why="one of three multiples of 10,00,000 in January",
    ),
    _q4(
        6,
        1,
        MAIN,
        debit=400000,
        test=OWN,
        why="names the main account, which has no line of 4,00,000 within 3 days",
    ),
    _q4(
        9,
        1,
        "FD MATURITY-0002",
        credit=2000000,
        test=ROUND,
        why="one of three multiples of 10,00,000 in January",
    ),
    _q4(
        13,
        1,
        f"NEFT-{BELLWETHER}-ADV",
        debit=350000,
        test=TRIP,
        why="paid out and the same amount back on 17-Jan; both lines name the customer",
    ),
    _q4(
        16,
        1,
        "FD MATURITY-0003",
        credit=3000000,
        test=ROUND,
        why="one of three multiples of 10,00,000 in January",
    ),
    _q4(17, 1, f"NEFT-{BELLWETHER}-RET", credit=350000),
    _q4(
        22,
        1,
        "NEFT-INTEREST-FD",
        credit=120000,
        test=CHAIN,
        why="near miss: printed 90,000 above (below the threshold); the row below 90,000 back",
        off=90000,
    ),
    _q4(27, 1, "NEFT-Office Supplies", debit=130000),
    _q4(
        3,
        2,
        f"NEFT-{SARANGA}-ADV",
        debit=360000,
        test=TRIP,
        why="near miss: the same amount back 8 days later (window 7)",
    ),
    _q4(
        5,
        2,
        FROM_MAIN,
        credit=170000,
        test=OWN,
        why="near miss: the main account's 1,70,000 (rent, 01-Feb) is 4 days away (window 3)",
    ),
    _q4(
        7,
        2,
        "FD MATURITY-0004",
        credit=1000000,
        test=ROUND,
        why="near miss: only two multiples of 10,00,000 in February",
    ),
    _q4(
        10,
        2,
        "CASH DEP BRANCH 0412",
        credit=60000,
        test=CASH,
        why="February's cash: a deposit of 60,000 and a withdrawal of 50,000, 1,10,000 in all",
    ),
    _q4(11, 2, f"NEFT-{SARANGA}-RET", credit=360000),
    _q4(18, 2, "NEFT-Courier Services", debit=140000),
    _q4(
        14,
        2,
        "NEFT-Printing Services",
        debit=300000,
        test=ORDER,
        why="dated 14-Feb below a row dated 18-Feb",
    ),
    _q4(20, 2, "ATM WDL 0412", debit=50000),
    _q4(24, 2, "FD MATURITY-0005", credit=2000000),
    _q4(26, 2, "NEFT-Travel Desk", debit=110000),
    _q4(
        25,
        2,
        "NEFT-Stationery",
        debit=85000,
        test=ORDER,
        why="near miss: out of order, 85,000 is below the threshold",
    ),
    _q4(
        5,
        3,
        "NEFT-Legal Retainer-MAR",
        debit=250000,
        reference="N501",
        test=DUP,
        why="the same row twice",
    ),
    _q4(
        5,
        3,
        "NEFT-Legal Retainer-MAR",
        debit=250000,
        reference="N501",
        test=DUP,
        why="the same row twice",
    ),
    _q4(
        12,
        3,
        "NEFT-Audit Fee",
        debit=90000,
        reference="N502",
        test=DUP,
        why="near miss: the same row twice, 90,000 is below the threshold",
    ),
    _q4(12, 3, "NEFT-Audit Fee", debit=90000, reference="N502"),
    _q4(
        14,
        3,
        "CASH DEP BRANCH 0412",
        credit=85000,
        test=CASH,
        why="near miss: March's cash is 85,000, below the threshold",
    ),
    _q4(
        28,
        3,
        f"NEFT-{ORRERY}-ADV",
        credit=600000,
        test=END,
        why="debited again on 08-Apr, day 8 of the stub",
    ),
    _q4(
        29,
        3,
        f"NEFT-{ORRERY}-ADV2",
        credit=650000,
        test=END,
        why="near miss: debited again on 16-Apr, day 16 (window 15)",
    ),
    _q4(
        31,
        3,
        "NEFT-Insurance Premium",
        debit=125000,
        test=CHAIN,
        why="printed 2,00,000 above the balance above less the debit",
        off=200000,
    ),
    _q1(3, 4, "NEFT-INTEREST-FD", credit=140000),
    _q1(8, 4, f"NEFT-{ORRERY}-REFUND", debit=600000),
    _q1(16, 4, f"NEFT-{ORRERY}-REFUND2", debit=650000),
    _q1(17, 4, "NEFT-Sundry Receipt", credit=150000, blank=True),
    _q1(25, 5, "NEFT-Office Deposit", debit=120000),
    _q1(22, 6, "NEFT-INTEREST-FD", credit=160000),
    _q1(30, 6, "NEFT-Insurance Premium", debit=105000),
]
# Rows of the main account a default flags: the cash word SELF is in the yearly "CBDT-SELF
# ASSESSMENT TAX" payment, a tax payment, not cash (marked for tuning: docs/decisions/forensic.md).
EXISTING = (
    "bank_cash",
    "SELF ASSESSMENT",
    "the default cash word SELF in the yearly self-assessment tax payment",
)
# Stretches with no line: (statement, first day, last day, why); flagged unless "near miss".
GAPS = [
    (Q1, date(2025, 4, 18), date(2025, 5, 24), "37 days with no line (more than 31)"),
    (Q1, date(2025, 5, 26), date(2025, 6, 21), "near miss: 27 days with no line"),
]
