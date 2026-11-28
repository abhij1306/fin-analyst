"""The Files screen (docs/specs/SPEC-03): create a company, upload, ingest as a job with one outcome
row per file, the next step of each failure, re-ingest, delete, and a reading started from a failed
file. Every chat turn runs tests/fake_claude.py. Needs the `ui` extra; skipped without it."""

import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient
from markupsafe import escape
from typer.testing import CliRunner

from fin.cli import app
from fin.db.conn import connect, utcnow
from fin.ingest import UNIT_ANSWERS
from fin.map.review import read_pending
from fin.paths import audit_db_path, company_db_path
from fin.services.jobs import list_jobs
from fin.services.next_steps import NEXT_STEPS
from fin.ui import chat_store
from fin.ui.file_rows import file_rows
from fin.ui.server import create_app

TOKEN = "t0ken"
PORT = 8765
HOST = f"127.0.0.1:{PORT}"
ORIGIN = {"Origin": f"http://{HOST}"}
FAKE = [sys.executable, str(Path(__file__).parents[1] / "fake_claude.py")]
KESTREL = "Kestrel_Forgings_Financials_FY23-FY25.xlsx"
runner = CliRunner()


@pytest.fixture
def root(data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "calls.jsonl"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert runner.invoke(app, ["init"]).exit_code == 0
    return data_root


@pytest.fixture
def client(root: Path) -> TestClient:
    client = TestClient(
        create_app(root, TOKEN, PORT, FAKE), base_url=f"http://{HOST}", follow_redirects=False
    )
    client.get(f"/?token={TOKEN}")
    return client


def create(client: TestClient, code: str, name: str = "Acme Synthetic Ltd"):
    return client.post("/company", data={"code": code, "name": name}, headers=ORIGIN)


def upload(client: TestClient, company: str, files: list[Path]) -> None:
    parts = [("files", (f.name, f.read_bytes(), "application/octet-stream")) for f in files]
    page = client.post("/upload", data={"company": company}, files=parts, headers=ORIGIN)
    assert "Not done" not in page.text, page.text


def job(client: TestClient, company: str, action: str, item: str = "", choice: str | None = None):
    data = {"action": action, "company": company, "item": item}
    if choice is not None:
        data["choice"] = choice
    return client.post("/job", data=data, headers=ORIGIN)


def settled(root: Path, company: str) -> list[dict[str, Any]]:
    for _ in range(240):  # a slow poll: each read makes the job's writes wait (DuckDB, one process)
        time.sleep(0.5)
        jobs = list_jobs(root, company)
        if jobs and all(j["state"] not in ("queued", "running") for j in jobs):
            return jobs
    raise AssertionError(f"jobs did not end: {list_jobs(root, company)}")


def rows_page(client: TestClient, company: str) -> str:
    return client.get(f"/partials/file-rows?company={company}").text


def query(root: Path, company: str, sql: str) -> list[tuple[Any, ...]]:
    with connect(company_db_path(root, company)) as con:
        return con.execute(sql).fetchall()


def test_from_an_empty_data_folder_a_company_is_created_and_its_file_loads(
    client: TestClient, root: Path, samples: Path
) -> None:
    created = create(client, "kestrel", "Kestrel Forgings (synthetic)")
    assert created.headers["HX-Redirect"] == "/files?company=kestrel"
    assert query(root, "kestrel", "SELECT code, name FROM company_profile") == [
        ("kestrel", "Kestrel Forgings (synthetic)")
    ]
    assert "Kestrel Forgings (synthetic)" in client.get("/files?company=kestrel").text
    upload(client, "kestrel", [samples / "clean" / "kestrel" / KESTREL])
    assert [r["state"] for r in file_rows(root, "kestrel", [KESTREL])] == ["waiting"]

    queued = job(client, "kestrel", "ingest")

    assert queued.headers["HX-Trigger"] == "job-queued"
    [done] = settled(root, "kestrel")
    assert done["state"] == "done", done
    assert done["lines"][0].startswith(f"[1/1] kestrel/{KESTREL}: loaded as doc_id 1 version 1")
    [row] = file_rows(root, "kestrel", [])
    [(facts, unmapped)] = query(
        root,
        "kestrel",
        "SELECT count(*), count(DISTINCT r.statement || '|' || r.raw_label) "
        "FILTER (WHERE f.account IS NULL) FROM facts f JOIN raw_values r USING (raw_id)",
    )
    assert (row["state"], row["facts"], row["unmapped"], row["read_by_ai"]) == (
        "loaded", facts, unmapped, 0
    )  # fmt: skip
    page = rows_page(client, "kestrel")
    assert f"{facts} figures · {unmapped} unmapped labels" in page
    assert "every 1s" not in page


def test_the_rows_follow_a_running_job_and_show_the_file_being_read(
    client: TestClient, root: Path
) -> None:
    create(client, "acme")
    with connect(audit_db_path(root), write=True) as con:
        con.execute(
            "INSERT INTO jobs (company, action, arguments, state, progress, queued_at) "
            "VALUES ('acme', 'ingest', '{}', 'running', 'reading 2 of 3: acme/b.xlsx', ?)",
            [utcnow()],
        )
    assert "job-queued from:body, every 1s" in rows_page(client, "acme")
    assert "reading 2 of 3: acme/b.xlsx" in client.get("/partials/jobs?company=acme").text


def test_each_failed_file_shows_its_code_message_and_next_step(
    client: TestClient, root: Path, samples: Path
) -> None:
    broken = samples / "broken"
    names = {
        "Bluepeak_Polymers_FY2024-25_NO_UNIT.xlsx": "E203",
        "Tarang_FY2024-25_ENCRYPTED.pdf": "E102",
        "Tarang_FY2024-25_SCANNED.pdf": "E104",
    }
    held = "Kestrel_UNBALANCED_balance_sheet.xlsx"
    create(client, "problems")
    upload(client, "problems", [broken / name for name in [*names, held]])
    job(client, "problems", "ingest")
    settled(root, "problems")

    rows = {r["file"]: r for r in file_rows(root, "problems", [])}
    unbalanced = rows.pop(held)
    open_codes = "SELECT code FROM findings WHERE status = 'open'"
    codes = {code for (code,) in query(root, "problems", open_codes)}
    assert unbalanced["state"] == "held"
    assert "E401" in codes
    assert set(unbalanced["code"].split(", ")) == codes

    no_unit = "Bluepeak_Polymers_FY2024-25_NO_UNIT.xlsx"
    assert {name: (r["state"], r["code"]) for name, r in rows.items()} == {
        name: ("held" if name == no_unit else "failed", code) for name, code in names.items()
    }
    page = rows_page(client, "problems")
    for name, row in rows.items():
        reason = root / "review" / "problems" / f"{name}.error.json"
        assert row["message"] == json.loads(reason.read_text(encoding="utf-8"))["message"]
        assert str(escape(row["message"]))[:60] in page
        assert str(escape(NEXT_STEPS[row["code"]].text)) in page
    assert rows[no_unit]["units"] == list(UNIT_ANSWERS)
    for unit in UNIT_ANSWERS:
        assert f'name="choice" value="{unit}"' in page
    assert page.count(">Read with the assistant</button>") == 1  # the scan; the other asks the unit
    assert page.count(">Choose a PDF add-on</button>") == 1


def test_the_unit_answer_loads_the_held_file_as_given_by_the_user(
    client: TestClient, root: Path, samples: Path
) -> None:
    no_unit = "Bluepeak_Polymers_FY2024-25_NO_UNIT.xlsx"
    create(client, "acme")
    upload(client, "acme", [samples / "broken" / no_unit])
    job(client, "acme", "ingest")
    settled(root, "acme")
    assert [(r["state"], r["code"]) for r in file_rows(root, "acme", [])] == [("held", "E203")]

    job(client, "acme", "unit", no_unit, "lakhs")

    assert settled(root, "acme")[0]["state"] == "done"
    unit_given = "SELECT unit_given, unit_given_by FROM documents WHERE file_name = ?"
    with connect(company_db_path(root, "acme")) as con:
        assert con.execute(unit_given, [no_unit]).fetchall() == [("lakh", "user")]
    [row] = file_rows(root, "acme", [])
    assert (row["file"], row["doc_id"] is not None) == (no_unit, True)


def test_read_with_the_assistant_starts_a_turn_and_the_scan_loads_from_its_reading(
    client: TestClient, root: Path, samples: Path, tmp_path: Path
) -> None:
    scan = tmp_path / "scan.pdf"
    shutil.copy(samples / "broken" / "Tarang_FY2024-25_SCANNED.pdf", scan)
    create(client, "acme")
    upload(client, "acme", [scan])
    job(client, "acme", "ingest")
    settled(root, "acme")
    [row] = file_rows(root, "acme", [])
    prompt = row["prompts"]["reading"]
    assert str(escape(prompt)) in rows_page(client, "acme")

    sent = client.post(
        "/chat/send", data={"conv": "", "company": "acme", "text": prompt}, headers=ORIGIN
    )

    assert sent.status_code == 200
    conv = str(chat_store.listing(root)[0]["id"])
    chat = client.app.state.chat  # type: ignore[attr-defined]
    for _ in range(100):
        if not chat.running(conv):
            break
        time.sleep(0.1)
    calls = Path(tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(calls[0])["prompt"] == prompt
    # What the assistant does next: its reading, loaded with the command the message names.
    reading = root / "review" / "acme" / "scan.reading1.csv"
    reading.write_text(
        "statement,scope,period_end,period_months,unit,section,label,value_as_printed,source\n"
        "balance_sheet,standalone,2025-03-31,,(Rs. in Lakhs),,TOTAL ASSETS,100.00,page 1\n"
        "balance_sheet,standalone,2025-03-31,,(Rs. in Lakhs),,TOTAL EQUITY AND LIABILITIES,"
        "100.00,page 1\n",
        encoding="utf-8",
    )
    original = root / "review" / "acme" / "scan.pdf"
    loaded = runner.invoke(app, ["ingest", "acme", str(original), "--reading", str(reading)])
    assert loaded.exit_code == 0, loaded.output
    [row] = file_rows(root, "acme", [])
    assert (row["file"], row["state"], row["read_by_ai"]) == ("scan.pdf", "loaded", 2)
    assert "read by AI, not checked" in rows_page(client, "acme")


@pytest.mark.timeout(180)  # three loads: ingest, re-ingest and the delete's metrics rebuild
def test_reingest_adds_a_version_and_delete_removes_the_file_after_a_backup(
    client: TestClient, root: Path, samples: Path
) -> None:
    create(client, "kestrel")
    upload(client, "kestrel", [samples / "clean" / "kestrel" / KESTREL])
    job(client, "kestrel", "ingest")
    settled(root, "kestrel")
    [(archive,)] = query(root, "kestrel", "SELECT archive_path FROM documents")
    # A failure of another file with the same name still shows; once its content is loaded, not.
    review = root / "review" / "kestrel"
    review.mkdir(parents=True)
    (review / KESTREL).write_bytes(b"another workbook")
    (review / f"{KESTREL}.error.json").write_text('{"code": "E101", "message": "bad"}')
    assert [r["state"] for r in file_rows(root, "kestrel", [])] == ["loaded", "failed"]
    shutil.copy(samples / "clean" / "kestrel" / KESTREL, review / KESTREL)
    assert [r["state"] for r in file_rows(root, "kestrel", [])] == ["loaded"]
    shutil.rmtree(review)

    job(client, "kestrel", "reingest", "1")

    jobs = settled(root, "kestrel")
    assert jobs[0]["state"] == "done", jobs[0]
    assert query(root, "kestrel", "SELECT doc_id, status, version FROM documents ORDER BY 1") == [
        (1, "superseded", 1),
        (2, "loaded", 2),
    ]
    assert query(root, "kestrel", "SELECT DISTINCT file_name FROM documents") == [(KESTREL,)]
    assert not (root / "cache" / "reingest").exists() or not any(
        (root / "cache" / "reingest").iterdir()
    )
    assert [(r["doc_id"], r["version"]) for r in file_rows(root, "kestrel", [])] == [(2, 2)]

    deleted = client.post(
        "/act",
        data={"action": "document-delete", "company": "kestrel", "item": "2"},
        headers=ORIGIN,
    )

    assert f"deleted {KESTREL} (doc_id 1, 2)" in deleted.text
    for table in ("documents", "facts", "raw_values", "findings"):
        assert query(root, "kestrel", f"SELECT count(*) FROM {table}") == [(0,)], table
    assert not (root / archive).exists()
    assert not (root / "inbox" / "kestrel" / KESTREL).exists()
    assert read_pending(root, "kestrel")["items"] == []  # its labels left the Mappings screen
    with connect(audit_db_path(root)) as con:
        [(removed, backup)] = con.execute(
            "SELECT files_removed, backup_path FROM deletions"
        ).fetchall()
    assert sorted(json.loads(removed)) == sorted([archive, f"inbox/kestrel/{KESTREL}"])
    assert (root / backup / "companies" / "kestrel.duckdb").is_file()
    assert (root / backup / archive).is_file()
    assert file_rows(root, "kestrel", []) == []


def test_refusals_say_what_was_wrong_and_change_nothing(client: TestClient, root: Path) -> None:
    assert create(client, "acme").headers["HX-Redirect"] == "/files?company=acme"
    again = create(client, "acme")
    assert "E008" in again.text
    assert "there is a company acme already" in again.text
    bad = create(client, "Acme Co")
    assert "E008" in bad.text
    assert not company_db_path(root, "Acme Co").exists()
    for action, item, choice in (
        ("ingest", "1", None),
        ("ingest", "", "lakhs"),
        ("reingest", "99", None),
        ("reingest", "x", None),
        ("reingest", "", None),
        ("unit", "a.xlsx", "lakhs"),  # no file held for its unit
        ("unit", "../a.xlsx", "lakhs"),
        ("unit", "a.xlsx", "dozens"),
    ):
        refused = job(client, "acme", action, item, choice)
        assert refused.status_code == 400, (action, item, choice)
        assert "E008" in refused.text
    assert list_jobs(root, "acme") == []
