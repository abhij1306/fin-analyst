"""The UI's decision buttons and uploads (docs/web-ui-plan.md): each calls the CLI's function in an
audited run, refuses what the CLI refuses, shows the code and run_id, and is protected against
requests from other sites. Needs the `ui` extra; skipped without it."""

from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from fin.audit.logger import Run  # noqa: E402
from fin.db.conn import connect  # noqa: E402
from fin.diligence import queries  # noqa: E402
from fin.paths import audit_db_path  # noqa: E402
from fin.ui import uploads  # noqa: E402
from fin.ui.actions import perform  # noqa: E402
from fin.ui.server import create_app  # noqa: E402
from tests.engagement_root import engagement_root  # noqa: E402

TOKEN = "t0ken"
PORT = 8765
HOST = f"127.0.0.1:{PORT}"
ORIGIN = {"Origin": f"http://{HOST}"}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return engagement_root(tmp_path)


@pytest.fixture
def client(root: Path) -> TestClient:
    client = TestClient(
        create_app(root, TOKEN, PORT), base_url=f"http://{HOST}", follow_redirects=False
    )
    client.get(f"/?token={TOKEN}")
    return client


def act(client: TestClient, action: str, item: str, reason: str = "", **headers: str):
    data = {"action": action, "company": "acme", "item": item, "reason": reason}
    return client.post("/act", data=data, headers=ORIGIN | headers)


def runs(root: Path) -> list[tuple[str, str]]:
    with connect(audit_db_path(root)) as con:
        return con.execute(
            "SELECT command, status FROM runs WHERE record = 'end' ORDER BY started_at"
        ).fetchall()


def raise_query(root: Path) -> int:
    row = queries.raise_manual(
        Run("query raise", {}, root),
        "acme",
        amount=Decimal(500000),
        period="FY25",
        scope=None,
        sources="a.xlsx Sheet1!B2",
        question="Why?",
    )
    return int(row["query_id"])


def test_a_post_needs_an_origin_that_is_this_server(client: TestClient) -> None:
    for headers in ({}, {"Origin": "http://evil.example"}, {"Origin": "http://127.0.0.1:1"}):
        data = {"action": "gate-approve", "company": "acme", "item": "G1"}
        response = client.post("/act", data=data, headers=headers)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "E007"
    upload = client.post(
        "/upload", data={"company": "acme"}, headers={"Origin": "http://evil.example"}
    )
    assert upload.status_code == 403


def test_a_post_needs_the_token_cookie(root: Path) -> None:
    bare = TestClient(create_app(root, TOKEN, PORT), base_url=f"http://{HOST}")
    response = bare.post("/act", data={"action": "gate-approve"}, headers=ORIGIN)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "E007"
    assert runs(root) == []


def test_a_defect_in_an_action_shows_e012_and_the_run_id_instead_of_raising(root: Path) -> None:
    def body(run: Run) -> list[str]:
        raise TypeError("sequence item 4: expected str instance, NoneType found")

    outcome = perform(root, "probe", {}, body)

    assert (outcome.ok, outcome.code) == (False, "E012")
    assert outcome.run_id is not None
    [line] = outcome.lines
    assert line.startswith(
        "unexpected TypeError: sequence item 4: expected str instance, NoneType found. This is a "
        f"defect in fin: report E012 and run_id {outcome.run_id}; the traceback is in logs/"
    )
    assert ("probe", "error") in runs(root)


def test_gate_approve_refuses_what_the_cli_refuses_and_shows_code_and_run_id(
    client: TestClient, root: Path
) -> None:
    page = act(client, "gate-approve", "G2").text
    assert "Not done: E802" in page
    assert "cannot be approved before" in page
    assert "run_id " in page
    assert ("gate approve", "blocked") in runs(root)
    assert "<h1>Gates</h1>" in page  # the screen is shown again


def test_the_gates_screen_confirms_approval_in_a_dialog(client: TestClient) -> None:
    page = client.get("/partials/gates?company=acme").text
    assert '<dialog id="d-gate-approve-G1"' in page
    assert 'hx-post="/act"' in page


def test_a_query_goes_from_draft_to_approved_to_sent_in_logged_runs(
    client: TestClient, root: Path
) -> None:
    query_id = str(raise_query(root))
    assert "approved" in act(client, "query-approve", query_id).text
    assert "Mark sent" in client.get("/partials/queries?company=acme").text
    assert "sent" in act(client, "query-sent", query_id).text
    assert ("query approve", "ok") in runs(root)
    assert ("query sent", "ok") in runs(root)
    again = act(client, "query-approve", query_id).text
    assert "Not done: E805" in again  # only a draft can be approved


def test_withdrawing_needs_a_reason_and_runs_nothing_without_it(
    client: TestClient, root: Path
) -> None:
    query_id = str(raise_query(root))
    before = runs(root)
    page = act(client, "query-withdraw", query_id).text
    assert "Not done: E008" in page
    assert runs(root) == before
    done = act(client, "query-withdraw", query_id, reason="duplicate").text
    assert "withdrawn" in done


@pytest.mark.parametrize(
    ("action", "code"),
    [
        ("adjust-accept", "E801"),  # the CLI refuses it too: gate G2 is not approved
        ("fdd-final", "E971"),
        ("fdd-draft", "E971"),
        ("mapping-reject", "E304"),
        ("finding-accept", "E406"),
        ("query-close", "E805"),
    ],
)
def test_an_unknown_item_is_refused_with_the_cli_error(
    client: TestClient, action: str, code: str
) -> None:
    assert f"Not done: {code}" in act(client, action, "99").text


def test_a_bad_action_or_id_is_e008(client: TestClient) -> None:
    assert "E008" in act(client, "no-such-action", "1").text
    assert "E008" in act(client, "query-approve", "1 OR 1=1").text
    assert "E008" in act(client, "query-approve", "²").text  # a digit, but not 0-9
    assert "E008" in act(client, "query-approve", "9" * 19).text  # past a BIGINT
    assert "E008" in act(client, "gate-approve", "G9").text
    assert "E008" in act(client, "adjust-reject", "1").text  # reason missing


def test_an_unknown_company_is_e001(client: TestClient) -> None:
    data = {"action": "gate-approve", "company": "ghost", "item": "G1"}
    assert "E001" in client.post("/act", data=data, headers=ORIGIN).text


def upload(client: TestClient, files: list[tuple[str, bytes]], company: str = "acme"):
    parts = [("files", (name, body, "application/octet-stream")) for name, body in files]
    return client.post("/upload", data={"company": company}, files=parts, headers=ORIGIN)


def test_an_upload_lands_in_the_inbox_and_is_not_ingested(client: TestClient, root: Path) -> None:
    page = upload(client, [("Q1 results.xlsx", b"x"), ("c:\\docs\\audit.pdf", b"%PDF")]).text
    assert "Q1 results.xlsx is waiting in inbox/acme/" in page
    assert "ask Claude to ingest" in page
    assert sorted(p.name for p in (root / "inbox" / "acme").iterdir()) == [
        "Q1 results.xlsx",
        "audit.pdf",
    ]
    assert ("ui upload", "ok") in runs(root)
    assert not any(c == "ingest" for c, _ in runs(root))


@pytest.mark.parametrize(
    "name", ["../evil.xlsx", ".hidden.xlsx", "a..b.xlsx", "notes.txt", "run.exe", "x;rm.xlsx"]
)
def test_an_unsafe_or_unsupported_name_is_refused_and_nothing_is_written(
    client: TestClient, root: Path, name: str
) -> None:
    page = upload(client, [("ok.xlsx", b"x"), (name, b"x")]).text
    if name.startswith("../"):  # a path is cut to its last part: only that name is checked
        assert "evil.xlsx is waiting" in page
        assert not (root / "evil.xlsx").exists()
        return
    assert "Not done: E008" in page
    assert not (root / "inbox" / "acme").exists()


def test_an_existing_file_is_not_overwritten(client: TestClient, root: Path) -> None:
    upload(client, [("a.xlsx", b"first")])
    assert "Not done: E008" in upload(client, [("a.xlsx", b"second")]).text
    assert (root / "inbox" / "acme" / "a.xlsx").read_bytes() == b"first"


def test_a_file_over_the_limit_is_refused(
    client: TestClient, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(uploads, "MAX_FILE_BYTES", 3)
    assert "Not done: E008" in upload(client, [("big.xlsx", b"12345")]).text
    assert not (root / "inbox" / "acme").exists()


def test_an_upload_to_an_unknown_company_is_e001(client: TestClient) -> None:
    assert "E001" in upload(client, [("a.xlsx", b"x")], company="ghost").text
