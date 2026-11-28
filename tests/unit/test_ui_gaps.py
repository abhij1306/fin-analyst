"""The web UI gaps the system review left open (#138): provisions and net debt items decided on the
Findings screen, every waiting kind counted on Home and the tabs, the screen refreshed when a job
ends, a data-room upload saved in a run of its own, and a form with no company refused. Needs the
`ui` extra; skipped without it."""

import io
import json
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient

from fin.audit.logger import Run
from fin.db.conn import connect, utcnow
from fin.diligence import netdebt_items
from fin.paths import audit_db_path, company_db_path, dataroom_dir
from fin.services.jobs import Jobs
from fin.ui.server import create_app
from tests.engagement_root import engagement_root

TOKEN = "t0ken"
PORT = 8765
HOST = f"127.0.0.1:{PORT}"
ORIGIN = {"Origin": f"http://{HOST}"}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = engagement_root(tmp_path)
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "INSERT INTO provision_ledgers (ledger, kind, status, reason, run_id, changed_at) "
            "VALUES ('Provision for doubtful debts', 'doubtful_debts', 'proposed', 'by name', "
            "'r0', now())"
        )
    netdebt_items.propose(
        Run("netdebt item propose", {}, root), "acme", "FY25", Decimal(100), "shortfall", "q 1"
    )
    return root


@pytest.fixture
def client(root: Path) -> TestClient:
    client = TestClient(
        create_app(root, TOKEN, PORT), base_url=f"http://{HOST}", follow_redirects=False
    )
    client.get(f"/?token={TOKEN}")
    return client


def act(client: TestClient, action: str, item: str, reason: str = "", company: str = "acme"):
    data = {"action": action, "company": company, "item": item, "reason": reason}
    return client.post("/act", data=data, headers=ORIGIN)


def runs(root: Path) -> list[tuple[str, dict[str, Any]]]:
    with connect(audit_db_path(root)) as con:
        rows = con.execute(
            "SELECT command, args FROM runs WHERE record = 'start' ORDER BY started_at"
        ).fetchall()
    return [(command, json.loads(args)) for command, args in rows]


def test_provisions_and_net_debt_items_are_the_users_clicks_on_the_findings_screen(
    client: TestClient, root: Path
) -> None:
    page = client.get("/partials/findings?company=acme").text
    assert "Provision for doubtful debts" in page
    assert "shortfall" in page
    assert 'value="provision-confirm"' in page
    assert 'value="netdebt-item-accept"' in page
    assert runs(root) == []  # showing them decides nothing

    assert "Done" in act(client, "provision-confirm", "1").text
    refused = act(client, "netdebt-item-reject", "1").text  # the reason is required
    assert "E008" in refused
    assert "Done" in act(client, "netdebt-item-reject", "1", "not debt-like").text

    decided = [(c, a["via"], a["actor"]) for c, a in runs(root)]
    assert decided == [("provisions confirm", "ui", "user"), ("netdebt item reject", "ui", "user")]
    with connect(company_db_path(root, "acme")) as con:
        assert con.execute("SELECT status FROM provision_ledgers").fetchall() == [("confirmed",)]
        assert con.execute("SELECT status, reason FROM netdebt_items").fetchall() == [
            ("rejected", "not debt-like")
        ]


def test_home_and_the_tabs_count_provisions_and_net_debt_items_on_the_findings_screen(
    client: TestClient,
) -> None:
    home = client.get("/?company=acme").text
    assert "1 provision ledgers to confirm or reject" in home
    assert "1 net debt items to accept or reject" in home
    assert "Open Findings" in home
    tabs = client.get("/partials/tabs?screen=findings&company=acme").text
    assert 'id="tab-findings"' in tabs
    assert '<span class="badge" data-variant="primary">2</span></a>' in tabs  # on Findings
    assert 'aria-current="page"' in tabs


def test_the_tabs_partial_refuses_a_screen_that_does_not_exist(client: TestClient) -> None:
    assert "E001" in client.get("/partials/tabs?screen=nowhere&company=acme").text


def _job(root: Path, state: str) -> None:
    with connect(audit_db_path(root), write=True) as con:
        con.execute(
            "INSERT INTO jobs (company, action, arguments, state, queued_at) "
            "VALUES ('acme', 'export', '{}', ?, ?)",
            [state, utcnow()],
        )


def test_the_jobs_card_tells_the_screen_when_the_last_job_has_ended(
    client: TestClient, root: Path
) -> None:
    polled = {"HX-Request": "true", "HX-Trigger": "jobs"}
    _job(root, "running")
    assert "HX-Trigger" not in client.get("/partials/jobs?company=acme", headers=polled).headers
    with connect(audit_db_path(root), write=True) as con:
        con.execute("UPDATE jobs SET state = 'done'")
    ended = client.get("/partials/jobs?company=acme", headers=polled)
    assert ended.headers["HX-Trigger"] == "job-done"
    assert "HX-Trigger" not in client.get("/partials/jobs?company=acme").headers  # not polled


def test_the_parts_that_show_a_jobs_result_ask_again_when_it_ends(client: TestClient) -> None:
    listens = 'hx-trigger="job-done from:body"'
    for path, part in (
        ("/reports", 'id="report-files"'),
        ("/dashboards", 'id="dashboard-frame"'),
        ("/statements", 'id="statements-export"'),
        ("/files", 'id="tabs"'),
    ):
        page = client.get(f"{path}?company=acme").text
        start = page.index(part)
        tag = page[page.rindex("<", 0, start) : page.index(">", start)]
        assert listens in tag, path


def test_an_act_with_no_company_is_the_e008_alert_and_never_the_first_company(
    client: TestClient, root: Path
) -> None:
    for data in (
        {"action": "provision-confirm", "company": "", "item": "1"},
        {"action": "provision-confirm", "item": "1"},
    ):
        response = client.post("/act", data=data, headers=ORIGIN)
        assert response.status_code == 200
        assert "E008" in response.text
        assert "choose the company; nothing changed" in response.text
    assert runs(root) == []


def test_a_data_room_upload_is_saved_in_a_run_of_its_own(
    client: TestClient, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queued: list[dict[str, Any]] = []
    monkeypatch.setattr(Jobs, "submit", lambda self, co, action, args: queued.append(args) or 1)
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w") as archive:
        archive.writestr("notes.txt", "synthetic")
    sent = [("files", ("room.zip", packed.getvalue()))]

    response = client.post("/dataroom-upload", data={"company": "acme"}, files=sent, headers=ORIGIN)
    refused = client.post(
        "/dataroom-upload", data={"company": "acme"}, files=[("files", ("a.xlsx", b"x"))],
        headers=ORIGIN,
    )  # fmt: skip

    assert response.headers["HX-Trigger"] == "job-queued"
    [(command, args), (again, _)] = runs(root)
    assert (command, again, args["via"], args["actor"]) == (
        "dataroom upload", "dataroom upload", "ui", "user",
    )  # fmt: skip
    assert queued == [{"folder": args["folder"]}]
    assert (Path(args["folder"]) / "room.zip").is_file()
    assert refused.status_code == 400
    assert "E008" in refused.text
    assert "run " in refused.text
    assert list((dataroom_dir(root, "acme") / "uploads").iterdir()) == [Path(args["folder"])]
