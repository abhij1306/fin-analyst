"""Audit logger (PLAN.md §8). Every `fin` command runs through `execute`.

A run gets a `run_id` and appends records:
- `runs`: a 'start' row when it begins and an 'end' row when it finishes (status, exit code, summary
  counts);
- `events`: structured events (stage, file, level, error code, message, context);
- any other audit table a command records into.

Each record is written at once to `logs/<date>/<run_id>.jsonl`, and appended to
`shared/audit.duckdb` when the run starts and when it ends (`Run.flush` writes earlier). The audit
database is append-only: this module only INSERTs.

Before `fin init` has created the data folder and the audit database, records wait in memory and are
written as soon as their destination exists. A run that still cannot be recorded says so on stderr.
Commands that need the audit database (`audit_required=True`, the default) stop with E001 or E002
when it is missing or was made by an older fin.
"""

import json
import platform
import shutil
import subprocess
import uuid
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import typer

from fin import __version__
from fin.audit.errors import (
    EXIT_BLOCKED,
    EXIT_COMMAND_ERROR,
    EXIT_FAILURES,
    EXIT_OK,
    DataFolderNotWritable,
    DependencyMissing,
    FinError,
)
from fin.db.conn import connect
from fin.db.schema import is_old_format, old_format_error
from fin.paths import audit_db_path, data_dir

STATUS_BY_EXIT = {
    EXIT_OK: "ok",
    EXIT_FAILURES: "failures",
    EXIT_COMMAND_ERROR: "error",
    EXIT_BLOCKED: "blocked",
}

_REPO = Path(__file__).resolve().parents[3]
_git_commit_cache: list[str | None] = []


def git_commit() -> str | None:
    """HEAD of the repo fin runs from, or None when it is not a git checkout."""
    if not _git_commit_cache:
        git = shutil.which("git")
        commit = None
        if git is not None and (_REPO / ".git").exists():
            result = subprocess.run(
                [git, "-C", str(_REPO), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if result.returncode == 0:
                commit = result.stdout.strip()
        _git_commit_cache.append(commit)
    return _git_commit_cache[0]


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _json_default(value: object) -> str:
    if isinstance(value, datetime | date | Path | Decimal):
        return str(value)
    raise TypeError(f"cannot write {type(value).__name__} to the audit log")


class Run:
    """One `fin` command invocation."""

    def __init__(self, command: str, args: dict[str, Any], root: Path) -> None:
        self.started_at = _utcnow()
        self.run_id = f"{self.started_at:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        self.command = command
        self.args = args
        self.root = root
        self.exit_code = EXIT_OK
        self.loaded = 0
        self.skipped = 0
        self.failed = 0
        self.failure_codes: list[str] = []  # one error code per failed file
        self.pending_review = 0
        self.held = 0  # documents held with open findings (fin.findings)
        self._seq = 0
        self._records: list[tuple[str, dict[str, Any]]] = []
        self._jsonl_done = 0
        self._db_done = 0
        self._db_problem: FinError | None = None
        self._jsonl_problem: FinError | None = None

    @property
    def jsonl_path(self) -> Path:
        return self.root / "logs" / f"{self.started_at:%Y-%m-%d}" / f"{self.run_id}.jsonl"

    def record(self, table: str, row: dict[str, Any]) -> None:
        """Append a row to an audit table (and the JSONL file). `run_id` and `recorded_at` are added
        unless present."""
        full = {"run_id": self.run_id, **row}
        if table != "runs":
            full.setdefault("recorded_at", _utcnow())
        self._records.append((table, full))
        self._write_jsonl()

    def event(
        self,
        stage: str,
        message: str,
        *,
        level: str = "info",
        code: str | None = None,
        file: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self._seq += 1
        self.record(
            "events",
            {
                "seq": self._seq,
                "stage": stage,
                "file": file,
                "level": level,
                "error_code": code,
                "message": message,
                "context": None if context is None else json.dumps(context, default=_json_default),
            },
        )

    def log_error(self, stage: str, message: str, code: str | None = None) -> None:
        """Record an error event. A failure to write the JSONL log here is not raised again:
        `finish` reports it and raises the exit code."""
        with suppress(DataFolderNotWritable):
            self.event(stage, message, level="error", code=code)

    def _run_row(self, record: str, exit_code: int | None) -> dict[str, Any]:
        end = record == "end"
        return {
            "record": record,
            "command": self.command,
            "args": json.dumps(self.args, default=_json_default),
            "started_at": self.started_at,
            "ended_at": _utcnow() if end else None,
            "status": STATUS_BY_EXIT[exit_code] if end and exit_code is not None else "started",
            "exit_code": exit_code if end else None,
            "loaded": self.loaded if end else None,
            "skipped": self.skipped if end else None,
            "failed": self.failed if end else None,
            "pending_review": self.pending_review if end else None,
            "git_commit": git_commit(),
            "fin_version": __version__,
            "duckdb_version": duckdb.__version__,
            "python_version": platform.python_version(),
        }

    def _write_jsonl(self) -> None:
        """Append the pending records to the JSONL file. A failure raises E003 once and is not
        retried (the records stay pending): logging that error must not fail the same way again."""
        if self._jsonl_problem is not None or not (self.root / "logs").is_dir():
            return
        pending = self._records[self._jsonl_done :]
        try:
            self.jsonl_path.parent.mkdir(exist_ok=True)
            with self.jsonl_path.open("a", encoding="utf-8") as handle:
                for table, row in pending:
                    line = {"table": table, **row}
                    handle.write(json.dumps(line, default=_json_default) + "\n")
        except OSError as exc:
            self._jsonl_problem = DataFolderNotWritable(f"cannot write {self.jsonl_path}: {exc}")
            raise self._jsonl_problem from exc
        self._jsonl_done = len(self._records)

    def flush(self, *, required: bool = False) -> None:
        """Append every record not yet in the audit database. If the database is missing or was made
        by an older fin, keep the records for later; raise E001/E002 instead when `required`."""
        path = audit_db_path(self.root)
        if not path.is_file():
            self._db_problem = DependencyMissing(
                f"audit database {path} does not exist; run `uv run fin init`"
            )
        else:
            with connect(path, write=True) as con:
                if not is_old_format(con, "audit"):
                    self._insert(con)
                    self._db_problem = None
                    return
            self._db_problem = old_format_error(path)
        if required:
            raise self._db_problem

    def _insert(self, con: duckdb.DuckDBPyConnection) -> None:
        pending = self._records[self._db_done :]
        if not pending:
            return
        con.execute("BEGIN TRANSACTION")
        for table, row in pending:
            columns = ", ".join(row)
            marks = ", ".join("?" for _ in row)
            con.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", list(row.values()))
        con.execute("COMMIT")
        self._db_done = len(self._records)

    def start(self, *, required: bool) -> None:
        """Append the start row and write it out (see `flush` for `required`)."""
        self.record("runs", self._run_row("start", None))
        self.flush(required=required)

    def _write_out(self, exit_code: int) -> int:
        """Write every pending record to the JSONL file and the audit database. A failure is
        printed and raises the exit code, which is returned."""
        for write in (self._write_jsonl, self.flush):
            try:
                write()
            except FinError as exc:
                typer.echo(f"{exc} (while writing the audit log)", err=True)
                exit_code = max(exit_code, exc.exit_code)
        if self._jsonl_problem is not None:
            exit_code = max(exit_code, self._jsonl_problem.exit_code)
        return exit_code

    def finish(self, exit_code: int) -> int:
        """Write everything out, then append the end row with the exit code that results and write
        it too, and print the summary. Returns the final exit code."""
        exit_code = self._write_out(exit_code)
        self._records.append(("runs", {"run_id": self.run_id, **self._run_row("end", exit_code)}))
        exit_code = self._write_out(exit_code)
        if self._db_problem is not None:
            typer.echo(
                f"run {self.run_id} not recorded in the audit database: {self._db_problem}",
                err=True,
            )
        if self._jsonl_done < len(self._records):
            why = self._jsonl_problem or "no logs folder"
            typer.echo(f"run {self.run_id} not written to logs/: {why}", err=True)
        codes = Counter(self.failure_codes)
        by_code = ", ".join(f"{code} x{n}" if n > 1 else code for code, n in sorted(codes.items()))
        failed = f"{self.failed} ({by_code})" if by_code else str(self.failed)
        typer.echo(
            f"summary: loaded {self.loaded}, skipped {self.skipped}, failed {failed}, "
            f"pending review {self.pending_review}, held {self.held}, run_id {self.run_id}",
            err=True,
        )
        return exit_code


def execute(
    command: str,
    args: dict[str, Any],
    body: Callable[[Run], None],
    *,
    audit_required: bool = True,
) -> None:
    """Run a command body inside an audited run, then exit with the run's exit code.

    A raised FinError is printed, logged as an error event with its code, and becomes the exit code.
    The body sets `run.exit_code` (for example EXIT_FAILURES) and the summary counters. Any other
    exception is logged and re-raised.
    """
    run = Run(command, args, data_dir())
    exit_code = EXIT_OK
    try:
        run.start(required=audit_required)
        body(run)
        exit_code = run.exit_code
    except FinError as exc:
        typer.echo(str(exc), err=True)
        run.log_error(command, exc.message, exc.code)
        exit_code = exc.exit_code
    except BaseException as exc:
        run.log_error(command, f"unexpected {type(exc).__name__}: {exc}")
        run.finish(EXIT_COMMAND_ERROR)
        raise
    exit_code = run.finish(exit_code)
    if exit_code != EXIT_OK:
        raise typer.Exit(exit_code)
