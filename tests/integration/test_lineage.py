"""Lineage of a figure to its sources: a value summed from two printed lines keeps both (`fin sql`,
the evidence page, `fin statements --json`), a per-share figure is traced in rupees per share, an
archived workbook that cannot be opened is flagged (E604), and a batch ingest stays in a company's
history after the company is deleted."""

import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from fin.cli import app
from tests.synthetic_pdf import BALANCE_SHEET, PROFIT_AND_LOSS, WIDTH, write_pdf

runner = CliRunner()


def invoke(*args: str) -> Result:
    return runner.invoke(app, list(args))


@pytest.fixture
def root(data_root: Path, tmp_path: Path) -> Path:
    """acme: one annual report whose trade payables are printed on two lines (5.00 + 10.00 lakh in
    FY25), with basic EPS 12.34; ingested with `fin ingest-batch` next to a file that fails."""
    assert invoke("init").exit_code == 0
    folder = tmp_path / "batch" / "acme"
    folder.mkdir(parents=True)
    write_pdf(folder / "acme_ar25.pdf", BALANCE_SHEET, PROFIT_AND_LOSS, width=WIDTH)
    (folder / "broken.pdf").write_bytes(b"not a pdf")
    result = invoke("ingest-batch", str(tmp_path / "batch"))
    assert "acme/acme_ar25.pdf: loaded as doc_id 1" in result.stdout, result.output
    return data_root


def select(sql: str, *extra: str) -> dict[str, object]:
    result = invoke("sql", "acme", "--sql", sql, *extra)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


PAYABLES_SQL = (
    "SELECT value, fact_ids FROM current_facts WHERE account = 'trade_payables' AND period = 'FY25'"
)


def test_a_summed_value_has_every_line_as_its_source(root: Path) -> None:
    answer = select(PAYABLES_SQL, "--evidence")

    [row] = answer["rows"]
    assert (row["value"], len(row["fact_ids"]), row["source_ref"]) == (
        "1500000.00",
        2,
        "sum of 2 sources: page 1; page 1",
    )
    page = (root / answer["evidence_path"]).read_text(encoding="utf-8")
    titles = re.findall(r"<h3>fact \d+: (.*?)</h3>", page)
    assert titles == [
        "trade_payables FY25 standalone = 500000.00 rupees (confirmed)",
        "trade_payables FY25 standalone = 1000000.00 rupees (confirmed)",
    ]
    assert "1 row(s) are sums of several source lines; each line is below." in page


def test_a_fact_ids_list_with_null_or_unknown_ids_has_empty_provenance(root: Path) -> None:
    answer = select(
        "SELECT a.account, list(f.fact_id) AS fact_ids FROM (SELECT 'trade_payables' AS account "
        "UNION ALL SELECT 'nonexistent') a LEFT JOIN current_facts f USING (account) "
        "WHERE f.period IS NULL OR f.period = 'FY25' GROUP BY a.account "
        "UNION ALL SELECT 'unknown', [999999] ORDER BY account",
        "--evidence",
    )

    rows = {row["account"]: row for row in answer["rows"]}
    assert rows["nonexistent"]["fact_ids"] == [None]
    assert rows["nonexistent"]["source_ref"] is None
    assert rows["unknown"]["source_ref"] is None
    assert rows["trade_payables"]["source_ref"] == "page 1"


def test_statements_json_lists_every_line_of_a_summed_value(root: Path) -> None:
    result = invoke("statements", "acme", "--period", "FY25", "--json")

    assert result.exit_code == 0, result.output
    [payables] = [row for row in json.loads(result.stdout) if row["account"] == "trade_payables"]
    assert (payables["value"], payables["fact_ids"]) == (
        "1500000.00",
        select(PAYABLES_SQL)["rows"][0]["fact_ids"],
    )
    assert len(payables["fact_ids"]) == 2


def test_a_per_share_fact_is_traced_in_rupees_per_share(root: Path) -> None:
    sql = "SELECT fact_id FROM current_facts WHERE account = 'eps_basic' AND period = 'FY25'"
    fact_id = select(sql)["rows"][0]["fact_id"]

    result = invoke("audit", "acme", "--fact", str(fact_id))

    assert result.exit_code == 0, result.output
    assert (
        f"fact {fact_id}: eps_basic FY25 standalone = 12.34 rupees per share "
        "(mapping confirmed: True)"
    ) in result.stdout


def test_batch_ingest_stays_in_the_history_after_delete(root: Path) -> None:
    assert invoke("delete", "acme", "--yes").exit_code == 0

    result = invoke("audit", "acme")

    assert result.exit_code == 0, result.output
    runs = [line for line in result.stdout.splitlines() if " ingest-batch -> " in line]
    assert len(runs) == 1
    assert runs[0].endswith("ingest-batch -> failures (loaded 1, failed 1)")
    assert re.search(r"error E\d+ acme/broken\.pdf ", result.stdout)


def test_an_archived_workbook_that_cannot_be_opened_is_e604(
    clean_root: Callable[..., Path],
) -> None:
    root = clean_root("kestrel")
    [archived] = (root / "archive" / "kestrel").iterdir()
    archived.write_bytes(b"not a workbook")
    sql = "SELECT fact_id FROM current_facts WHERE account = 'revenue' AND period = 'FY25'"

    result = invoke("sql", "kestrel", "--sql", sql, "--evidence")

    assert result.exit_code == 1, result.output
    answer = json.loads(result.stdout)
    page = (root / answer["evidence_path"]).read_text(encoding="utf-8")
    assert (
        "E604: Kestrel_Forgings_Financials_FY23-FY25.xlsx: archived source archive/kestrel/" in page
    )
    assert "cannot be opened" in page
