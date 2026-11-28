"""A synthetic Sparsh payroll snapshot (made-up employees), written with openpyxl in a tmp_path.

`write_payroll`: title lines ("Employee Master as on 31-Mar-2025"), one header row (Employee Code |
Employee Name | Designation | Department | Vertical | Sub Vertical | Date of Joining | Date of
Resignation | Gross Salary | CTC), one row per employee and a Total row. `monthly` writes monthly
amounts (the planted unit problem); `blank_code` removes one employee's code.
"""

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

COMPANY = "Acme Synthetic Private Limited"
HEADER = [
    "Employee Code",
    "Employee Name",
    "Designation",
    "Department",
    "Vertical",
    "Sub Vertical",
    "Date of Joining",
    "Date of Resignation",
    "Gross Salary",
    "CTC",
]
# (code, name, designation, department, vertical, sub vertical, joined, resigned, gross, CTC):
# annual rupees
EMPLOYEES = [
    (
        "A1001",
        "Meera Kulkarni",
        "Engineer",
        "Technology",
        "Products",
        "Platform",
        (2019, 4, 1),
        None,
        1200000,
        1380000,
    ),
    (
        "A1002",
        "Dev Malhotra",
        "Senior Engineer",
        "Technology",
        "Products",
        "Mobile",
        (2020, 7, 15),
        None,
        1800000,
        2040000,
    ),
    (
        "A1003",
        "Isha Trivedi",
        "Analyst",
        "Finance",
        "Corporate",
        "Accounts",
        (2021, 1, 4),
        (2024, 12, 31),
        720000,
        828000,
    ),
    (
        "A1004",
        "Rohan Bhatt",
        "Manager",
        "Sales",
        "Services",
        "Enterprise",
        (2018, 9, 3),
        None,
        2400000,
        2760000,
    ),
]
EMPLOYEE_NAMES = tuple(e[1] for e in EMPLOYEES)
EMPLOYEE_CODES = tuple(e[0] for e in EMPLOYEES)


def write_payroll(
    path: Path,
    *,
    as_on: str = "31-Mar-2025",
    monthly: bool = False,
    blank_code: bool = False,
    bad_date: bool = False,
) -> Path:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Employee Master"
    for line in (COMPANY, f"Employee Master as on {as_on}"):
        sheet.append([line])
    sheet.append(HEADER)
    divisor = 12 if monthly else 1
    for i, (code, name, *middle, joined, resigned, gross, ctc) in enumerate(EMPLOYEES):
        sheet.append(
            [
                None if blank_code and i == 1 else code,
                name,
                *middle,
                "not a date" if bad_date and i == 0 else datetime(*joined),
                datetime(*resigned) if resigned else None,
                gross // divisor,
                ctc // divisor,
            ]
        )
    sheet.append(["Total", None, None, None, None, None, None, None, 1, 1])
    book.save(path)
    return path
