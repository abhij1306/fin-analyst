"""Build the mock set: every file of `DOCUMENTS` under mock/dataroom/, its zip (mock/zipped/),
mock/extra/ and mock/EXPECTED.md, from the data definition (`data`). Everything is built in a
temporary folder first and replaces the committed files only when all of it succeeded.

`uv run --with xlwt python -m mock.generator` (xlwt writes the one .xls file).
"""

import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mock.generator import bank_extra, write
from mock.generator import books as b
from mock.generator import data as d
from mock.generator import gstr2b as g2
from mock.generator import reports as r
from mock.generator import sources as s

ROOT = Path(__file__).resolve().parent.parent
COMPANY, AUDITOR, BANK, ADVISER = (
    "the company (management)",
    "the auditor",
    "the bank",
    "a third-party adviser",
)


@dataclass(frozen=True)
class Document:
    path: str  # under mock/dataroom/
    source: str  # who sent it
    kind: str  # the kind fin's data-room plan should give it
    fmt: str  # xlsx, xls or pdf
    build: Callable[[b.Books], list]
    note: str = ""
    copy_of: str = ""  # an exact duplicate of another document (the same bytes)


def _tb(period: str, audited: bool = True) -> Callable[[b.Books], list]:
    return lambda books: s.trial_balance(books, period, audited)


def _lv(period: str) -> Callable[[b.Books], list]:
    return lambda books: s.ledger_vouchers(books, period)


def _bank(period: str) -> Callable[[b.Books], list]:
    return lambda books: s.bank_statement(books, period)


C, A, K, V = "01_company", "02_auditor", "03_bank", "04_adviser"
DOCUMENTS = [
    Document(
        f"{C}/statements/corvane_fy25_provisional_statements_2025-04-30.xlsx",
        COMPANY,
        "statements",
        "xlsx",
        lambda x: r.statement_sheets(x, "FY25", False, True),
        "version 1 of FY25: before the auditor's adjustment",
    ),
    Document(
        f"{A}/corvane_fy23_audited_statements.xls",
        AUDITOR,
        "statements",
        "xls",
        lambda x: r.statement_sheets(x, "FY23", True, False),
        "FY23 only",
    ),
    Document(
        f"{A}/corvane_fy24_audited_statements.xlsx",
        AUDITOR,
        "statements",
        "xlsx",
        lambda x: r.statement_sheets(x, "FY24", True, True),
        "FY24 with FY23",
    ),
    Document(
        f"{A}/corvane_fy25_annual_report_audited.pdf",
        AUDITOR,
        "statements",
        "pdf",
        r.annual_report,
        "version 2 of FY25: audited, with notes (pages 5)",
    ),
    Document(
        f"{C}/trial_balances/corvane_tb_fy23_2023-05-10.xlsx",
        COMPANY,
        "trial_balance",
        "xlsx",
        _tb("FY23"),
    ),
    Document(
        f"{C}/trial_balances/corvane_tb_fy24_2024-05-12.xlsx",
        COMPANY,
        "trial_balance",
        "xlsx",
        _tb("FY24"),
    ),
    Document(
        f"{C}/trial_balances/corvane_tb_fy25_v1_2025-04-15.xlsx",
        COMPANY,
        "trial_balance",
        "xlsx",
        _tb("FY25", False),
        "version 1: before the auditor's adjustment",
    ),
    Document(
        f"{C}/trial_balances/corvane_tb_fy25_v2_2025-06-20.xlsx",
        COMPANY,
        "trial_balance",
        "xlsx",
        _tb("FY25"),
        "version 2, sent again after the audit: supersedes v1",
    ),
    Document(
        f"{C}/trial_balances/corvane_tb_fy26_h1_2025-10-15.xlsx",
        COMPANY,
        "trial_balance",
        "xlsx",
        _tb(d.STUB),
        "the stub: unaudited management accounts",
    ),
    *(
        Document(
            f"{C}/ledgers/corvane_ledger_vouchers_{p.lower().replace('-ytd-sep', '_h1')}.xlsx",
            COMPANY,
            "ledger_vouchers",
            "xlsx",
            _lv(p),
        )
        for p in d.PERIODS
    ),
    Document(
        f"{C}/billing/corvane_registers_fy23_to_fy26_h1.xlsx",
        COMPANY,
        "registers",
        "xlsx",
        s.registers,
        "1-Apr-2022 to 30-Sep-2025; fin labels it FY23-FY26",
    ),
    Document(
        f"{C}/billing/corvane_orderbook_2025-03-31.xlsx", COMPANY, "orderbook", "xlsx", s.orderbook
    ),
    Document(
        f"{C}/hr/corvane_employee_master_2025-03-31.xlsx",
        COMPANY,
        "payroll",
        "xlsx",
        lambda x: s.payroll(),
        "amounts are annual: `fin payroll confirm-unit ... --annual`",
    ),
    Document(
        f"{C}/gst/corvane_gstr1_fy25.xlsx",
        COMPANY,
        "gst (gstr1)",
        "xlsx",
        lambda x: s.gst_return(x, "gstr1"),
    ),
    Document(
        f"{C}/gst/corvane_gstr3b_fy25.xlsx",
        COMPANY,
        "gst (gstr3b)",
        "xlsx",
        lambda x: s.gst_return(x, "gstr3b"),
        "the plan names profile gstr1 (the first that matches): load it with --profile gstr3b",
    ),
    Document(
        f"{C}/gst/corvane_gstr2b_fy25.xlsx",
        COMPANY,
        "gst (gstr2b)",
        "xlsx",
        g2.gstr2b,
        "SPEC-20: load it with --profile gstr2b; see EXPECTED.md for its differences",
    ),
    Document(
        f"{C}/contracts/bellwether_renewal_2025-26.pdf",
        COMPANY,
        "skipped (no statement page)",
        "pdf",
        lambda x: r.contract(),
        "a contract: Claude reads it (fin recast contracts)",
    ),
    *(
        Document(
            f"{K}/icici_{d.BANK_ACCOUNT}_{p.lower().replace('-ytd-sep', '_h1')}.xlsx",
            BANK,
            "bank",
            "xlsx",
            _bank(p),
        )
        for p in ("FY24", "FY25", d.STUB)
    ),
    Document(
        f"{C}/bank/icici_{d.BANK_ACCOUNT}_fy25.xlsx",
        COMPANY,
        "bank (duplicate)",
        "xlsx",
        _bank("FY25"),
        "the company forwarded the bank's file",
        copy_of=f"{K}/icici_{d.BANK_ACCOUNT}_fy25.xlsx",
    ),
    Document(
        f"{V}/saltmarsh_databook_corvane_fy23-fy25.xlsx",
        ADVISER,
        "reference_databook",
        "xlsx",
        r.databook,
        "FY25 from the provisional statements",
    ),
]
ZIP = "zipped/corvane_dataroom.zip"  # in a folder of its own: `fin dataroom scan` takes a folder
EXTRA = {"walk_sheet_fy23-fy25.xlsx": r.walk_sheet, **bank_extra.extra()}


def _write(doc: Document, books: b.Books, folder: Path) -> None:
    path = folder / doc.path
    if doc.copy_of:
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(folder / doc.copy_of, path)
    elif doc.fmt == "pdf":
        write.pdf(path, doc.build(books))
    else:
        getattr(write, doc.fmt)(path, doc.build(books))


def build(target: Path, books: b.Books | None = None) -> None:
    """Write dataroom/, zipped/, extra/ and EXPECTED.md under `target`."""
    from mock.generator import expected

    books = books or b.build()
    room = target / "dataroom"
    for doc in sorted(DOCUMENTS, key=lambda doc: bool(doc.copy_of)):
        _write(doc, books, room)
    (target / "zipped").mkdir(parents=True, exist_ok=True)
    write.zip_folder(room, target / ZIP)
    for name, sheets in EXTRA.items():
        write.xlsx(target / "extra" / name, sheets())
    for name, text in expected.extra_texts().items():
        (target / "extra" / name).parent.mkdir(parents=True, exist_ok=True)
        (target / "extra" / name).write_text(text, encoding="utf-8", newline="\n")
    (target / "EXPECTED.md").write_text(expected.render(books), encoding="utf-8", newline="\n")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        build(Path(tmp))
        for name in ("dataroom", "extra", "zipped"):
            shutil.rmtree(ROOT / name, ignore_errors=True)
            shutil.copytree(Path(tmp) / name, ROOT / name)
        shutil.copyfile(Path(tmp) / "EXPECTED.md", ROOT / "EXPECTED.md")
