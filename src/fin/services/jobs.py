"""Background jobs (docs/specs/SPEC-02): a long action runs in one worker thread while the web UI
shows its progress, so the page answers at once and a reload finds the job again.

Each job is a row of `jobs` in audit.duckdb: queued, running, then done, refused or failed, with
its last progress line, its result lines and the run_id of the run the action recorded. One worker
runs the jobs one at a time, in order; a later job waits as queued. Jobs left queued or running by a
server that stopped are marked interrupted when the next one starts (`recover`)."""

import json
import queue
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fin.audit.errors import UiActionInvalid, UnexpectedError
from fin.db.conn import connect, utcnow
from fin.db.rows import fetch_dicts, fetch_one
from fin.paths import audit_db_path
from fin.services.result import ActionResult

Work = Callable[..., ActionResult]  # work(root, company, progress=..., **arguments)
ACTIVE = ("queued", "running")
SHOWN = 10  # jobs listed per company
LEFT_RUNNING = "failed: its worker stopped before it could record the outcome"


class Jobs:
    """The job queue of one data folder. `work` names the actions that may run as jobs."""

    def __init__(self, root: Path, work: dict[str, Work]) -> None:
        self.root = root
        self.work = work
        self._queue: queue.Queue[int] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def recover(self) -> int:
        """Mark the jobs a stopped server left queued or running as interrupted; returns how many.
        Nothing to do before `fin init` made the audit database."""
        path = audit_db_path(self.root)
        if not path.is_file():
            return 0
        with connect(path, write=True) as con:
            rows = con.execute(
                "UPDATE jobs SET state = 'interrupted', ended_at = ? "
                "WHERE state IN ('queued', 'running') RETURNING job_id",
                [utcnow()],
            ).fetchall()
        return len(rows)

    def submit(self, company: str, action: str, arguments: dict[str, Any]) -> int:
        """Queue a job and return its id. E008 for an action that is not a job."""
        if action not in self.work:
            raise UiActionInvalid(f"unknown job {action!r}")
        with connect(audit_db_path(self.root), write=True) as con:
            (job_id,) = fetch_one(
                con,
                "INSERT INTO jobs (company, action, arguments, state, queued_at) "
                "VALUES (?, ?, ?, 'queued', ?) RETURNING job_id",
                [company, action, json.dumps(arguments), utcnow()],
            )
        self._queue.put(job_id)
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._start_worker()
        return int(job_id)

    def _start_worker(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="fin-jobs", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                self._run(job_id)
            except BaseException as exc:  # the run logged it (E012, with its traceback)
                lines = [f"unexpected {type(exc).__name__}: {exc}"]
                try:
                    self._update(
                        job_id, state="failed", code=UnexpectedError.code,
                        lines=json.dumps(lines), ended_at=utcnow(),
                    )  # fmt: skip
                finally:  # even when that write fails: the next job marks this one failed
                    with self._lock:
                        self._start_worker()  # the jobs still queued run in a new worker
                raise

    def _update(self, job_id: int, **fields: Any) -> None:
        sets = ", ".join(f"{name} = ?" for name in fields)  # names come from this module only
        with connect(audit_db_path(self.root), write=True) as con:
            con.execute(f"UPDATE jobs SET {sets} WHERE job_id = ?", [*fields.values(), job_id])

    def _run(self, job_id: int) -> None:
        with connect(audit_db_path(self.root)) as con:
            [job] = fetch_dicts(con, "SELECT * FROM jobs WHERE job_id = ?", [job_id])
        with connect(audit_db_path(self.root), write=True) as con:
            # one worker runs one job at a time: another job still running was left by a worker
            # that died before it could record the failure
            con.execute(
                "UPDATE jobs SET state = 'failed', code = ?, lines = ?, ended_at = ? "
                "WHERE state = 'running' AND job_id <> ?",
                [UnexpectedError.code, json.dumps([LEFT_RUNNING]), utcnow(), job_id],
            )
            con.execute(
                "UPDATE jobs SET state = 'running', started_at = ?, progress = 'started' "
                "WHERE job_id = ?",
                [utcnow(), job_id],
            )

        def progress(line: str) -> None:
            self._update(job_id, progress=line)

        work = self.work[job["action"]]
        arguments = json.loads(job["arguments"])
        result = work(self.root, job["company"], progress=progress, **arguments)
        self._update(
            job_id,
            state="done" if result.ok else _failed_or_refused(result.code),
            code=result.code,
            lines=json.dumps(result.lines),
            run_id=result.run_id,
            ended_at=utcnow(),
        )


def _failed_or_refused(code: str | None) -> str:
    """A defect inside the action (E012, logged by its run) is a failed job; any other refusal is
    refused."""
    return "failed" if code == UnexpectedError.code else "refused"


def list_jobs(root: Path, company: str) -> list[dict[str, Any]]:
    """The company's latest jobs, newest first, with their result lines."""
    path = audit_db_path(root)
    if not path.is_file():
        return []
    with connect(path) as con:
        rows = fetch_dicts(
            con,
            "SELECT job_id, action, state, progress, lines, code, run_id, queued_at, started_at, "
            "ended_at FROM jobs WHERE company = ? ORDER BY job_id DESC LIMIT ?",
            [company, SHOWN],
        )
    return [{**row, "lines": json.loads(row["lines"] or "[]")} for row in rows]
