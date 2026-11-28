"""Background jobs (fin.services.jobs, SPEC-02 PR 3): one worker runs them in order with a progress
line; a refusal or a defect is recorded and the next job still runs; jobs a stopped server left are
interrupted at the next start; the UI shows them again after a reload."""

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from fin.db.conn import connect, utcnow
from fin.paths import audit_db_path
from fin.services.jobs import Jobs, list_jobs
from fin.services.result import ActionResult, Done, Progress, Refused
from tests.engagement_root import engagement_root


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return engagement_root(tmp_path)


def wait_for(root: Path, done: Any, seconds: float = 60) -> list[dict[str, Any]]:
    """The jobs once done(jobs) is true; fails after `seconds`."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        jobs = list_jobs(root, "acme")
        if done(jobs):
            return jobs
        time.sleep(0.2)
    raise AssertionError(f"jobs did not get there: {list_jobs(root, 'acme')}")


def settled(jobs: list[dict[str, Any]]) -> bool:
    return all(job["state"] not in ("queued", "running") for job in jobs)


def test_a_job_shows_its_progress_and_the_next_one_waits(root: Path) -> None:
    release, half_way = threading.Event(), threading.Event()

    def slow(root: Path, company: str, *, progress: Progress) -> ActionResult:
        progress("half way")
        half_way.set()
        assert release.wait(60)
        return Done([f"did it for {company}"], "run-1")

    jobs = Jobs(root, {"slow": slow})
    first = jobs.submit("acme", "slow", {})
    second = jobs.submit("acme", "slow", {})
    assert half_way.wait(60)
    states = list_jobs(root, "acme")
    assert [(j["job_id"], j["state"]) for j in states] == [(second, "queued"), (first, "running")]
    release.set()
    finished = wait_for(root, settled)
    assert [(j["state"], j["lines"], j["run_id"]) for j in finished] == [
        ("done", ["did it for acme"], "run-1")
    ] * 2


def test_a_refusal_and_a_defect_are_recorded_and_the_next_job_still_runs(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(threading, "excepthook", lambda args: None)  # the defect ends its worker

    def refuse(root: Path, company: str, *, progress: Progress) -> ActionResult:
        return Refused("E801", "gate G2 is not approved", "run-2")

    def boom(root: Path, company: str, *, progress: Progress) -> ActionResult:
        raise RuntimeError("a defect")

    def fine(root: Path, company: str, *, progress: Progress) -> ActionResult:
        return Done(["ok"], "run-3")

    jobs = Jobs(root, {"refuse": refuse, "boom": boom, "fine": fine})
    for action in ("refuse", "boom", "fine"):
        jobs.submit("acme", action, {})
    finished = wait_for(root, lambda js: len(js) == 3 and settled(js))
    assert [(j["state"], j["code"], j["lines"]) for j in reversed(finished)] == [
        ("refused", "E801", ["gate G2 is not approved"]),
        ("failed", "E012", ["unexpected RuntimeError: a defect"]),
        ("done", None, ["ok"]),
    ]


def test_an_unknown_job_is_e008_and_nothing_is_queued(root: Path) -> None:
    jobs = Jobs(root, {})
    with pytest.raises(Exception, match="unknown job") as raised:
        jobs.submit("acme", "nope", {})
    assert getattr(raised.value, "code", None) == "E008"
    assert list_jobs(root, "acme") == []


def add_job(root: Path, state: str) -> None:
    with connect(audit_db_path(root), write=True) as con:
        con.execute(
            "INSERT INTO jobs (company, action, arguments, state, queued_at) "
            "VALUES ('acme', 'export', '{}', ?, ?)",
            [state, utcnow()],
        )


def test_jobs_a_stopped_server_left_are_interrupted_at_the_next_start(root: Path) -> None:
    for state in ("done", "running", "queued"):
        add_job(root, state)
    assert Jobs(root, {}).recover() == 2
    assert [j["state"] for j in list_jobs(root, "acme")] == ["interrupted", "interrupted", "done"]


def test_without_an_audit_database_there_is_nothing_to_recover(tmp_path: Path) -> None:
    assert Jobs(tmp_path, {}).recover() == 0
    assert list_jobs(tmp_path, "acme") == []


# The UI: needs the `ui` extra.

TOKEN = "t0ken"
PORT = 8765
HOST = f"127.0.0.1:{PORT}"


def client_for(root: Path) -> Any:
    pytest.importorskip("fastapi")
    pytest.importorskip("jinja2")
    pytest.importorskip("httpx")
    pytest.importorskip("multipart")
    from fastapi.testclient import TestClient

    from fin.ui.server import create_app

    client = TestClient(
        create_app(root, TOKEN, PORT), base_url=f"http://{HOST}", follow_redirects=False
    )
    client.get(f"/?token={TOKEN}")
    return client


def test_an_export_job_is_shown_again_after_a_reload(root: Path) -> None:
    client = client_for(root)
    started = client.post(
        "/job",
        data={"action": "export", "company": "acme"},
        headers={"Origin": f"http://{HOST}"},
    )
    assert started.status_code == 200
    assert 'id="jobs"' in started.text
    [job] = wait_for(root, settled)
    assert job["state"] in ("done", "refused")
    assert job["run_id"]
    page = client.get("/reports?company=acme").text  # a reload reads the jobs table again
    assert "Export to Excel" in page
    assert job["run_id"] in page
    assert f">{job['state']}<" in page
    with connect(audit_db_path(root)) as con:
        [(args,)] = con.execute(
            "SELECT args FROM runs WHERE run_id = ? AND record = 'end'", [job["run_id"]]
        ).fetchall()
    assert '"actor": "user"' in args


def test_a_job_the_last_server_left_running_is_shown_as_interrupted(root: Path) -> None:
    add_job(root, "running")
    page = client_for(root).get("/partials/jobs?company=acme").text
    assert ">interrupted<" in page
    assert "The server stopped before it ended" in page
    assert "every 1s" not in page  # nothing to wait for


def test_starting_a_job_needs_this_servers_origin(root: Path) -> None:
    response = client_for(root).post("/job", data={"action": "export", "company": "acme"})
    assert response.status_code == 403
    assert list_jobs(root, "acme") == []
