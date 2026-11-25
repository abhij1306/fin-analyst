"""What a query export holds: the approved queries (issues), their lines, and the real names.

An issue is an approved `accountant_queries` row. A query raised from reconciliation lines has its
lines in `query_items`: the count and value of each kind of difference come from them. Claude drafts
with tokens (`CUST-0001`); the export is the one place that reads `mask_values` and writes the real
value in their place (`Unmasker`). A token with no real value is E861: it is never written as is.
"""

import re
from dataclasses import dataclass, field
from typing import Any

import duckdb

from fin.audit.errors import NothingToExport, UnknownToken
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import rupees
from fin.diligence.mask import PREFIX
from fin.reconcile.format import LEFT
from fin.reconcile.items import kind_text, kind_totals, line_rupees

TOKEN = re.compile(r"(?<![\w-])(?:" + "|".join(PREFIX.values()) + r")-\d{4,}(?![\w-])")


class Unmasker:
    """Real values for tokens, read from `mask_values` once. Unknown tokens are collected."""

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self.names = dict(con.execute("SELECT token, real_value FROM mask_values").fetchall())
        self.unknown: set[str] = set()

    def __call__(self, text: Any) -> Any:
        if not isinstance(text, str):
            return text

        def real(match: re.Match[str]) -> str:
            token = match.group(0)
            if token not in self.names:
                self.unknown.add(token)
            return self.names.get(token, token)

        return TOKEN.sub(real, text)

    def check(self) -> None:
        if self.unknown:
            raise UnknownToken(
                f"{', '.join(sorted(self.unknown))} has no real value in the masked names of "
                "this company; nothing written. Correct the token in the query (fin query edit "
                "while it is a draft) or the line's source"
            )


@dataclass
class Issue:
    query: dict[str, Any]
    lines: list[dict[str, Any]] = field(default_factory=list)
    kinds: list[dict[str, Any]] = field(default_factory=list)  # label, count, value (rupees)

    @property
    def query_id(self) -> int:
        return int(self.query["query_id"])

    @property
    def title(self) -> str:
        parts = (self.query["period"], self.query["scope"], self.query.get("topic"))
        return " ".join(x for x in parts if x) or "-"

    def difference_text(self) -> str:
        """'3 only in register ₹4,00,000.00; 1 different amounts ₹50,000.00'."""
        return "; ".join(kind_text(k["count"], k["label"], k["value"]) for k in self.kinds)


def line_difference(line: dict[str, Any]) -> str:
    """The line's difference in rupees; in its own currency when it has no rupee value."""
    value = line_rupees(line)
    return rupees(value) if value is not None else f"{line['currency']} {line['difference']:,}"


def label(name: str, kind: str) -> str:
    """What a kind of difference means in a reconciliation: 'only in register', 'only in books',
    'amounts differ'."""
    first, second = LEFT.get(name, ("first source", "second source"))
    return {
        "left_only": f"only in the {first}",
        "right_only": f"only in the {second}",
        "difference": "amounts differ",
    }.get(kind, kind.replace("_", " "))


_LINES = (
    "SELECT i.item_id, r.name, i.kind, i.level, i.key, i.left_amount, i.right_amount, "
    "i.difference, i.currency, i.amount_inr, i.left_source, i.right_source, i.note "
    "FROM query_items q "
    "JOIN recon_items i USING (item_id) JOIN recon_runs r USING (recon_id) "
    "WHERE q.query_id = ? ORDER BY i.item_id"
)


def approved_issues(
    con: duckdb.DuckDBPyConnection, company: str, engagement: dict[str, Any]
) -> list[Issue]:
    """The open engagement's approved queries, with their lines and the count and value (the
    lines' differences without their signs) of each kind. E858 when there are none."""
    queries = fetch_dicts(
        con,
        "SELECT q.*, i.topic FROM accountant_queries q LEFT JOIN inquiries i USING (query_id) "
        "WHERE q.engagement_id = ? AND q.status = 'approved' ORDER BY q.query_id",
        [engagement["engagement_id"]],
    )
    if not queries:
        raise NothingToExport(
            f"{company} has no approved query to export; nothing written. Approve drafts with "
            f"`fin query approve {company} <ids>` (`fin query list {company}`)"
        )
    issues = []
    for query in queries:
        issue = Issue(query, fetch_dicts(con, _LINES, [query["query_id"]]))
        totals = kind_totals(issue.lines, lambda line: label(line["name"], line["kind"]))
        issue.kinds = [{"label": k, "count": v[0], "value": v[1]} for k, v in totals.items()]
        issues.append(issue)
    return issues
