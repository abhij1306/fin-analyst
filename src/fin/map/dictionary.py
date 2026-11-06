"""The shared account dictionary, `shared/dictionary.duckdb` (PLAN.md §7).

- `load_seed` adds the Schedule III accounts (`templates/dictionary/schedule_iii.csv`) and the seed
  label mappings (`templates/dictionary/labels.csv`) that are not in the dictionary yet. It never
  changes or removes a row, so `fin init` can run it every time.
- `load_label_map` reads the mappings that apply to one company; `find` looks a label up in them.
"""

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb

from fin.audit.errors import DependencyVersionMismatch
from fin.db.conn import connect
from fin.map.normalize import normalize_label
from fin.paths import dictionary_db_path

if TYPE_CHECKING:
    from fin.audit.logger import Run

TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "templates" / "dictionary"
STATEMENTS = ("balance_sheet", "profit_and_loss", "cash_flow")


@dataclass(frozen=True)
class Mapping:
    mapping_id: int
    account: str


@dataclass(frozen=True)
class SeedResult:
    accounts_added: int
    labels_added: int


def _read_csv(name: str, columns: list[str]) -> list[dict[str, str]]:
    path = TEMPLATES_DIR / name
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != columns:
            raise DependencyVersionMismatch(
                f"{path}: columns are {reader.fieldnames}, expected {columns}"
            )
        return list(reader)


def read_seed() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Read and check both seed CSVs. Raises E002 if they are inconsistent."""
    accounts = _read_csv(
        "schedule_iii.csv", ["account", "statement", "parent", "sign", "description"]
    )
    labels = _read_csv("labels.csv", ["label", "statement", "section", "account"])
    statement_of = {row["account"]: row["statement"] for row in accounts}
    for row in accounts:
        if row["statement"] not in STATEMENTS:
            raise DependencyVersionMismatch(f"schedule_iii.csv: {row['account']}: bad statement")
        if row["parent"] and statement_of.get(row["parent"]) != row["statement"]:
            raise DependencyVersionMismatch(
                f"schedule_iii.csv: {row['account']}: parent {row['parent']!r} is not an account "
                "of the same statement"
            )
        if (row["parent"] == "") != (row["sign"] == "") or row["sign"] not in ("", "1", "-1"):
            raise DependencyVersionMismatch(
                f"schedule_iii.csv: {row['account']}: sign must be 1 or -1 with a parent, "
                "empty without"
            )
    seen: dict[tuple[str, str, str], str] = {}
    for row in labels:
        if statement_of.get(row["account"]) != row["statement"]:
            raise DependencyVersionMismatch(
                f"labels.csv: {row['label']!r}: account {row['account']!r} is not a "
                f"{row['statement']} account in schedule_iii.csv"
            )
        key = (normalize_label(row["label"]), row["statement"], normalize_label(row["section"]))
        if seen.setdefault(key, row["account"]) != row["account"]:
            raise DependencyVersionMismatch(
                f"labels.csv: {row['label']!r} maps to both {seen[key]} and {row['account']}"
            )
    return accounts, labels


def load_seed(root: Path, run: "Run") -> SeedResult:
    """Add seed accounts and global label mappings that are missing, in one transaction."""
    accounts, labels = read_seed()
    now = datetime.now(UTC).replace(tzinfo=None)
    with connect(dictionary_db_path(root), write=True) as con:
        con.execute("BEGIN TRANSACTION")
        try:
            known = {row[0] for row in con.execute("SELECT account FROM accounts").fetchall()}
            new_accounts = [row for row in accounts if row["account"] not in known]
            for row in new_accounts:
                con.execute(
                    "INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
                    [
                        row["account"],
                        row["statement"],
                        row["parent"] or None,
                        int(row["sign"]) if row["sign"] else None,
                        row["description"],
                    ],
                )
            mapped = set(
                con.execute(
                    "SELECT label, statement, section FROM label_map WHERE scope = 'global'"
                ).fetchall()
            )
            added = 0
            for row in labels:
                key = (
                    normalize_label(row["label"]),
                    row["statement"],
                    normalize_label(row["section"]),
                )
                if key in mapped:
                    continue
                mapped.add(key)
                con.execute(
                    "INSERT INTO label_map (label, statement, section, account, scope, company, "
                    "source, created_at, run_id) VALUES (?, ?, ?, ?, 'global', NULL, 'seed', ?, ?)",
                    [*key, row["account"], now, run.run_id],
                )
                added += 1
            con.execute("COMMIT")
        except duckdb.Error:
            con.execute("ROLLBACK")
            raise
    return SeedResult(len(new_accounts), added)


LabelMap = dict[tuple[str, str, str], Mapping]


def load_label_map(root: Path, company: str) -> LabelMap:
    """Every mapping that applies to `company`, keyed by (statement, section, label), all
    normalized. A company-scope row replaces a global row with the same key."""
    with connect(dictionary_db_path(root)) as con:
        rows = con.execute(
            "SELECT statement, section, label, mapping_id, account FROM label_map "
            "WHERE scope = 'global' OR company = ? ORDER BY scope = 'company', mapping_id",
            [company],
        ).fetchall()
    return {(st, sec, label): Mapping(mid, account) for st, sec, label, mid, account in rows}


def find(label_map: LabelMap, statement: str, section: str, label: str) -> Mapping | None:
    """Look up a label as written, under the section heading it sits under (as written). A mapping
    for that exact section wins over one for any section ('')."""
    key = normalize_label(label)
    return label_map.get((statement, normalize_label(section), key)) or label_map.get(
        (statement, "", key)
    )
