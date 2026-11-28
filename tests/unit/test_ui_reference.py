"""The References screen (SPEC-10 PR 3) and the data-room plan on the Files screen (SPEC-13 PR 3):
the comparison with its hand-offs to the chat, the user's confirmation of a reference line, and a
data room scanned (a folder or an uploaded .zip), approved row by row and loaded as a job. Needs
the `ui` extra; skipped without it."""

import io
import json
import sys
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from fin.cli import app  # noqa: E402
from fin.db.conn import connect  # noqa: E402
from fin.paths import company_db_path, dataroom_dir  # noqa: E402
from fin.services.jobs import list_jobs  # noqa: E402
from fin.ui.server import create_app  # noqa: E402
from tests.unit.test_reference import PLANTED, fin_lines, write_databook  # noqa: E402

pytestmark = pytest.mark.timeout(240)  # the first test also ingests the shared kestrel template
TOKEN = "t0ken"
PORT = 8765
HOST = f"127.0.0.1:{PORT}"
ORIGIN = {"Origin": f"http://{HOST}"}
FAKE = [sys.executable, str(Path(__file__).parents[1] / "fake_claude.py")]
runner = CliRunner()


def fin(*args: str) -> str:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result.output


def query(root: Path, company: str, sql: str) -> list[tuple[Any, ...]]:
    with connect(company_db_path(root, company)) as con:
        return con.execute(sql).fetchall()


def make_client(root: Path) -> TestClient:
    client = TestClient(
        create_app(root, TOKEN, PORT, FAKE), base_url=f"http://{HOST}", follow_redirects=False
    )
    client.get(f"/?token={TOKEN}")
    return client


def settled(root: Path, company: str) -> list[dict[str, Any]]:
    for _ in range(240):  # a slow poll: each read makes the job's writes wait (DuckDB, one process)
        time.sleep(0.5)
        jobs = list_jobs(root, company)
        if jobs and all(j["state"] not in ("queued", "running") for j in jobs):
            return jobs
    raise AssertionError(f"jobs did not end: {list_jobs(root, company)}")


@pytest.fixture
def kestrel(
    clean_root: Callable[..., Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "calls.jsonl"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return clean_root("kestrel")


def databook(root: Path, path: Path) -> Path:
    scope, lines = fin_lines(root)
    shown = [
        (label, value + (PLANTED[i] if i < 3 else 0)) for i, (label, _, value) in enumerate(lines)
    ]
    return write_databook(path, scope, [*shown, ("Banker normalised overheads", 7)])


def test_the_references_screen_lists_the_differences_and_takes_the_users_confirmation(
    kestrel: Path, tmp_path: Path
) -> None:
    fin(
        "reference",
        "add",
        "kestrel",
        str(databook(kestrel, tmp_path / "db.xlsx")),
        "--source",
        "Banker",
    )
    client = make_client(kestrel)

    page = client.get("/references?company=kestrel").text

    assert "3 differ · 3 agree · 0 with no fin figure · 1 not mapped" in page
    # a query or an adjustment needs an open engagement: no hand-off is offered before one
    assert ">Draft a query<" not in page and "needs an open engagement" in page
    fin("engagement", "open", "kestrel", "--periods", "FY25")
    page = client.get("/references?company=kestrel").text
    assert page.count(">Draft a query<") == 3 and page.count(">Propose an adjustment<") == 3
    assert page.count("at or above the threshold") == 1
    assert "Profit and Loss!B7" in page
    [(line_id,)] = query(
        kestrel, "kestrel", "SELECT line_id FROM reference_lines WHERE status = 'pending'"
    )
    assert f"reference line #{line_id} of kestrel" in page  # Ask for a proposal, in the chat
    proposals = tmp_path / "p.json"
    proposals.write_text(
        json.dumps(
            [{"line": line_id, "account": "other_expenses", "confidence": 0.6, "reason": "r"}]
        ),
        encoding="utf-8",
    )
    fin("reference", "propose", "kestrel", str(proposals))
    form = {"action": "reference-confirm", "company": "kestrel", "item": str(line_id)}

    acted = client.post("/act", data=form, headers=ORIGIN).text

    assert "confirmed" in acted and "No line waits for a mapping." in acted
    assert query(
        kestrel, "kestrel", f"SELECT status, actor FROM reference_lines WHERE line_id = {line_id}"
    ) == [("confirmed", "user")]


def test_a_scanned_data_room_is_approved_row_by_row_and_loaded_as_a_job(
    kestrel: Path, tmp_path: Path
) -> None:
    room = tmp_path / "room"
    room.mkdir()
    databook(kestrel, room / "banker.xlsx")
    (room / "notes.txt").write_text("synthetic", encoding="utf-8")
    client = make_client(kestrel)
    assert "No data-room plan." in client.get("/files?company=kestrel").text
    assert (
        client.post(
            "/job",
            data={"action": "dataroom-scan", "company": "kestrel", "item": str(tmp_path / "none")},
            headers=ORIGIN,
        ).status_code
        == 400
    )

    client.post(
        "/job",
        data={"action": "dataroom-scan", "company": "kestrel", "item": str(room)},
        headers=ORIGIN,
    )
    assert settled(kestrel, "kestrel")[0]["code"] is None
    rows = client.get("/partials/file-rows?company=kestrel").text
    assert "banker.xlsx" in rows and "reference databook" in rows and "Load approved (0)" in rows
    assert "notes.txt" in rows and "Skipped file" in rows
    [(file_id,)] = query(
        kestrel, "kestrel", "SELECT file_id FROM dataroom_files WHERE path = 'banker.xlsx'"
    )

    approved = client.post(
        "/act",
        data={"action": "dataroom-approve", "company": "kestrel", "item": str(file_id)},
        headers=ORIGIN,
    ).text
    assert "Load approved (1)" in approved and "Undo approval" in approved
    client.post("/job", data={"action": "dataroom-load", "company": "kestrel"}, headers=ORIGIN)

    [load, _] = settled(kestrel, "kestrel")
    assert load["code"] is None and "banker.xlsx: reference file #1: 7 figures" in load["lines"][0]
    assert query(kestrel, "kestrel", "SELECT source FROM reference_files") == [("banker.xlsx",)]
    assert "Load approved (0)" in client.get("/partials/file-rows?company=kestrel").text


def test_an_uploaded_zip_is_scanned_and_anything_else_is_refused(
    data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fin("init")
    client = make_client(data_root)
    client.post("/company", data={"code": "acme", "name": "Acme Synthetic Ltd"}, headers=ORIGIN)
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w") as archive:
        archive.writestr("notes.txt", "synthetic")
    send = {"company": "acme"}

    refused = client.post(
        "/dataroom-upload", data=send, files=[("files", ("a.xlsx", b"x"))], headers=ORIGIN
    )
    client.post(
        "/dataroom-upload",
        data=send,
        files=[("files", ("room.zip", packed.getvalue()))],
        headers=ORIGIN,
    )

    assert refused.status_code == 400 and "E008" in refused.text
    assert settled(data_root, "acme")[0]["code"] is None
    paths = query(
        data_root, "acme", "SELECT path, kind, reason IS NULL FROM dataroom_files ORDER BY path"
    )
    assert paths == [("room.zip", "archive", True), ("room.zip/notes.txt", "unknown", False)]
    assert (
        len(list((dataroom_dir(data_root, "acme") / "uploads").iterdir())) == 1
    )  # the refused one left none
