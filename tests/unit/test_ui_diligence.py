"""SPEC-09: the diligence screens. With no terminal and no chat: open an engagement, import a walk
sheet, approve G1 and G2, accept the adjustments and read the bridge; every decision is the user's.
Needs the `ui` extra; skipped without it."""

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient

from fin.db.conn import connect
from fin.paths import audit_db_path, company_db_path
from fin.ui.diligence_screens import MISSING, bridge_screen
from fin.ui.server import create_app
from fin.ui.views import gate_fix
from tests.diligence_ui_fixtures import ROWS, build_root, write_walk

TOKEN = "t0ken"
PORT = 8765
HOST = f"127.0.0.1:{PORT}"
ORIGIN = {"Origin": f"http://{HOST}"}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return build_root(tmp_path)


@pytest.fixture
def client(root: Path) -> TestClient:
    client = TestClient(
        create_app(root, TOKEN, PORT), base_url=f"http://{HOST}", follow_redirects=False
    )
    client.get(f"/?token={TOKEN}")
    return client


def post(client: TestClient, path: str, **data: str) -> str:
    response = client.post(path, data={"company": "acme", **data}, headers=ORIGIN)
    assert response.status_code == 200, response.text
    return response.text


def act(client: TestClient, action: str, item: str, reason: str = "") -> str:
    return post(client, "/act", action=action, item=item, reason=reason)


def upload(client: TestClient, walk: Path) -> str:
    with walk.open("rb") as handle:
        response = client.post(
            "/adjust/import",
            data={"company": "acme", "sheet": ""},
            files={"file": (walk.name, handle, "application/octet-stream")},
            headers=ORIGIN,
        )
    assert response.status_code == 200, response.text
    return response.text


def opened(client: TestClient) -> None:
    assert "Done" in post(client, "/engagement/open", periods="FY25")


def decisions(root: Path) -> list[tuple[str, dict[str, object]]]:
    with connect(audit_db_path(root)) as con:
        rows = con.execute(
            "SELECT command, args FROM runs WHERE record = 'end' AND status = 'ok' "
            "AND command IN ('gate approve', 'adjust accept', 'engagement open', 'adjust import')"
            " ORDER BY started_at"
        ).fetchall()
    return [(command, json.loads(args)) for command, args in rows]


@pytest.mark.timeout(180)  # a dozen audited runs and the bridge views
def test_the_walk_through_needs_no_terminal_and_every_decision_is_the_users(
    client: TestClient, root: Path, tmp_path: Path
) -> None:
    assert "Open an engagement" in client.get("/engagement?company=acme").text
    opened(client)
    assert "Engagement #1" in client.get("/engagement?company=acme").text
    shown = upload(client, write_walk(tmp_path / "walk.xlsx"))
    assert "acme: 2 adjustments proposed from walk.xlsx" in shown
    for gate in ("G1", "G2"):
        assert "Done" in act(client, "gate-approve", gate), gate
    for adj in ("1", "2"):
        assert "Done" in act(client, "adjust-accept", adj), adj
    bridge = client.get("/bridge?company=acme").text
    # in the display unit of the default config.toml (INR Mn, one decimal, negatives in brackets)
    assert "INR Mn" in bridge
    assert '<td class="num">1.5</td>' in bridge  # reported EBITDA, 15,00,000
    assert '<td class="num">0.1</td>' in bridge  # +1,00,000 legal fees
    assert '<td class="num">(0.2)</td>' in bridge  # -2,00,000 revenue
    assert '<td class="num">1.4</td>' in bridge  # adjusted EBITDA, 14,00,000
    assert "walk.xlsx Walk!G2; Notes p. 12" in bridge
    made = decisions(root)
    assert [c for c, _ in made] == [
        "engagement open", "adjust import", "gate approve", "gate approve", "adjust accept",
        "adjust accept",
    ]  # fmt: skip
    assert {(args["actor"], args["via"]) for _, args in made} == {("user", "ui")}


def test_a_bad_row_changes_nothing_and_names_the_row(
    client: TestClient, root: Path, tmp_path: Path
) -> None:
    opened(client)
    bad = ["FY25", "standalone", "bogus", None, None, -5, "x", None]
    shown = upload(client, write_walk(tmp_path / "walk.xlsx", [ROWS[0], bad]))
    assert "E847" in shown
    assert "row 3 (walk.xlsx Walk!G3)" in shown
    with connect(company_db_path(root, "acme")) as con:
        assert con.execute("SELECT count(*) FROM adjustments").fetchone() == (0,)
    with connect(audit_db_path(root)) as con:
        assert con.execute(
            "SELECT count(*) FROM diligence_events WHERE kind = 'adjustment'"
        ).fetchone() == (0,)


def test_a_refusal_for_an_unapproved_gate_links_to_it(client: TestClient, tmp_path: Path) -> None:
    opened(client)
    upload(client, write_walk(tmp_path / "walk.xlsx"))
    shown = act(client, "adjust-accept", "1")
    assert "E801" in shown
    assert (
        'href="/gates?company=acme#gate-G2"' in shown
        or 'href="/gates?company=acme#gate-G1"' in shown
    )
    gates = client.get("/gates?company=acme").text
    assert 'id="gate-G1"' in gates and 'id="gate-G2"' in gates


def test_queries_are_raised_edited_answered_and_exported_from_the_screen(
    client: TestClient, root: Path
) -> None:
    opened(client)
    raised = post(
        client, "/query/raise", amount="50000", period="FY25", scope="standalone",
        source="afs-FY25.xlsx PL!C5", question="What are the other expenses?",
    )  # fmt: skip
    assert "Done" in raised and "What are the other expenses?" in raised
    edited = post(client, "/query/edit", item="1", question="Break down other expenses.")
    assert "Break down other expenses." in edited
    gates = client.get("/gates?company=acme").text
    assert 'href="/queries?company=acme"' in gates  # the open query's fix is on the Queries screen
    assert "Done" in act(client, "query-approve", "1")
    exported = post(client, "/query/export")
    assert "Done" in exported and ".xlsx" in exported
    assert "Done" in act(client, "query-sent", "1")
    answered = post(client, "/query/answer", item="1", text="Rent and power.", source="email")
    assert "Done" in answered and "answered" in answered
    refused = post(client, "/query/raise", amount="ten", period="FY25", source="x", question="y")
    assert "Not done" in refused
    with connect(company_db_path(root, "acme")) as con:
        assert con.execute("SELECT count(*) FROM accountant_queries").fetchone() == (1,)


def test_the_bridge_shows_the_adjusted_pl_beside_the_reported_one(
    client: TestClient, tmp_path: Path
) -> None:
    assert "No engagement" in client.get("/bridge?company=acme").text
    opened(client)
    upload(client, write_walk(tmp_path / "walk.xlsx"))
    bridge = client.get("/bridge?company=acme").text
    assert "Not applied" in bridge or "not applied" in bridge  # proposed shown apart
    pl = client.get("/bridge?company=acme&view=pl").text
    assert "profit and loss, adjusted" in pl.lower()
    assert "balance sheet, as reported" in pl.lower()
    assert client.get("/bridge?company=acme&view=nope").status_code != 200


def test_a_year_without_ebitda_shows_n_a_not_a_dash_that_reads_as_zero(root: Path) -> None:
    with connect(company_db_path(root, "acme"), write=True) as con:
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, status, opened_run_id, opened_at) "
            "VALUES ('FY24,FY25', 100000, 'open', 'r0', now())"
        )

    blocks = {b["title"]: b for b in bridge_screen(root, "acme")["blocks"]}

    assert [s["amount"] for s in blocks["standalone FY24"]["steps"]] == [MISSING, MISSING]
    assert [s["amount"] for s in blocks["standalone FY25"]["steps"]] == ["1.5", "1.5"]
    assert MISSING == "n/a"


def test_an_engagement_opens_once_and_closes_from_the_screen(client: TestClient) -> None:
    opened(client)
    again = post(client, "/engagement/open", periods="FY25")
    assert "E803" in again
    bad = post(client, "/engagement/open", periods="FY25", threshold="-1")
    assert "Not done" in bad
    closed = post(client, "/engagement/close")
    assert "Done" in closed
    assert "Open an engagement" in client.get("/engagement?company=acme").text


@pytest.mark.parametrize(
    ("line", "fix"),
    [
        ("3 labels wait for review (`fin review list acme`)", "mappings"),
        ("2 net debt items wait (`fin review list acme --netdebt`)", None),
        ("tax ledgers wait (`fin review list acme --tax`)", None),
        ("1 open finding (`fin findings list acme`)", "files"),
        ("2 draft queries (`fin query list acme`)", "queries"),
        ("no command here", None),
    ],
)
def test_a_failing_gate_line_links_to_the_screen_that_closes_it_or_to_claude(
    line: str, fix: str | None
) -> None:
    found = gate_fix(line)
    assert (found["screen"] if found else None) == fix
