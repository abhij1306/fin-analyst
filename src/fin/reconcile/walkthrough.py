"""The walkthrough reconciliation (docs/fdd.md F1): exceptions Claude recorded for sampled lines.

`fin walkthrough record` stores Claude's checks of each sample line; this reads them as a
reconciliation named `walkthrough` in `recon_runs`/`recon_items` so an exception at or above the
engagement threshold can be grouped into a query (`fin query raise --items`) and gate G2 sees it.
One line per sample line with at least one exception (level invoice, kind left_only): left is
the line's Amount in Rs, there is no right side, the note says which checks failed and why. A
period with walkthroughs but no exception has no lines. Nothing here changes company data.
"""

from typing import Any

import duckdb

from fin.audit.errors import ReconCannotRun
from fin.reconcile.base import Computed, make_line

NAME = "walkthrough"

_EXCEPTIONS = """
SELECT s.sample_id, l.line_no, l.invoice_no, l.party, l.amount_inr, f.file_name, l.sheet, l."row",
       w.check_name, w.evidence, w.evidence_file, w.evidence_page
FROM samples s
JOIN sample_lines l USING (sample_id)
JOIN source_files f ON f.file_id = l.file_id
JOIN walkthroughs w ON w.sample_id = l.sample_id AND w.line_no = l.line_no
WHERE s.engagement_id = ? AND s.period = ? AND w.result = 'exception'
ORDER BY s.sample_id, l.line_no, w.check_name
"""


def applies(con: duckdb.DuckDBPyConnection, engagement: dict[str, Any], period: str) -> bool:
    """The period has a sample with a walkthrough recorded."""
    row = con.execute(
        "SELECT count(*) FROM walkthroughs w JOIN samples s USING (sample_id) "
        "WHERE s.engagement_id = ? AND s.period = ?",
        [engagement["engagement_id"], period],
    ).fetchone()
    return bool(row and row[0])


def compute(
    con: duckdb.DuckDBPyConnection, company: str, engagement: dict[str, Any], period: str
) -> Computed:
    if not applies(con, engagement, period):
        raise ReconCannotRun(
            f"{company} {period}: no walkthrough recorded (`fin sample draw`, then "
            "`fin walkthrough record`); nothing recorded"
        )
    file_row = con.execute(
        "SELECT min(l.file_id) FROM sample_lines l JOIN samples s USING (sample_id) "
        "WHERE s.engagement_id = ? AND s.period = ?",
        [engagement["engagement_id"], period],
    ).fetchone()
    assert file_row is not None
    grouped: dict[tuple[int, int], dict[str, Any]] = {}
    for row in con.execute(_EXCEPTIONS, [engagement["engagement_id"], period]).fetchall():
        sample, number, invoice, party, amount, file_name, sheet, source_row, check = row[:9]
        line = grouped.setdefault(
            (sample, number),
            {
                "invoice": invoice,
                "party": party,
                "amount": amount,
                "source": f"{file_name} {sheet}!{source_row}",
                "checks": [],
                "notes": [],
                "evidence": [],
            },
        )
        line["checks"].append(check)
        line["notes"].append(f"{check}: {row[9]}")
        if row[10]:
            line["evidence"].append(f"{row[10]} p.{row[11]}" if row[11] else row[10])
    lines = [
        make_line(
            "invoice",
            f"sample {sample} line {number}: {', '.join(line['checks'])}",
            line["amount"],
            None,
            line["source"],
            "; ".join(dict.fromkeys(line["evidence"])) or None,
            note=f"invoice {line['invoice']} ({line['party']}): " + "; ".join(line["notes"]),
        )
        for (sample, number), line in grouped.items()
    ]
    return Computed(lines=lines, left_file_id=file_row[0], right_file_id=None)
