"""The mapping of reference lines (SPEC-10): the same proposal and review flow as a company's
labels (docs/product.md "Mapping review"), as its own review kind.

A line the dictionary did not map waits ('pending'). The assistant proposes an account or a metric
for it (`fin reference propose`: always the AI's proposal); only the user confirms or rejects it
(`fin reference confirm|reject`, or the References screen). A confirmed or dictionary line is
compared; a rejected one is not. An account must be of the line's statement and in rupees; a
metric must be in rupees: a ratio or a per-share figure is never compared as money.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from fin.actor import CLAUDE
from fin.audit.errors import ReferenceLineInvalid
from fin.audit.logger import Run
from fin.db.conn import connect, transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.map.dictionary import Account, load_accounts
from fin.paths import company_db_path

OPEN = ("pending", "proposed")
FIELDS = {"line", "confidence", "reason"}


def list_lines(root: Path, company: str, open_only: bool = True) -> list[dict[str, Any]]:
    """The company's reference lines (only those waiting when `open_only`) with how many figures
    each has, by id."""
    path = company_db_path(root, company)
    if not path.is_file():
        return []
    with connect(path) as con:
        return fetch_dicts(
            con,
            "SELECT l.*, (SELECT count(*) FROM reference_values v WHERE v.line_id = l.line_id) "
            "AS figures FROM reference_lines l WHERE NOT ? OR l.status IN ('pending', 'proposed') "
            "ORDER BY l.line_id",
            [open_only],
        )


def _target(
    con: duckdb.DuckDBPyConnection,
    accounts: dict[str, Account],
    line: dict[str, Any],
    kind: str,
    name: Any,
) -> None:
    """E981 unless `name` is an account of the line's statement in rupees, or a metric in
    rupees."""
    where = f"line #{line['line_id']} ({line['label']!r})"
    if not isinstance(name, str):
        raise ReferenceLineInvalid(f"{where}: the {kind} must be text")
    if kind == "account":
        account = accounts.get(name)
        if account is None or account.statement != line["statement"]:
            raise ReferenceLineInvalid(
                f"{where}: {name!r} is not a {line['statement']} account (fin map accounts)"
            )
        if account.unit != "rupees":
            raise ReferenceLineInvalid(f"{where}: {name!r} is per share, not money")
        return
    found = con.execute("SELECT unit FROM metric_definitions WHERE metric = ?", [name]).fetchone()
    if found is None or found[0] != "rupees":
        state = "not a metric" if found is None else f"in {found[0]}, not rupees"
        raise ReferenceLineInvalid(f"{where}: {name!r} is {state}")


def _checked(number: int, proposal: Any, lines: dict[int, dict[str, Any]]) -> tuple[str, Any]:
    """(target kind, target) of a proposal whose fields are right. E981 otherwise."""
    what = f"proposal {number}"
    if not isinstance(proposal, dict):
        raise ReferenceLineInvalid(f"{what}: expected an object")
    kinds = [k for k in ("account", "metric") if k in proposal]
    if len(kinds) != 1 or set(proposal) != FIELDS | set(kinds):
        raise ReferenceLineInvalid(
            f"{what}: expected line, account or metric (one of them), confidence and reason"
        )
    confidence, reason = proposal["confidence"], proposal["reason"]
    if isinstance(confidence, bool) or not isinstance(confidence, int | float):
        raise ReferenceLineInvalid(f"{what}: confidence must be a number from 0 to 1")
    if not 0 <= confidence <= 1:
        raise ReferenceLineInvalid(f"{what}: confidence must be from 0 to 1")
    if not isinstance(reason, str) or not reason.strip():
        raise ReferenceLineInvalid(f"{what}: give the reason as text")
    line_id = proposal["line"]
    line = (
        lines.get(line_id) if isinstance(line_id, int) and not isinstance(line_id, bool) else None
    )
    if line is None or line["status"] not in (*OPEN, "rejected"):
        state = "no such line" if line is None else line["status"]
        raise ReferenceLineInvalid(f"{what}: line {proposal['line']!r} is not waiting ({state})")
    return kinds[0], proposal[kinds[0]]


def propose(run: Run, company: str, proposals: Any) -> list[int]:
    """Store the assistant's proposals, all or nothing (E981); the line ids."""
    if not isinstance(proposals, list) or not proposals:
        raise ReferenceLineInvalid("expected a JSON list of proposals; none stored")
    accounts = load_accounts(run.root)
    with connect(company_db_path(run.root, company), write=True) as con, transaction(con):
        lines = {r["line_id"]: r for r in fetch_dicts(con, "SELECT * FROM reference_lines")}
        checked = []
        for number, proposal in enumerate(proposals, start=1):
            kind, target = _checked(number, proposal, lines)
            _target(con, accounts, lines[proposal["line"]], kind, target)
            checked.append((proposal, kind, target))
        ids = [p["line"] for p, _, _ in checked]
        if len(set(ids)) != len(ids):
            raise ReferenceLineInvalid("a line is proposed twice in the file; none stored")
        for proposal, kind, target in checked:
            confidence = Decimal(str(proposal["confidence"]))
            values = [kind, target, confidence, proposal["reason"].strip(), CLAUDE]
            con.execute(
                "UPDATE reference_lines SET status = 'proposed', target_kind = ?, target = ?, "
                "confidence = ?, reason = ?, actor = ?, run_id = ?, changed_at = ? "
                "WHERE line_id = ?",
                [*values, run.run_id, utcnow(), proposal["line"]],
            )
            _log(run, company, proposal["line"], f"proposed as {kind} {target}")
    return ids


def decide(
    run: Run, company: str, ids: list[int], status: str, *, actor: str
) -> list[dict[str, Any]]:
    """The user's decision, all or nothing: 'confirmed' (a proposed line) or 'rejected' (any line
    not rejected yet). E981 for an unknown id or a line not waiting for it."""
    ids = list(dict.fromkeys(ids))
    with connect(company_db_path(run.root, company), write=True) as con, transaction(con):
        lines = {r["line_id"]: r for r in fetch_dicts(con, "SELECT * FROM reference_lines")}
        for line_id in ids:
            line = lines.get(line_id)
            allowed = line is not None and (
                line["status"] == "proposed" if status == "confirmed" else line["status"] != status
            )
            if not allowed:
                state = "no such line" if line is None else line["status"]
                raise ReferenceLineInvalid(
                    f"reference line #{line_id} cannot be {status} ({state}); nothing changed "
                    f"(fin reference lines {company} --all)"
                )
        for line_id in ids:
            con.execute(
                "UPDATE reference_lines SET status = ?, actor = ?, run_id = ?, changed_at = ? "
                "WHERE line_id = ?",
                [status, actor, run.run_id, utcnow(), line_id],
            )
            line = lines[line_id]
            _log(run, company, line_id, f"{status}: {line['target_kind']} {line['target']}")
        return [
            r
            for r in fetch_dicts(con, "SELECT * FROM reference_lines ORDER BY line_id")
            if r["line_id"] in ids
        ]


def _log(run: Run, company: str, line_id: int, what: str) -> None:
    run.event(
        "reference",
        f"reference line #{line_id} {what}",
        context={"company": company, "line_id": line_id},
    )


def format_line(row: dict[str, Any]) -> str:
    line = f"line #{row['line_id']} [{row['status']}] {row['statement']} / {row['section']!r} "
    line += (
        f"{row['label']!r} ({row['figures']} figures)" if "figures" in row else repr(row["label"])
    )
    if row["target"]:
        line += f" -> {row['target_kind']} {row['target']}"
    if row["status"] == "proposed":
        line += f" (confidence {row['confidence']}): {row['reason']}"
    return line
