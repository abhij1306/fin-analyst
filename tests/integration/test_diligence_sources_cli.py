"""`fin ingest --kind` end to end: a synthetic Tally trial balance and Ledger Vouchers load through
the CLI; fin's output, its findings and its audit log carry tokens, never the real names; `fin sql`
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
from tests.tally_fixtures import CUSTOMERS, VENDORS, write_ledger_vouchers, write_trial_balance

runner = CliRunner()
NAMES = (*CUSTOMERS, *VENDORS, "Neem Consulting")


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


def test_source_files_load_and_print_only_tokens(
    root: Path, tmp_path: Path, audit_query: Any
) -> None:
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
        "tally_trial_balance)\n  trial balance FY25: 10 ledgers in 9 groups, 4 names newly "
        "masked, 1 findings\n"
    )
    assert vouchers.exit_code == 0, vouchers.output
    assert "ledgers not in the FY25 trial balance, masked because" in vouchers.stdout
    assert "trial balance ledgers are missing from it" in vouchers.stdout
    assert findings.stdout.startswith(
        "#1 open tb_group_sum acme/tb.xlsx (source file_id 1): trial balance FY25: group "
        "'Sundry Debtors' net closing balance"
    )
    assert json.loads(entries.stdout)["rows"][:2] == [
        {"ledger": "CUST-0001", "particulars": "Software Sales"},
        {"ledger": "CUST-0001", "particulars": "Axis Bank Current Account"},
    ]
    printed = loaded.output + vouchers.output + findings.output + entries.output
    logged = " ".join(str(row) for row in audit_query("SELECT message, context FROM events"))
    logged += " ".join(str(row) for row in audit_query("SELECT * FROM findings"))
    for name in NAMES:
        assert name not in printed
        assert name not in logged


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
        (["--kind", "bank"], "--kind takes"),
        (["--kind", "trial_balance", "--force"], "--kind does not take --force or --reading"),
    ],
)
def test_kind_usage_errors(root: Path, tmp_path: Path, args: list[str], message: str) -> None:
    tb = write_trial_balance(tmp_path / "tb.xlsx")

    result = fin("ingest", "acme", str(tb), *args)

    assert result.exit_code == 2
    assert message in plain(result.output)
