"""`fin dataroom propose|approve|load` (SPEC-13): decide the plan's rows and load the approved ones.

- `propose`: the assistant's kind (and profile) for a file fin could not name, with its reason.
  Proposing clears the row's approval: the user confirms the kind by approving the row.
- `approve`: the user's choice of rows to load (`--undo` takes it back). A file fin cannot use
  can be approved only once a kind is proposed for it.
- `load`: every approved row not loaded yet, all under the one run id of the command, each document
  in its own transaction as `fin ingest` does (statements through `ingest_file`, source files
  through `ingest_source` or `ingest_bank`). Statements first, then trial balances before the
  Ledger Vouchers checked against them, then the other kinds. A file that fails is recorded and the
  next one loads; a file already loaded (same content) is marked loaded and not loaded twice.
  Gates reopen as they do on any ingest.
"""

from pathlib import Path

from fin.audit.errors import EXIT_FAILURES, DataroomRowInvalid, DuplicateFile, FinError
from fin.audit.logger import Run
from fin.config import Config
from fin.dataroom.scan import PlanRow, read_plan
from fin.db.conn import connect
from fin.diligence.bank import ingest_bank
from fin.diligence.sources import ingest_source
from fin.extract.profiles import KINDS, load_profile
from fin.ingest import ingest_file
from fin.map.dictionary import LabelMap, load_label_map
from fin.map.review import write_pending
from fin.paths import company_db_path

LOAD_ORDER = ("statements", "trial_balance", "ledger_vouchers", "registers", "payroll", "bank")
LOAD_ORDER += ("orderbook", "gst")
PROPOSABLE = (*KINDS, "statements")


def _row(rows: list[PlanRow], file_id: int) -> PlanRow:
    found = next((r for r in rows if r.file_id == file_id), None)
    if found is None:
        raise DataroomRowInvalid(f"no data-room plan row {file_id}").with_context(file_id=file_id)
    if found.kind == "archive" or found.status == "loaded":
        why = "an archive" if found.kind == "archive" else "already loaded"
        raise DataroomRowInvalid(f"plan row {file_id} ({found.path}) is {why}")
    return found


def propose(
    run: Run, company: str, file_id: int, kind: str, profile: str | None, reason: str
) -> PlanRow:
    """Record the assistant's kind for a row; the row waits for the user's approval. E819."""
    row = _row(read_plan(run.root, company), file_id)
    _check_proposal(row, kind, profile, reason)
    with connect(company_db_path(run.root, company), write=True) as con:
        con.execute(
            "UPDATE dataroom_files SET proposed_kind = ?, proposed_profile = ?, "
            "proposal_reason = ?, approved = false WHERE file_id = ?",
            [kind, profile, reason.strip(), file_id],
        )
    run.event("dataroom", f"{row.path}: proposed as {kind}", context={"file_id": file_id})
    return _row(read_plan(run.root, company), file_id)


def _check_proposal(row: PlanRow, kind: str, profile: str | None, reason: str) -> None:
    where = f"plan row {row.file_id} ({row.path})"
    if kind not in PROPOSABLE:
        raise DataroomRowInvalid(f"{where}: kind {kind!r} is not one of {', '.join(PROPOSABLE)}")
    if not reason.strip():
        raise DataroomRowInvalid(f"{where}: give the reason for the kind (--reason)")
    if kind == "statements" and profile is not None:
        raise DataroomRowInvalid(f"{where}: statements take no column profile")
    if kind == "gst" and profile is None:
        raise DataroomRowInvalid(f"{where}: a GST return needs its profile (gstr1, gstr3b)")
    if profile is not None:
        try:
            found = load_profile(profile).kind
        except FinError as exc:
            raise DataroomRowInvalid(f"{where}: {exc.message}") from exc
        if found != kind:
            raise DataroomRowInvalid(f"{where}: profile {profile!r} reads {found}, not {kind}")


def approve(run: Run, company: str, ids: list[int], *, undo: bool = False) -> list[PlanRow]:
    """Mark rows approved for the load (`undo`: not approved). All rows are checked before any
    changes. E819."""
    rows = read_plan(run.root, company)
    chosen = [_row(rows, file_id) for file_id in ids]
    for row in chosen:
        if not undo and row.reason is not None and row.proposed_kind is None:
            raise DataroomRowInvalid(
                f"plan row {row.file_id} ({row.path}): {row.reason}; propose its kind first"
            )
    with connect(company_db_path(run.root, company), write=True) as con:
        con.execute(
            "UPDATE dataroom_files SET approved = ? WHERE list_contains(?::INTEGER[], file_id)",
            [not undo, ids],
        )
    verb = "not approved" if undo else "approved"
    run.event("dataroom", f"plan rows {verb}: {', '.join(map(str, ids))}")
    return [r for r in read_plan(run.root, company) if r.file_id in ids]


def waiting(rows: list[PlanRow]) -> list[PlanRow]:
    """The approved rows not loaded yet, in load order."""
    todo = [r for r in rows if r.approved and r.status != "loaded"]
    return sorted(todo, key=lambda r: (LOAD_ORDER.index(r.load_kind[0]), r.file_id))


def load(run: Run, config: Config, company: str) -> list[tuple[PlanRow, str]]:
    """Load every approved row not loaded yet; (row, one-line outcome) for each."""
    todo = waiting(read_plan(run.root, company))
    if not todo:
        return []
    out = []
    label_map = load_label_map(run.root, company)
    for row in todo:
        status, outcome = _load_one(run, config, company, row, label_map)
        with connect(company_db_path(run.root, company), write=True) as con:
            con.execute(
                "UPDATE dataroom_files SET status = ?, outcome = ?, load_run_id = ? "
                "WHERE file_id = ?",
                [status, outcome, run.run_id, row.file_id],
            )
        out.append((row, outcome))
    write_pending(run, company)
    return out


def _load_one(
    run: Run, config: Config, company: str, row: PlanRow, label_map: LabelMap
) -> tuple[str, str]:
    """(status, outcome) of loading one row."""
    path = Path(row.location)
    kind, profile = row.load_kind
    if kind == "statements":
        failed, skipped = run.failed, run.skipped
        outcome = ingest_file(run, config, company, path, label_map, force=False)
        if run.failed > failed:
            return "failed", outcome
        return "loaded", ("already loaded: " + outcome) if run.skipped > skipped else outcome
    try:
        if kind == "bank":
            loaded = ingest_bank(run, config, company, path, profile)
        else:
            loaded = ingest_source(run, config, company, path, kind, profile)
    except DuplicateFile as exc:
        run.skipped += 1
        run.event("ingest", exc.message, level="warning", code=exc.code, file=path.name)
        return "loaded", f"already loaded: {exc}"
    except FinError as exc:
        run.failed += 1
        run.failure_codes.append(exc.code)
        run.exit_code = EXIT_FAILURES
        run.log_error("ingest", f"{path.name}: {exc.message}", exc.code, exc.context)
        return "failed", str(exc)
    return "loaded", "; ".join(loaded.lines)
