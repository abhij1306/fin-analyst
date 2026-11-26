"""FDD proposals under gate G4 (`fin.diligence.gates`, docs/diligence.md "Gates").

G4 Adjustments also needs every FDD proposal the user decides to be decided: no proposed net debt
item, no draft FDD finding, and no proposed net debt class, tax class or related party link. Its
inputs include those tables, so a new proposal, a decision or a changed final finding reopens an
approved G4 (and so `fin engagement close` refuses until the user approves it again).
Other FDD data (provision ledgers, contracts, disclosures, samples and walkthroughs, orderbook and
GST returns) is not under a gate of its own; what of it feeds a reconciliation is under G2 through
that reconciliation's inputs.
"""

from typing import Any

# (input name, what the line names, SQL of the open proposals' ids, SQL of the input rows, where to
# look). The engagement id is the only parameter, for the tables that have one.
_CHECKS = (
    (
        "net debt items",
        "proposed net debt items",
        "SELECT item_id FROM netdebt_items WHERE engagement_id = ? AND status = 'proposed' "
        "ORDER BY item_id",
        "SELECT item_id, period, amount, description, evidence, status FROM netdebt_items "
        "WHERE engagement_id = ? ORDER BY item_id",
        "`fin netdebt item list {co}`",
    ),
    (
        "fdd findings",
        "draft FDD findings",
        "SELECT finding_id FROM fdd_findings WHERE engagement_id = ? AND status = 'draft' "
        "ORDER BY finding_id",
        "SELECT finding_id, status, area, title, observation, implication, impact_type, severity, "
        "deal_consideration, evidence, adj_id, netdebt_item_id, recon_id, query_id "
        "FROM fdd_findings WHERE engagement_id = ? ORDER BY finding_id",
        "`fin fdd finding list {co} --status draft`",
    ),
    (
        "net debt classes",
        "proposed net debt classes",
        "SELECT class_id FROM netdebt_classes WHERE status = 'proposed' AND ? IS NOT NULL "
        "ORDER BY class_id",
        "SELECT class_id, ledger, debt_class, status FROM netdebt_classes WHERE ? IS NOT NULL "
        "ORDER BY class_id",
        "`fin review list {co} --netdebt`",
    ),
    (
        "tax classes",
        "proposed tax classes",
        "SELECT tax_ledger_id FROM tax_ledgers WHERE status = 'proposed' AND ? IS NOT NULL "
        "ORDER BY tax_ledger_id",
        "SELECT tax_ledger_id, ledger, tax_class, status FROM tax_ledgers WHERE ? IS NOT NULL "
        "ORDER BY tax_ledger_id",
        "`fin review list {co} --tax`",
    ),
    (
        "related party links",
        "proposed related party links",
        "SELECT link_id FROM related_party_links WHERE engagement_id = ? AND status = 'proposed' "
        "ORDER BY link_id",
        "SELECT link_id, party, link_kind, target, status FROM related_party_links "
        "WHERE engagement_id = ? ORDER BY link_id",
        "`fin review list {co} --related`",
    ),
)


def _rows(ctx: Any, sql: str) -> list[list[Any]]:
    return [list(r) for r in ctx.con.execute(sql, [ctx.engagement["engagement_id"]]).fetchall()]


def checklist(ctx: Any) -> list[tuple[bool, str]]:
    """One line per kind of FDD proposal: passes when none waits for the user."""
    lines = []
    for _, what, open_sql, _, where in _CHECKS:
        found = _rows(ctx, open_sql)
        if not found:
            lines.append((True, f"no {what}"))
            continue
        ids = ", ".join(f"#{r[0]}" for r in found)
        look = where.format(co=ctx.company)
        lines.append((False, f"{what}: {ids} (the user decides them: {look})"))
    return lines


def inputs(ctx: Any) -> dict[str, Any]:
    return {name: _rows(ctx, rows_sql) for name, _, _, rows_sql, _ in _CHECKS}
