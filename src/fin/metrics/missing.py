"""Why a metric has no value: the inputs it lacks, and what the user can do about each.

A metric with a missing input is NULL (never zero; company_views.sql). `not_computed` turns that
into a sentence: "EBITDA not computed for FY24 standalone: no finance_cost for FY24. Load a document
that has it, or state it (...)". An input is missing when `metric_inputs` has neither a document
line (fact_id) nor a figure the user stated (stated_id) for it. The `fin engagement bridge` text
and `fin audit <co> <metric> <period>` print it.
"""

from typing import Any

import duckdb

from fin.db.rows import fetch_dicts

# The metric's label and, when it is NULL, each input with no line and no stated figure.
_MISSING_SQL = """
SELECT d.label, i.account, i.input_period
FROM metrics m
JOIN metric_definitions d USING (metric)
LEFT JOIN metric_inputs i ON i.metric = m.metric AND i.period = m.period AND i.scope = m.scope
    AND i.fact_id IS NULL AND i.stated_id IS NULL
WHERE m.value IS NULL AND m.metric = ? AND m.period = ? AND m.scope = ?
ORDER BY i.input_period DESC, i.account
"""


def missing_inputs(
    con: duckdb.DuckDBPyConnection, metric: str, period: str, scope: str
) -> tuple[str, list[tuple[str, str]]] | None:
    """(label, [(account, input period)]) when the metric exists for the period and scope with
    no value, else None. An empty list: no input is missing (a denominator is zero)."""
    rows = fetch_dicts(con, _MISSING_SQL, [metric, period, scope])
    if not rows:
        return None
    pairs = [(r["account"], r["input_period"]) for r in rows if r["account"] is not None]
    return rows[0]["label"], list(dict.fromkeys(pairs))


def not_computed(
    con: duckdb.DuckDBPyConnection, company: str, metric: str, period: str, scope: str
) -> str | None:
    """The sentence saying why the metric is not computed, or None when it has a value (or no
    row for the period and scope)."""
    found = missing_inputs(con, metric, period, scope)
    if found is None:
        return None
    label, pairs = found
    head = f"{label} not computed for {period} {scope}"
    if not pairs:
        return f"{head}: a denominator is zero"
    lacks = ", ".join(f"no {account} for {when}" for account, when in pairs)
    account, when = pairs[0]
    return (
        f"{head}: {lacks}. Load a document that has it, or state it (`fin fact state {company} "
        f"--account {account} --period {when} --scope {scope} --value 0 --reason ...` for a nil)"
    )


def not_computed_rows(
    con: duckdb.DuckDBPyConnection, company: str, metric: str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """For each row (period, scope), the sentence when the metric is not computed there."""
    out = []
    for row in rows:
        message = not_computed(con, company, metric, row["period"], row["scope"])
        if message is not None:
            out.append({"metric": metric, "period": row["period"], "scope": row["scope"],
                        "message": message})  # fmt: skip
    return out
