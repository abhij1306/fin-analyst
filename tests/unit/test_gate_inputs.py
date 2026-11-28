"""Every column in the gate-input registry (fin.diligence.gate_inputs) is a real input: changing
it changes the gate's input hash, which reopens an approved gate (evaluate compares the hash with
the one approved; tests/unit/test_gates.py shows the reopening)."""

from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from fin.db.conn import connect
from fin.db.rows import fetch_dicts
from fin.db.schema import ensure_database
from fin.diligence.gate_inputs import GATE_INPUTS
from fin.diligence.gates import GATES, Context, digest

AUDIT = "'r0', now()"
# One row per table, inside engagement #1 (FY24) and the filters the gates read through.
SEEDS = [
    "INSERT INTO engagement (periods, query_threshold, status, opened_run_id, opened_at) "
    f"VALUES ('FY24', 100000, 'open', {AUDIT})",
    "INSERT INTO documents (doc_id, file_name, sha256, version, fiscal_year, scope, status, "
    "archive_path, run_id, ingested_at) VALUES (1, 'a.xlsx', 'sha-1', 1, 'FY24', 'standalone', "
    f"'loaded', 'x', {AUDIT})",
    "INSERT INTO facts (fact_id, raw_id, doc_id, account, fiscal_year, period_end, scope, value, "
    "confirmed, run_id, period, period_months) VALUES (1, 1, 1, 'revenue', 'FY24', "
    "DATE '2024-03-31', 'standalone', 1, true, 'r0', 'FY24', 12)",
    "INSERT INTO stated_figures (account, fiscal_year, period, period_months, period_end, scope, "
    "value, reason, actor, status, run_id, stated_at) VALUES ('other_income', 'FY24', 'FY24', 12, "
    f"DATE '2024-03-31', 'standalone', 5, 'board minutes', 'user', 'stated', {AUDIT})",
    "INSERT INTO fact_corrections (fact_id, doc_id, original_value, value, reason, actor, "
    f"on_request, run_id, corrected_at) VALUES (1, 1, 1, 2, 'misread', 'user', true, {AUDIT})",
    "INSERT INTO source_files (file_id, engagement_id, kind, file_name, sha256, archive_path, "
    "period, period_start, period_end, basis, profile, status, run_id, ingested_at) VALUES (1, 1, "
    "'bank', 'b.xlsx', 'sha-b', 'x', 'FY24', DATE '2023-04-01', DATE '2024-03-31', 'audited', "
    f"'p', 'loaded', {AUDIT})",
    "INSERT INTO ledger_accounts (ledger, status, account, run_id, changed_at) "
    f"VALUES ('Sales', 'confirmed', 'revenue', {AUDIT})",
    f"INSERT INTO payroll_units VALUES (1, 'annual', {AUDIT})",
    "INSERT INTO bank_statements (file_id, bank, account, currency, period_start, period_end, "
    "opening_balance, opening_from, closing_balance, closing_from, total_debits, total_credits, "
    "line_count, ties, reader, run_id) VALUES (1, 'B', 'TOKEN', 'INR', DATE '2023-04-01', "
    "DATE '2024-03-31', 0, 'header', 10, 'header', 0, 10, 3, true, 'fin', 'r0')",
    "INSERT INTO findings (file_id, check_name, finding, status, run_id, recorded_at, "
    f"status_run_id, status_at) VALUES (1, 'bank_ties', 'x', 'open', {AUDIT}, {AUDIT})",
    "INSERT INTO accountant_queries (query_id, engagement_id, origin, question, sources, status, "
    f"created_run_id, created_at, status_run_id, status_at) VALUES (1, 1, 'manual', 'Why?', 's', "
    f"'draft', {AUDIT}, {AUDIT})",
    "INSERT INTO adjustments (adj_id, engagement_id, period, scope, category, account, amount, "
    "revenue_effect, cost_effect, description, source_ref, status, created_run_id, created_at, "
    "status_run_id, status_at) VALUES (1, 1, 'FY24', 'standalone', 'one_off', 'revenue', 5, 5, 0, "
    f"'d', 'a.xlsx C7', 'proposed', {AUDIT}, {AUDIT})",
    "INSERT INTO netdebt_items (engagement_id, period, amount, description, evidence, status, "
    f"created_run_id, created_at, status_run_id, status_at) VALUES (1, 'FY24', 5, 'loan', 'e', "
    f"'proposed', {AUDIT}, {AUDIT})",
    "INSERT INTO fdd_findings (engagement_id, area, title, observation, impact_type, severity, "
    "deal_consideration, evidence, status, created_run_id, created_at, status_run_id, status_at) "
    "VALUES (1, 'qoe', 't', 'o', 'ebitda', 'low', 'none', 'e', 'draft', "
    f"{AUDIT}, {AUDIT})",
    "INSERT INTO netdebt_classes (ledger, debt_class, status, reason, run_id, changed_at) "
    f"VALUES ('Loan', 'debt', 'proposed', 'r', {AUDIT})",
    "INSERT INTO tax_ledgers (ledger, status, tax_class, run_id, changed_at) "
    f"VALUES ('GST', 'proposed', 'gst_output', {AUDIT})",
    "INSERT INTO related_party_links (engagement_id, party, link_kind, target, status, reason, "
    f"run_id, changed_at) VALUES (1, 'P', 'customer', 'T', 'proposed', 'r', {AUDIT})",
]
# The value each registered column changes to, valid under the table's checks.
NEW = {
    ("documents", "status"): "'held'",
    ("facts", "account"): "'other_income'",
    ("source_files", "kind"): "'payroll'",
    ("source_files", "status"): "'superseded'",
    ("ledger_accounts", "status"): "'proposed'",
    ("payroll_units", "unit"): "'monthly'",
    ("bank_statements", "ties"): "false",
    ("findings", "status"): "'accepted'",
    ("accountant_queries", "status"): "'approved'",
    ("adjustments", "status"): "'accepted'",
    ("adjustments", "amount"): "6, revenue_effect = 6",
    ("adjustments", "scope"): "'consolidated'",
    ("netdebt_items", "status"): "'accepted'",
    ("fdd_findings", "status"): "'dropped'",
    ("fdd_findings", "area"): "'tax'",
    ("fdd_findings", "severity"): "'high'",
    ("netdebt_classes", "debt_class"): "'cash'",
    ("netdebt_classes", "status"): "'confirmed'",
    ("tax_ledgers", "tax_class"): "'tds'",
    ("tax_ledgers", "status"): "'confirmed'",
    ("related_party_links", "link_kind"): "'vendor'",
    ("related_party_links", "status"): "'confirmed'",
}
CASES = [
    (gate, table, column)
    for gate, tables in GATE_INPUTS.items()
    for table, columns in tables.items()
    for column in columns
]


@pytest.fixture
def con(tmp_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    path = tmp_path / "acme.duckdb"
    ensure_database(path, "company")
    with connect(path, write=True) as con:
        for sql in SEEDS:
            con.execute(sql)
        yield con


def ctx(con: duckdb.DuckDBPyConnection) -> Context:
    [engagement] = fetch_dicts(con, "SELECT * FROM engagement")
    return Context(con, "acme", engagement, [])


def input_hash(con: duckdb.DuckDBPyConnection, name: str) -> str:
    [gate] = [g for g in GATES if g.name == name]
    return digest(gate.inputs(ctx(con)))


def new_value(con: duckdb.DuckDBPyConnection, table: str, column: str) -> str:
    """The SET value: the registered one, else a changed copy of the current value by type."""
    if (table, column) in NEW:
        return NEW[(table, column)]
    [(kind,)] = con.execute(
        "SELECT data_type FROM information_schema.columns WHERE table_name = ? AND column_name = ?",
        [table, column],
    ).fetchall()
    if kind == "VARCHAR":
        return f"coalesce({column}, '') || ' changed'"
    return f"coalesce({column}, 0) + 1"


def test_every_gate_has_an_entry() -> None:
    assert sorted(GATE_INPUTS) == sorted(g.name for g in GATES)


@pytest.mark.parametrize(("gate", "table", "column"), CASES)
def test_changing_a_registered_column_changes_the_gate_hash(
    con: duckdb.DuckDBPyConnection, gate: str, table: str, column: str
) -> None:
    before = input_hash(con, gate)

    con.execute(f"UPDATE {table} SET {column} = {new_value(con, table, column)}")

    assert input_hash(con, gate) != before, f"{gate}: {table}.{column} is not an input"
