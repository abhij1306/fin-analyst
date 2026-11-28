"""fin dataroom propose|approve|load (SPEC-13 PR 2): the approved plan loads in one run, once."""

from pathlib import Path

import pytest
from openpyxl import Workbook
from typer.testing import CliRunner

from fin.cli import app
from fin.dataroom.scan import read_plan
from fin.db.conn import connect
from fin.paths import audit_db_path, company_db_path
from tests.synthetic_pdf import BALANCE_SHEET, PROFIT_AND_LOSS, WIDTH, write_pdf
from tests.tally_fixtures import (
    VOUCHERS,
    write_ledger_vouchers,
    write_trial_balance,
    write_unknown_layout,
)

runner = CliRunner()


def fin(*args: str, code: int = 0) -> str:
    result = runner.invoke(app, list(args))
    assert result.exit_code == code, result.output
    return result.output


@pytest.fixture
def room(data_root: Path, tmp_path: Path) -> Path:
    """An open FY25 engagement and a scanned room: Ledger Vouchers (found before its trial
    balance), the trial balance and a copy of it, statements and an unknown workbook."""
    fin("init")
    room = tmp_path / "room"
    (room / "books").mkdir(parents=True)
    write_ledger_vouchers(room / "books" / "a_ledger_vouchers.xlsx", list(VOUCHERS))
    tb = write_trial_balance(room / "books" / "b_trial_balance.xlsx")
    (room / "books" / "c_copy.xlsx").write_bytes(tb.read_bytes())
    write_pdf(room / "statements.pdf", BALANCE_SHEET, PROFIT_AND_LOSS, width=WIDTH)
    write_unknown_layout(room / "notes.xlsx")
    fin("dataroom", "scan", "acme", str(room))  # makes the company database
    fin("engagement", "open", "acme", "--periods", "FY25")
    return room


def ids(data_root: Path) -> dict[str, int]:
    return {r.path: r.file_id for r in read_plan(data_root, "acme")}


def counts(data_root: Path) -> tuple[int, int]:
    with connect(company_db_path(data_root, "acme")) as con:
        row = con.execute(
            "SELECT (SELECT count(*) FROM source_files), (SELECT count(*) FROM documents)"
        ).fetchone()
    assert row is not None
    return row[0], row[1]


def test_the_approved_plan_loads_in_one_run_and_a_second_scan_and_load_changes_nothing(
    data_root: Path, room: Path
) -> None:
    rows = ids(data_root)
    approved = ["books/a_ledger_vouchers.xlsx", "books/b_trial_balance.xlsx", "statements.pdf"]
    fin("dataroom", "approve", "acme", *(str(rows[p]) for p in approved))
    output = fin("dataroom", "load", "acme")
    lines = [line for line in output.splitlines() if line.startswith("[")]
    assert [line.split(":")[0] for line in lines] == [
        "[1/3] statements.pdf",
        "[2/3] books/b_trial_balance.xlsx",
        "[3/3] books/a_ledger_vouchers.xlsx",
    ]
    plan = {r.path: r for r in read_plan(data_root, "acme")}
    assert {p: plan[p].status for p in approved} == dict.fromkeys(approved, "loaded")
    with connect(company_db_path(data_root, "acme")) as con:
        load_runs = con.execute(
            "SELECT DISTINCT load_run_id FROM dataroom_files WHERE load_run_id IS NOT NULL"
        ).fetchall()
        source_runs = con.execute("SELECT DISTINCT run_id FROM source_files").fetchall()
    with connect(audit_db_path(data_root)) as con:
        runs = con.execute(
            "SELECT DISTINCT run_id FROM runs WHERE command = 'dataroom load'"
        ).fetchall()
    assert len(runs) == 1
    assert load_runs == runs and source_runs == runs
    before = counts(data_root)
    assert before == (2, 1)

    fin("dataroom", "scan", "acme", str(room))
    assert "nothing approved is waiting to load" in fin("dataroom", "load", "acme")
    assert counts(data_root) == before


def test_a_file_fin_cannot_name_loads_only_with_a_kind_the_user_approves(
    data_root: Path, room: Path
) -> None:
    notes = str(ids(data_root)["notes.xlsx"])
    assert "E819" in fin("dataroom", "approve", "acme", notes, code=2)
    for args in (
        ["--kind", "databook", "--reason", "a databook"],
        ["--kind", "payroll", "--reason", " "],
        ["--kind", "gst", "--reason", "a return"],
        ["--kind", "payroll", "--profile", "tally_trial_balance", "--reason", "staff"],
        ["--kind", "statements", "--profile", "gstr1", "--reason", "accounts"],
    ):
        assert "E819" in fin("dataroom", "propose", "acme", notes, *args, code=2)
    fin("dataroom", "propose", "acme", notes, "--kind", "trial_balance", "--reason", "balances")
    plan = fin("dataroom", "plan", "acme")
    assert "notes.xlsx: trial_balance, proposed by the assistant: balances" in plan
    assert "not approved" in plan
    fin("dataroom", "approve", "acme", notes)
    output = fin("dataroom", "load", "acme", code=1)
    assert "notes.xlsx: E807" in output
    (row,) = [r for r in read_plan(data_root, "acme") if r.path == "notes.xlsx"]
    assert row.status == "failed" and row.outcome is not None and row.outcome.startswith("E807")
    assert counts(data_root) == (0, 0)


def test_a_file_loaded_before_is_not_loaded_twice(data_root: Path, room: Path) -> None:
    tb = room / "books" / "b_trial_balance.xlsx"
    fin("ingest", "acme", str(tb), "--kind", "trial_balance")
    fin("dataroom", "approve", "acme", str(ids(data_root)["books/b_trial_balance.xlsx"]))
    assert "already loaded" in fin("dataroom", "load", "acme")
    (row,) = [r for r in read_plan(data_root, "acme") if r.path.endswith("b_trial_balance.xlsx")]
    assert row.status == "loaded"
    assert counts(data_root) == (1, 0)


def test_an_archive_or_a_loaded_row_cannot_be_approved_and_undo_takes_it_back(
    data_root: Path, room: Path
) -> None:
    assert "E819: no data-room plan row 99" in fin("dataroom", "approve", "acme", "99", code=2)
    tb = str(ids(data_root)["books/b_trial_balance.xlsx"])
    fin("dataroom", "approve", "acme", tb)
    assert "not approved" in fin("dataroom", "approve", "acme", tb, "--undo")
    assert "nothing approved is waiting to load" in fin("dataroom", "load", "acme")


def test_a_statements_file_held_for_its_unit_is_not_marked_loaded(
    data_root: Path, tmp_path: Path
) -> None:
    fin("init")
    room = tmp_path / "unitless"
    room.mkdir()
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Balance Sheet"
    for line in (["Balance Sheet"], ["Particulars", "Note", "31.03.2025", "31.03.2024"]):
        sheet.append(line)
    sheet.append(["Borrowings", "5", 1200.5, 1000])
    book.save(room / "bs.xlsx")
    fin("dataroom", "scan", "acme", str(room))
    file_id = str(ids(data_root)["bs.xlsx"])
    fin("dataroom", "propose", "acme", file_id, "--kind", "statements", "--reason", "accounts")
    fin("dataroom", "approve", "acme", file_id)

    assert "which unit?" in fin("dataroom", "load", "acme")

    (row,) = read_plan(data_root, "acme")
    assert (row.status, row.approved) == ("failed", True)
    assert row.outcome is not None and row.outcome.startswith("held: bs.xlsx: which unit?")
    assert counts(data_root) == (0, 0)


def test_deleting_a_document_sets_its_plan_row_back_to_planned(data_root: Path, room: Path) -> None:
    fin("dataroom", "approve", "acme", str(ids(data_root)["statements.pdf"]))
    fin("dataroom", "load", "acme")
    with connect(company_db_path(data_root, "acme")) as con:
        (doc_id,) = con.execute("SELECT doc_id FROM documents").fetchone() or (None,)

    fin("document", "delete", "acme", str(doc_id))

    plan = {r.path: r for r in read_plan(data_root, "acme")}
    row = plan["statements.pdf"]
    assert (row.status, row.approved, row.outcome) == ("planned", False, None)
    assert "nothing approved is waiting to load" in fin("dataroom", "load", "acme")
