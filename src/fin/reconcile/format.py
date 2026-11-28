"""The text of a D4 reconciliation result (`fin reconcile`): counts, then the lines to look at.

Detail lines (invoice, ledger, payroll) that are not matched are listed with their item number
(the id `fin query raise --items` takes) and what happens to them: needs a query, in query #n,
listed (below the threshold, fx, not reconciled, unmapped). Month and party lines are roll-ups:
only those that differ are listed. Coverage lines (the cut-off tests, F3) say what was not tested.
"""

from typing import Any

from fin.diligence.engagement import rupees
from fin.reconcile import bank, gst
from fin.reconcile.base import DETAIL_LEVELS

LEFT = {
    "sales_register_vs_gl": ("register", "books"),
    "purchase_register_vs_gl": ("register", "books"),
    "gl_vs_tb": ("Ledger Vouchers", "trial balance"),
    "payroll_vs_gl": ("payroll", "books"),
    "walkthrough": ("register", "evidence"),
    "sales_cutoff": ("register", "not compared"),
    "purchase_cutoff": ("register", "not compared"),
    "orderbook_vs_register": ("orderbook", "register"),
    **gst.LEFT,
    "rpt_vs_books": ("disclosed", "books"),
    "purchase_register_vs_gstr2b": ("GSTR-2B", "register"),
}


def _mark(line: dict[str, Any], threshold: Any) -> str:
    if line["query_id"]:
        return f"query #{line['query_id']}"
    if line["needs_query"]:
        return "needs a query: `fin query raise --items`"
    if line["kind"] in ("fx", "not_reconciled", "unmapped"):
        return "listed, not queried"
    return "below the threshold, listed" if abs(line["difference"]) < threshold else "listed"


def _text(line: dict[str, Any], names: tuple[str, str], threshold: Any) -> str:
    item = f"[item #{line['item_id']}] " if line["level"] in DETAIL_LEVELS else ""
    what = f"{line['level']} {line['key']}"
    if line["kind"] in ("unmapped", "not_reconciled"):
        return f"  {item}{line['kind']} {what} ({_mark(line, threshold)}): {line['note']}"
    out = (
        f"  {item}{line['kind']} {what}: {names[0]} {rupees(line['left_amount'])}, {names[1]} "
        f"{rupees(line['right_amount'])}, difference {rupees(line['difference'])} "
        f"({_mark(line, threshold)})"
    )
    for source in (line["left_source"], line["right_source"]):
        if source and line["level"] in DETAIL_LEVELS:
            out += f"\n      {source}"
    if line["note"]:
        out += f"\n      {line['note']}"
    return out


def format_result(result: dict[str, Any]) -> str:
    if result["name"] in bank.NAMES:
        return bank.format_result(result)
    lines, threshold = result["lines"], result["threshold"]
    names = LEFT[result["name"]]
    detail = [line for line in lines if line["level"] in DETAIL_LEVELS]
    count = lambda kind: sum(1 for line in detail if line["kind"] == kind)  # noqa: E731
    needing = [line for line in detail if line["needs_query"]]
    not_reconciled = sum(1 for line in lines if line["kind"] == "not_reconciled")
    head = (
        f"{result['name']} {result['period']} (recon #{result['recon_id']}, threshold "
        f"{rupees(threshold)}): {len(detail)} {detail[0]['level'] if detail else 'line'} lines, "
        f"{count('matched')} matched, {len(needing)} at or above the threshold "
        f"({sum(1 for line in needing if line['query_id'])} already in a query), "
        f"{count('fx')} fx, {not_reconciled} not reconciled, "
        f"{count('unmapped')} unmapped"
    )
    out = [head]
    out += [_text(line, names, threshold) for line in detail if line["kind"] != "matched"]
    for level in ("month", "party", "coverage"):
        rolled = [line for line in lines if line["level"] == level and line["kind"] != "matched"]
        out += [_text(line, names, threshold) for line in rolled]
    if result["unmapped"]:
        out.append(
            f"{result['unmapped']} ledgers of the Ledger Vouchers are not confirmed to an account "
            "(`fin map ledgers`): the books side may be incomplete"
        )
    return "\n".join(out)
