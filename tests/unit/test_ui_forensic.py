"""The Forensic screen and the forensic settings on the Settings screen (SPEC-18 PR 1): the status
per test and period, the flags by test with their source, the hand-off of ticked flags to the
assistant's chat, and a setting changed from the Settings screen. Needs the `ui` extra (CI's ui
job syncs it); skipped without it."""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from fin.actor import USER
from fin.services import forensic as services
from fin.ui.server import create_app
from tests.unit import forensic_mock as fm

TOKEN = "t0ken"
PORT = 8766
HOST = f"127.0.0.1:{PORT}"
CO = fm.COMPANY


@pytest.fixture(scope="module")
def setup(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, TestClient]:
    root = fm.build(tmp_path_factory.mktemp("ui_forensic") / "data")
    services.run_tests(root, CO, via="cli", actor=USER)
    client = TestClient(create_app(root, TOKEN, PORT), base_url=f"http://{HOST}")
    client.get(f"/?token={TOKEN}")
    return root, client


def flag_ids(root: Path, test: str) -> list[str]:
    return [str(f["flag_id"]) for f in services.read_flags(fm.run(root), root, CO, test=test)]


def test_the_screen_shows_each_state_and_the_flags_by_test(setup: tuple[Path, TestClient]) -> None:
    root, client = setup
    page = client.get(f"/forensic?company={CO}")

    assert page.status_code == 200
    assert 'id="tab-forensic"' in page.text
    assert "ran: 2 flagged, 2 at or above the threshold" in page.text
    assert "cannot run: needs the previous period" in page.text
    assert f"{len(services.read_flags(fm.run(root), root, CO))} flags" in page.text
    assert "Period-end revenue" in page.text and "No sales register" in page.text
    assert "corvane_ledger_vouchers_fy25.xlsx" in page.text
    assert f'name="item" value="{flag_ids(root, "je_gap")[0]}"' in page.text
    assert 'value="forensic-run"' in page.text
    # A finished run job refreshes the status and the flags without a reload.
    assert f'hx-get="/partials/forensic?company={CO}" hx-trigger="job-done from:body"' in page.text


def test_ticked_flags_open_the_chat_with_a_request_to_draft_one_query(
    setup: tuple[Path, TestClient],
) -> None:
    root, client = setup
    ids = flag_ids(root, "je_round")
    answer = client.get("/partials/forensic-ask", params={"company": CO, "item": ids})

    assert answer.status_code == 204
    location = json.loads(answer.headers["HX-Location"])
    prefill = parse_qs(urlparse(location["path"]).query)["prefill"][0]
    assert f"fin query raise {CO} --flags {','.join(ids)} --question" in prefill
    assert "Do not approve or send it" in prefill


def test_flags_of_two_periods_or_none_are_refused(setup: tuple[Path, TestClient]) -> None:
    root, client = setup
    rare = services.read_flags(fm.run(root), root, CO, test="je_rare_ledger")
    two = [str(f["flag_id"]) for f in rare if f["period"] == "FY23"][:1] + [
        str(f["flag_id"]) for f in rare if f["period"] == "FY25"
    ][:1]
    mixed = client.get("/partials/forensic-ask", params={"company": CO, "item": two})
    none = client.get("/partials/forensic-ask", params={"company": CO})

    assert mixed.status_code == 400 and "one query per period" in mixed.text
    assert none.status_code == 400


def test_a_forensic_setting_is_changed_on_the_settings_screen(
    setup: tuple[Path, TestClient],
) -> None:
    root, client = setup
    page = client.get(f"/settings?company={CO}")
    assert "approval_limits" in page.text and "500000" in page.text

    done = client.post(
        "/forensic/set",
        data={"company": CO, "key": "period_end_days", "value": "4"},
        headers={"Origin": f"http://{HOST}"},
    )
    bad = client.post(
        "/forensic/set",
        data={"company": CO, "key": "period_end_days", "value": "four"},
        headers={"Origin": f"http://{HOST}"},
    )

    assert done.status_code == 200
    assert {r["key"]: r["value"] for r in services.read_settings(root, CO)}[
        "period_end_days"
    ] == "4"
    assert "E990" in bad.text
    stale = client.get(f"/forensic?company={CO}").text
    assert "stale (data or settings changed since" in stale
