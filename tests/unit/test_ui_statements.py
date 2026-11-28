"""The Statements screen (SPEC-06, SPEC-14 PR 3): the databook layout with its pickers and source
drawer, and reported, rebuilt and difference side by side; money in the display unit. Needs the
`ui` extra (CI's ui job syncs it); skipped without it."""

import re
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
    assert "Profit and loss, standalone" in page.text
    assert "INR Mn" in page.text
    row = page.text.split("Revenue from operations", 1)[1].split("</tr>", 1)[0]
    assert re.findall(r'hx-target="#source"[^>]*>([^<]+)</a>', row) == ["2.5"]
    assert '<select name="unit" class="select">' in page.text


def test_the_pickers_change_the_unit_the_statement_and_add_the_ratios(client: TestClient) -> None:
    text = client.get(
        "/partials/statements?company=acme&statement=profit_and_loss&unit=lakhs&of_revenue=1"
    ).text
    assert "Balance sheet, standalone" not in text
    assert "INR lakh" in text
    row = text.split("Revenue from operations", 1)[1].split("</tr>", 1)[0]
    cells = re.findall(r'<td class="num">(?:<a[^>]*>)?([^<]*)', row)
    assert cells == ["25.0", "100.0%"]


def test_a_figure_opens_its_source(client: TestClient) -> None:
    page = client.get("/statements?company=acme").text
    link = re.search(r'hx-get="(/partials/statement-source[^"]+)"', page)
    assert link is not None
    text = client.get(link.group(1).replace("&amp;", "&")).text
    assert "Revenue from operations" in text
    assert "(stored: 2500000.00 rupees)" in text
    assert "<code>" in text  # the file and sheet!cell
    missing = client.get(
        "/partials/statement-source?company=acme&scope=standalone&period=FY25&line=999"
    )
    assert missing.status_code == 400
    assert "E001: no figure of acme at standalone FY25 line" in missing.text


def test_the_rebuilt_view_shows_reported_rebuilt_and_difference(client: TestClient) -> None:
    text = client.get("/partials/statements?company=acme&view=rebuilt").text
    assert "<html" not in text
    assert re.findall(r"<th[^>]*>([^<]+)</th>", text)[:4] == [
        "Line",
        "Reported",
        "Rebuilt",
        "Difference",
    ]
    row = text.split("Revenue from operations", 1)[1].split("</tr>", 1)[0]
    cells = [c.split("<", 1)[0] for c in row.split('<td class="num">')[1:4]]
    assert cells == ["2.5", "2.4", "(0.1)"]
    assert '<td class="sub">tb_FY25.xlsx Trial Balance!23</td>' in text
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


def test_an_unknown_unit_or_picker_is_refused(client: TestClient) -> None:
    unit = client.get("/partials/statements?company=acme&unit=billions")
    assert unit.status_code == 400
    assert "E001: no display unit &#39;billions&#39;" in unit.text
    statement = client.get("/partials/statements?company=acme&statement=notes")
    assert statement.status_code == 400
    assert "E001: no statements statement &#39;notes&#39;" in statement.text
