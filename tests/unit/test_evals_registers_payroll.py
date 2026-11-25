"""Golden evaluation of registers and payroll (kinds register_total, payroll_total): a total a
person summed from the file passes, a planted wrong one fails, `fin eval init` lists each total
with its source, the counts show them and a bundle keeps the kind of total but no name."""

import csv
import json
from pathlib import Path

import pytest

from fin.audit.errors import GoldenSetInvalid
from fin.audit.logger import Run
from fin.config import load_config
from fin.diligence.payroll import confirm_unit
from fin.diligence.sources import ingest_source
from fin.evals.bundle import make_bundle
from fin.evals.golden import COLUMNS, golden_path, read_golden
from fin.evals.scaffold import init_golden
from fin.evals.score import run_eval
from fin.map.dictionary import load_accounts
from tests.engagement_root import engagement_root
from tests.payroll_fixtures import write_payroll
from tests.register_fixtures import write_registers

SALES, PURCHASE = 120000 + 80000 + 160000, 90000 + 40000
GROSS, CTC = 1200000 + 1800000 + 720000 + 2400000, 1380000 + 2040000 + 828000 + 2760000


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = engagement_root(tmp_path)
    config = load_config(root)
    for kind, path in (
        ("registers", write_registers(tmp_path / "reg.xlsx")),
        ("payroll", write_payroll(tmp_path / "payroll.xlsx")),
    ):
        ingest_source(Run("t", {}, root), config, "acme", path, kind, None)
    return root


def golden(root: Path, *lines: tuple[str, str, str]) -> None:
    """Checked lines (kind, ledger, expected) for the file period FY25."""
    path = golden_path(root, "acme")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for kind, ledger, expected in lines:
            writer.writerow([kind, "FY25", "", "", ledger, expected, "rupees", "f.xlsx", "yes"])


def outcomes(root: Path) -> list[str]:
    return [c["outcome"] for c in run_eval(Run("t", {}, root), "acme")["checks"]]


def test_totals_checked_against_the_files_pass(root: Path) -> None:
    golden(
        root,
        ("register_total", "sales", f"{SALES:,}.00"),
        ("register_total", "purchase", f"{PURCHASE:,}"),
        ("payroll_total", "gross_salary", f"{GROSS:,}"),
        ("payroll_total", "ctc", f"{CTC:,}"),
    )

    result = run_eval(Run("t", {}, root), "acme")

    assert [c["outcome"] for c in result["checks"]] == ["pass"] * 4
    assert result["counts"]["registers"] == {"purchase": 2, "sales": 3}
    assert result["counts"]["payroll"] == {"not confirmed": 1}


def test_a_wrong_total_fails_and_an_unloaded_period_is_missing(root: Path) -> None:
    golden(root, ("register_total", "sales", f"{SALES + 1:,}"), ("payroll_total", "ctc", "1"))
    assert outcomes(root) == ["fail", "fail"]

    path = golden_path(root, "acme")
    path.write_text(path.read_text(encoding="utf-8").replace("FY25", "FY24"), encoding="utf-8")
    assert outcomes(root) == ["missing", "missing"]


def test_a_ledger_that_is_not_a_total_is_refused(root: Path) -> None:
    golden(root, ("payroll_total", "net_pay", "1"))
    with pytest.raises(GoldenSetInvalid, match="gross_salary, ctc"):
        read_golden(root, "acme", load_accounts(root))


def test_init_lists_each_total_with_its_source(root: Path) -> None:
    result = init_golden(Run("t", {}, root), "acme")
    rows = list(csv.DictReader(result["path"].open(encoding="utf-8")))

    assert [(r["kind"], r["period"], r["ledger"], r["expected"], r["checked"]) for r in rows] == [
        ("register_total", "FY25", "sales", "", ""),
        ("register_total", "FY25", "purchase", "", ""),
        ("payroll_total", "FY25", "gross_salary", "", ""),
        ("payroll_total", "FY25", "ctc", "", ""),
    ]
    assert rows[0]["source"] == "reg.xlsx Sales Register, Amount in Rs column"
    assert rows[3]["source"] == "payroll.xlsx Employee Master, CTC column"


def test_counts_show_the_confirmed_unit_and_the_bundle_keeps_the_kind(root: Path) -> None:
    run = Run("t", {}, root)
    confirm_unit(run, "acme", "payroll.xlsx", "annual")
    golden(root, ("register_total", "sales", "1"))

    md, js = make_bundle(run, "acme", False)

    [company] = json.loads(js.read_text(encoding="utf-8"))["companies"]
    assert company["counts"]["payroll"] == {"annual": 1}
    assert [(f["kind"], f["ledger"]) for f in company["failures"]] == [("register_total", "sales")]
    assert "reg.xlsx" not in md.read_text(encoding="utf-8")
