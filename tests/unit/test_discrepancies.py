"""The discrepancies view (SPEC-15) on the synthetic set of tests/recon_fixtures.py with audited
FY25 figures planted against it: each difference at its level under the right parent, explained
plus unexplained equal to every parent's difference, a query's status on its rows, the masked
party tokens, and the latest run only."""

import json
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fin.audit.logger import Run
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.diligence import queries, recon
from fin.discrepancies.rows import discrepancy_rows
from fin.discrepancies.view import add_view
from fin.paths import audit_db_path, company_db_path
from fin.reconcile import items, run_new
from fin.statements import add_view as add_statement_view
from tests.recon_fixtures import write_set
from tests.tally_fixtures import CUSTOMERS
from tests.unit.test_reconcile_d4 import load, map_all, open_engagement, template  # noqa: F401

# The audited FY25 standalone figures: revenue, employee expense, purchases and other expenses
# differ from the trial balance; cash agrees with it (so the bank ledger's difference has no
# headline above it).
FIGURES = {
    "revenue": 1000000,
    "employee_expense": 7000000,
    "purchases_stock_in_trade": 100000,
    "other_expenses": 150000,
    "cash": 5350000,
}


def run(root: Path) -> Run:
    return Run("test", {}, root)


def figures(root: Path) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "INSERT INTO documents (doc_id, file_name, sha256, version, fiscal_year, scope, "
            "status, archive_path, run_id, ingested_at) VALUES (1, 'fs.xlsx', 'x', 1, 'FY25', "
            "'standalone', 'loaded', 'x', 'r0', now())"
        )
        for raw_id, (account, value) in enumerate(FIGURES.items(), start=1):
            statement = "balance_sheet" if account == "cash" else "profit_and_loss"
            con.execute(
                "INSERT INTO raw_values (raw_id, doc_id, sheet, cell, statement, section, "
                "raw_label, raw_value_text, raw_unit, rupees_per_unit, run_id) "
                "VALUES (?, 1, 'FS', ?, ?, '', ?, '', '', 1, 'r0')",
                [raw_id, f"C{raw_id}", statement, account],
            )
            con.execute(
                "INSERT INTO facts (raw_id, doc_id, account, fiscal_year, period_end, scope, "
                "value, confirmed, run_id, period, period_months) VALUES (?, 1, ?, 'FY25', "
                "DATE '2025-03-31', 'standalone', ?, true, 'r0', 'FY25', 12)",
                [raw_id, account, value],
            )


def make_root(template: Path, folder: Path) -> Path:  # noqa: F811
    """A copy of the template with the set loaded, every ledger mapped and the figures planted."""
    data = Path(shutil.copytree(template, folder / "data"))
    open_engagement(data, "FY24,FY25")
    files = write_set(folder)
    for name, kind in (
        ("tb24", "trial_balance"),
        ("tb25", "trial_balance"),
        ("lv", "ledger_vouchers"),
        ("registers", "registers"),
    ):
        load(data, files[name], kind)
    map_all(data)
    figures(data)
    return data


@pytest.fixture
def root(template: Path, tmp_path: Path) -> Path:  # noqa: F811
    return make_root(template, tmp_path)


def reconcile(root: Path) -> None:
    recon.tb_vs_statements(run(root), "acme", "FY25")
    for name in ("sales_register_vs_gl", "purchase_register_vs_gl", "gl_vs_tb"):
        run_new(run(root), "acme", name, "FY25")


def rows(root: Path, **given: Any) -> list[dict[str, Any]]:
    return discrepancy_rows(root, "acme", **given)["rows"]


def by_key(found: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(r["level"], r["key"]): r for r in found}


def parent_key(found: list[dict[str, Any]], row: dict[str, Any]) -> str | None:
    ids = {r["item_id"]: r["key"] for r in found}
    return ids.get(row["parent_id"])


def test_each_planted_difference_is_listed_once_under_its_parent(root: Path) -> None:
    reconcile(root)

    found = rows(root)

    assert {
        k: (parent_key(found, r), r["kind"], r["difference"]) for k, r in by_key(found).items()
    } == {
        ("headline", "revenue"): (None, "difference", Decimal("-186000.00")),
        ("headline", "employee_expense"): (None, "difference", Decimal("-100000.00")),
        ("headline", "purchases_stock_in_trade"): (None, "difference", Decimal("30000.00")),
        ("headline", "other_expenses"): (None, "difference", Decimal("-30000.00")),
        # no audited figure for these: the trial balance has them, the statements do not
        ("headline", "other_equity"): (None, "left_only", Decimal("-4836000.00")),
        ("headline", "trade_receivables"): (None, "left_only", Decimal("764000.00")),
        ("headline", "share_capital"): (None, "left_only", Decimal("500000.00")),
        ("headline", "trade_payables"): (None, "left_only", Decimal("200000.00")),
        # Ledger Vouchers - trial balance, debit - credit times the account's side
        ("account", "Rent"): ("other_expenses", "difference", Decimal("-20000.00")),
        # cash agrees with the statements: no headline above the bank ledger, so it is a root
        ("account", "Axis Bank Current Account"): (None, "difference", Decimal("-150000.00")),
        ("party", "CUST-0001"): ("revenue", "difference", Decimal("396000.00")),
        ("party", "CUST-0002"): ("revenue", "difference", Decimal("-100000.00")),
        ("party", "VEND-0001"): ("purchases_stock_in_trade", "difference", Decimal("120000.00")),
        ("transaction", "INV-2002"): ("CUST-0002", "difference", Decimal("50000.00")),
        ("transaction", "INV-2003"): ("CUST-0001", "left_only", Decimal("400000.00")),
        ("transaction", "INV-2004"): ("CUST-0002", "right_only", Decimal("-150000.00")),
        ("transaction", "INV-2005"): ("CUST-0001", "fx", Decimal("-4000.00")),
        ("transaction", "PINV-73"): ("VEND-0001", "left_only", Decimal("120000.00")),
    }
    assert [abs(r["difference"]) for r in found] == sorted(
        (abs(r["difference"]) for r in found), reverse=True
    )


def test_explained_plus_unexplained_is_the_difference_of_every_parent(root: Path) -> None:
    reconcile(root)
    with connect(company_db_path(root, "acme")) as con:
        add_statement_view(con, root)
        add_view(con)
        wrong = con.execute(
            "SELECT p.item_id FROM discrepancies p WHERE p.children > 0 AND ("
            "p.explained + p.unexplained <> p.difference OR p.explained <> "
            "(SELECT sum(c.difference) FROM discrepancies c WHERE c.parent_id = p.item_id))"
        ).fetchall()
        parents = con.execute(
            "SELECT key, explained, unexplained FROM discrepancies WHERE children > 0 ORDER BY key"
        ).fetchall()

    assert wrong == []
    assert parents == [
        ("CUST-0001", Decimal("396000.00"), Decimal("0.00")),
        ("CUST-0002", Decimal("-100000.00"), Decimal("0.00")),
        ("VEND-0001", Decimal("120000.00"), Decimal("0.00")),
        ("other_expenses", Decimal("-20000.00"), Decimal("-10000.00")),
        ("purchases_stock_in_trade", Decimal("120000.00"), Decimal("-90000.00")),
        ("revenue", Decimal("296000.00"), Decimal("-482000.00")),
    ]


def test_a_raised_query_shows_on_its_rows_and_closing_it_updates_them(root: Path) -> None:
    reconcile(root)
    found = by_key(rows(root))
    ids = [found[("transaction", k)]["item_id"] for k in ("INV-2003", "INV-2004")]
    assert [found[("transaction", k)]["queryable"] for k in ("INV-2003", "INV-2004")] == [
        True,
        True,
    ]

    query, _ = items.raise_from_items(run(root), "acme", ids, "Please explain INV-2003/2004.")
    raised = by_key(rows(root))
    for target in ("approved", "sent", "answered", "closed"):
        queries.set_status(run(root), "acme", [query["query_id"]], target, note="answered")
    closed = by_key(rows(root))

    state = lambda f: [  # noqa: E731
        (f[("transaction", k)]["query_id"], f[("transaction", k)]["query_status"],
         f[("transaction", k)]["queryable"]) for k in ("INV-2003", "INV-2004")
    ]  # fmt: skip
    assert state(raised) == [(query["query_id"], "draft", False)] * 2
    assert state(closed) == [(query["query_id"], "closed", False)] * 2


def test_party_and_transaction_rows_carry_the_register_tokens_not_names(root: Path) -> None:
    reconcile(root)

    found = rows(root)

    named = [r for r in found if r["level"] in ("party", "transaction")]
    parties = {r["key"] for r in named if r["level"] == "party"} | {
        r["party"] for r in named if r["level"] == "transaction"
    }
    assert parties == {"CUST-0001", "CUST-0002", "VEND-0001"}
    text = repr(named)
    assert not [name for name in CUSTOMERS if name in text]


def test_only_the_latest_run_of_each_reconciliation_is_listed(root: Path) -> None:
    reconcile(root)
    first = rows(root)
    reconcile(root)

    again = rows(root)

    assert len(again) == len(first)
    assert not {r["item_id"] for r in again} & {
        r["item_id"] for r in first if r["level"] != "headline"
    }
    assert rows(root, level="party", over=True) == [
        r for r in again if r["level"] == "party" and r["over_threshold"]
    ]


def bank_line(root: Path, key: str, currency: str | None, amount_inr: int | None) -> None:
    """A bank_vs_invoices run of FY25 with one unmatched receipt of 2,000 (in currency)."""
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "INSERT INTO recon_runs (recon_id, engagement_id, name, period, scope, left_file_id, "
            "threshold, input_hash, unmapped, run_id, created_at) VALUES (900, 1, "
            "'bank_vs_invoices', 'FY25', 'standalone', 1, 100000, 'h', 0, 'r', now())"
        )
        con.execute(
            "INSERT INTO recon_items (recon_id, item_no, kind, level, key, currency, amount_inr, "
            "left_amount, right_amount, difference) VALUES (900, 1, 'left_only', 'receipt', "
            "?, ?, ?, 2000, NULL, 2000)",
            [key, currency, amount_inr],
        )


def test_a_foreign_currency_line_without_a_rupee_value_has_no_difference(root: Path) -> None:
    reconcile(root)
    bank_line(root, "Bank!7", "USD", None)

    [line] = rows(root, level="transaction", over=False)[-1:]

    assert (line["key"], line["difference"], line["over_threshold"]) == ("Bank!7", None, None)
    assert (line["left_amount_shown"], line["difference_shown"]) == (None, None)


def test_a_rupee_bank_line_is_shown_in_the_display_unit(root: Path) -> None:
    reconcile(root)
    bank_line(root, "Bank!8", "INR", 2000)

    [line] = [r for r in rows(root, level="transaction") if r["key"] == "Bank!8"]

    assert (line["currency"], line["difference"]) == (None, Decimal("2000.00"))
    assert (line["left_amount_shown"], line["difference_shown"]) == (
        Decimal("0.0"),
        Decimal("0.0"),
    )


def reference(root: Path) -> None:
    """A banker's databook for FY25: revenue differs from the audited figure, employee expense
    agrees, and finance costs is a line fin holds no figure for."""
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "INSERT INTO reference_files (source, file_name, sha256, archive_path, run_id, "
            "added_at) VALUES ('Banker', 'databook.xlsx', 's', 'a', 'r', now())"
        )
        for line_id, (account, value) in enumerate(
            (("revenue", 1200000), ("employee_expense", 7000000), ("finance_costs", 5000)),
            start=1,
        ):
            con.execute(
                "INSERT INTO reference_lines (statement, section_key, label_key, section, label, "
                "status, target_kind, target, run_id, changed_at) VALUES ('profit_and_loss', '', "
                "?, '', ?, 'dictionary', 'account', ?, 'r', now())",
                [account, account.replace("_", " ").title(), account],
            )
            con.execute(
                "INSERT INTO reference_values (ref_file_id, line_id, sheet, cell, raw_value_text, "
                "raw_unit, rupees_per_unit, fiscal_year, period, period_months, period_end, scope, "
                "value, read_by) VALUES (1, ?, 'PL', ?, '', '', 1, 'FY25', 'FY25', 12, "
                "DATE '2025-03-31', 'standalone', ?, 'fin')",
                [line_id, f"D{line_id}", value],
            )


def test_a_reference_figure_that_differs_is_a_headline_row_and_goes_to_its_own_query(
    root: Path,
) -> None:
    from fin.audit.errors import UiActionInvalid
    from fin.ui.differences_screen import query_prompt

    reconcile(root)
    reference(root)

    found = rows(root, level="headline")
    [ref] = [r for r in found if r["reconciliation"] == "reference_comparison"]
    line = by_key(found)[("headline", "revenue")]

    assert {k: ref[k] for k in (
        "row_id", "item_id", "value_id", "parent_id", "key", "left_name", "right_name",
        "left_amount", "right_amount", "difference", "children", "unexplained", "left_source",
        "right_source", "query_id", "queryable",
    )} == {
        "row_id": "ref-1", "item_id": None, "value_id": 1, "parent_id": None, "key": "revenue",
        "left_name": "fin", "right_name": "Banker", "left_amount": Decimal("1000000.00"),
        "right_amount": Decimal("1200000.00"), "difference": Decimal("-200000.00"),
        "children": 0, "unexplained": Decimal("-200000.00"), "left_source": "fs.xlsx FS!C1",
        "right_source": "databook.xlsx PL!D1", "query_id": None, "queryable": True,
    }  # fmt: skip
    assert (ref["threshold"], ref["over_threshold"]) == (line["threshold"], True)
    assert "ref-1" in query_prompt(root, "acme", ["ref-1"])
    assert "fin reference query acme <n>" in query_prompt(root, "acme", ["ref-1"])
    invoice = by_key(rows(root))[("transaction", "INV-2003")]
    mixed = ["ref-1", str(invoice["item_id"])]
    with pytest.raises(UiActionInvalid, match="not both"):
        query_prompt(root, "acme", mixed)


def test_a_reference_query_links_its_figure_so_it_is_not_queried_twice(root: Path) -> None:
    from fin.audit.errors import ReferenceNotQueryable, UiActionInvalid
    from fin.reference.handoff import raise_query
    from fin.ui.differences_screen import query_prompt

    reconcile(root)
    reference(root)

    query = raise_query(run(root), "acme", 1, "Why does revenue differ?")

    [ref] = [r for r in rows(root) if r["row_id"] == "ref-1"]
    assert (ref["query_id"], ref["query_status"], ref["queryable"]) == (
        query["query_id"], "draft", False,
    )  # fmt: skip
    again_run = run(root)
    with pytest.raises(ReferenceNotQueryable, match=f"already in query #{query['query_id']}"):
        raise_query(again_run, "acme", 1, "Again?")
    with pytest.raises(UiActionInvalid, match="cannot be queried"):
        query_prompt(root, "acme", ["ref-1"])
    manual = [q for q in queries.list_queries(run(root), "acme") if q["origin"] == "manual"]
    assert manual == [query]  # nothing raised a second time

    queries.set_status(run(root), "acme", [query["query_id"]], "withdrawn", note="raised twice")

    [ref] = [r for r in rows(root) if r["row_id"] == "ref-1"]
    assert (ref["query_status"], ref["queryable"]) == ("withdrawn", True)
    again = raise_query(run(root), "acme", 1, "Why does revenue differ?")
    [ref] = [r for r in rows(root) if r["row_id"] == "ref-1"]
    assert (ref["query_id"], ref["queryable"]) == (again["query_id"], False)


def test_a_reference_difference_below_the_threshold_cannot_be_queried(root: Path) -> None:
    from fin.audit.errors import ReferenceNotQueryable
    from fin.reference.handoff import raise_query

    reconcile(root)
    reference(root)
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE reference_values SET value = 1000001 WHERE value_id = 1")

    [ref] = [r for r in rows(root) if r["row_id"] == "ref-1"]
    assert (ref["difference"], ref["over_threshold"], ref["queryable"]) == (
        Decimal("-1.00"), False, False,
    )  # fmt: skip
    below_run = run(root)
    with pytest.raises(ReferenceNotQueryable, match="below the query threshold"):
        raise_query(below_run, "acme", 1, "Why?")
    assert [q for q in queries.list_queries(run(root), "acme") if q["origin"] == "manual"] == []


def test_a_reference_difference_outside_the_engagement_periods_cannot_be_queried(
    root: Path,
) -> None:
    from fin.audit.errors import QueryPeriodInvalid
    from fin.reference.handoff import raise_query

    reconcile(root)
    reference(root)
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE engagement SET periods = 'FY24'")  # the FY25 figure is outside it

    [ref] = [r for r in rows(root) if r["row_id"] == "ref-1"]
    assert (ref["period"], ref["over_threshold"], ref["queryable"]) == ("FY25", True, False)
    outside_run = run(root)
    with pytest.raises(QueryPeriodInvalid, match="FY25 is not a period"):
        raise_query(outside_run, "acme", 1, "Why?")


def test_the_cli_lists_ranked_rows_and_refuses_an_unknown_level(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from fin.cli import app

    reconcile(root)
    ensure_database(audit_db_path(root), "audit")
    (root / "logs").mkdir()
    monkeypatch.setenv("FIN_DATA_DIR", str(root))

    shown = CliRunner().invoke(app, ["discrepancies", "acme", "--level", "party"])
    as_json = CliRunner().invoke(app, ["discrepancies", "acme", "--level", "party", "--json"])
    refused = CliRunner().invoke(app, ["discrepancies", "acme", "--level", "ledger"])

    assert shown.exit_code == 0, shown.output
    lines = shown.output.splitlines()
    assert lines[0] == "acme: 3 differences, ranked by amount (INR Mn)"
    assert lines[1].startswith("#") and " party FY25 CUST-0001 (under #" in lines[1]
    assert "register 0.9, books 0.5, difference 0.4" in lines[1]
    assert [r["key"] for r in json.loads(as_json.stdout)] == ["CUST-0001", "VEND-0001", "CUST-0002"]
    assert refused.exit_code == 2


def test_the_export_has_a_differences_sheet_with_document_text_as_text(root: Path) -> None:
    from openpyxl import load_workbook

    from fin.workbook import export_workbook

    reconcile(root)
    bank_line(root, "=1+1", None, None)

    sheet = load_workbook(export_workbook(root, "acme"))["Differences"]

    assert sheet["A1"].value == "acme: differences, ranked by amount (INR Mn)"
    assert [c.value for c in sheet[3]][:12] == [
        2, None, "headline", "FY25", "tb_vs_statements", "other_equity", "left_only",
        "trial balance", -4.8, "statements", None, -4.8,
    ]  # fmt: skip
    [formula] = [row for row in sheet.iter_rows(min_row=3) if row[5].value == "=1+1"]
    assert (formula[5].data_type, formula[11].value) == ("s", 0)
