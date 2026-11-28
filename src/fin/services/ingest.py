"""Ingest as a service (docs/specs/SPEC-03): the loop `fin ingest` and `fin ingest-batch` run, and
the two ingest actions of the Files screen, which the UI runs as jobs (`fin.services.jobs`): load
the company's inbox, or load one document again as a new version."""

import json
import shutil
from collections.abc import Callable
from pathlib import Path

from fin.audit.errors import DataFolderNotWritable, FileUnreadable, UiActionInvalid
from fin.audit.logger import Run
from fin.config import load_config
from fin.db.conn import connect
from fin.db.rows import fetch_dicts
from fin.db.schema import ensure_database
from fin.ingest import UNIT_ANSWERS, GivenUnit, files_in, ingest_file
from fin.map.dictionary import load_label_map
from fin.map.review import write_pending
from fin.metrics.export import refresh_analytics
from fin.paths import company_db_path, inbox_dir, inside, review_dir
from fin.services.result import ActionResult, Progress, perform

Batch = list[tuple[str, list[Path]]]  # (company, its files)


def ensure_company_db(root: Path, company: str) -> bool:
    """Create the company database if missing; True when this call created it."""
    path = company_db_path(root, company)
    existed = path.is_file()
    ensure_database(path, "company")
    return not existed


def discard_if_empty(root: Path, company: str) -> None:
    """Remove a company database this run created when nothing was loaded into it (a first
    ingest where every file failed must not leave an empty company behind)."""
    path = company_db_path(root, company)
    with connect(path) as con:
        row = con.execute(
            "SELECT (SELECT count(*) FROM documents) + (SELECT count(*) FROM source_files)"
        ).fetchone()
    if row and row[0] == 0:
        path.unlink()
        wal = path.with_name(path.name + ".wal")
        if wal.is_file():
            wal.unlink()


def ingest_files(
    run: Run,
    batch: Batch,
    force: bool,
    reading: Path | None = None,
    unit: GivenUnit | None = None,
    *,
    progress: Progress | None = None,
    told: Callable[[str], None] | None = None,
) -> list[str]:
    """Ingest each company's files; returns one line per file, `[i/N] company/file: outcome`.
    `progress` is told which file is being read, `told` each line as soon as it is known."""
    config = load_config(run.root)
    total = sum(len(files) for _, files in batch)
    lines: list[str] = []
    for company, files in batch:
        created = ensure_company_db(run.root, company)
        try:
            label_map = load_label_map(run.root, company)
            for path in files:
                if progress is not None:
                    progress(f"reading {len(lines) + 1} of {total}: {company}/{path.name}")
                outcome = ingest_file(
                    run, config, company, path, label_map, force=force, reading=reading, unit=unit
                )
                lines.append(f"[{len(lines) + 1}/{total}] {company}/{path.name}: {outcome}")
                if told is not None:
                    told(lines[-1])
            write_pending(run, company)
        finally:
            if created:
                discard_if_empty(run.root, company)
    if total == 0:
        lines.append("no files to ingest")
        if told is not None:
            told(lines[-1])
    return lines


def refreshed(run: Run) -> str:
    """Rebuild the shared metrics; the line that says so."""
    refresh = refresh_analytics(run)
    return (
        f"metrics: shared/analytics.duckdb refreshed, {refresh.companies} companies, "
        f"{refresh.metric_rows} metric rows"
    )


def ingest_inbox(
    root: Path, company: str, *, via: str, actor: str, progress: Progress | None = None
) -> ActionResult:
    """`fin ingest <company>`: load the files in inbox/<company>/ (those loaded already are
    skipped, E105)."""

    def body(run: Run) -> list[str]:
        files = files_in(inbox_dir(run.root, company))
        return [*ingest_files(run, [(company, files)], False, progress=progress), refreshed(run)]

    args = {"company": company, "path": None, "force": False, "reading": None}
    return perform(root, "ingest", args | {"via": via, "actor": actor}, body)


def document(root: Path, company: str, doc_id: int) -> dict[str, object]:
    """The document row; E008 when the company has no document with that id."""
    path = company_db_path(root, company)
    rows = []
    if path.is_file():
        with connect(path) as con:
            rows = fetch_dicts(
                con,
                "SELECT doc_id, file_name, archive_path, reading_path, unit_given, unit_given_by "
                "FROM documents "
                "WHERE doc_id = ?",
                [doc_id],
            )
    if not rows:
        raise UiActionInvalid(f"{company} has no document {doc_id}; nothing was started")
    return rows[0]


def reingest_document(
    root: Path,
    company: str,
    doc_id: int,
    *,
    via: str,
    actor: str,
    progress: Progress | None = None,
) -> ActionResult:
    """Load a document again from its archived original, as a new version (`fin ingest
    --force`, with the reading and the unit the user gave it was loaded with): the earlier version
    and its findings are superseded. E008 (no run) for a document that does not exist."""
    row = document(root, company, doc_id)
    name, archived = str(row["file_name"]), str(row["archive_path"])

    def body(run: Run) -> list[str]:
        source = inside(run.root, run.root / archived)  # a path from a database row
        if not source.is_file():
            raise FileUnreadable(f"the archived original {archived} of {name} is missing")
        folder = run.root / "cache" / "reingest" / run.run_id  # this run's own folder
        copy = folder / name
        if copy.resolve().parent != folder.resolve():
            raise FileUnreadable(f"{name!r} is not a plain file name; nothing was loaded")
        try:
            folder.mkdir(parents=True)
            shutil.copy2(source, copy)  # ingest names the document after the file it reads
        except OSError as exc:
            raise DataFolderNotWritable(f"cannot copy {archived} to {folder}: {exc}") from exc
        reading = (
            inside(run.root, run.root / str(row["reading_path"])) if row["reading_path"] else None
        )
        given = row["unit_given"]
        unit = GivenUnit(str(given), str(row["unit_given_by"])) if given else None
        try:
            lines = ingest_files(run, [(company, [copy])], True, reading, unit, progress=progress)
        finally:
            shutil.rmtree(
                folder,
                onexc=lambda *_: run.event("ingest", f"could not remove {folder}", level="warning"),
            )
        return [*lines, refreshed(run)]

    args = {"company": company, "doc_id": doc_id, "force": True, "via": via, "actor": actor}
    return perform(root, "ingest", args, body)


def held_for_unit(root: Path, company: str, name: str) -> Path:
    """The copy in review/<company>/ of a file held with the question "which unit?"; E008 when
    there is no such file."""
    folder = review_dir(root, company)
    copy = folder / name
    reason = folder / f"{name}.error.json"
    question = None
    if name and copy.parent.resolve() == folder.resolve() and reason.is_file() and copy.is_file():
        try:
            question = json.loads(reason.read_text(encoding="utf-8")).get("question")
        except (OSError, ValueError):
            question = None
    if question is None:
        raise UiActionInvalid(f"{name!r} is not a file waiting for its unit; nothing was started")
    return copy


def load_with_unit(
    root: Path,
    company: str,
    name: str,
    unit: str,
    *,
    via: str,
    actor: str,
    progress: Progress | None = None,
) -> ActionResult:
    """`fin ingest <company> review/<company>/<file> --unit <unit>`: load a file held for its unit
    with the user's answer (stored as given by the user). E008 (no run) for a file not held for
    its unit."""
    copy = held_for_unit(root, company, name)
    given = GivenUnit(UNIT_ANSWERS[unit], actor)

    def body(run: Run) -> list[str]:
        lines = ingest_files(run, [(company, [copy])], False, None, given, progress=progress)
        return [*lines, refreshed(run)]

    path = copy.relative_to(root).as_posix()
    args = {"company": company, "path": path, "force": False, "reading": None, "unit": unit}
    return perform(root, "ingest", args | {"via": via, "actor": actor}, body)
