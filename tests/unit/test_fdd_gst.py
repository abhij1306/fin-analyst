"""F6 GST and income tax (docs/fdd.md F6) on the synthetic set of tests/gst_fixtures.py: the GST
return summaries load as their returns, tax ledger classes go through review, the four GST
reconciliations find every planted difference and nothing else, gate G2 requires them, the golden
eval scores them, and the tax charge view reads the statements."""

import csv
import shutil
from pathlib import Path
from typing import Any

import pytest

from fin.audit.errors import GstReturnInvalid, LayoutUnknown, ReconCannotRun, TaxLedgerInvalid
from fin.audit.logger import Run
from fin.config import load_config, write_default_config
from fin.db.conn import connect, utcnow
from fin.db.rows import fetch_dicts
from fin.db.schema import SCHEMA_DIR, SCHEMA_FILES, ensure_database
from fin.diligence.sources import ingest_source
from fin.evals.golden import COLUMNS, golden_path
from fin.evals.scaffold import init_golden
from fin.evals.score import run_eval
from fin.extract.profiles import load_profile
from fin.fdd import tax_data, tax_ledgers
from fin.map.dictionary import load_seed
from fin.paths import company_db_path, dictionary_db_path
from fin.reconcile import checks, run_new
from tests.gst_fixtures import CLASSES, GSTR1, GSTR1_HEADER, write_gst_set, write_return


def run(root: Path) -> Run:
    return Run("test", {}, root)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("template") / "data"
    root.mkdir()
    write_default_config(root)
    ensure_database(dictionary_db_path(root), "dictionary")
    load_seed(root, Run("init", {}, root))
    ensure_database(company_db_path(root, "acme"), "company")
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, status, opened_run_id, opened_at) "
            "VALUES ('FY25', 100000, 'open', 'r0', ?)",
            [utcnow()],
        )
    return root


def load(root: Path, path: Path, kind: str, profile: str | None = None) -> list[str]:
    return ingest_source(run(root), load_config(root), "acme", path, kind, profile).lines


@pytest.fixture
def files(tmp_path: Path) -> dict[str, Path]:
    return write_gst_set(tmp_path)


@pytest.fixture
def loaded(template: Path, tmp_path: Path, files: dict[str, Path]) -> Path:
    data = Path(shutil.copytree(template, tmp_path / "data"))
    load(data, files["tb"], "trial_balance")
    load(data, files["lv"], "ledger_vouchers")
    load(data, files["registers"], "registers")
    load(data, files["gstr1"], "gst", "gstr1")
    load(data, files["gstr3b"], "gst", "gstr3b")
    return data


@pytest.fixture
def root(loaded: Path) -> Path:
    proposals = [
        {"ledger": k, "class": v, "confidence": 0.9, "reason": "by name"}
        for k, v in CLASSES.items()
    ]
    tax_ledgers.propose(run(loaded), "acme", proposals)
    ids = [r["tax_ledger_id"] for r in tax_ledgers.list_rows(run(loaded), "acme")]
    tax_ledgers.confirm(run(loaded), "acme", ids)
    return loaded


def recon(root: Path, name: str) -> dict[str, Any]:
    [result] = run_new(run(root), "acme", name, "FY25")
    return result


def odd(result: dict[str, Any]) -> dict[str, tuple[str, Any, bool]]:
    return {
        x["key"]: (x["kind"], x["difference"], x["needs_query"])
        for x in result["lines"]
        if x["kind"] != "matched"
    }


def query(root: Path, sql: str) -> list[tuple[Any, ...]]:
    with connect(company_db_path(root, "acme")) as con:
        return con.execute(sql).fetchall()


def test_gst_profiles_read_their_return() -> None:
    assert load_profile("gstr1").gst_return == "gstr1"
    assert load_profile("gstr3b").columns["itc claimed"] == "itc"


def test_returns_load_as_their_return_with_rows_and_missing_months(
    template: Path, tmp_path: Path, files: dict[str, Path]
) -> None:
    data = Path(shutil.copytree(template, tmp_path / "data"))

    lines = load(data, files["gstr3b"], "gst", "gstr3b")
    again = load(
        data,
        write_return(tmp_path / "g1.xlsx", "x", GSTR1_HEADER, GSTR1, "1-Apr-2024 to 31-Mar-2025"),
        "gst",
        "gstr1",
    )  # noqa: E501

    assert lines[0].startswith("loaded as source file_id 1 (gstr3b FY25, profile gstr3b)")
    assert lines[1] == "GSTR-3B FY25: 3 months"
    assert lines[2].startswith("9 months of the period have no row")
    assert again[0].startswith("loaded as source file_id 2 (gstr1 FY25")
    rows = query(
        data,
        "SELECT return_type, strftime(month, '%Y-%m'), \"row\", taxable_value, itc, rcm_liability "
        "FROM gst_returns ORDER BY return_type DESC, month",
    )
    assert rows[:3] == [
        ("gstr3b", "2024-04", 5, 0, 0, 0),
        ("gstr3b", "2024-05", 6, 1110000, 36000, 18000),
        ("gstr3b", "2024-06", 7, 450000, 0, 0),
    ]
    assert rows[3] == ("gstr1", "2024-04", 5, 0, None, None)


def test_a_later_return_supersedes_the_same_return_only(loaded: Path, tmp_path: Path) -> None:
    newer = write_return(
        tmp_path / "gstr1b.xlsx", "GSTR-1", GSTR1_HEADER, GSTR1[:2], "1-Apr-2024 to 31-Mar-2025"
    )

    lines = load(loaded, newer, "gst", "gstr1")

    assert "supersedes file_id 4" in lines[0]
    sql = "SELECT kind, status FROM source_files WHERE kind LIKE 'gstr%' ORDER BY file_id"
    assert query(loaded, sql) == [
        ("gstr1", "superseded"),
        ("gstr3b", "loaded"),
        ("gstr1", "loaded"),
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([("Sep 2024", 1, 0, 0, 0, 0)], "'Sep 2024' is not a month"),
        ([("Apr-2025", 1, 0, 0, 0, 0)], "Apr-2025 is outside the file's period FY25"),
        ([GSTR1[0], GSTR1[0]], "Apr-2024 is also on row 5"),
        ([], "no month rows"),
    ],
)
def test_a_bad_return_loads_nothing(
    template: Path, tmp_path: Path, rows: list[tuple[object, ...]], message: str
) -> None:
    data = Path(shutil.copytree(template, tmp_path / "data"))
    path = write_return(tmp_path / "bad.xlsx", "x", GSTR1_HEADER, rows, "1-Apr-2024 to 31-Mar-2025")

    with pytest.raises(GstReturnInvalid, match=message):
        load(data, path, "gst", "gstr1")

    assert query(data, "SELECT count(*) FROM source_files") == [(0,)]


def test_a_gstr3b_profile_needs_the_3b_columns(tmp_path: Path, monkeypatch: Any) -> None:
    text = (SCHEMA_DIR.parents[3] / "templates" / "profiles" / "gstr3b.toml").read_text()
    (tmp_path / "bad3b.toml").write_text(text.replace('"ITC Claimed" = "itc"\n', ""))
    monkeypatch.setattr("fin.extract.profiles.PROFILES_DIR", tmp_path)

    with pytest.raises(LayoutUnknown, match="no header for itc"):
        load_profile("bad3b")


def test_tax_ledger_proposals_need_review(loaded: Path) -> None:
    r = run(loaded)
    tax_ledgers.propose(
        r,
        "acme",
        [{"ledger": "Output CGST", "class": "gst_output", "confidence": 0.5, "reason": "name"}],
    )  # noqa: E501
    [row] = tax_ledgers.list_rows(r, "acme", open_only=True)
    assert row["status"] == "proposed" and row["group_name"] == "Duties & Taxes"
    assert query(loaded, "SELECT count(*) FROM tax_ledger_balances") == [(0,)]

    [done] = tax_ledgers.confirm(r, "acme", [row["tax_ledger_id"]], "gst_input")
    assert (done["status"], done["tax_class"]) == ("confirmed", "gst_input")
    with pytest.raises(TaxLedgerInvalid, match="is confirmed as gst_input"):
        tax_ledgers.propose(
            r, "acme", [{"ledger": "Output CGST", "class": "rcm", "confidence": 1, "reason": ""}]
        )  # noqa: E501
    [rejected] = tax_ledgers.reject(r, "acme", [row["tax_ledger_id"]])
    assert rejected["status"] == "rejected"
    with pytest.raises(TaxLedgerInvalid, match="has no proposed class"):
        tax_ledgers.confirm(r, "acme", [row["tax_ledger_id"]])
    assert any(x["ledger"] == "Output CGST" for x in tax_ledgers.unclassified(r, "acme"))


@pytest.mark.parametrize(
    ("proposal", "message"),
    [
        ({"ledger": "Nope", "class": "tds", "confidence": 1, "reason": ""}, "not a ledger of a"),
        ({"ledger": "TDS Payable", "class": "vat", "confidence": 1, "reason": ""}, "class 'vat'"),
        ({"ledger": "TDS Payable", "class": "tds", "confidence": 2, "reason": ""}, "from 0 to 1"),
        ({"ledger": "TDS Payable"}, "expected an object"),
    ],
)
def test_a_bad_tax_proposal_stores_nothing(
    loaded: Path, proposal: dict[str, Any], message: str
) -> None:
    good = {"ledger": "RCM Payable", "class": "rcm", "confidence": 1, "reason": ""}

    with pytest.raises(TaxLedgerInvalid, match=message):
        tax_ledgers.propose(run(loaded), "acme", [good, proposal])

    assert tax_ledgers.list_rows(run(loaded), "acme") == []
    with pytest.raises(TaxLedgerInvalid, match="no tax ledger with id 7"):
        tax_ledgers.reject(run(loaded), "acme", [7])


def test_gstr1_vs_sales_register(root: Path) -> None:
    result = recon(root, "gstr1_vs_sales_register")

    assert odd(result) == {"2024-06 taxable_value": ("left_only", 500000, True)}
    may = next(x for x in result["lines"] if x["key"] == "2024-05 taxable_value")
    assert may["left_source"] == "gstr1.xlsx Summary!6"
    assert may["right_source"].startswith("registers.xlsx Sales Register!")


def test_gstr3b_vs_gstr1(root: Path) -> None:
    result = recon(root, "gstr3b_vs_gstr1")

    assert odd(result) == {
        "2024-06 output_tax": ("difference", -9000, False),
        "2024-06 taxable_value": ("difference", -50000, False),
    }
    assert len(result["lines"]) == 6


def test_gstr3b_vs_books_counts_tax_booked_not_set_offs(root: Path) -> None:
    result = recon(root, "gstr3b_vs_books")

    assert odd(result) == {"2024-06 output_tax": ("difference", -150000, True)}
    june = next(x for x in result["lines"] if x["key"] == "2024-06 output_tax")
    assert june["right_source"].startswith("lv.xlsx Ledger Vouchers!")


def test_rcm_ties(root: Path) -> None:
    assert odd(recon(root, "rcm")) == {}


def test_books_reconciliations_need_confirmed_tax_ledgers(loaded: Path) -> None:
    with pytest.raises(ReconCannotRun, match="no ledger is confirmed as gst_output"):
        recon(loaded, "gstr3b_vs_books")
    with pytest.raises(ReconCannotRun, match="no ledger is confirmed as rcm"):
        recon(loaded, "rcm")


def test_gate_g2_requires_the_gst_reconciliations(root: Path) -> None:
    with connect(company_db_path(root, "acme")) as con:
        [engagement] = fetch_dicts(con, "SELECT * FROM engagement")
        required = checks.required(con, engagement)

    assert {n for n, _ in required} >= {
        "gstr1_vs_sales_register", "gstr3b_vs_gstr1", "gstr3b_vs_books", "rcm"
    }  # fmt: skip


def test_report_data_and_tax_ledger_balances(root: Path) -> None:
    recon(root, "gstr3b_vs_books")
    with connect(company_db_path(root, "acme")) as con:
        data = tax_data.collect(con)

    assert [(r["return_type"], r["months"], r["taxable_value"]) for r in data["returns"]] == [
        ("gstr1", 3, 1610000),
        ("gstr3b", 3, 1560000),
    ]
    [books] = data["reconciliations"]
    assert (books["name"], books["differences"], books["at_or_above_threshold"]) == (
        "gstr3b_vs_books", 1, 1
    )  # fmt: skip
    balances = {(r["tax_class"], r["ledger"]): r["balance"] for r in data["tax_ledgers"]}
    assert balances[("mat_credit", "MAT Credit Entitlement")] == 25000
    assert balances[("tds", "TDS Payable")] == -12000
    assert data["tax_ledgers_waiting"] == 0


def test_golden_eval_scores_gst_lines(root: Path) -> None:
    recon(root, "gstr3b_vs_books")
    r = run(root)
    init_golden(r, "acme")
    path = golden_path(root, "acme")
    with path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    wanted = {
        ("gst_return", "gstr3b 2024-05 itc"): "36000",
        ("gst_return", "gstr1 2024-06 taxable_value"): "4,00,000",  # planted wrong
        ("gstr3b_vs_books", "2024-06 output_tax"): "(150000)",
    }
    for row in rows:
        if (row["kind"], row["ledger"]) in wanted:
            row.update(expected=wanted[(row["kind"], row["ledger"])], checked="yes")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[*COLUMNS, "note"])
        writer.writeheader()
        writer.writerows(rows)

    report = run_eval(r, "acme")
    outcomes = {
        (c["kind"], c["ledger"]): c["outcome"]
        for c in report["checks"]
        if c["outcome"] != "unchecked"
    }  # noqa: E501
    assert outcomes == {
        ("gst_return", "gstr3b 2024-05 itc"): "pass",
        ("gst_return", "gstr1 2024-06 taxable_value"): "fail",
        ("gstr3b_vs_books", "2024-06 output_tax"): "pass",
    }


def test_tax_charge_view() -> None:
    texts = [(SCHEMA_DIR / name).read_text(encoding="utf-8") for name in SCHEMA_FILES["company"]]
    with connect(Path(":memory:"), write=True) as con:
        for text in texts:
            con.execute(text)
        con.execute(
            "CREATE OR REPLACE VIEW current_facts AS SELECT * FROM (VALUES "
            "('pbt', 'FY24', 'standalone', 400.00), ('tax_expense', 'FY24', 'standalone', 100.00), "
            "('deferred_tax_liabilities', 'FY24', 'standalone', 30.00), "
            "('pbt', 'FY25', 'standalone', 1000.00), "
            "('tax_expense', 'FY25', 'standalone', 240.00), "
            "('deferred_tax', 'FY25', 'standalone', 10.00), "
            "('deferred_tax_assets', 'FY25', 'standalone', 5.00), "
            "('deferred_tax_liabilities', 'FY25', 'standalone', 45.00), "
            "('pbt', 'FY25-Q1', 'standalone', 9.00)"
            ") AS t(account, period, scope, value), (SELECT 12 AS period_months)"
        )
        rows = con.execute(
            "SELECT period, pbt, current_tax, deferred_tax, total_tax, effective_tax_rate, "
            "net_deferred_tax, deferred_tax_movement, mat_credit FROM tax_charge"
        ).fetchall()

    assert [
        tuple(float(v) if v is not None and not isinstance(v, str) else v for v in r) for r in rows
    ] == [  # noqa: E501
        ("FY24", 400.0, 100.0, None, 100.0, 0.25, -30.0, None, None),
        ("FY25", 1000.0, 240.0, 10.0, 250.0, 0.25, -40.0, -10.0, None),
    ]
