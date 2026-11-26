"""`fin ingest --kind` end to end: a synthetic Tally trial balance and Ledger Vouchers load through
the CLI with ledger names as filed (the books are not masked: docs/decisions/masking.md); `fin sql`
refuses `mask_values` (E810)."""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from fin.cli import app
from fin.db.schema import ensure_database
from fin.paths import company_db_path
from tests.register_fixtures import HANDLERS, write_registers
from tests.tally_fixtures import CUSTOMERS, VENDORS, write_ledger_vouchers, write_trial_balance

runner = CliRunner()


def fin(*args: str) -> Result:
    return runner.invoke(app, list(args))


def plain(output: str) -> str:
    """A usage error's text without the colour codes, panel border and line wrapping Typer adds
    (CI's terminal is coloured and may be narrower)."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", output).replace("\N{BOX DRAWINGS LIGHT VERTICAL}", " ")
    return " ".join(text.split())


@pytest.fixture
def root(data_root: Path) -> Path:
    assert fin("init").exit_code == 0
    ensure_database(company_db_path(data_root, "acme"), "company")
    opened = fin("engagement", "open", "acme", "--periods", "FY25")
    assert opened.exit_code == 0, opened.output
    return data_root


def test_source_files_load_with_ledgers_as_filed(root: Path, tmp_path: Path) -> None:
    tb = write_trial_balance(tmp_path / "tb.xlsx", plant=True)
    lv = write_ledger_vouchers(
        tmp_path / "lv.xlsx", [CUSTOMERS[0], "Software Sales", "Neem Consulting"]
    )

    loaded = fin("ingest", "acme", str(tb), "--kind", "trial_balance")
    vouchers = fin("ingest", "acme", str(lv), "--kind", "ledger_vouchers")
    findings = fin("findings", "list", "acme")
    sql = 'SELECT ledger, particulars FROM ledger_entries ORDER BY sheet, "row"'
    entries = fin("sql", "acme", "--sql", sql)

    assert loaded.exit_code == 0, loaded.output
    assert loaded.stdout.startswith(
        "acme/tb.xlsx: loaded as source file_id 1 (trial_balance FY25, profile "
        "tally_trial_balance)\n  trial balance FY25: 10 ledgers in 9 groups, 1 findings\n"
    )
    assert vouchers.exit_code == 0, vouchers.output
    assert "1 ledgers not in the FY25 trial balance: Neem Consulting" in vouchers.stdout
    assert "trial balance ledgers are missing from it" in vouchers.stdout
    assert findings.stdout.startswith(
        "#1 open tb_group_sum acme/tb.xlsx (source file_id 1): trial balance FY25: group "
        "'Sundry Debtors' net closing balance"
    )
    assert json.loads(entries.stdout)["rows"][:2] == [
        {"ledger": CUSTOMERS[0], "particulars": "Software Sales"},
        {"ledger": CUSTOMERS[0], "particulars": "Axis Bank Current Account"},
    ]
    printed = loaded.output + vouchers.output + findings.output + entries.output
    assert "CUST-0001" not in printed  # no token from the books
    assert "LEDG-" not in printed


def test_fin_sql_refuses_mask_values(root: Path, audit_query: Any) -> None:
    sql = "SELECT real_value FROM mask_values"

    result = fin("sql", "acme", "--sql", sql)

    assert result.exit_code == 2
    assert result.stderr.startswith("E810: `fin sql` does not read mask_values")
    assert audit_query("SELECT mode, row_count FROM queries WHERE sql = ?", [sql]) == [
        ("read", None)
    ]


def test_a_groups_only_trial_balance_is_refused(root: Path, tmp_path: Path) -> None:
    tb = write_trial_balance(tmp_path / "tb.xlsx", groups_only=True)

    result = fin("ingest", "acme", str(tb), "--kind", "trial_balance")

    assert result.exit_code == 2
    assert result.stderr.startswith("E809: tb.xlsx: the trial balance has group rows only")


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--profile", "tally_trial_balance"], "--profile needs --kind"),
        (["--kind", "weather"], "--kind takes"),
        (["--kind", "trial_balance", "--force"], "--kind does not take --force or --reading"),
    ],
)
def test_kind_usage_errors(root: Path, tmp_path: Path, args: list[str], message: str) -> None:
    tb = write_trial_balance(tmp_path / "tb.xlsx")

    result = fin("ingest", "acme", str(tb), *args)

    assert result.exit_code == 2
    assert message in plain(result.output)


def test_registers_load_through_the_cli_and_a_duplicate_invoice_becomes_a_query(
    root: Path, tmp_path: Path, audit_query: Any
) -> None:
    reg = write_registers(tmp_path / "reg.xlsx", duplicate=True)

    loaded = fin("ingest", "acme", str(reg), "--kind", "registers")
    findings = fin("findings", "list", "acme")
    query = fin(
        "query", "raise", "acme", "--finding", "1", "--question", "Is INV-1002 one invoice?"
    )

    assert loaded.exit_code == 0, loaded.output
    assert "(registers FY25, profile billing_registers)" in loaded.stdout
    assert "4 sales lines, 3 purchase lines" in loaded.stdout
    assert "register_duplicate_invoice" in findings.stdout
    assert query.exit_code == 0, query.output
    printed = loaded.output + findings.output + query.output
    logged = " ".join(str(row) for row in audit_query("SELECT message, context FROM events"))
    logged += " ".join(str(row) for row in audit_query("SELECT * FROM findings"))
    for name in (*CUSTOMERS, *VENDORS, *HANDLERS):  # the client's starred fields stay tokens
        assert name not in printed
        assert name not in logged
