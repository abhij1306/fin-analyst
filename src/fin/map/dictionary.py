"""The shared account dictionary, `shared/dictionary.duckdb` (PLAN.md §7).

- `load_seed` adds the Schedule III accounts (`templates/dictionary/schedule_iii.csv`) and the seed
  label mappings (`templates/dictionary/labels.csv`) that are not in the dictionary yet. It never
  changes or removes a row, so `fin init` can run it every time.
- `load_label_map` reads the mappings that apply to one company; `find` looks a label up in them and
  `map_rows` maps a document's rows in order (it knows which part of the balance sheet a row is in).

Stored keys are normalized again when read, so a mapping saved before a normalization rule changed
("iii total income", saved before leading roman numerals were dropped) still matches.
"""

import csv
from collections.abc import Iterable
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
UNITS = ("rupees", "rupees_per_share")
ACCOUNT_COLUMNS = ["account", "statement", "parent", "sign", "unit", "description"]


@dataclass(frozen=True)
class Mapping:
    mapping_id: int
    account: str
    per_share: bool = False  # the account's unit is rupees_per_share: store the figure as printed


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
    accounts = _read_csv("schedule_iii.csv", ACCOUNT_COLUMNS)
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
        if row["unit"] not in UNITS or (row["unit"] != "rupees" and row["parent"]):
            raise DependencyVersionMismatch(
                f"schedule_iii.csv: {row['account']}: unit must be one of {', '.join(UNITS)}, "
                "and only a rupees account can have a parent"
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
                    "INSERT INTO accounts (account, statement, parent, sign, description, unit) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        row["account"],
                        row["statement"],
                        row["parent"] or None,
                        int(row["sign"]) if row["sign"] else None,
                        row["description"],
                        row["unit"],
                    ],
                )
            mapped = {
                (normalize_label(label), statement, normalize_label(section))
                for label, statement, section in con.execute(
                    "SELECT label, statement, section FROM label_map WHERE scope = 'global'"
                ).fetchall()
            }
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


# (company's own mapping?, statement, section, label); section and label normalized.
LabelMap = dict[tuple[bool, str, str, str], Mapping]

# Balance-sheet part headings that `map_rows` follows, and the total line after which a part starts.
PARTS = ("non current assets", "current assets", "non current liabilities", "current liabilities")
NEXT_PART = {
    "total_non_current_assets": "current assets",
    "total_equity": "non current liabilities",
    "total_non_current_liabilities": "current liabilities",
}


def load_label_map(root: Path, company: str) -> LabelMap:
    """Every mapping that applies to `company`: the global ones and the company's own."""
    with connect(dictionary_db_path(root)) as con:
        rows = con.execute(
            "SELECT m.scope = 'company', m.statement, m.section, m.label, m.mapping_id, m.account, "
            "a.unit = 'rupees_per_share' FROM label_map m LEFT JOIN accounts a USING (account) "
            "WHERE m.scope = 'global' OR m.company = ? ORDER BY m.mapping_id",
            [company],
        ).fetchall()
    return {
        (own, st, normalize_label(sec), normalize_label(label)): Mapping(mid, account, bool(unit))
        for own, st, sec, label, mid, account, unit in rows
    }


def find(
    label_map: LabelMap, statement: str, section: str, label: str, part: str = ""
) -> Mapping | None:
    """Look up a label as written, under the section heading it sits under (as written). The
    company's own mappings win over global ones. Within each, a mapping for that exact section
    wins over one for the balance-sheet part the row is in (`part`, see `map_rows`), which wins
    over one for any section ('')."""
    key = normalize_label(label)
    for own in (True, False):
        for heading in (normalize_label(section), part, ""):
            mapping = label_map.get((own, statement, heading, key))
            if mapping is not None:
                return mapping
    return None


def map_rows(
    label_map: LabelMap, rows: Iterable[tuple[str, str, str, str]]
) -> list[Mapping | None]:
    """Map a document's rows, given in document order as (statement, scope, section, label).

    A row's heading can be a sub-heading ("Financial liabilities") that does not say whether the
    row is current or non-current. So each row is also looked up under the part of the balance
    sheet it is in: the last part heading seen ("Non-current liabilities"), or the part that comes
    after the last total line (current liabilities follow "Total non-current liabilities").
    """
    found: list[Mapping | None] = []
    place: tuple[str, str] | None = None
    part = heading = ""
    for statement, scope, section, label in rows:
        if (statement, scope) != place:
            place, part, heading = (statement, scope), "", ""
        if normalize_label(section) != heading:
            heading = normalize_label(section)
            if heading in PARTS:
                part = heading
        mapping = find(label_map, statement, section, label, part)
        if mapping is not None and mapping.account in NEXT_PART:
            part = NEXT_PART[mapping.account]
        found.append(mapping)
    return found
