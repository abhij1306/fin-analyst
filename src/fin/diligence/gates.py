"""Gates (`fin gate status|approve`): checkpoints of an engagement that only the user approves.

A gate has a checklist fin evaluates and inputs: the rows its checklist depends on. Approving it
(`fin gate approve`, only when its checklist passes and every earlier gate is approved) appends a
row to `gate_approvals` (db/schema/diligence.sql) with the sha256 of its inputs, and a `gate` row to
the audit table `diligence_events`. A gate is then

    approved  the hash of its inputs now equals the one recorded at its latest approval, its
              checklist passes, and every earlier gate is approved;
    reopened  approved before, but its inputs changed since, its checklist fails now (a change its
              inputs do not cover), or an earlier gate is not approved;
    open      never approved.

So a gate reopens on its own when the data under it changes. Commands a gate unlocks refuse with
E801 until it is approved (`require`), checked in the same transaction as their change.

Gates in this build (docs/DILIGENCE_BUILD.md); G3 (recast) and G5 (report) come later:
    G1 Data         every annual period in scope has figures in current_facts (a loaded document,
                    mapped and confirmed: what the metrics use), no held documents, no mapping
                    items waiting for review. Its inputs are the
                    periods, the documents, the mapping items and the figures (every
                    current_facts value by account, period and scope), so a changed figure
                    (`fin sql --write` on facts, a review that maps a line) reopens it;
    G2 Queries      no query in draft: every query's wording is reviewed. Its inputs are each
                    query's id, wording and whether it is a draft, so a new or edited query
                    reopens it; sending, answering, closing or withdrawing an approved one does
                    not. Unlocks `fin adjust accept|reject`;
    G4 Adjustments  no proposed adjustment, and every accepted one has evidence. Unlocks
                    `fin engagement close`.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from fin.audit.errors import GateNotApproved, GateNotReady
from fin.audit.logger import Run
from fin.db.conn import transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database, require_open
from fin.map.review import open_items, read_pending

Line = tuple[bool, str]  # (passes, what it says)


@dataclass(frozen=True)
class Context:
    """What a gate reads: the company database, the open engagement and the company's mapping
    items waiting for review (review/<company>/pending.json)."""

    con: duckdb.DuckDBPyConnection
    company: str
    engagement: dict[str, Any]
    pending: list[dict[str, Any]]


@dataclass(frozen=True)
class Gate:
    name: str
    title: str
    unlocks: str
    checklist: Callable[[Context], list[Line]]
    inputs: Callable[[Context], dict[str, Any]]  # input name -> its rows, in a fixed order


def context(con: duckdb.DuckDBPyConnection, root: Path, company: str) -> Context:
    """The context of the company's open engagement. E803 when there is none."""
    engagement = require_open(con, company)
    return Context(con, company, engagement, open_items(read_pending(root, company)))


def digest(value: Any) -> str:
    """sha256 of a value as canonical JSON (keys sorted; dates and decimals as text)."""
    text = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rows(ctx: Context, sql: str, params: list[Any] | None = None) -> list[list[Any]]:
    return [list(row) for row in ctx.con.execute(sql, params or []).fetchall()]


def _ids(rows: list[list[Any]]) -> str:
    return ", ".join(f"#{row[0]}" for row in rows)


# G1 Data


def _periods(ctx: Context) -> list[str]:
    return ctx.engagement["periods"].split(",")


def _data_checklist(ctx: Context) -> list[Line]:
    covered = {
        period
        for (period,) in ctx.con.execute("SELECT DISTINCT period FROM current_facts").fetchall()
    }
    lines = [
        (True, f"{period}: a loaded document has mapped figures for it")
        if period in covered
        else (False, f"{period}: no loaded document has mapped figures for it")
        for period in _periods(ctx)
    ]
    held = _rows(
        ctx, "SELECT doc_id, file_name FROM documents WHERE status = 'held' ORDER BY doc_id"
    )
    if held:
        files = ", ".join(f"doc_id {doc_id} {name}" for doc_id, name in held)
        lines.append((False, f"held documents: {files} (`fin findings list {ctx.company}`)"))
    else:
        lines.append((True, "no held documents"))
    if ctx.pending:
        lines.append(
            (
                False,
                f"{len(ctx.pending)} mapping items waiting for review "
                f"(`fin review list {ctx.company}`)",
            )
        )
    else:
        lines.append((True, "no mapping items waiting for review"))
    return lines


def _data_inputs(ctx: Context) -> dict[str, Any]:
    return {
        "periods": _periods(ctx),
        "documents": _rows(ctx, "SELECT doc_id, sha256, status FROM documents ORDER BY doc_id"),
        "figures": _rows(
            ctx,
            "SELECT account, period, scope, value FROM current_facts "
            "ORDER BY account, period, scope",
        ),
        "mapping items": sorted(
            [item["id"], item["status"], item["statement"], item["section"], item["label"]]
            for item in ctx.pending
        ),
    }


# G2 Queries


def _queries_checklist(ctx: Context) -> list[Line]:
    drafts = _rows(
        ctx,
        "SELECT query_id FROM accountant_queries WHERE engagement_id = ? AND status = 'draft' "
        "ORDER BY query_id",
        [ctx.engagement["engagement_id"]],
    )
    if not drafts:
        return [(True, "no queries in draft")]
    return [
        (
            False,
            f"queries in draft: {_ids(drafts)} (approve or withdraw them: "
            f"`fin query list {ctx.company} --status draft`)",
        )
    ]


def _queries_inputs(ctx: Context) -> dict[str, Any]:
    return {
        "queries": _rows(
            ctx,
            "SELECT query_id, question, status = 'draft' FROM accountant_queries "
            "WHERE engagement_id = ? ORDER BY query_id",
            [ctx.engagement["engagement_id"]],
        )
    }


# G4 Adjustments


def _adjustments_checklist(ctx: Context) -> list[Line]:
    engagement_id = ctx.engagement["engagement_id"]
    proposed = _rows(
        ctx,
        "SELECT adj_id FROM adjustments WHERE engagement_id = ? AND status = 'proposed' "
        "ORDER BY adj_id",
        [engagement_id],
    )
    bare = _rows(
        ctx,
        "SELECT adj_id FROM adjustments WHERE engagement_id = ? AND status = 'accepted' "
        "AND query_id IS NULL AND finding_id IS NULL AND coalesce(source_ref, '') = '' "
        "ORDER BY adj_id",
        [engagement_id],
    )
    return [
        (True, "no proposed adjustments")
        if not proposed
        else (
            False,
            f"proposed adjustments: {_ids(proposed)} (accept or reject them: "
            f"`fin adjust list {ctx.company} --status proposed`)",
        ),
        (True, "every accepted adjustment has evidence")
        if not bare
        else (False, f"accepted adjustments without evidence: {_ids(bare)}"),
    ]


def _adjustments_inputs(ctx: Context) -> dict[str, Any]:
    return {
        "adjustments": _rows(
            ctx,
            "SELECT adj_id, status, amount, period, scope, query_id, finding_id, source_ref "
            "FROM adjustments WHERE engagement_id = ? ORDER BY adj_id",
            [ctx.engagement["engagement_id"]],
        )
    }


GATES = (
    Gate("G1", "Data", "approving G2", _data_checklist, _data_inputs),
    Gate("G2", "Queries", "`fin adjust accept|reject`", _queries_checklist, _queries_inputs),
    Gate(
        "G4",
        "Adjustments",
        "`fin engagement close`",
        _adjustments_checklist,
        _adjustments_inputs,
    ),
)
NAMES = tuple(gate.name for gate in GATES)


def _latest_approvals(ctx: Context) -> dict[str, dict[str, Any]]:
    rows = fetch_dicts(
        ctx.con,
        "SELECT * FROM gate_approvals WHERE engagement_id = ? "
        "QUALIFY row_number() OVER (PARTITION BY gate ORDER BY approval_id DESC) = 1",
        [ctx.engagement["engagement_id"]],
    )
    return {row["gate"]: row for row in rows}


def evaluate(ctx: Context) -> list[dict[str, Any]]:
    """Every gate in order: name, title, state (approved, reopened or open), reason (why it is
    reopened, else None), its checklist now, what it unlocks, and the hashes of its inputs now."""
    approvals = _latest_approvals(ctx)
    blocker: str | None = None  # the first gate in order that is not approved
    result = []
    for gate in GATES:
        hashes = {name: digest(rows) for name, rows in gate.inputs(ctx).items()}
        input_hash = digest(hashes)
        checklist = gate.checklist(ctx)
        approval = approvals.get(gate.name)
        reason = None
        if approval is None:
            state = "open"
        elif approval["input_hash"] != input_hash:
            before = json.loads(approval["input_hashes"])
            changed = [name for name in hashes if before.get(name) != hashes[name]]
            state, reason = "reopened", f"changed since it was approved: {', '.join(changed)}"
        elif not all(ok for ok, _ in checklist):
            state, reason = "reopened", "its checklist fails now"
        elif blocker is not None:
            state, reason = "reopened", f"{blocker} is not approved"
        else:
            state = "approved"
        if state != "approved" and blocker is None:
            blocker = gate.name
        result.append(
            {
                "gate": gate.name,
                "title": gate.title,
                "state": state,
                "reason": reason,
                "checklist": [{"ok": ok, "text": text} for ok, text in checklist],
                "unlocks": gate.unlocks,
                "input_hash": input_hash,
                "input_hashes": hashes,
            }
        )
    return result


def _label(gate: dict[str, Any]) -> str:
    return f"gate {gate['gate']} ({gate['title']})"


def _state(gate: dict[str, Any]) -> str:
    return gate["state"] + (f" ({gate['reason']})" if gate["reason"] else "")


def require(ctx: Context, name: str, command: str) -> None:
    """E801 unless gate `name` and every gate before it are approved. Call it inside the
    transaction of the change the gate unlocks."""
    gates = evaluate(ctx)
    upto = gates[: NAMES.index(name) + 1]
    needed = upto[-1]
    for gate in upto:
        if gate["state"] == "approved":
            continue
        cause = "it is" if gate is needed else f"{_label(gate)} before it is"
        raise GateNotApproved(
            f"`{command}` needs {_label(needed)} approved; {cause} {_state(gate)}. Nothing "
            f"changed. See `fin gate status {ctx.company}`"
        )


def approve_in(run: Run, ctx: Context, name: str) -> dict[str, Any]:
    """Record the user's approval of gate `name` (in the caller's transaction). E802 when an
    earlier gate is not approved or its checklist fails."""
    gates = evaluate(ctx)
    index = NAMES.index(name)
    gate = gates[index]
    for earlier in gates[:index]:
        if earlier["state"] != "approved":
            raise GateNotReady(
                f"{_label(gate)} of {ctx.company} cannot be approved before {_label(earlier)}, "
                f"which is {_state(earlier)}; nothing approved. See `fin gate status "
                f"{ctx.company}`"
            )
    failing = [line["text"] for line in gate["checklist"] if not line["ok"]]
    if failing:
        raise GateNotReady(
            f"{_label(gate)} of {ctx.company} cannot be approved; its checklist fails: "
            f"{'; '.join(failing)}. Nothing approved"
        )
    new = ctx.con.execute(
        "INSERT INTO gate_approvals (engagement_id, gate, input_hash, input_hashes, checklist, "
        "approved_run_id, approved_at) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING approval_id",
        [
            ctx.engagement["engagement_id"],
            name,
            gate["input_hash"],
            json.dumps(gate["input_hashes"], sort_keys=True),
            json.dumps(gate["checklist"]),
            run.run_id,
            utcnow(),
        ],
    ).fetchone()
    assert new is not None
    [row] = fetch_dicts(ctx.con, "SELECT * FROM gate_approvals WHERE approval_id = ?", [new[0]])
    run.record(
        "diligence_events",
        {
            "company": ctx.company,
            "engagement_id": row["engagement_id"],
            "kind": "gate",
            "item_id": name,
            "status": "approved",
            "detail": json.dumps(row, default=str),
        },
    )
    return evaluate(ctx)[index]


def approve(run: Run, company: str, name: str) -> dict[str, Any]:
    """Approve gate `name` of the company's open engagement, in one transaction. E803 when there
    is no open engagement; E802 (nothing approved) when it is not ready."""
    with company_database(run, company, write=True) as con, transaction(con):
        return approve_in(run, context(con, run.root, company), name)


def gate_status(run: Run, company: str) -> dict[str, Any]:
    """The open engagement's gates, in order (`evaluate`). E803 when there is none."""
    with company_database(run, company) as con:
        ctx = context(con, run.root, company)
        return {
            "company": company,
            "engagement_id": ctx.engagement["engagement_id"],
            "gates": evaluate(ctx),
        }


def format_gate(gate: dict[str, Any]) -> str:
    lines = [f"{gate['gate']} {gate['title']}: {_state(gate)}"]
    lines += [
        f"    {'pass' if line['ok'] else 'FAIL'}: {line['text']}" for line in gate["checklist"]
    ]
    lines.append(f"    unlocks: {gate['unlocks']}")
    return "\n".join(lines)


def format_status(result: dict[str, Any]) -> str:
    head = f"{result['company']}: engagement #{result['engagement_id']}, gates in order"
    return "\n".join([head, *map(format_gate, result["gates"])])
