"""`fin disclosures load|list`: Claude's reading of notes to accounts and notices, stored in
`disclosures` and shown as one schedule per kind.

Loading the same document again replaces its rows. Each counterparty is masked on load: a name fin
already masked (a customer, vendor or party ledger) gets that token, which is how a related party is
matched to the books (view `related_party_matches`); a new name gets a PRTY- token. Known names in
nature and relationship are replaced by their tokens. A `ledger` must be a ledger of the
engagement's loaded trial balance or Ledger Vouchers (E940). Whether a Claude-read figure is trusted
is the user's decision: rows are stored with read_by 'claude' and printed as not verified.
"""

import hashlib
from pathlib import Path
from typing import Any

import duckdb

from fin.audit.errors import DisclosureReadingInvalid
from fin.audit.logger import Run
from fin.db.conn import transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database, require_open, rupees
from fin.diligence.mask import Masker
from fin.disclosures.reading import KINDS, read_rows
from fin.reconcile import periods_of

COLUMNS = (
    "kind", "period", "counterparty", "nature", "amount", "forum", "stage", "assessment_year",
    "demand", "paid_under_protest", "relationship", "books", "ledger", "page",
)  # fmt: skip


def _ledgers(con: duckdb.DuckDBPyConnection, engagement_id: int) -> set[str]:
    return {
        ledger
        for (ledger,) in con.execute(
            "SELECT t.ledger FROM trial_balance t JOIN source_files f USING (file_id) "
            "WHERE f.engagement_id = ? AND f.status = 'loaded' AND t.row_type = 'ledger' "
            "UNION SELECT b.ledger FROM ledger_blocks b JOIN source_files f USING (file_id) "
            "WHERE f.engagement_id = ? AND f.status = 'loaded'",
            [engagement_id, engagement_id],
        ).fetchall()
    }


def _mask(masker: Masker, rows: list[dict[str, Any]], ledgers: set[str]) -> None:
    """Counterparties to tokens (new names first, so the free text masks them too), then the free
    text and the ledgers. E940 for a ledger in no loaded file (its name is not printed)."""
    for row in rows:
        value = row["counterparty"]
        if value is not None and value not in masker.names:
            row["counterparty"] = masker.known(value) or masker.token("party", value)
    unknown = []
    for row in rows:
        row["nature"] = masker.text(row["nature"])
        row["relationship"] = masker.text(row["relationship"])
        if row["ledger"] is not None:
            row["ledger"] = masker.known(row["ledger"]) or row["ledger"]
            if row["ledger"] not in ledgers:
                unknown.append(f"line {row['line']}")
    if unknown:
        raise DisclosureReadingInvalid(
            f"{', '.join(unknown)}: the ledger is in no loaded trial balance or Ledger Vouchers of "
            "the engagement (use the ledger as `fin map ledgers` lists it); nothing recorded"
        )


def load(run: Run, company: str, path: Path, reading: Path) -> list[dict[str, Any]]:
    """Store the reading's items for the open engagement, replacing an earlier load of the same
    document. E803 without an open engagement; E940 (nothing recorded)."""
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        engagement_id = engagement["engagement_id"]
        rows = read_rows(reading, periods_of(engagement))
        old = con.execute(
            "DELETE FROM disclosure_files WHERE engagement_id = ? AND sha256 = ? RETURNING file_id",
            [engagement_id, sha256],
        ).fetchall()
        for (file_id,) in old:
            con.execute("DELETE FROM disclosures WHERE file_id = ?", [file_id])
        new = con.execute(
            "INSERT INTO disclosure_files (engagement_id, file_name, sha256, reading_name, run_id, "
            "loaded_at) VALUES (?, ?, ?, ?, ?, ?) RETURNING file_id",
            [engagement_id, path.name, sha256, reading.name, run.run_id, utcnow()],
        ).fetchone()
        assert new is not None
        masker = Masker(con, run.run_id)
        masker.file_id = new[0]
        _mask(masker, rows, _ledgers(con, engagement_id))
        masker.remask()
        marks = ", ".join("?" for _ in COLUMNS)
        now = utcnow()
        for row in rows:
            con.execute(
                f"INSERT INTO disclosures (engagement_id, file_id, {', '.join(COLUMNS)}, read_by, "
                f"run_id, created_at) VALUES (?, ?, {marks}, 'claude', ?, ?)",
                [engagement_id, new[0], *(row[c] for c in COLUMNS), run.run_id, now],
            )
        run.event(
            "disclosures",
            f"{len(rows)} items read from {path.name} loaded",
            context={"company": company, "file_id": new[0]},
        )
        return read(con, engagement_id)


def read(
    con: duckdb.DuckDBPyConnection, engagement_id: int, kind: str | None = None
) -> list[dict[str, Any]]:
    """The engagement's items (only `kind`), by schedule, period and id, with their file name."""
    return fetch_dicts(
        con,
        "SELECT d.*, f.file_name FROM disclosures d JOIN disclosure_files f USING (file_id) "
        "WHERE d.engagement_id = ? AND (? IS NULL OR d.kind = ?) "
        "ORDER BY list_position(?::VARCHAR[], d.kind), d.period, d.disclosure_id",
        [engagement_id, kind, kind, list(KINDS)],
    )


def list_disclosures(run: Run, company: str, kind: str | None = None) -> list[dict[str, Any]]:
    with company_database(run, company) as con:
        return read(con, require_open(con, company)["engagement_id"], kind)


def _fields(row: dict[str, Any]) -> list[str]:
    out = [f"amount {rupees(row['amount'])}"] if row["amount"] is not None else []
    for name in ("relationship", "forum", "stage", "assessment_year"):
        if row[name]:
            out.append(f"{name.replace('_', ' ')} {row[name]}")
    for name in ("demand", "paid_under_protest"):
        if row[name] is not None:
            out.append(f"{name.replace('_', ' ')} {rupees(row[name])}")
    if row["books"]:
        ledger = f" {row['ledger']}" if row["ledger"] else ""
        out.append(f"compared with books {row['books']}{ledger}")
    return out


def format_schedules(rows: list[dict[str, Any]]) -> str:
    """One block per kind (a schedule), each row with its source, marked not verified."""
    out: list[str] = []
    for kind in KINDS:
        chosen = [row for row in rows if row["kind"] == kind]
        if not chosen:
            continue
        out.append(f"{kind} ({len(chosen)})")
        for row in chosen:
            party = f" {row['counterparty']}" if row["counterparty"] else ""
            fields = "".join(f", {field}" for field in _fields(row))
            out.append(
                f"  #{row['disclosure_id']} {row['period']}{party}: {row['nature']}{fields} "
                f"({row['file_name']} p.{row['page']}, read by {row['read_by']}, not verified)"
            )
    return "\n".join(out) or "no disclosures"
