"""Reference documents (SPEC-10): a synthetic banker's databook with three planted differences
loads apart from the company's facts, its lines map through the proposal and review flow, the
comparison lists exactly the three, and a difference hands off to a query or an adjustment with
both sources."""

import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook
from typer.testing import CliRunner

from fin.cli import app
from fin.db.conn import connect
from fin.map.dictionary import find, load_label_map
from fin.paths import company_db_path
from fin.reference.compare import comparison
from fin.reference.lines import list_lines
from fin.statements import statement_rows

KESTREL = "Kestrel_Forgings_Financials_FY23-FY25.xlsx"
SOURCE = "Banker databook"
UNKNOWN = "Banker normalised overheads"
runner = CliRunner()
pytestmark = pytest.mark.timeout(240)  # the first test also ingests the shared kestrel template


def fin(*args: str, code: int = 0) -> str:
    result = runner.invoke(app, list(args))
    assert result.exit_code == code, result.output
    return result.output


def query(root: Path, sql: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
    with connect(company_db_path(root, "kestrel")) as con:
        return con.execute(sql, params or []).fetchall()


@pytest.fixture
def root(clean_root: Callable[..., Path]) -> Path:
    return clean_root("kestrel")  # a copy of kestrel ingested once per test process


def fin_lines(root: Path) -> tuple[str, list[tuple[str, str, Decimal]]]:
    """(scope, six FY25 P&L lines (label, account, value)) fin holds from one printed line each,
    whose labels the dictionary maps with no heading."""
    rows = query(
        root,
        "SELECT c.scope, s.label, c.account, c.value FROM current_facts c "
        "JOIN fact_sources s USING (fact_id) WHERE c.period = 'FY25' "
        "AND s.statement = 'profit_and_loss' AND len(c.fact_ids) = 1 AND c.value <> 0 "
        "ORDER BY c.scope, s.label",
    )
    scope = rows[0][0]
    label_map = load_label_map(root, "kestrel")
    chosen: dict[str, tuple[str, str, Decimal]] = {}
    for row_scope, label, account, value in rows:
        mapping = find(label_map, "profit_and_loss", "", label)
        if row_scope == scope and mapping is not None and mapping.account == account:
            chosen.setdefault(account, (label, account, value))
    assert len(chosen) >= 6, chosen
    return scope, list(chosen.values())[:6]


def write_databook(path: Path, scope: str, lines: list[tuple[str, Decimal]]) -> Path:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Profit and Loss"
    for row in [
        ["Banker databook (synthetic)"],
        [f"{scope.title()} Statement of Profit and Loss"],
        ["(Amount in rupees)"],
        ["Particulars", "Year ended 31-Mar-2025"],
        *[[label, str(value)] for label, value in lines],
    ]:
        sheet.append(row)
    book.create_sheet("Net debt").append(["Net debt summary: see the banker's notes"])
    book.save(path)
    return path


PLANTED = (Decimal("1000.00"), Decimal("-5000.00"), Decimal("250000.00"))


@pytest.fixture
def databook(root: Path, tmp_path: Path) -> tuple[Path, list[tuple[str, str, Decimal]]]:
    scope, lines = fin_lines(root)
    shown = [
        (label, value + (PLANTED[i] if i < 3 else 0)) for i, (label, _, value) in enumerate(lines)
    ]
    path = write_databook(tmp_path / "databook.xlsx", scope, [*shown, (UNKNOWN, Decimal("7.00"))])
    return path, lines


def snapshot(root: Path) -> tuple[Any, ...]:
    facts = query(root, "SELECT * FROM current_facts ORDER BY account, period, scope")
    metrics = query(root, "SELECT * FROM metrics ORDER BY metric, period, scope")
    return facts, metrics, statement_rows(root, "kestrel")


def test_the_comparison_lists_exactly_the_three_planted_differences(
    root: Path, databook: tuple[Path, list[tuple[str, str, Decimal]]]
) -> None:
    path, lines = databook
    output = fin("reference", "add", "kestrel", str(path), "--source", SOURCE)
    assert "reference file #1: 7 figures, 7 new lines, 1 lines waiting for a mapping" in output

    data = comparison(root, "kestrel")

    differs = [r for r in data["rows"] if r["state"] == "differs"]
    assert [(r["label"], r["target"], r["difference"]) for r in differs] == [
        (lines[2][0], lines[2][1], -PLANTED[2]),
        (lines[1][0], lines[1][1], -PLANTED[1]),
        (lines[0][0], lines[0][1], -PLANTED[0]),
    ]  # fin minus the reference; at or above the threshold (1,00,000) first, then by size
    assert [r["above"] for r in differs] == [True, False, False]
    assert data["counts"] == {"differs": 3, "no_fin_figure": 0, "unmapped": 1, "agrees": 3}
    assert data["threshold"] == Decimal("100000.00")
    first = differs[0]
    assert first["reference_ref"] == "Profit and Loss!B7"
    assert first["fin_source"].startswith(f"{KESTREL} ")
    text = fin("reference", "compare", "kestrel")
    assert "kestrel: 3 differences" in text
    assert text.count("[differs") == 3


def test_no_reference_value_reaches_current_facts_metrics_or_statements(
    root: Path, databook: tuple[Path, list[tuple[str, str, Decimal]]]
) -> None:
    before = snapshot(root)
    fin("reference", "add", "kestrel", str(databook[0]), "--source", SOURCE)
    assert snapshot(root) == before
    assert query(root, "SELECT count(*) FROM reference_values") == [(7,)]
    in_documents = "SELECT count(*) FROM documents WHERE file_name = 'databook.xlsx'"
    assert query(root, in_documents) == [(0,)]


def test_a_file_is_added_once_and_needs_a_source(
    root: Path, databook: tuple[Path, list[tuple[str, str, Decimal]]]
) -> None:
    path = str(databook[0])
    assert "E980" in fin("reference", "add", "kestrel", path, "--source", " ", code=2)
    fin("reference", "add", "kestrel", path, "--source", SOURCE)
    assert "E105" in fin("reference", "add", "kestrel", path, "--source", SOURCE, code=1)
    assert query(root, "SELECT count(*) FROM reference_files") == [(1,)]


def test_a_waiting_line_is_compared_only_once_the_user_confirms_the_proposal(
    root: Path, databook: tuple[Path, list[tuple[str, str, Decimal]]], tmp_path: Path
) -> None:
    fin("reference", "add", "kestrel", str(databook[0]), "--source", SOURCE)
    [line] = list_lines(root, "kestrel")
    assert (line["label"], line["status"], line["figures"]) == (UNKNOWN, "pending", 1)
    proposals = tmp_path / "p.json"
    for bad in (
        '[{"line": %d, "account": "total_assets", "confidence": 0.9, "reason": "r"}]',
        '[{"line": %d, "metric": "net_profit_margin", "confidence": 0.9, "reason": "r"}]',
        '[{"line": %d, "account": "other_expenses", "confidence": 2, "reason": "r"}]',
        '[{"line": 999, "account": "other_expenses", "confidence": 0.5, "reason": "r"}]',
    ):
        proposals.write_text(bad.replace("%d", str(line["line_id"])), encoding="utf-8")
        assert "E981" in fin("reference", "propose", "kestrel", str(proposals), code=2)
    assert "E981" in fin("reference", "confirm", "kestrel", str(line["line_id"]), code=2)
    proposals.write_text(
        json.dumps(
            [
                {
                    "line": line["line_id"],
                    "account": "other_expenses",
                    "confidence": 0.6,
                    "reason": "overheads",
                }
            ]
        ),
        encoding="utf-8",
    )
    fin("reference", "propose", "kestrel", str(proposals))
    assert comparison(root, "kestrel")["counts"]["unmapped"] == 1

    fin("reference", "confirm", "kestrel", str(line["line_id"]))

    [row] = [r for r in comparison(root, "kestrel")["rows"] if r["label"] == UNKNOWN]
    assert (row["line_status"], row["target"]) == ("confirmed", "other_expenses")
    assert row["state"] in ("differs", "no_fin_figure")
    fin("reference", "reject", "kestrel", str(line["line_id"]))
    assert comparison(root, "kestrel")["counts"]["unmapped"] == 1


def test_a_query_and_an_adjustment_from_a_difference_carry_both_sources(
    root: Path, databook: tuple[Path, list[tuple[str, str, Decimal]]]
) -> None:
    fin("reference", "add", "kestrel", str(databook[0]), "--source", SOURCE)
    fin("engagement", "open", "kestrel", "--periods", "FY25")
    rows = comparison(root, "kestrel")["rows"]
    first = rows[0]
    agrees = next(r for r in rows if r["state"] == "agrees")
    assert "E982" in fin(
        "reference", "query", "kestrel", str(agrees["value_id"]), "--question", "q", code=2
    )

    fin(
        "reference", "query", "kestrel", str(first["value_id"]), "--question", "Why does it differ?"
    )

    [(amount, period, scope, sources, status)] = query(
        root, "SELECT amount, period, scope, sources, status FROM accountant_queries"
    )
    assert (amount, period, scope, status) == (first["difference"], "FY25", first["scope"], "draft")
    assert sources == (
        f"fin: {first['fin_source']}; {SOURCE}: databook.xlsx {first['reference_ref']}"
    )
    fin(
        "reference", "adjust", "kestrel", str(first["value_id"]), "--category", "normalisation",
        "--description", "As the banker reads it", "--amount", "250000",
    )  # fmt: skip
    [(source_ref, account, adj_status)] = query(
        root, "SELECT source_ref, account, status FROM adjustments"
    )
    assert (source_ref, account, adj_status) == (sources, first["target"], "proposed")


def test_a_databook_in_the_data_room_loads_as_a_reference(
    root: Path, databook: tuple[Path, list[tuple[str, str, Decimal]]], tmp_path: Path
) -> None:
    room = tmp_path / "room"
    room.mkdir()
    (room / "banker.xlsx").write_bytes(databook[0].read_bytes())
    fin("dataroom", "scan", "kestrel", str(room))
    [(file_id, kind, reason)] = query(root, "SELECT file_id, kind, reason FROM dataroom_files")
    assert (kind, reason) == ("reference_databook", None)
    fin("dataroom", "approve", "kestrel", str(file_id))
    assert "banker.xlsx: reference file #1: 7 figures" in fin("dataroom", "load", "kestrel")
    assert query(root, "SELECT source FROM reference_files") == [("banker.xlsx",)]
