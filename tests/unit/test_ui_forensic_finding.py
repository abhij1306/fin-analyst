"""The Forensic screen's hand-off of ticked flags to an FDD finding draft (SPEC-21): the chat opens
with a request to run `fin fdd finding propose --area forensic --flags`, naming the query raised on
the flags when there is one; a flag of a stale run, or none, is refused (E008). Needs the `ui`
extra (CI's ui job syncs it); skipped without it."""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from fin.actor import USER
from fin.forensic import settings
from fin.forensic.handoff import raise_from_flags
from fin.services import forensic as services
from fin.ui.server import create_app
from tests.unit import forensic_mock as fm

TOKEN = "t0ken"
PORT = 8767
HOST = f"127.0.0.1:{PORT}"
CO = fm.COMPANY


@pytest.fixture(scope="module")
def setup(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, TestClient]:
    root = fm.build(tmp_path_factory.mktemp("ui_forensic_finding") / "data")
    services.run_tests(root, CO, via="cli", actor=USER)
    client = TestClient(create_app(root, TOKEN, PORT), base_url=f"http://{HOST}")
    client.get(f"/?token={TOKEN}")
    return root, client


def ids(root: Path, test: str) -> list[str]:
    return [str(f["flag_id"]) for f in services.read_flags(fm.run(root), root, CO, test=test)]


def prefill(client: TestClient, items: list[str]) -> str:
    answer = client.get("/partials/forensic-finding", params={"company": CO, "item": items})
    assert answer.status_code == 204, answer.text
    location = json.loads(answer.headers["HX-Location"])
    return parse_qs(urlparse(location["path"]).query)["prefill"][0]


def test_the_screen_offers_the_finding_hand_off(setup: tuple[Path, TestClient]) -> None:
    _, client = setup
    page = client.get(f"/forensic?company={CO}").text

    assert 'hx-get="/partials/forensic-finding"' in page
    assert "Ask the assistant to propose a finding" in page


def test_ticked_flags_open_the_chat_with_a_request_to_propose_a_finding(
    setup: tuple[Path, TestClient],
) -> None:
    root, client = setup
    rare = ids(root, "je_rare_ledger")  # several periods: one finding may span them
    text = prefill(client, rare)

    assert f"fin fdd finding propose {CO} --area forensic --flags {','.join(rare)}" in text
    assert "Do not mark it final" in text and "--query" not in text

    weekend = ids(root, "je_weekend")
    query = raise_from_flags(fm.run(root), root, CO, [int(i) for i in weekend], "Why?")
    assert f"--query {query['query_id']}" in prefill(client, weekend)
    assert "--query" not in prefill(client, weekend + rare[:1])  # not every flag is in it


def test_no_flag_or_a_stale_one_is_refused(setup: tuple[Path, TestClient]) -> None:
    root, client = setup
    none = client.get("/partials/forensic-finding", params={"company": CO})
    assert none.status_code == 400

    settings.set_value(fm.run(root), root, CO, "round_unit", "100000", USER)
    stale = client.get(
        "/partials/forensic-finding", params={"company": CO, "item": ids(root, "je_round")}
    )
    assert stale.status_code == 400 and "not of a current run" in stale.text
