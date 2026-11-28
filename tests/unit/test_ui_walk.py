"""End to end on the synthetic company, through the web UI only (SPEC-11): create it, upload its
workbook on Files, ingest it as a job, open Mappings and Statements, make the health report (the
stub, then the render with a stand-in Quarto) and download it. Needs the `ui` extra; skipped
without it."""

import os
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
from typer.testing import CliRunner

from fin.cli import app
from fin.services.jobs import list_jobs
from fin.ui.server import create_app

TOKEN = "t0ken"
PORT = 8765
HOST = f"127.0.0.1:{PORT}"
ORIGIN = {"Origin": f"http://{HOST}"}
KESTREL = "Kestrel_Forgings_Financials_FY23-FY25.xlsx"
NARRATIVE = Path(__file__).parents[1] / "fixtures" / "report" / "kestrel_narrative.md"
# Stands in for `quarto render health.qmd --to all`: writes the three outputs next to the source.
FAKE_QUARTO = """\
import sys
from pathlib import Path

assert sys.argv[1:] == ["render", "health.qmd", "--to", "all"], sys.argv
text = Path("health.qmd").read_text(encoding="utf-8")
for name in ("health.html", "health.docx", "health.pdf"):
    Path(name).write_text(text, encoding="utf-8")
"""


@pytest.fixture
def fake_quarto(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "fake_quarto.py"
    script.write_text(FAKE_QUARTO, encoding="utf-8")
    if os.name == "nt":
        (bin_dir / "quarto.cmd").write_text(f'@"{sys.executable}" "{script}" %*\r\n')
    else:
        launcher = bin_dir / "quarto"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        launcher.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def latest_job(root: Path, company: str) -> dict[str, Any]:
    for _ in range(240):  # a slow poll: each read makes the job's writes wait
        time.sleep(0.5)
        jobs = list_jobs(root, company)
        if jobs and jobs[0]["state"] not in ("queued", "running"):
            return jobs[0]
    raise AssertionError(f"the job did not end: {list_jobs(root, company)}")


def job(client: TestClient, action: str, **form: str) -> None:
    queued = client.post(
        "/job", data={"action": action, "company": "kestrel", **form}, headers=ORIGIN
    )
    assert queued.headers["HX-Trigger"] == "job-queued", queued.text


@pytest.mark.timeout(300)  # an ingest and two report runs, each a job
def test_files_ingest_mappings_statements_and_a_report_download(
    data_root: Path, samples: Path, fake_quarto: None
) -> None:
    assert CliRunner().invoke(app, ["init"]).exit_code == 0
    client = TestClient(
        create_app(data_root, TOKEN, PORT), base_url=f"http://{HOST}", follow_redirects=False
    )
    client.get(f"/?token={TOKEN}")

    created = client.post(
        "/company", data={"code": "kestrel", "name": "Kestrel (synthetic)"}, headers=ORIGIN
    )
    assert created.headers["HX-Redirect"] == "/files?company=kestrel"
    workbook = samples / "clean" / "kestrel" / KESTREL
    parts = [("files", (KESTREL, workbook.read_bytes(), "application/octet-stream"))]
    uploaded = client.post("/upload", data={"company": "kestrel"}, files=parts, headers=ORIGIN)
    assert "Not done" not in uploaded.text, uploaded.text

    job(client, "ingest")
    ingested = latest_job(data_root, "kestrel")
    assert ingested["state"] == "done", ingested
    assert ingested["lines"][0].startswith(f"[1/1] kestrel/{KESTREL}: loaded as doc_id 1 version 1")

    mappings = client.get("/mappings?company=kestrel")
    assert mappings.status_code == 200
    assert 'id="tab-mappings" href="/mappings?company=kestrel" aria-current="page"' in mappings.text
    statements = client.get("/statements?company=kestrel")
    assert statements.status_code == 200
    assert "Revenue" in statements.text

    job(client, "report", choice="health")
    stub = latest_job(data_root, "kestrel")
    assert (stub["state"], stub["lines"][0]) == (
        "done", "wrote the narrative stub reports/kestrel/standalone/narrative.md"
    )  # fmt: skip
    narrative = data_root / "reports" / "kestrel" / "standalone" / "narrative.md"
    narrative.write_text(NARRATIVE.read_text(encoding="utf-8"), encoding="utf-8")
    job(client, "report", choice="health")
    rendered = latest_job(data_root, "kestrel")
    assert rendered["state"] == "done", rendered
    assert "rendered reports/kestrel/standalone/health.pdf" in rendered["lines"]

    listing = client.get("/partials/reports?company=kestrel").text
    assert "standalone/health.pdf</a>" in listing
    pdf = client.get("/report-file?company=kestrel&name=standalone/health.pdf")
    assert pdf.status_code == 200
    assert 'attachment; filename="health.pdf"' in pdf.headers["content-disposition"]
