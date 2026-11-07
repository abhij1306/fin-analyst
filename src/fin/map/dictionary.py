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


def account_problem(row: dict[str, str], statement_of: dict[str, str]) -> str | None:
    """What is wrong with an account row (the schedule_iii.csv columns, as text), or None.
    `statement_of` is {account: statement} for the accounts a parent may be."""
    if row["statement"] not in STATEMENTS:
        return f"statement must be one of {', '.join(STATEMENTS)}"
    if row["parent"] and statement_of.get(row["parent"]) != row["statement"]:
        return f"parent {row['parent']!r} is not an account of the same statement"
    if (row["parent"] == "") != (row["sign"] == "") or row["sign"] not in ("", "1", "-1"):
        return "sign must be 1 or -1 with a parent, empty without"
    if row["unit"] not in UNITS or (row["unit"] != "rupees" and row["parent"]):
        return (
            f"unit must be one of {', '.join(UNITS)}, and only a rupees account can have a parent"
        )
    return None


def read_seed() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Read and check both seed CSVs. Raises E002 if they are inconsistent."""
    accounts = _read_csv("schedule_iii.csv", ACCOUNT_COLUMNS)
    labels = _read_csv("labels.csv", ["label", "statement", "section", "account"])
    statement_of = {row["account"]: row["statement"] for row in accounts}
    for row in accounts:
        problem = account_problem(row, statement_of)
        if problem:
            raise DependencyVersionMismatch(f"schedule_iii.csv: {row['account']}: {problem}")
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


def added_rows(root: Path) -> tuple[list[list[str]], list[list[str]]]:
    """The accounts added in review and the global labels confirmed in review that the seed CSVs
    do not have yet, as rows in the schedule_iii.csv and labels.csv formats (labels normalized)."""
    seed_accounts, seed_labels = read_seed()
    known = {row["account"] for row in seed_accounts}
    seeded = {
        (normalize_label(row["label"]), row["statement"], normalize_label(row["section"]))
        for row in seed_labels
    }
    with connect(dictionary_db_path(root)) as con:
        accounts = con.execute(
            "SELECT account, statement, coalesce(parent, ''), coalesce(CAST(sign AS VARCHAR), ''), "
            "unit, description FROM accounts WHERE source = 'review' ORDER BY statement, account"
        ).fetchall()
        labels = con.execute(
            "SELECT label, statement, section, account FROM label_map "
            "WHERE scope = 'global' AND source <> 'seed' ORDER BY mapping_id"
        ).fetchall()
    return (
        [list(row) for row in accounts if row[0] not in known],
        [
            list(row)
            for row in labels
            if (normalize_label(row[0]), row[1], normalize_label(row[2])) not in seeded
        ],
    )


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
    The part is followed for each statement and scope on its own, so a document that prints
    standalone and consolidated columns side by side (rows interleaved) keeps its parts.

    A lender's balance sheet (NBFC Division III, bank Form A) has no current / non-current parts,
    so lines such as "Loans" or "Provisions" would be left to review. It is told apart from a
    Division II balance sheet whose part headings were not read by the whole statement (see
    `_lender_balance_sheets`); its rows are then looked up under the lender part instead:
    'lender assets' or 'lender liabilities' (templates/dictionary/labels.csv), by the side of the
    balance sheet the row is on.
    """
    rows = list(rows)
    found: list[Mapping | None] = []
    parts: dict[tuple[str, str], str] = {}  # (statement, scope) -> the part its rows are in
    headings: dict[tuple[str, str], str] = {}  # (statement, scope) -> its last heading
    for statement, scope, section, label in rows:
        place = (statement, scope)
        heading = normalize_label(section)
        if heading != headings.get(place, "") and heading in PARTS:
            parts[place] = heading
        headings[place] = heading
        mapping = find(label_map, statement, section, label, parts.get(place, ""))
        if mapping is not None and mapping.account in NEXT_PART:
            parts[place] = NEXT_PART[mapping.account]
        found.append(mapping)

    lenders = _lender_balance_sheets(rows, found)
    sides: dict[tuple[str, str], str] = {}
    for n, (statement, scope, section, label) in enumerate(rows):
        place = (statement, scope)
        if place not in lenders:
            continue
        heading = normalize_label(section)
        if "liabilit" in heading:
            sides[place] = "lender liabilities"
        elif "asset" in heading:
            sides[place] = "lender assets"
        found[n] = find(label_map, statement, section, label, sides.get(place, "lender assets"))
        if found[n] is not None and found[n].account == "total_assets":
            sides[place] = "lender liabilities"
    return found


# Totals of a Division II balance-sheet part.
PART_TOTALS = {
    "total_non_current_assets",
    "total_current_assets",
    "total_non_current_liabilities",
    "total_current_liabilities",
}
# Accounts only a lender's balance sheet has (NBFC Division III, bank Form A).
LENDER_ACCOUNTS = {
    "debt_securities",
    "deposits",
    "subordinated_liabilities",
    "balances_with_rbi",
    "balances_with_banks",
    "other_non_financial_assets",
    "other_non_financial_liabilities",
    "other_liabilities_and_provisions",
    "total_financial_assets",
    "total_non_financial_assets",
    "total_financial_liabilities",
    "total_non_financial_liabilities",
}


def _lender_balance_sheets(
    rows: list[tuple[str, str, str, str]], found: list[Mapping | None]
) -> set[tuple[str, str]]:
    """The (statement, scope) balance sheets that are a lender's: no current / non-current part
    heading or part total anywhere, and a lender marker: a line only lenders have
    (LENDER_ACCOUNTS), or "Loans" or "Investments" under a "Financial assets" heading. A Division
    II balance sheet always has its part totals, even when its headings were not read."""
    division_ii: set[tuple[str, str]] = set()
    marked: set[tuple[str, str]] = set()
    for (statement, scope, section, label), mapping in zip(rows, found, strict=True):
        if statement != "balance_sheet":
            continue
        place = (statement, scope)
        heading, key = normalize_label(section), normalize_label(label)
        account = mapping.account if mapping is not None else None
        if heading in PARTS or key in PARTS or account in PART_TOTALS:
            division_ii.add(place)
        if account in LENDER_ACCOUNTS or (
            heading == "financial assets" and key in ("loans", "investments")
        ):
            marked.add(place)
    return marked - division_ii
