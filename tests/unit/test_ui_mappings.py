"""SPEC-05: the Mappings screen has every review kind the CLI has, a bulk confirm only for
proposals at confidence 0.8 or above, and a choice of account from the dictionary; Home lists the
checks that did not run with what would make them run. Needs the `ui` extra; skipped without it."""

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from fin.db.conn import connect  # noqa: E402
from fin.paths import company_db_path  # noqa: E402
from fin.services.reviews import KINDS  # noqa: E402
from fin.ui.server import create_app  # noqa: E402
from tests.engagement_root import mapping_decisions, with_proposal  # noqa: E402

TOKEN = "t0ken"
PORT = 8765
HOST = f"127.0.0.1:{PORT}"
ORIGIN = {"Origin": f"http://{HOST}"}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return with_proposal(tmp_path)


@pytest.fixture
def client(root: Path) -> TestClient:
    client = TestClient(
        create_app(root, TOKEN, PORT), base_url=f"http://{HOST}", follow_redirects=False
    )
    client.get(f"/?token={TOKEN}")
    return client


def test_every_review_kind_the_cli_has_is_on_the_mappings_screen(client: TestClient) -> None:
    page = client.get("/partials/mappings?company=acme").text
    assert 'id="review-mappings"' in page
    for kind, spec in KINDS.items():
        assert f'id="review-{kind}"' in page
        assert f"<h2>{spec.title}</h2>" in page


def test_the_bulk_confirm_takes_only_proposals_at_0_8_or_above(client: TestClient) -> None:
    page = client.get("/partials/mappings?company=acme").text
    assert "Confirm the 1 proposals at 0.8 or above" in page
    assert 'name="item" value="1"' in page
    assert 'list="accounts-balance_sheet"' in page  # choose another account
    assert '<datalist id="accounts-balance_sheet">' in page
    assert '<option value="trade_receivables">' in page


def test_choosing_another_account_confirms_with_it(client: TestClient, root: Path) -> None:
    data = {"action": "mapping-confirm", "company": "acme", "item": "1", "choice": "inventories"}
    page = client.post("/act", data=data, headers=ORIGIN).text
    assert "#1 confirmed (company scope)" in page
    assert mapping_decisions(root) == [("edited", "user", False)]


def test_home_lists_the_checks_that_did_not_run_and_what_they_need(
    client: TestClient, root: Path
) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "INSERT INTO documents (doc_id, file_name, sha256, version, fiscal_year, scope, "
            "status, archive_path, run_id, ingested_at, period_end) VALUES (1, 'ar.xlsx', 's', 1, "
            "'FY24', 'standalone', 'loaded', 'a', 'r', now(), DATE '2024-03-31')"
        )
        con.execute(
            "INSERT INTO checks_not_run VALUES (1, 'cash_flow', 'FY24', 'standalone', "
            "'no confirmed cash figure', 'confirm a cash figure for FY24 standalone', 'r')"
        )
    page = client.get("/partials/home?company=acme").text
    assert 'id="checks-not-run"' in page
    assert "no confirmed cash figure" in page
    assert "confirm a cash figure for FY24 standalone" in page
