"""The Statements screen (SPEC-14 PR 3): the latest year as reported, and reported, rebuilt and
difference side by side. Needs the `ui` extra (CI's ui job syncs it); skipped without it."""

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from fin.ui.server import create_app  # noqa: E402
from tests.rebuilt_fixtures import make_root  # noqa: E402

TOKEN = "t0ken"
PORT = 8765


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    root = make_root(tmp_path_factory.mktemp("ui_rebuilt"))
    client = TestClient(
        create_app(root, TOKEN, PORT), base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False
    )
    client.get(f"/?token={TOKEN}")
    return client


def test_the_statements_tab_shows_the_reported_statements(client: TestClient) -> None:
    page = client.get("/statements?company=acme")
    assert page.status_code == 200
    assert 'id="tab-statements"' in page.text
    assert "standalone FY25: profit and loss" in page.text
    assert "2,500,000.00" in page.text  # reported revenue


def test_the_rebuilt_view_shows_reported_rebuilt_and_difference(client: TestClient) -> None:
    text = client.get("/partials/statements?company=acme&view=rebuilt").text
    assert "<html" not in text
    assert '<th class="num">Reported</th><th class="num">Rebuilt</th>' in text
    row = text.split("Revenue from operations", 1)[1].split("</tr>", 1)[0]
    cells = [c.split("<", 1)[0] for c in row.split('<td class="num">')[1:4]]
    assert cells == ["2,500,000.00", "2,400,000.00", "-100,000.00"]
    assert 'title="tb_FY25.xlsx Trial Balance!23"' in text
    assert "FY25: cash flow" in text
    assert "not derivable: the balances give the tax charged" in text


def test_a_company_with_no_trial_balance_says_what_to_ask_for(tmp_path: Path) -> None:
    root = make_root(tmp_path, periods=())
    client = TestClient(
        create_app(root, TOKEN, PORT), base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False
    )
    client.get(f"/?token={TOKEN}")
    text = client.get("/partials/statements?company=acme&view=rebuilt").text
    assert "E822: acme: no year has a ledger-level trial balance to rebuild from" in text


def test_an_unknown_view_is_refused(client: TestClient) -> None:
    page = client.get("/partials/statements?company=acme&view=bogus")
    assert page.status_code == 400
    assert "E001: no statements view &#39;bogus&#39;" in page.text
