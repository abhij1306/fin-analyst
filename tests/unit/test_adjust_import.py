"""SPEC-09: `fin adjust import` proposes one adjustment per row of a walk sheet, all or none, and
the adjusted P&L view applies only accepted adjustments."""

from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fin.actor import USER
from fin.audit.logger import Run
from fin.cli import app
from fin.db.conn import connect
from fin.diligence import adjust_import, adjusted, adjustments
from fin.paths import audit_db_path, company_db_path
from fin.services import diligence as services
from tests.diligence_ui_fixtures import build_root, write_walk

runner = CliRunner()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = build_root(tmp_path)
    opened = services.open_engagement(root, "acme", services.Terms(["FY25"]), via="cli", actor=USER)
    assert opened.ok, opened.lines
    return root


def rows(root: Path) -> list[tuple[object, ...]]:
    with connect(company_db_path(root, "acme")) as con:
        return con.execute(
            "SELECT adj_id, status, period, scope, category, account, amount, revenue_effect, "
            "cost_effect, description, source_ref FROM adjustments ORDER BY adj_id"
        ).fetchall()


def adjustment_events(root: Path) -> int:
    with connect(audit_db_path(root)) as con:
        [(n,)] = con.execute(
            "SELECT count(*) FROM diligence_events WHERE kind = 'adjustment'"
        ).fetchall()
    return n


def test_each_row_becomes_a_proposed_adjustment_with_the_file_and_cell_as_evidence(
    root: Path, tmp_path: Path
) -> None:
    walk = write_walk(tmp_path / "walk.xlsx")
    result = services.import_adjustments(root, "acme", walk, None, via="cli", actor=USER)
    assert result.ok, result.lines
    assert result.lines[-1] == "acme: 2 adjustments proposed from walk.xlsx"
    assert rows(root) == [
        (1, "proposed", "FY25", "standalone", "normalisation", "other_expenses",
         Decimal("100000.00"), Decimal("0.00"), Decimal("-100000.00"), "One-off legal fees",
         "walk.xlsx Walk!G2; Notes p. 12"),
        (2, "proposed", "FY25", "standalone", "management", "revenue", Decimal("-200000.00"),
         Decimal("-200000.00"), Decimal("0.00"), "Revenue booked ahead of delivery",
         "walk.xlsx Walk!G3"),
    ]  # fmt: skip
    assert adjustment_events(root) == 2
    assert len(list((root / "archive" / "acme").glob("*.xlsx"))) == 1


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (["FY25", "standalone", "bogus", None, None, -5, "x", None],
         "row 3 (walk.xlsx Walk!G3): E804 category 'bogus' is not in config.toml"),
        (["FY25", "standalone", "management", None, "ten", None, "x", None],
         "row 3 (walk.xlsx Walk!G3): revenue effect: 'ten' is not an amount in rupees"),
        (["FY25", "group", "management", None, 1, None, "x", None],
         "row 3 (walk.xlsx Walk!G3): scope 'group' is not one of standalone, consolidated"),
        (["FY24", "standalone", "management", None, 1, None, "x", None],
         "row 3 (walk.xlsx Walk!G3): E804 period FY24 is not in the engagement (FY25)"),
        (["FY25", "standalone", "management", None, None, None, "x", None],
         "row 3 (walk.xlsx Walk!G3): give a revenue effect or a cost effect"),
        (["FY25", "standalone", "management", "no_such", 1, None, "x", None],
         "row 3 (walk.xlsx Walk!G3): E804 account 'no_such' is not in the dictionary"),
    ],
)  # fmt: skip
def test_one_bad_row_imports_nothing_and_names_the_row(
    root: Path, tmp_path: Path, bad: list[object], message: str
) -> None:
    from tests.diligence_ui_fixtures import ROWS

    walk = write_walk(tmp_path / "walk.xlsx", [ROWS[0], bad])
    result = services.import_adjustments(root, "acme", walk, None, via="cli", actor=USER)
    assert (result.ok, result.code) == (False, "E847")
    assert result.lines[-1].startswith(message), result.lines
    assert result.lines[-1].endswith("nothing imported")
    assert rows(root) == []
    assert adjustment_events(root) == 0


def test_a_file_fin_cannot_use_is_refused(root: Path, tmp_path: Path) -> None:
    extra = tmp_path / "extra.csv"
    extra.write_text("Period,Scope,Category,Amount,Description\n", encoding="utf-8")
    with pytest.raises(Exception) as caught:
        adjust_import.read_rows(extra)
    assert caught.value.code == "E847"  # type: ignore[attr-defined]
    assert "unknown column 'Amount'" in str(caught.value)
    walk = write_walk(tmp_path / "walk.xlsx")
    with pytest.raises(Exception) as missing_sheet:
        adjust_import.read_rows(walk, "Other")
    assert "walk.xlsx has no sheet 'Other' (sheets: Walk)" in str(missing_sheet.value)


def test_the_cli_imports_a_csv_and_prints_each_adjustment(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDECODE", raising=False)
    walk = write_walk(tmp_path / "walk.csv")
    ran = runner.invoke(
        app, ["adjust", "import", "acme", str(walk)], env={"FIN_DATA_DIR": str(root)}
    )
    assert ran.exit_code == 0, ran.output
    assert "#1 proposed FY25 standalone normalisation other_expenses ₹100,000.00" in ran.stdout
    assert "    evidence: source walk.csv G2; Notes p. 12" in ran.stdout
    assert ran.stdout.endswith("acme: 2 adjustments proposed from walk.csv\n")
    again = runner.invoke(
        app, ["adjust", "import", "acme", str(walk), "--sheet", "Walk"],
        env={"FIN_DATA_DIR": str(root)},
    )  # fmt: skip
    assert again.exit_code != 0
    assert again.stderr.startswith("E847: walk.csv is a CSV file: it has no sheets")


def test_adjusted_pl_totals_are_reported_plus_accepted_adjustments_in_sql(
    root: Path, tmp_path: Path
) -> None:
    services.import_adjustments(
        root, "acme", write_walk(tmp_path / "walk.xlsx"), None, via="cli", actor=USER
    )
    with connect(company_db_path(root, "acme"), write=True) as con:  # gates are tested elsewhere
        con.execute("UPDATE adjustments SET status = 'accepted' WHERE adj_id = 1")
    adjustments.propose(
        Run("adjust propose", {}, root), "acme", period="FY25", scope="standalone",
        category="management", description="not accepted", amount=Decimal(999),
        evidence=adjustments.Evidence(source_ref="x"),
    )  # fmt: skip
    with connect(company_db_path(root, "acme")) as con:
        mismatches = con.execute(
            """
            WITH acc AS (
                SELECT sum(revenue_effect) AS rev, sum(cost_effect) AS cost, sum(amount) AS ebitda
                FROM adjustments WHERE status = 'accepted'
            ),
            expected AS (
                SELECT 'revenue' AS account, rev AS adj FROM acc
                UNION ALL SELECT 'total_income', rev FROM acc
                UNION ALL SELECT 'total_expenses', cost FROM acc
                UNION ALL SELECT 'pbt', ebitda FROM acc
            )
            SELECT p.account FROM adjusted_pl p JOIN expected e USING (account)
            WHERE p.adjustment <> e.adj OR p.adjusted <> p.reported + e.adj
            """
        ).fetchall()
        totals = con.execute(
            "SELECT account, reported, adjustment, adjusted FROM adjusted_pl "
            "WHERE account IN ('revenue', 'total_expenses', 'pbt', 'employee_expense') "
            "ORDER BY account"
        ).fetchall()
    assert mismatches == []
    assert totals == [
        ("employee_expense", Decimal("3000000.00"), Decimal("0.00"), Decimal("3000000.00")),
        ("pbt", Decimal("1300000.00"), Decimal("100000.00"), Decimal("1400000.00")),
        ("revenue", Decimal("9000000.00"), Decimal("0.00"), Decimal("9000000.00")),
        ("total_expenses", Decimal("7750000.00"), Decimal("-100000.00"), Decimal("7650000.00")),
    ]
    shown = adjusted.adjusted_pl(Run("ui bridge", {}, root), "acme")
    assert [line["account"] for line in shown["lines"]][-1] == "pbt"  # nothing after tax
    assert "trade_receivables" not in [line["account"] for line in shown["lines"]]
    [ebitda] = shown["ebitda"]
    assert (ebitda["reported_ebitda"], ebitda["adjusted_ebitda"]) == (
        Decimal("1500000.00"),
        Decimal("1600000.00"),
    )


def test_a_line_with_no_reported_figure_has_no_adjusted_figure(root: Path, tmp_path: Path) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("DELETE FROM facts WHERE account = 'total_expenses'")
    services.import_adjustments(
        root, "acme", write_walk(tmp_path / "walk.xlsx"), None, via="cli", actor=USER
    )
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE adjustments SET status = 'accepted' WHERE adj_id = 1")
    with connect(company_db_path(root, "acme")) as con:
        line = con.execute(
            "SELECT reported, adjustment, adjusted FROM adjusted_pl "
            "WHERE account = 'total_expenses'"
        ).fetchall()
    assert line == [(None, Decimal("-100000.00"), None)]
