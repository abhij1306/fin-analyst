"""What waits for the user (`fin.status.WAITING`): every kind is counted once by `fin status`,
and the Home screen and the tab counts read those counts. Synthetic rows in a small company
database."""

from pathlib import Path

import pytest

from fin.audit.logger import Run
from fin.db.conn import connect, utcnow
from fin.paths import company_db_path
from fin.status import WAITING, company_status, format_status
from tests.engagement_root import engagement_root, with_proposal

# One row waiting for the user per kind, one decided row beside it that must not count, and an
# item of another engagement (id 2, not the open one) that must not count either.
SEED = [
    "INSERT INTO ledger_accounts (ledger, status, run_id, changed_at) VALUES "
    "('Rent', 'proposed', 'r', $now), ('Cash', 'confirmed', 'r', $now)",
    "INSERT INTO customers (customer, status, run_id, changed_at) VALUES "
    "('C1', 'proposed', 'r', $now), ('C2', 'rejected', 'r', $now)",
    "INSERT INTO tax_ledgers (ledger, status, run_id, changed_at) VALUES "
    "('GST Out', 'proposed', 'r', $now), ('TDS', 'confirmed', 'r', $now)",
    "INSERT INTO related_party_links (engagement_id, party, link_kind, target, status, reason, "
    "run_id, changed_at) VALUES (1, 'P', 'customer', 'C1', 'proposed', 'name', 'r', $now), "
    "(2, 'P', 'customer', 'C2', 'proposed', 'name', 'r', $now)",
    "INSERT INTO netdebt_classes (ledger, debt_class, status, reason, run_id, changed_at) VALUES "
    "('Loan', 'debt', 'proposed', 'loan', 'r', $now), "
    "('Bank', 'cash', 'confirmed', 'b', 'r', $now)",
    "INSERT INTO match_proposals (engagement_id, bank_lines, invoice_lines, reason, status, "
    "run_id, changed_at) VALUES (1, '[]', '[]', 'same amount', 'proposed', 'r', $now)",
    "INSERT INTO reference_lines (statement, section_key, label_key, section, label, status, "
    "target_kind, target, run_id, changed_at) VALUES ('profit_and_loss', 's', 'l1', 'S', 'L1', "
    "'proposed', 'account', 'revenue', 'r', $now), ('profit_and_loss', 's', 'l2', 'S', 'L2', "
    "'pending', NULL, NULL, 'r', $now)",
    "INSERT INTO dataroom_files (folder, path, location, kind, duplicates, approved, status, "
    "run_id, scanned_at) VALUES ('d', 'tb.xlsx', 'tb.xlsx', 'trial_balance', [], false, "
    "'failed', 'r', $now), ('d', 'b.xlsx', 'b.xlsx', 'bank', [], true, 'planned', 'r', $now), "
    "('d', 'gl.xlsx', 'gl.xlsx', 'ledger_vouchers', [], false, 'loaded', 'r', $now)",
    "INSERT INTO fdd_findings (engagement_id, area, title, observation, impact_type, severity, "
    "deal_consideration, evidence, status, created_run_id, created_at, status_run_id, status_at) "
    "VALUES (1, 'qoe', 'T', 'O', 'none', 'low', 'none', 'E', 'draft', 'r', $now, 'r', $now), "
    "(1, 'qoe', 'T2', 'O', 'none', 'low', 'none', 'E', 'final', 'r', $now, 'r', $now)",
    "INSERT INTO provision_ledgers (ledger, kind, status, reason, run_id, changed_at) VALUES "
    "('Prov DD', 'doubtful_debts', 'proposed', 'name', 'r', $now), "
    "('Prov Gr', 'gratuity', 'confirmed', 'name', 'r', $now)",
    "INSERT INTO netdebt_items (engagement_id, period, amount, description, evidence, status, "
    "created_run_id, created_at, status_run_id, status_at) VALUES "
    "(1, 'FY25', 100, 'shortfall', 'query 1', 'proposed', 'r', $now, 'r', $now), "
    "(2, 'FY25', 100, 'old', 'query 2', 'proposed', 'r', $now, 'r', $now)",
    "INSERT INTO accountant_queries (engagement_id, origin, status, question, sources, amount, "
    "period, created_run_id, created_at, status_run_id, status_at) VALUES "
    "(1, 'manual', 'draft', 'Why?', 'a.xlsx', 500000, 'FY25', 'r', $now, 'r', $now), "
    "(1, 'manual', 'sent', 'And?', 'a.xlsx', 500000, 'FY25', 'r', $now, 'r', $now)",
    "INSERT INTO adjustments (engagement_id, period, scope, category, amount, revenue_effect, "
    "cost_effect, description, source_ref, status, created_run_id, created_at, status_run_id, "
    "status_at) VALUES (1, 'FY25', 'standalone', 'one_off', 100, 0, -100, 'd', 'a.xlsx B2', "
    "'proposed', 'r', $now, 'r', $now)",
]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = with_proposal(tmp_path)
    with connect(company_db_path(root, "acme"), write=True) as con:
        for sql in SEED:
            con.execute(sql, {"now": utcnow()})
    return root


def waiting(root: Path) -> dict[str, int]:
    return company_status(Run("status", {}, root), "acme")["waiting"]


def test_every_kind_that_waits_for_the_user_is_counted_and_decided_rows_are_not(
    root: Path,
) -> None:
    counts = waiting(root)
    assert list(counts) == list(WAITING)
    assert counts == dict.fromkeys(WAITING, 1) | {"open_findings": 0, "inbox": 0}


def test_items_of_an_engagement_count_only_while_it_is_open(root: Path) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute("UPDATE engagement SET status = 'closed', closed_at = now()")
    counts = waiting(root)
    closed = ("related", "matches", "fdd_drafts", "netdebt_items", "queries", "adjustments")
    for key in (*closed, "provisions"):  # provisions: decided on Findings, which needs one open
        assert counts[key] == 0, key
    assert counts["ledgers"] == counts["netdebt"] == 1  # company-wide reviews still wait


def test_fin_status_prints_what_waits_kind_by_kind(root: Path) -> None:
    text = format_status([company_status(Run("status", {}, root), "acme")])
    assert "waiting for you: 1 mapping items to review; 1 Tally ledgers to review;" in text
    assert "1 net debt items to accept or reject" in text


def test_nothing_waits_in_a_company_with_nothing_proposed(tmp_path: Path) -> None:
    assert waiting(engagement_root(tmp_path)) == dict.fromkeys(WAITING, 0)
