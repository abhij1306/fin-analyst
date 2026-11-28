"""Golden evaluation (fin.evals) on a synthetic company: a golden set checked against the source
scores 100%, a planted wrong value fails, an unchecked value is not scored, `fin eval init` never
changes a person's lines, and the results bundle has no real names and no figures unless asked."""

import csv
import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fin.actor import USER
from fin.audit.errors import BundleNotAnonymous, GoldenSetInvalid, GoldenSetMissing
from fin.audit.logger import Run
from fin.cli import app
from fin.config import load_config, write_default_config
from fin.db.conn import connect, utcnow
from fin.db.schema import ensure_database
from fin.diligence import ledgers, passthrough, recon
from fin.diligence.sources import ingest_source
from fin.doctor import Check as DoctorCheck
from fin.evals import bundle
from fin.evals.golden import COLUMNS, golden_path
from fin.evals.scaffold import init_golden
from fin.evals.score import relative, run_eval
from fin.map.dictionary import load_seed
from fin.paths import audit_db_path, company_db_path, dictionary_db_path
from tests.register_fixtures import write_registers
from tests.tally_fixtures import CUSTOMERS, VENDORS, write_trial_balance
from tests.unit.test_diligence_recon import ACCOUNTS, FIGURES

STUB_PERIOD = "1-Apr-2025 to 30-Sep-2025"
# Values as a person reads them in the synthetic documents (fs.xlsx, tb.xlsx, stub.xlsx).
GOLDEN = [
    ("statement", "FY25", "standalone", "revenue", "", "10.00", "Lakhs", "fs.xlsx BS!C8", "yes"),
    ("statement", "FY25", "standalone", "cash", "", "2,50,000", "rupees", "fs.xlsx BS!C5", "yes"),
    ("statement", "FY25", "standalone", "other_expenses", "", "1", "", "", ""),  # a draft
    ("tb_ledger", "FY25", "", "", "Software Sales", "(9,00,000)", "rupees", "tb.xlsx TB!21", "yes"),
    ("tb_ledger", "FY25", "", "", CUSTOMERS[0], "200000", "rupees", "tb.xlsx TB!12", "yes"),
    ("tb_vs_statements", "FY25", "", "revenue", "", "-1,00,000", "rupees", "tb; fs", "yes"),
    ("stub", "FY26-YTD-SEP", "", "revenue", "", "9.00", "lakh", "stub.xlsx", "yes"),
]


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Company acme: audited FY25 statement figures, an engagement for FY25 with the stub
    FY26-YTD-SEP, both trial balances loaded and mapped, and tb_vs_statements run."""
    base = tmp_path_factory.mktemp("evals")
    root = base / "data"
    root.mkdir()
    write_default_config(root)
    ensure_database(audit_db_path(root), "audit")
    ensure_database(dictionary_db_path(root), "dictionary")
    load_seed(root, Run("init", {}, root))
    path = company_db_path(root, "acme")
    ensure_database(path, "company")
    with connect(path, write=True) as con:
        con.execute(
            "INSERT INTO documents (doc_id, file_name, sha256, version, fiscal_year, scope, "
            "status, archive_path, run_id, ingested_at) VALUES (1, 'fs.xlsx', 'x', 1, 'FY25', "
            "'standalone', 'loaded', 'x', 'r0', now())"
        )
        for raw_id, (statement, account, value) in enumerate(FIGURES, start=1):
            con.execute(
                "INSERT INTO raw_values (raw_id, doc_id, sheet, cell, statement, section, "
                "raw_label, raw_value_text, raw_unit, rupees_per_unit, run_id) "
                "VALUES (?, 1, 'BS', ?, ?, '', ?, '', '', 1, 'r0')",
                [raw_id, f"C{raw_id}", statement, account],
            )
            con.execute(
                "INSERT INTO facts (raw_id, doc_id, account, fiscal_year, period_end, scope, "
                "value, confirmed, run_id, period, period_months) VALUES (?, 1, ?, 'FY25', "
                "DATE '2025-03-31', 'standalone', ?, true, 'r0', 'FY25', 12)",
                [raw_id, account, value],
            )
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, stub, stub_start, stub_end, "
            "status, opened_run_id, opened_at) VALUES ('FY25', 100000, 'FY26-YTD-SEP', "
            "DATE '2025-04-01', DATE '2025-09-30', 'open', 'r0', ?)",
            [utcnow()],
        )
    config = load_config(root)
    for name, period in (("tb.xlsx", None), ("stub.xlsx", STUB_PERIOD)):
        file = write_trial_balance(base / name, **({"period": period} if period else {}))
        ingest_source(Run("t", {}, root), config, "acme", file, "trial_balance", None)
    proposals = [
        {"ledger": ledger, "account": account, "confidence": 0.9, "reason": "by name"}
        for ledger, account in ACCOUNTS.items()
    ]
    ledgers.propose(Run("t", {}, root), "acme", proposals)
    ids = [row["ledger_id"] for row in ledgers.list_ledgers(Run("t", {}, root), "acme")]
    ledgers.confirm(Run("t", {}, root), "acme", ids, actor=USER)
    recon.tb_vs_statements(Run("t", {}, root), "acme")
    return root


@pytest.fixture
def root(template: Path, tmp_path: Path) -> Path:
    return Path(shutil.copytree(template, tmp_path / "data"))


def run(root: Path) -> Run:
    return Run("test", {}, root)


def write_golden(root: Path, lines: list[tuple[str, ...]]) -> Path:
    path = golden_path(root, "acme")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(lines)
    return path


def outcomes(result: dict) -> list[str]:
    return [check["outcome"] for check in result["checks"]]


def test_a_golden_set_checked_against_the_source_scores_100_percent(root: Path) -> None:
    write_golden(root, GOLDEN)

    result = run_eval(run(root), "acme")

    assert outcomes(result) == ["pass", "pass", "unchecked", "pass", "pass", "pass", "pass"]
    s = result["summary"]
    assert (str(s["score_percent"]), s["scored"], s["unchecked"]) == ("100.0", 6, 1)
    assert result["report_md"].is_file()
    assert result["report_json"].is_file()
    assert result["counts"]["ledgers"] == {"confirmed": 10}


def test_a_planted_wrong_value_fails_and_an_unchecked_one_is_not_scored(root: Path) -> None:
    wrong = list(GOLDEN)
    wrong[0] = (*GOLDEN[0][:5], "10.01", *GOLDEN[0][6:])
    wrong[3] = (*GOLDEN[3][:5], "(9,00,001)", *GOLDEN[3][6:])
    wrong.append(("statement", "FY24", "standalone", "revenue", "", "5", "lakh", "p 4", "yes"))
    write_golden(root, wrong)

    result = run_eval(run(root), "acme")

    assert outcomes(result) == ["fail", "pass", "unchecked", "fail"] + ["pass"] * 3 + ["missing"]
    revenue = result["checks"][0]
    assert (revenue["expected"], revenue["fin"]) == (1001000, 1000000)
    assert str(revenue["relative_difference"]) == "-0.000999001"
    assert str(result["summary"]["score_percent"]) == "57.1"  # 4 of 7 scored


def test_a_golden_set_fin_cannot_read_is_e831_and_none_is_e830(root: Path) -> None:
    evaluation = run(root)
    with pytest.raises(GoldenSetMissing, match="fin eval init acme"):
        run_eval(evaluation, "acme")
    write_golden(
        root,
        [
            ("statement", "FY25", "group", "revenue", "", "ten", "lakh", "x", "yes"),
            ("statement", "FY25", "standalone", "sales", "", "", "", "", "maybe"),
            ("tb_ledger", "FY25", "", "", "Rent", "1", "rupees", "", "yes"),
            ("statement", "FY25", "standalone", "eps_basic", "", "2.5", "lakh", "p 3", "yes"),
        ],
    )
    with pytest.raises(GoldenSetInvalid) as error:
        run_eval(evaluation, "acme")
    message = str(error.value)
    for problem in (
        "line 2: scope 'group'",
        "line 2: expected 'ten' is not a figure",
        "line 3: checked is 'maybe'",
        "line 4: checked, but source is empty",
        "line 5: eps_basic is stored as printed (rupees_per_share): use unit rupees",
    ):
        assert problem in message


def test_init_lists_what_to_check_and_never_changes_a_persons_lines(root: Path) -> None:
    first = init_golden(run(root), "acme")
    rows = list(csv.DictReader(first["path"].open(encoding="utf-8")))
    revenue = next(r for r in rows if r["kind"] == "statement" and r["account"] == "revenue")
    assert (revenue["source"], revenue["expected"], revenue["checked"]) == ("fs.xlsx BS!C8", "", "")
    kinds = {r["kind"] for r in rows}
    assert kinds == {
        "statement",
        "tb_ledger",
        "tb_vs_statements",
        "stub",
        "adjusted_revenue",
        "nwc_year_end",
        "net_debt",
    }
    # the golden set stays in the data folder: it lists ledgers as filed (party ledgers too)
    assert CUSTOMERS[0] in first["path"].read_text(encoding="utf-8")

    revenue.update(expected="10.00", unit="lakh", checked="yes")
    with first["path"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    again = init_golden(run(root), "acme")

    assert (again["added"], again["kept"]) == (0, len(rows))
    kept = list(csv.DictReader(again["path"].open(encoding="utf-8")))
    assert kept == rows
    assert outcomes(run_eval(run(root), "acme")).count("pass") == 1


def test_diligence_checks_read_only_the_latest_engagement(root: Path, tmp_path: Path) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE engagement SET status = 'closed'")
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, status, opened_run_id, opened_at) "
            "VALUES ('FY25', 100000, 'open', 'r1', ?)",
            [utcnow()],
        )
    revised = write_trial_balance(tmp_path / "tb-revised.xlsx", plant=True)
    ingest_source(run(root), load_config(root), "acme", revised, "trial_balance", None)
    write_golden(root, GOLDEN)

    result = run_eval(run(root), "acme")

    assert outcomes(result)[3:] == ["pass", "pass", "missing", "missing"]
    assert result["checks"][5]["codes"] == ["not_reconciled", "tb_group_sum"]


def no_doctor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bundle, "run_checks", lambda root: [DoctorCheck("python", True, "3.12")])


def test_the_bundle_has_no_real_names_and_no_figures_unless_asked(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_doctor(monkeypatch)
    wrong = [*GOLDEN[:3], (*GOLDEN[3][:5], "(9,00,001)", *GOLDEN[3][6:]), *GOLDEN[4:]]
    wrong[0] = (*GOLDEN[0][:5], "10.01", *GOLDEN[0][6:])
    write_golden(root, wrong)

    md, js = bundle.make_bundle(run(root), None, with_figures=False)

    text = md.read_text(encoding="utf-8") + js.read_text(encoding="utf-8")
    for name in (*CUSTOMERS, *VENDORS, "acme", "tb.xlsx", "fs.xlsx", "Software Sales"):
        assert name.lower() not in text.lower()
    data = json.loads(js.read_text(encoding="utf-8"))
    [company] = data["companies"]
    assert company["company"] == "company-1"
    assert [
        (f["kind"], f["account"] or f["ledger"], f["source_kind"]) for f in company["failures"]
    ] == [
        ("statement", "revenue", "sheet_cell"),
        ("tb_ledger", "ledger-1", "sheet_row"),
    ]
    assert company["failures"][0]["relative_difference"] == "-0.10%"
    assert "expected" not in company["failures"][0]
    for figure in ("1001000", "1000000", "900001", "900000"):
        assert figure not in text

    _, with_figures = bundle.make_bundle(run(root), "acme", with_figures=True)
    [company] = json.loads(with_figures.read_text(encoding="utf-8"))["companies"]
    assert (company["failures"][0]["expected"], company["failures"][0]["fin"]) == (
        "1001000.00",
        "1000000.00",
    )


# a party ledger (a name: ledgers are no longer tokens) and an ordinary ledger name
@pytest.mark.parametrize("name", [CUSTOMERS[0], "Software Sales"])
def test_a_bundle_that_would_carry_a_name_is_not_written(
    root: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    no_doctor(monkeypatch)
    real = bundle.format_bundle
    monkeypatch.setattr(bundle, "format_bundle", lambda b: real(b) + name)

    attempt = run(root)
    with pytest.raises(BundleNotAnonymous):
        bundle.make_bundle(attempt, None, with_figures=False)
    assert not list((root / "evals").glob("bundle-*"))


def test_fin_eval_run_prints_the_score_and_the_report_path(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FIN_DATA_DIR", str(root))
    write_golden(root, GOLDEN)

    result = CliRunner().invoke(app, ["eval", "run", "acme"])

    assert result.exit_code == 0, result.output
    assert "acme: score 100.0%: 6 pass, 0 fail, 0 missing in fin, 1 unchecked" in result.output
    assert "eval-" in result.output


def test_pass_through_and_adjusted_figures_are_scored(root: Path, tmp_path: Path) -> None:
    config = load_config(root)
    registers = write_registers(tmp_path / "reg.xlsx")
    ingest_source(run(root), config, "acme", registers, "registers", None)
    with connect(company_db_path(root, "acme"), write=True) as con:
        [(token,)] = con.execute(
            "SELECT customer FROM register_lines WHERE invoice_no = 'INV-1001'"
        ).fetchall()
        con.execute(
            "INSERT INTO adjustments (engagement_id, period, scope, category, amount, "
            "revenue_effect, cost_effect, description, source_ref, status, created_run_id, "
            "created_at, status_run_id, status_at) VALUES (1, 'FY25', 'standalone', "
            "'normalisation', -190000, -280000, -90000, 'd', 'x', 'accepted', 'r', now(), 'r', "
            "now())"
        )
    passthrough.set_customers(run(root), "acme", [token], "email")
    lines = [
        ("core_revenue", "FY25", "", "", "", "80,000", "rupees", "reg.xlsx", "yes"),
        ("passthrough_revenue", "FY25", "", "", "", "2,80,000", "rupees", "reg.xlsx", "yes"),
        ("adjusted_revenue", "FY25", "standalone", "", "", "7.20", "lakh", "fs.xlsx; adj 1", "yes"),
    ]
    write_golden(root, lines)

    assert outcomes(run_eval(run(root), "acme")) == ["pass", "pass", "pass"]

    write_golden(root, [lines[0], (*lines[1][:5], "2,80,001", *lines[1][6:]), lines[2]])
    result = run_eval(run(root), "acme")
    assert outcomes(result) == ["pass", "fail", "pass"]
    assert result["counts"]["passthrough"] == {"customers": 1, "adjustments": {"accepted": 1}}
    scaffold = {c["kind"] for c in init_golden_rows(root)}
    assert {"core_revenue", "passthrough_revenue", "adjusted_revenue"} <= scaffold


def test_a_misread_digit_in_a_large_figure_does_not_show_as_no_difference() -> None:
    # 13,409.92 lakh expected, 13,409.29 read: 0.0047%, which four places or two decimals hide
    difference = relative(Decimal("1340929000"), Decimal("1340992000"))
    assert difference is not None
    assert difference != 0
    assert bundle._percent(difference) == "-0.0047%"
    assert bundle._percent(Decimal("-0.001")) == "-0.10%"
    assert bundle._percent(None) is None


def init_golden_rows(root: Path) -> list[dict[str, str]]:
    path = golden_path(root, "acme")
    path.unlink()
    init_golden(run(root), "acme")
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))
