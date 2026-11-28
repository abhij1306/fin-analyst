"""The Differences screen (SPEC-15 PR 3): the tree from headline to invoice with explained and
unexplained amounts in the display unit, the over-the-threshold filter, a query's status on its
rows, and the hand-off of ticked rows to the assistant's chat. Needs the `ui` extra (CI's ui job
syncs it); skipped without it."""

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from fin.ui.server import create_app
from tests.unit.test_discrepancies import make_root, reconcile, rows
from tests.unit.test_reconcile_d4 import template  # noqa: F401

TOKEN = "t0ken"
PORT = 8765


@pytest.fixture(scope="module")
def setup(template: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, TestClient]:  # noqa: F811
    root = make_root(template, tmp_path_factory.mktemp("ui_differences"))
    reconcile(root)
    client = TestClient(
        create_app(root, TOKEN, PORT), base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False
    )
    client.get(f"/?token={TOKEN}")
    return root, client


def item(root: Path, level: str, key: str) -> int:
    [found] = [r["item_id"] for r in rows(root) if (r["level"], r["key"]) == (level, key)]
    return found


def test_the_tree_runs_from_the_headline_to_the_invoice(setup: tuple[Path, TestClient]) -> None:
    root, client = setup
    page = client.get("/differences?company=acme")

    assert page.status_code == 200
    assert 'id="tab-differences"' in page.text
    revenue = page.text.split(f"#{item(root, 'headline', 'revenue')} FY25 revenue", 1)[1]
    head = " ".join(revenue.split("</summary>", 1)[0].split())
    assert "trial balance 0.8, statements 1.0, difference <strong>(0.2)</strong>" in head
    assert "explained below 0.3, unexplained <strong>(0.5)</strong>" in head
    party = revenue.split('<ul class="tree">', 1)[1]
    assert party.index("CUST-0001") < party.index("INV-2003")
    assert re.search(r'name="item" value="(\d+)"', page.text) is not None


def test_the_over_threshold_filter_and_the_unit_picker(setup: tuple[Path, TestClient]) -> None:
    root, client = setup
    text = client.get("/partials/differences?company=acme&over=1&unit=lakhs").text

    assert "INR lakh" in text
    assert "INV-2003" in text  # over the threshold
    assert "INV-2005" not in text  # an fx line below it
    assert f"#{item(root, 'headline', 'other_expenses')} " not in text
    every = client.get("/partials/differences?company=acme").text
    assert f"<h2>{len(rows(root))} differences</h2>" in every
    shown = re.search(r"<h2>(\d+) differences</h2>", text)
    assert shown is not None and int(shown.group(1)) == text.count("<summary>")


def test_ticked_rows_open_the_chat_with_a_request_to_draft_one_query(
    setup: tuple[Path, TestClient],
) -> None:
    root, client = setup
    ids = [item(root, "transaction", k) for k in ("INV-2003", "INV-2004")]
    answer = client.get(
        "/partials/differences-ask", params={"company": "acme", "item": [str(i) for i in ids]}
    )

    assert answer.status_code == 204
    location = json.loads(answer.headers["HX-Location"])
    assert (location["target"], location["swap"]) == ("#chat", "outerHTML")
    url = urlparse(location["path"])
    assert url.path == "/chat/panel"
    prefill = parse_qs(url.query)["prefill"][0]
    assert f"fin query raise acme --items {ids[0]},{ids[1]} --question" in prefill
    assert "Do not approve or send it" in prefill


def test_a_row_that_cannot_be_queried_is_refused(setup: tuple[Path, TestClient]) -> None:
    root, client = setup
    fx = item(root, "transaction", "INV-2005")
    refused = client.get("/partials/differences-ask", params={"company": "acme", "item": [str(fx)]})
    none = client.get("/partials/differences-ask", params={"company": "acme"})

    assert refused.status_code == 400
    assert f"E008: rows #{fx} cannot be queried" in refused.text
    assert none.status_code == 400
