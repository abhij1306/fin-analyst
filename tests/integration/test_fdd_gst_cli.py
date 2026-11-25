"""F6 GST end to end through the CLI (docs/fdd.md F6): GST return summaries load with
`--kind gst --profile`, Claude's tax ledger classes go through `fin map tax` and `fin review --tax`,
`fin reconcile` runs the GST reconciliations, `fin sql --write` refuses the F6 tables (E806), and
the tax charge view reads a clean synthetic company's statements."""

import json
import re
import shutil
from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from fin.cli import app
from fin.db.schema import ensure_database
from fin.paths import company_db_path
from tests.gst_fixtures import CLASSES, write_gst_set

runner = CliRunner()


def fin(*args: str) -> Result:
    return runner.invoke(app, list(args))


def plain(output: str) -> str:
    """A usage error's text without Typer's colour codes, panel border and line wrapping."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", output).replace("\N{BOX DRAWINGS LIGHT VERTICAL}", " ")
    return " ".join(text.split())


@pytest.fixture
def root(data_root: Path, tmp_path: Path) -> Path:
    assert fin("init").exit_code == 0
    ensure_database(company_db_path(data_root, "acme"), "company")
    opened = fin("engagement", "open", "acme", "--periods", "FY25")
    assert opened.exit_code == 0, opened.output
    return data_root


def test_gst_returns_tax_ledgers_and_reconciliations(root: Path, tmp_path: Path) -> None:
    files = write_gst_set(tmp_path)
    for kind in ("tb", "lv", "registers"):
        name = {"tb": "trial_balance", "lv": "ledger_vouchers", "registers": "registers"}[kind]
        assert fin("ingest", "acme", str(files[kind]), "--kind", name).exit_code == 0

    no_profile = fin("ingest", "acme", str(files["gstr1"]), "--kind", "gst")
    gstr1 = fin("ingest", "acme", str(files["gstr1"]), "--kind", "gst", "--profile", "gstr1")
    gstr3b = fin("ingest", "acme", str(files["gstr3b"]), "--kind", "gst", "--profile", "gstr3b")

    assert no_profile.exit_code == 2
    assert "needs --profile" in plain(no_profile.output)
    assert gstr1.exit_code == 0, gstr1.output
    assert "(gstr1 FY25, profile gstr1)" in gstr1.stdout
    assert "GSTR-3B FY25: 3 months" in gstr3b.stdout

    listed = fin("map", "tax", "acme")
    assert "13 trial balance ledgers with no tax class" in listed.stdout
    proposals = tmp_path / "tax.json"
    proposals.write_text(
        json.dumps(
            [
                {"ledger": k, "class": v, "confidence": 0.9, "reason": "name"}
                for k, v in CLASSES.items()
            ]  # noqa: E501
        ),
        encoding="utf-8",
    )
    stored = fin("map", "tax", "acme", str(proposals))
    waiting = fin("review", "list", "acme", "--tax")
    before = fin("reconcile", "acme", "gstr3b_vs_books", "--period", "FY25")
    confirmed = fin("review", "confirm", "acme", *map(str, range(1, 9)), "--tax")
    rejected = fin("review", "reject", "acme", "6", "--tax")
    wrong = fin("review", "confirm", "acme", "1", "--tax", "--sector", "x")
    books = fin("reconcile", "acme", "gstr3b_vs_books", "--period", "FY25")

    assert stored.exit_code == 0
    assert "8 tax ledger proposals stored" in stored.stdout
    assert waiting.stdout.startswith("tax ledger #1 [proposed] 'Output CGST' (Duties & Taxes)")
    assert before.exit_code == 2
    assert "E840" in before.stderr
    assert confirmed.exit_code == 0, confirmed.output
    assert "tax ledger #8 'MAT Credit Entitlement' confirmed as mat_credit" in confirmed.stdout
    assert "tax ledger #6 'TDS Payable' rejected" in rejected.stdout
    assert wrong.exit_code == 2
    assert books.exit_code == 0, books.output
    assert (
        "difference gst 2024-06 output_tax: GSTR-3B ₹81,000.00, books ₹231,000.00" in books.stdout
    )
    assert "needs a query" in books.stdout

    every = fin("reconcile", "acme", "--period", "FY25")
    assert every.exit_code == 0, every.output
    for name in ("gstr1_vs_sales_register", "gstr3b_vs_gstr1", "gstr3b_vs_books", "rcm"):
        assert f"{name} FY25" in every.stdout

    for table in ("gst_returns", "tax_ledgers"):
        refused = fin("sql", "acme", "--write", "--sql", f"DELETE FROM {table}")
        assert refused.exit_code == 2
        assert refused.stderr.startswith("E806")


def test_tax_charge_reads_the_statements(
    clean_template: Callable[..., Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    answer_key: dict[str, Any],
) -> None:
    copy = Path(shutil.copytree(clean_template("kestrel"), tmp_path / "FinAnalysis"))
    monkeypatch.setenv("FIN_DATA_DIR", str(copy))
    facts = answer_key["companies"]["kestrel"]["facts"]["standalone"]["FY25"]

    result = fin(
        "sql", "kestrel", "--sql",
        "SELECT effective_tax_rate, total_tax = current_tax AS same, deferred_tax "
        "FROM tax_charge WHERE scope = 'standalone' AND period = 'FY25'",
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    [row] = json.loads(result.stdout)["rows"]
    rate = (Decimal(facts["tax_expense"]) / Decimal(facts["pbt"])).quantize(
        Decimal("0.000001"), ROUND_HALF_UP
    )
    assert Decimal(str(row["effective_tax_rate"])) == rate
    assert row["same"] is True
    assert row["deferred_tax"] is None
