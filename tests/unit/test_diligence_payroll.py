"""Payroll snapshot (fin.diligence.payroll): a synthetic Sparsh file loads with sheet!row lineage
and masked employee names and codes; amounts that look monthly are warned about; the unit is
the user's to confirm; gate G1 waits for it; what cannot load is refused with its code."""

import shutil
from pathlib import Path
from typing import Any

import pytest

from fin.audit.errors import (
    PayrollFileNotFound,
    PayrollRowInvalid,
    SourcePeriodInvalid,
)
from fin.audit.logger import Run
from fin.config import load_config, write_default_config
from fin.db.conn import connect, utcnow
from fin.db.schema import ensure_database
from fin.diligence import gates, payroll
from fin.diligence.sources import ingest_source
from fin.paths import company_db_path
from tests.payroll_fixtures import EMPLOYEE_CODES, EMPLOYEE_NAMES, write_payroll


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A data folder with company acme and an open engagement for FY25, made once."""
    root = tmp_path_factory.mktemp("template") / "data"
    root.mkdir()
    write_default_config(root)
    path = company_db_path(root, "acme")
    ensure_database(path, "company")
    with connect(path, write=True) as con:
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, status, opened_run_id, opened_at) "
            "VALUES ('FY25', 100000, 'open', 'r0', ?)",
            [utcnow()],
        )
    return root


@pytest.fixture
def root(template: Path, tmp_path: Path) -> Path:
    return Path(shutil.copytree(template, tmp_path / "data"))


def load(root: Path, path: Path) -> list[str]:
    run = Run("ingest", {}, root)
    return ingest_source(run, load_config(root), "acme", path, "payroll", None).lines


def confirm(root: Path, ref: str, unit: str) -> list[str]:
    return payroll.confirm_unit(Run("payroll confirm-unit", {}, root), "acme", ref, unit)


def rows(root: Path, sql: str) -> list[tuple[Any, ...]]:
    with connect(company_db_path(root, "acme")) as con:
        return con.execute(sql).fetchall()


def g1_payroll_lines(root: Path) -> list[dict[str, Any]]:
    with connect(company_db_path(root, "acme")) as con:
        ctx = gates.context(con, root, "acme")
        g1 = gates.evaluate(ctx)[0]
    return [line for line in g1["checklist"] if "payroll" in line["text"]]


def test_payroll_loads_masked_with_lineage(root: Path, tmp_path: Path) -> None:
    lines = load(root, write_payroll(tmp_path / "payroll.xlsx"))

    assert lines[0] == "loaded as source file_id 1 (payroll FY25, profile sparsh_payroll)"
    assert lines[1] == (
        "payroll FY25: 4 employees as on 2025-03-31, 8 names newly masked "
        "(amounts are read as annual rupees)"
    )
    assert not any("WARNING" in line for line in lines)
    assert "unit not confirmed" in lines[-1]
    stored = rows(
        root,
        'SELECT sheet, "row", snapshot_date::VARCHAR, employee_name, employee_code, department, '
        "joining_date::VARCHAR, resign_date::VARCHAR, gross_salary, ctc FROM payroll_lines "
        'ORDER BY "row"',
    )
    assert [r[:2] for r in stored] == [("Employee Master", n) for n in (4, 5, 6, 7)]  # no Total row
    assert stored[0][2:8] == (
        "2025-03-31",
        "EMP-0001",
        "ECODE-0001",
        "Technology",
        "2019-04-01",
        None,
    )
    assert stored[2][7] == "2024-12-31"
    assert [int(r[8]) for r in stored] == [1200000, 1800000, 720000, 2400000]
    everything = repr(rows(root, "SELECT * FROM payroll_lines"))
    everything += repr(rows(root, "SELECT * FROM source_files"))
    for value in (*EMPLOYEE_NAMES, *EMPLOYEE_CODES):
        assert value not in everything
    masked = rows(root, "SELECT kind, count(*) FROM mask_values GROUP BY kind ORDER BY kind")
    assert masked == [("employee_code", 4), ("employee_name", 4)]


def test_amounts_that_look_monthly_are_warned_about(root: Path, tmp_path: Path) -> None:
    lines = load(root, write_payroll(tmp_path / "payroll.xlsx", monthly=True))

    warning = next(line for line in lines if line.startswith("WARNING"))
    assert warning == (
        "WARNING: the amounts look monthly (median gross Rs 125,000 and CTC Rs 142,500; "
        "under Rs 150,000)"
    )
    assert "fin payroll confirm-unit acme payroll.xlsx --annual|--monthly" in lines[-1]


def test_unit_is_confirmed_by_the_user_and_gate_g1_waits_for_it(root: Path, tmp_path: Path) -> None:
    assert g1_payroll_lines(root) == []  # no payroll file loaded: no line
    load(root, write_payroll(tmp_path / "payroll.xlsx", monthly=True))

    [pending] = g1_payroll_lines(root)
    assert pending["ok"] is False
    assert pending["text"].startswith(
        "payroll payroll.xlsx (FY25): unit not confirmed (the amounts look monthly"
    )

    said_annual = confirm(root, "payroll.xlsx", "annual")
    [annual] = g1_payroll_lines(root)
    assert annual["ok"] is True
    assert annual["text"].startswith(
        "payroll payroll.xlsx (FY25): unit confirmed annual by the user"
    )
    assert said_annual[1].startswith("note: the amounts look monthly")

    said_monthly = confirm(root, "1", "monthly")  # by file_id; replaces the earlier answer
    [monthly] = g1_payroll_lines(root)
    assert monthly["ok"] is False
    assert "the user says the amounts are monthly" in monthly["text"]
    assert "does not convert" in said_monthly[1]
    assert rows(root, "SELECT file_id, unit FROM payroll_units") == [(1, "monthly")]


def test_confirm_unit_names_a_loaded_payroll_file(root: Path, tmp_path: Path) -> None:
    with pytest.raises(PayrollFileNotFound, match="Loaded payroll files: none"):
        confirm(root, "payroll.xlsx", "annual")
    load(root, write_payroll(tmp_path / "payroll.xlsx"))
    with pytest.raises(PayrollFileNotFound, match=r"file_id 1 payroll\.xlsx"):
        confirm(root, "other.xlsx", "annual")
    load(
        root,
        write_payroll(
            tmp_path / "later.xlsx", blank_code=False, bad_date=False, as_on="30-Mar-2025"
        ),
    )
    with pytest.raises(PayrollFileNotFound, match="a later snapshot superseded it"):
        confirm(root, "payroll.xlsx", "annual")
    assert rows(root, "SELECT file_id, status FROM source_files ORDER BY file_id") == [
        (1, "superseded"),
        (2, "loaded"),
    ]


def test_snapshot_outside_the_engagement_is_refused(root: Path, tmp_path: Path) -> None:
    with pytest.raises(SourcePeriodInvalid, match="snapshot date 2024-03-31 is in FY24"):
        load(root, write_payroll(tmp_path / "payroll.xlsx", as_on="31-Mar-2024"))
    assert rows(root, "SELECT count(*) FROM source_files") == [(0,)]


def test_unidentifiable_row_or_bad_date_loads_nothing(root: Path, tmp_path: Path) -> None:
    with pytest.raises(PayrollRowInvalid, match="no employee code"):
        load(root, write_payroll(tmp_path / "a.xlsx", blank_code=True))
    with pytest.raises(PayrollRowInvalid, match="'not a date' is not a date"):
        load(root, write_payroll(tmp_path / "b.xlsx", bad_date=True))
    assert rows(root, "SELECT count(*) FROM payroll_lines") == [(0,)]
    assert rows(root, "SELECT count(*) FROM source_files") == [(0,)]
    assert rows(root, "SELECT count(*) FROM mask_values") == [(0,)]
