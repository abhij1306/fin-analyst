"""Checks across an engagement's bank statements, and G1's bank line (docs/diligence.md D3, §5).

Statements of one account (bank, account number, currency) are put in date order. A statement that
starts on or before the end of an earlier one overlaps it (`bank_overlap`: the same days twice, so
a transaction may be counted twice). Otherwise its opening balance must be the previous
statement's closing balance; when it is not, that is `bank_balance_gap`: a transaction, or a whole
statement, may be missing. Both are findings on the later statement, to become accountant queries;
nothing is changed. `record_all` runs after every bank load and again on every statement, so
loading the missing statement resolves a gap.

G1 (`checklist`, `inputs`): every loaded bank parse ties (a statement that does not has a
`bank_tie` finding the user must accept first). Overlaps and balance gaps are listed, not blocking:
they go to the accountant as queries (G2).
"""

from typing import Any

import duckdb

from fin.audit.logger import Run
from fin.findings import record

Line = tuple[bool, str]
CHECKS = "('bank_overlap', 'bank_balance_gap')"


def _statements(con: duckdb.DuckDBPyConnection, engagement_id: int) -> list[dict[str, Any]]:
    cursor = con.execute(
        "SELECT b.file_id, s.file_name, b.bank, b.account, b.currency, b.period_start, "
        "b.period_end, b.opening_balance, b.closing_balance FROM bank_statements b "
        "JOIN source_files s USING (file_id) WHERE s.status = 'loaded' AND s.engagement_id = ? "
        "ORDER BY b.bank, b.account, b.currency, b.period_start, b.period_end, b.file_id",
        [engagement_id],
    )
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def consistency_findings(statements: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Overlap and balance gap findings (`findings` columns) by the file they are on."""
    out: dict[int, list[dict[str, Any]]] = {s["file_id"]: [] for s in statements}
    latest: dict[tuple[Any, ...], dict[str, Any]] = {}  # the statement ending last, per account
    for cur in statements:
        key = (cur["bank"], cur["account"], cur["currency"])
        prev = latest.get(key)
        if prev is not None:
            label = f"{cur['bank']} {cur['account']} {cur['currency']}"
            period = f"{cur['period_start'].isoformat()} to {cur['period_end'].isoformat()}"
            base = {
                "file_name": cur["file_name"],
                "statement": "bank",
                "period": period,
                "label": label,
                "other_file": prev["file_name"],
            }
            if cur["period_start"] <= prev["period_end"]:
                out[cur["file_id"]].append(
                    base
                    | {
                        "check_name": "bank_overlap",
                        "finding": "bank_periods_overlap",
                        "fin_source": f"starts {cur['period_start']}",
                        "other_source": f"ends {prev['period_end']}",
                        "message": (
                            f"bank statements {label}: {cur['file_name']} ({period}) overlaps "
                            f"{prev['file_name']} (ends {prev['period_end']}); a transaction may "
                            "be in both"
                        ),
                    }
                )
            elif cur["opening_balance"] != prev["closing_balance"]:
                out[cur["file_id"]].append(
                    base
                    | {
                        "check_name": "bank_balance_gap",
                        "finding": "bank_balance_gap",
                        "fin_value": cur["opening_balance"],
                        "other_value": prev["closing_balance"],
                        "difference": cur["opening_balance"] - prev["closing_balance"],
                        "fin_source": "opening balance",
                        "other_source": "closing balance",
                        "message": (
                            f"bank statements {label}: {cur['file_name']} opens at "
                            f"{cur['opening_balance']} but {prev['file_name']} closed at "
                            f"{prev['closing_balance']}; a statement or a transaction may be "
                            "missing"
                        ),
                    }
                )
        if prev is None or cur["period_end"] >= prev["period_end"]:
            latest[key] = cur
    return out


def record_all(
    run: Run, company: str, con: duckdb.DuckDBPyConnection, engagement_id: int
) -> dict[int, list[dict[str, Any]]]:
    """Run the checks on every loaded bank statement of the engagement and record the findings
    (one no longer found becomes resolved). Returns the findings by file_id."""
    found = consistency_findings(_statements(con, engagement_id))
    for file_id, rows in found.items():
        where = f"file_id = ? AND check_name IN {CHECKS}"
        record(run, company, con, rows, where, [file_id], file_id)
    return {file_id: rows for file_id, rows in found.items() if rows}


def checklist(ctx: Any) -> list[Line]:
    """G1's bank lines (`ctx`: a `fin.diligence.gates.Context`)."""
    con, company = ctx.con, ctx.company
    rows = con.execute(
        "SELECT b.file_id, s.file_name, b.ties, (SELECT count(*) FROM findings f WHERE "
        "f.file_id = b.file_id AND f.check_name = 'bank_tie' AND f.status = 'accepted') "
        "FROM bank_statements b JOIN source_files s USING (file_id) "
        "WHERE s.status = 'loaded' AND s.engagement_id = ? ORDER BY b.file_id",
        [ctx.engagement["engagement_id"]],
    ).fetchall()
    if not rows:
        return []  # the bank checks apply once a statement is loaded
    untied = [
        f"file_id {file_id} {name}"
        for file_id, name, ties, accepted in rows
        if not ties and not accepted
    ]
    lines: list[Line] = []
    if untied:
        lines.append(
            (
                False,
                f"{len(untied)} bank parses do not tie (opening + credits - debits is not the "
                f"closing balance; a finding each: `fin findings list {company}`): "
                + ", ".join(untied),
            )
        )
    else:
        lines.append((True, f"every bank parse ties ({len(rows)} statements)"))
    open_checks = con.execute(
        f"SELECT count(*) FROM findings f JOIN source_files s ON s.file_id = f.file_id "
        f"WHERE s.status = 'loaded' AND s.engagement_id = ? AND f.status = 'open' "
        f"AND f.check_name IN {CHECKS}",
        [ctx.engagement["engagement_id"]],
    ).fetchone()
    assert open_checks is not None
    if open_checks[0]:
        lines.append(
            (
                True,
                f"{open_checks[0]} bank overlap or balance gap findings are open (for queries, "
                f"not blocking this gate): `fin findings list {company}`",
            )
        )
    return lines


def inputs(ctx: Any) -> dict[str, Any]:
    """G1's bank inputs: the statements and the status of their findings, so a new statement or
    a finding the user accepted reopens or settles it."""
    con, engagement_id = ctx.con, ctx.engagement["engagement_id"]
    return {
        "bank statements": [
            list(row)
            for row in con.execute(
                "SELECT b.file_id, s.sha256, b.ties, b.closing_balance, b.line_count "
                "FROM bank_statements b JOIN source_files s USING (file_id) "
                "WHERE s.status = 'loaded' AND s.engagement_id = ? ORDER BY b.file_id",
                [engagement_id],
            ).fetchall()
        ],
        "bank findings": [
            list(row)
            for row in con.execute(
                "SELECT f.file_id, f.check_name, f.status FROM findings f "
                "JOIN source_files s ON s.file_id = f.file_id "
                "WHERE s.kind = 'bank' AND s.engagement_id = ? ORDER BY f.finding_id",
                [engagement_id],
            ).fetchall()
        ],
    }
