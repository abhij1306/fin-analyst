"""`fin reference compare` and the References screen (SPEC-10): each mapped reference figure beside
fin's, from the view `reference_comparison` (every number comes from SQL).

Differences come first, those at or above the threshold (the open engagement's query threshold,
else config.toml's) before the rest, largest first. A line fin has no figure for, or that is not
mapped, is counted and listed after them, never shown as zero.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any

from fin.audit.errors import DependencyMissing
from fin.config import load_config
from fin.db.conn import connect, no_company_message
from fin.db.rows import fetch_dicts
from fin.paths import company_db_path

STATES = ("differs", "no_fin_figure", "unmapped", "agrees")
ORDER = (
    "ORDER BY state = 'differs' DESC, abs(difference) >= ? DESC NULLS LAST, "
    "abs(difference) DESC NULLS LAST, value_id"
)


def threshold(root: Path, company: str) -> Decimal:
    """The open engagement's query threshold, else `[diligence] query_threshold`."""
    with connect(company_db_path(root, company)) as con:
        row = con.execute("SELECT query_threshold FROM engagement WHERE status = 'open'").fetchone()
    return row[0] if row else load_config(root).query_threshold


def comparison(root: Path, company: str) -> dict[str, Any]:
    """{threshold, rows (with `above`), counts by state, files}. E001 for a company with no
    database."""
    path = company_db_path(root, company)
    if not path.is_file():
        raise DependencyMissing(no_company_message(company))
    limit = threshold(root, company)
    with connect(path) as con:
        rows = fetch_dicts(
            con,
            f"SELECT *, abs(difference) >= ? AS above FROM reference_comparison {ORDER}",
            [limit, limit],
        )
        counts = dict(
            con.execute(
                "SELECT state, count(*) FROM reference_comparison GROUP BY state"
            ).fetchall()
        )
        files = fetch_dicts(
            con,
            "SELECT f.ref_file_id, f.source, f.file_name, f.added_at, count(v.value_id) AS figures "
            "FROM reference_files f LEFT JOIN reference_values v USING (ref_file_id) "
            "GROUP BY ALL ORDER BY f.ref_file_id",
        )
    return {
        "threshold": limit,
        "rows": rows,
        "counts": {state: counts.get(state, 0) for state in STATES},
        "files": files,
    }


def _amount(value: Decimal | None) -> str:
    return "-" if value is None else f"{value:,}"


def format_row(r: dict[str, Any]) -> str:
    """One figure: id, state, period, scope, line, both figures and the difference in the
    reference's unit, and both sources."""
    flag = ", at or above the threshold" if r["above"] else ""
    target = f"{r['target_kind']} {r['target']}" if r["target"] else "not mapped"
    text = f"#{r['value_id']} [{r['state']}{flag}] {r['period']} {r['scope']} {r['label']!r} "
    text += f"({target}): fin {_amount(r['fin_shown'])}, reference {_amount(r['reference_shown'])}"
    if r["difference_shown"] is not None:
        text += f", difference {_amount(r['difference_shown'])}"
    text += f" {r['raw_unit']}\n    fin: {r['fin_source'] or 'no figure'}; "
    return text + f"{r['source']}: {r['file_name']} {r['reference_ref']}"


def format_comparison(company: str, data: dict[str, Any], all_rows: bool) -> str:
    counts = data["counts"]
    if not data["files"]:
        return f"{company} has no reference file; add one with fin reference add"
    shown = data["rows"] if all_rows else [r for r in data["rows"] if r["state"] == "differs"]
    out = [
        f"{company}: {counts['differs']} differences (threshold {data['threshold']:,} rupees), "
        f"{counts['agrees']} agree, {counts['no_fin_figure']} with no fin figure, "
        f"{counts['unmapped']} not mapped (fin reference lines {company})"
    ]
    out += [format_row(r) for r in shown]
    return "\n".join(out)
