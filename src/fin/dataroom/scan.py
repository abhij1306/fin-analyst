"""`fin dataroom scan` and `fin dataroom plan` (SPEC-13): walk a folder into a plan; load nothing.

The walk goes through every sub-folder, in name order, leaving out hidden files and folders (a name
starting with "."), as `fin ingest` does. A link is listed, never followed. Each file is hashed:
the first path of a content is its plan row and later paths are its duplicates. A .zip archive is
unpacked (`fin.dataroom.unpack`) into `dataroom/<company>/<sha256[:16]>/` in the data folder and
its files are walked as part of it, to `MAX_DEPTH` archives deep. Each new file gets its kind
(`fin.dataroom.kinds`).

Re-scanning adds only what the plan does not have: a content already in it gains the new path as a
duplicate, nothing else changes. The plan lives in the company database (`dataroom_files`).
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from fin.audit.errors import DataroomFolderInvalid, DataroomPlanMissing, FileUnreadable
from fin.audit.logger import Run
from fin.config import Config
from fin.dataroom.kinds import ARCHIVE_SUFFIXES, REASONS, Kind, kind_of, unusable
from fin.dataroom.unpack import unpack
from fin.db.conn import connect, transaction
from fin.db.schema import ensure_database
from fin.paths import company_db_path, dataroom_dir

MAX_DEPTH = 3  # archives inside archives unpacked
COLUMNS = (
    "file_id, sha256, folder, path, location, kind, profile, period, reason, next_step, "
    "duplicates, proposed_kind, proposed_profile, proposal_reason, approved, status, outcome"
)


@dataclass(frozen=True)
class PlanRow:
    file_id: int
    sha256: str | None
    folder: str
    path: str
    location: str
    kind: str
    profile: str | None
    period: str | None
    reason: str | None
    next_step: str | None
    duplicates: list[str]
    proposed_kind: str | None
    proposed_profile: str | None
    proposal_reason: str | None
    approved: bool
    status: str  # planned, loaded or failed
    outcome: str | None

    @property
    def load_kind(self) -> tuple[str, str | None]:
        """(kind, profile) the load uses: the proposal the user confirmed, else the scan's."""
        if self.proposed_kind is not None:
            return self.proposed_kind, self.proposed_profile
        return self.kind, self.profile


@dataclass
class Scan:
    company: str
    folder: Path
    new: int = 0  # rows added to the plan
    duplicates: int = 0  # paths added as duplicates of a content already in the plan


@dataclass
class _Walk:
    """What one scan carries through the walk."""

    run: Run
    config: Config
    con: duckdb.DuckDBPyConnection
    scan: Scan


def scan(run: Run, config: Config, company: str, folder: Path) -> Scan:
    """Walk `folder` and add what is new to the company's plan. E814 when the folder cannot be
    scanned; E003 when an archive cannot be unpacked into the data folder."""
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        raise DataroomFolderInvalid(f"{folder} is not a folder").with_context(folder=str(folder))
    if run.root.resolve().is_relative_to(folder):
        raise DataroomFolderInvalid(
            f"{folder} holds the data folder {run.root}: scan the data room's own folder"
        ).with_context(folder=str(folder))
    path = company_db_path(run.root, company)
    ensure_database(path, "company")
    result = Scan(company, folder)
    with connect(path, write=True) as con, transaction(con):
        _walk(_Walk(run, config, con, result), folder, "", 0)
    run.event("dataroom", f"scanned {folder}: {result.new} new, {result.duplicates} duplicates")
    return result


def _walk(state: _Walk, folder: Path, shown: str, depth: int) -> None:
    """Every entry under `folder`; `shown` is the shown path of the folder ("" at the top)."""
    for entry in sorted(folder.iterdir(), key=lambda p: p.name):
        if entry.name.startswith("."):
            continue
        name = f"{shown}{entry.name}"
        if entry.is_symlink():
            _add(state, None, name, entry, unusable("link"))
        elif entry.is_dir():
            _walk(state, entry, f"{name}/", depth)
        else:
            _file(state, entry, name, depth)


def _file(state: _Walk, entry: Path, name: str, depth: int) -> None:
    sha256 = _sha256(entry)
    if _known(state, sha256, name):
        return
    if entry.suffix.lower() not in ARCHIVE_SUFFIXES:
        _add(state, sha256, name, entry, kind_of(entry, state.config.fiscal_year_start_month))
        return
    if depth >= MAX_DEPTH:
        _add(state, sha256, name, entry, unusable("archive_depth", "archive"))
        return
    target = dataroom_dir(state.run.root, state.scan.company) / sha256[:16]
    reason = unpack(entry, target, state.config.archive_limit_mb * 1024 * 1024)
    _add(state, sha256, name, entry, Kind("archive", reason=reason))
    if reason is None:
        _walk(state, target, f"{name}/", depth + 1)
    else:
        state.run.event("dataroom", f"{name}: refused ({reason})", level="warning", file=entry.name)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1 << 20):
                digest.update(chunk)
    except OSError as exc:
        raise FileUnreadable(f"{path.name}: {exc}") from exc
    return digest.hexdigest()


def _known(state: _Walk, sha256: str, name: str) -> bool:
    """True when the plan has this content: `name` is added as a duplicate unless listed."""
    row = state.con.execute(
        "SELECT file_id, folder, path, duplicates FROM dataroom_files WHERE sha256 = ?", [sha256]
    ).fetchone()
    if row is None:
        return False
    file_id, folder, path, duplicates = row
    if (folder, path) != (str(state.scan.folder), name) and name not in duplicates:
        state.con.execute(
            "UPDATE dataroom_files SET duplicates = list_append(duplicates, ?) WHERE file_id = ?",
            [name, file_id],
        )
        state.scan.duplicates += 1
    return True


def _add(state: _Walk, sha256: str | None, name: str, location: Path, kind: Kind) -> None:
    if (
        sha256 is None
        and state.con.execute(
            "SELECT 1 FROM dataroom_files WHERE sha256 IS NULL AND location = ?", [str(location)]
        ).fetchone()
    ):
        return
    reason, step = REASONS[kind.reason] if kind.reason else (None, None)
    state.con.execute(
        "INSERT INTO dataroom_files (sha256, folder, path, location, kind, profile, period, "
        "reason, next_step, duplicates, run_id, scanned_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, [], ?, ?)",
        [sha256, str(state.scan.folder), name, str(location), kind.kind, kind.profile]
        + [kind.period, reason, step, state.run.run_id, datetime.now(UTC).replace(tzinfo=None)],
    )
    state.scan.new += 1


def read_plan(root: Path, company: str) -> list[PlanRow]:
    """The company's plan, in the order the files were found. E815 when it has none."""
    path = company_db_path(root, company)
    rows = []
    if path.is_file():
        with connect(path) as con:
            rows = con.execute(f"SELECT {COLUMNS} FROM dataroom_files ORDER BY file_id").fetchall()
    if not rows:
        raise DataroomPlanMissing(
            f"{company} has no data-room plan; run `uv run fin dataroom scan {company} <folder>`"
        )
    return [PlanRow(*row) for row in rows]


def plan_json(rows: list[PlanRow]) -> str:
    return json.dumps([asdict(row) for row in rows], indent=2)


def format_plan(company: str, rows: list[PlanRow]) -> str:
    """The plan as text: the files fin can use, the archives, then what will be skipped (with the
    assistant's proposed kind where there is one)."""
    usable = [r for r in rows if r.kind != "archive" and (r.reason is None or r.proposed_kind)]
    archives = [r for r in rows if r.reason is None and r.kind == "archive"]
    skipped = [r for r in rows if r.reason is not None and not r.proposed_kind]
    approved = sum(1 for r in usable if r.approved)
    lines = [
        f"data-room plan for {company}: {len(usable)} to load ({approved} approved), "
        f"{len(skipped)} skipped"
    ]
    for r in usable:
        lines.append(f"  [{r.file_id}] {r.path}: {_described(r)}; {_state(r)}")
        lines.extend(f"      duplicate: {d}" for d in r.duplicates)
    lines.extend(f"  [{r.file_id}] {r.path}: archive, unpacked" for r in archives)
    if skipped:
        lines.append("skipped (fin cannot use these):")
    for r in skipped:
        lines.append(f"  [{r.file_id}] {r.path}: {r.reason}. Next: {r.next_step}")
    return "\n".join(lines)


def _described(r: PlanRow) -> str:
    kind, profile = r.load_kind
    detail = ", ".join(x for x in (profile and f"profile {profile}", r.period) if x)
    text = kind + (f" ({detail})" if detail else "")
    if r.proposed_kind:
        text += f", proposed by the assistant: {r.proposal_reason} (fin: {r.reason})"
    return text


def _state(r: PlanRow) -> str:
    if r.status != "planned":
        return f"{r.status}: {r.outcome}"
    return "approved" if r.approved else "not approved"
