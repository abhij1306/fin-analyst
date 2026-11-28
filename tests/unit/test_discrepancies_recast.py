"""The recast against the books in the differences list (SPEC-17 item 5): per period, the
recognition difference (revenue in the books - the recast revenue) is one headline row of
`discrepancies`, with the recast's invoices under it, on the synthetic set of
tests/unit/test_discrepancies.py (sales lines billed in FY25 for service from May 2024 to April
2025, so part of each is recast into FY26)."""

from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from fin.db.conn import connect
from fin.discrepancies.rows import discrepancy_rows, format_rows
from fin.discrepancies.sheet import COLUMNS
from fin.discrepancies.view import RECOGNITION, add_view
from fin.paths import company_db_path
from fin.reconcile.recast_run import run_recast
from fin.statements import add_view as add_statement_view
from fin.workbook import export_workbook
from tests.unit.test_discrepancies import reconcile, root, rows, run  # noqa: F401
from tests.unit.test_reconcile_d4 import template  # noqa: F401

# Revenue in the books for FY25: the sales register by invoice date (Amount in Rs).
BOOKS_SQL = """
SELECT sum(r.amount_inr) FROM register_lines r JOIN source_files f USING (file_id)
WHERE f.status = 'loaded' AND r.kind = 'sales'
    AND r.invoice_date BETWEEN DATE '2024-04-01' AND DATE '2025-03-31'
"""
# The recast revenue of FY25 as `fin recast run` stored it (left of its period line).
RECAST_SQL = """
SELECT i.left_amount FROM recon_items i JOIN recon_runs r USING (recon_id)
WHERE r.name = 'service_recast' AND r.period = 'FY25' AND i.level = 'period'
"""


def recast_rows(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in found if r["reconciliation"] == "service_recast"]


def test_the_recognition_difference_is_books_minus_recast_revenue(root: Path) -> None:  # noqa: F811
    reconcile(root)
    run_recast(run(root), "acme", "FY25")
    with connect(company_db_path(root, "acme")) as con:
        add_statement_view(con, root)
        add_view(con)
        books = con.execute(BOOKS_SQL).fetchone()[0]
        recast = con.execute(RECAST_SQL).fetchone()[0]
        head = con.execute(
            "SELECT level, key, label, left_name, left_amount, right_name, right_amount, "
            "difference, parent_id FROM discrepancies "
            "WHERE reconciliation = 'service_recast' AND level = 'headline'"
        ).fetchall()
        check = con.execute(
            f"SELECT d.difference = ({BOOKS_SQL}) - ({RECAST_SQL}) FROM discrepancies d "
            "WHERE d.reconciliation = 'service_recast' AND d.level = 'headline'"
        ).fetchone()[0]

    assert books == Decimal("1110000.00")
    assert recast < books  # part of the service falls in FY26
    assert head == [
        ("headline", "revenue", RECOGNITION, "books", books, "recast", recast, books - recast,
         None),
    ]  # fmt: skip
    assert check is True


def test_the_invoices_behind_it_are_its_children_and_explain_it(root: Path) -> None:  # noqa: F811
    reconcile(root)
    run_recast(run(root), "acme", "FY25")

    found = recast_rows(rows(root))
    [head] = [r for r in found if r["level"] == "headline"]
    invoices = [r for r in found if r["level"] == "transaction"]

    assert sorted(r["key"] for r in invoices) == ["INV-2001", "INV-2002", "INV-2003", "INV-2005"]
    assert {r["parent_id"] for r in invoices} == {head["item_id"]}
    assert all(r["label"] == RECOGNITION and r["left_name"] == "books" for r in invoices)
    assert all(r["left_amount"] - r["right_amount"] == r["difference"] for r in invoices)
    assert head["children"] == 4
    assert head["explained"] == sum(r["difference"] for r in invoices)
    assert head["unexplained"] == Decimal("0.00")
    # the month roll-ups are not rows, and nothing here can be queried (the recast becomes an
    # adjustment with evidence)
    assert len(found) == 5
    assert not any(r["queryable"] for r in found)


def test_only_the_latest_recast_run_is_listed(root: Path) -> None:  # noqa: F811
    reconcile(root)
    run_recast(run(root), "acme", "FY25")
    run_recast(run(root), "acme", "FY25")

    found = recast_rows(rows(root))

    assert [r["level"] for r in found].count("headline") == 1
    assert len(found) == 5


def test_the_trial_balance_headline_keeps_its_own_children(root: Path) -> None:  # noqa: F811
    reconcile(root)
    before = {r["row_id"]: r["parent_id"] for r in rows(root)}
    run_recast(run(root), "acme", "FY25")

    after = {r["row_id"]: r["parent_id"] for r in rows(root) if r["row_id"] in before}

    assert after == before


def test_it_is_labelled_in_the_text_and_the_sheet(root: Path) -> None:  # noqa: F811
    reconcile(root)
    run_recast(run(root), "acme", "FY25")
    result = discrepancy_rows(root, "acme")
    display, found = result["display"], recast_rows(result["rows"])
    [head] = [r for r in found if r["level"] == "headline"]

    text = format_rows("acme", {"rows": found, "display": display})
    sheet = load_workbook(export_workbook(root, "acme"))["Differences"]
    column = {key: n for n, (_, key) in enumerate(COLUMNS)}
    [row] = [
        [c.value for c in r]
        for r in sheet.iter_rows(min_row=3)
        if (r[column["kind"]].value, r[column["level"]].value) == (RECOGNITION, "headline")
    ]

    assert f"headline FY25 revenue: {RECOGNITION}, books " in text
    assert (row[column["level"]], row[column["period"]]) == ("headline", head["period"])
    assert row[column["left_name"]] == "books"
    assert row[column["right_name"]] == "recast"
