"""SPEC-20 on the mock company (mock/, docs/specs/SPEC-20-forensic-counterparty.md): each
counterparty test flags exactly its plants (`mock.generator.data_counterparty`) and logs its near
misses; what a test cannot run without; the FY25 GSTR-2B against the purchase register; GSTR-2B
names masked; the reconciliation is not a gate input; period-end revenue and accruals."""

from collections import Counter
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from fin.actor import USER
from fin.audit.errors import Gstr2bLineInvalid
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.diligence.engagement import require_open
from fin.forensic import measures
from fin.paths import company_db_path
from fin.reconcile import run_new
from fin.reconcile.checks import required
from fin.reconcile.gstr2b import NAME
from fin.services import forensic as services
from mock.generator import books as mock_books
from mock.generator import data, statements, write
from mock.generator import data_counterparty as cp
from mock.generator import gstr2b as mock_gstr2b
from tests.unit import forensic_mock as fm

CO = fm.COMPANY


@pytest.fixture(scope="module")
def ran(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = fm.build(tmp_path_factory.mktemp("counterparty") / "data", registers=True)
    services.run_tests(root, CO, via="cli", actor=USER)
    return root


def counterparty_flags(root: Path) -> list[dict]:  # type: ignore[type-arg]
    rows = services.read_flags(fm.run(root), root, CO)
    return [f for f in rows if f["test"].startswith("cp_")]


def states(root: Path) -> dict[tuple[str, str], dict]:  # type: ignore[type-arg]
    return {(r["test"], r["period"]): r for r in services.read_status(fm.run(root), root, CO)}


def test_each_counterparty_test_flags_its_plants_and_logs_its_near_misses(ran: Path) -> None:
    found = {
        (f["test"], f["period"], f["voucher_no"], f["ledger"]) for f in counterparty_flags(ran)
    }

    assert found == {(t, p, n, ledger) for t, p, n, ledger, _ in cp.FLAGS}
    near = Counter((t, p) for t, p, _, _ in cp.NEAR)
    with connect(company_db_path(ran, CO)) as con:
        logged = con.execute(
            "SELECT test, period, near_misses FROM forensic_runs WHERE test LIKE 'cp_%'"
        ).fetchall()
    assert {(t, p): n for t, p, n in logged if n} == dict(near)
    assert not {n for _, _, n, _ in cp.NEAR} & {voucher for _, _, voucher, _ in found}


def test_a_register_flag_names_its_party_by_token_and_its_source_row(ran: Path) -> None:
    by_test = {f["test"]: f for f in counterparty_flags(ran) if f["ledger"] is None}
    names = [*cp.LEDGERS, cp.QUILLFEATHER, *cp.TWO_B_NAMES.values()]
    names += [c.ledger for c in data.CUSTOMERS.values()]

    new_vendor = by_test["cp_new_vendor"]
    assert new_vendor["party"].startswith("VEND-")
    assert new_vendor["source"].startswith("corvane_registers_fy23_to_fy26_h1.xlsx Purchase ")
    assert new_vendor["amount"] == Decimal("1200000.00")  # both Tessellate invoices
    assert by_test["cp_vendor_spike"]["amount"] == Decimal("2500000.00")  # January's total
    assert by_test["cp_credit_note"]["party"].startswith("CUST-")
    reasons = " ".join(f["reason"] for f in counterparty_flags(ran))
    assert not [name for name in names if name in reasons]


def test_what_a_test_cannot_run_without(ran: Path) -> None:
    rows = states(ran)

    assert rows[("cp_new_vendor", "FY23")]["text"].startswith("cannot run: needs a purchase")
    assert rows[("cp_vendor_spike", "FY23")]["text"] == "too few entries: 3 of 6 needed"
    assert rows[("cp_credit_note", data.STUB)]["text"] == (
        "cannot run: needs the next period's Ledger Vouchers (to find a credit note)"
    )


def test_without_registers_the_register_tests_cannot_run(tmp_path: Path) -> None:
    root = fm.build(tmp_path / "data", ("FY24", "FY25"))
    services.run_tests(root, CO, via="cli", actor=USER)
    rows = states(root)

    for test in ("cp_new_vendor", "cp_vendor_spike", "cp_under_limit", "cp_credit_note"):
        assert rows[(test, "FY25")]["outcome"] == "cannot_run", test
    assert rows[("cp_loan_no_trade", "FY25")]["text"].startswith("cannot run: needs a loaded")
    assert rows[("cp_setoff", "FY25")]["state"] == "ran"


def test_gstr2b_against_the_purchase_register(ran: Path) -> None:
    [result] = run_new(fm.run(ran), CO, NAME, "FY25")
    with connect(company_db_path(ran, CO)) as con:
        items = con.execute(
            "SELECT key, kind, difference, needs_query, note FROM recon_items WHERE recon_id = ?",
            [result["recon_id"]],
        ).fetchall()
        engagement = require_open(con, CO)
        gated = required(con, engagement)

    by_number = {
        key.split(" ")[-1]: (kind, diff, needs, note) for key, kind, diff, needs, note in items
    }
    planted = {number: kind for number, kind, _ in cp.TWO_B_LINES}
    assert {n: v[0] for n, v in by_number.items() if v[0] != "matched"} == {
        n: k for n, k in planted.items() if k != "matched"
    }
    assert by_number["QSD/25/X02"][1] == Decimal("-200000.00")  # GSTR-2B - register
    assert by_number["CSP/24-25/164"][2] and by_number["QSD/25/X02"][2]
    for number in ("TDL/24/0611", "TDL/24/0907"):
        assert by_number[number][0] == "matched"
        assert by_number[number][3].startswith("vendor names differ: GSTR-2B VEND-")
    assert NAME not in {name for name, _ in gated}  # not a gate G2 input


def test_gstr2b_names_are_masked(ran: Path) -> None:
    with connect(company_db_path(ran, CO)) as con:
        vendors = {v for (v,) in con.execute("SELECT DISTINCT vendor FROM gstr2b_lines").fetchall()}
        tokens = {t for (t,) in con.execute("SELECT token FROM mask_values").fetchall()}
        kept = con.execute(
            "SELECT count(*) FROM mask_values WHERE real_value = ?", [cp.TWO_B_NAMES[cp.TESSELLATE]]
        ).fetchone()

    assert vendors <= tokens and all(v.startswith("VEND-") for v in vendors)
    assert kept == (1,)


def nothing_loaded(root: Path, path: Path) -> bool:
    with connect(company_db_path(root, CO)) as con:
        found = con.execute(
            "SELECT count(*) FROM gstr2b_lines g JOIN source_files f USING (file_id) "
            "WHERE f.file_name = ?",
            [path.name],
        ).fetchone()
    return found == (0,)


def test_a_repeated_gstr2b_document_loads_nothing(ran: Path, tmp_path: Path) -> None:
    root = fm.copy(ran, tmp_path / "data")
    sheets = mock_gstr2b.gstr2b(mock_books.build())
    sheets[0].rows.append(list(sheets[0].rows[-1]))  # the same supplier and invoice twice
    path = tmp_path / "gstr2b_again.xlsx"
    write.xlsx(path, sheets)

    with pytest.raises(Gstr2bLineInvalid):
        fm.ingest_source(fm.run(root), fm.load_config(root), CO, path, "gst", "gstr2b")
    assert nothing_loaded(root, path)


def test_a_blank_taxable_value_loads_nothing(ran: Path, tmp_path: Path) -> None:
    root = fm.copy(ran, tmp_path / "data")
    sheets = mock_gstr2b.gstr2b(mock_books.build())
    sheets[0].rows[-1][8] = None  # Taxable Value
    path = tmp_path / "gstr2b_blank.xlsx"
    write.xlsx(path, sheets)

    with pytest.raises(Gstr2bLineInvalid, match="taxable value"):
        fm.ingest_source(fm.run(root), fm.load_config(root), CO, path, "gst", "gstr2b")
    assert nothing_loaded(root, path)


def test_a_blank_invoice_value_is_unknown_not_zero(ran: Path, tmp_path: Path) -> None:
    root = fm.copy(ran, tmp_path / "data")
    sheets = mock_gstr2b.gstr2b(mock_books.build())
    number = sheets[0].rows[-1][2]
    sheets[0].rows[-1][5] = None  # Invoice Value
    path = tmp_path / "gstr2b_no_value.xlsx"
    write.xlsx(path, sheets)
    fm.ingest_source(fm.run(root), fm.load_config(root), CO, path, "gst", "gstr2b")

    with connect(company_db_path(root, CO)) as con:
        found = con.execute(
            "SELECT g.invoice_value, g.taxable_value FROM gstr2b_lines g JOIN source_files f "
            "USING (file_id) WHERE f.file_name = ? AND g.invoice_no = ?",
            [path.name, number],
        ).fetchall()

    assert len(found) == 1 and found[0][0] is None and found[0][1] > 0


def test_period_end_revenue_is_the_registers_last_month_and_days(ran: Path) -> None:
    books = mock_books.build()
    start, end = data.PERIODS["FY25"]
    sales = [i for i in books.invoices if i.kind == "sales" and start <= i.when <= end]
    month = sum(i.amount_inr for i in sales if i.when >= end.replace(day=1))
    days = sum(i.amount_inr for i in sales if i.when > end - timedelta(days=5))
    with connect(company_db_path(ran, CO)) as con:
        rows = {r["period"]: r for r in measures.rows(con, ran, CO)}

    fy25 = rows["FY25"]
    assert fy25["period_end"] == end and fy25["last_days"] == 5
    assert fy25["revenue"] == sum(i.amount_inr for i in sales)
    assert (fy25["last_month_revenue"], fy25["last_days_revenue"]) == (month, days)
    assert rows[data.STUB]["period_end"] == data.PERIODS[data.STUB][1]
    assert "FY25 (audited, ends 2025-03-31)" in measures.lines([fy25])[0]


def test_accruals_are_profit_after_tax_less_operating_cash(tmp_path: Path) -> None:
    books = mock_books.build()
    path = company_db_path(tmp_path, "corvane")
    ensure_database(path, "company")
    want = {}
    with connect(path, write=True) as con:
        for n, period in enumerate(data.ANNUAL, start=1):
            figures = statements.printed(books, period)
            add_figures(
                con, n, period, data.PERIODS[period][1], 12, figures, ("pat", "cfo", "total_assets")
            )
            want[period] = Decimal(figures["pat"] - figures["cfo"])
        stub = statements.books_view(books, data.STUB)
        add_figures(con, 9, "FY26-H1", data.PERIODS[data.STUB][1], 6, stub, ("pat",))
        got = {
            p: (a, r)
            for p, a, r in con.execute(
                "SELECT period, accruals, accruals_to_assets FROM metrics_wide"
            ).fetchall()
        }

    assert {p: got[p][0] for p in data.ANNUAL} == want
    assert all(got[p][1] is not None for p in data.ANNUAL)
    assert got["FY26-H1"] == (None, None)  # no cash flow for the stub


def add_figures(con, doc_id, period, end, months, figures, accounts) -> None:  # type: ignore[no-untyped-def]
    con.execute(
        "INSERT INTO documents (doc_id, file_name, sha256, version, fiscal_year, scope, status, "
        "archive_path, run_id, ingested_at, period_end) VALUES (?, ?, ?, 1, ?, 'standalone', "
        "'loaded', 'a', 'r', now(), ?)",
        [doc_id, f"{period}.xlsx", f"sha{doc_id}", period[:4], end],
    )
    for n, account in enumerate(accounts, start=2):
        [(raw_id,)] = con.execute(
            "INSERT INTO raw_values (doc_id, sheet, cell, statement, section, raw_label, "
            "raw_value_text, raw_unit, rupees_per_unit, run_id) VALUES (?, 'FS', ?, "
            "'profit_and_loss', '', ?, ?, '(Rs.)', 1, 'r') RETURNING raw_id",
            [doc_id, f"B{n}", account, str(figures[account])],
        ).fetchall()
        con.execute(
            "INSERT INTO facts (raw_id, doc_id, account, fiscal_year, period_end, scope, value, "
            "confirmed, run_id, period, period_months) VALUES (?, ?, ?, ?, ?, 'standalone', ?, "
            "true, 'r', ?, ?)",
            [raw_id, doc_id, account, period[:4], end, figures[account], period, months],
        )
