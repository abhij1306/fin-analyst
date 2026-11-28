"""`fin reference compare` and the References screen (SPEC-10): each mapped reference figure beside
fin's, from the view `reference_comparison` (every number comes from SQL). Money is shown in the
company's display unit (config.toml [display]), converted by the `display_value` macro
(`fin.display`); a missing figure is "n/a", never zero.

Differences come first, those at or above the threshold (the open engagement's query threshold,
else config.toml's) before the rest, largest first. A line fin has no figure for, or that is not
mapped, is counted and listed after them, never shown as zero.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any

from fin.audit.errors import DependencyMissing
from fin.config import Display, load_config
from fin.db.conn import connect, no_company_message
from fin.db.rows import fetch_dicts
from fin.display import add_macro, display_settings, money, number, sql_params
from fin.paths import company_db_path

STATES = ("differs", "no_fin_figure", "unmapped", "agrees")
SHOWN = ("reference", "fin", "difference")
MISSING = "n/a"
_ROWS_SQL = """
SELECT *, abs(difference) >= $limit AS above,
    display_value(reference_value, $per_unit, $decimals) AS reference_shown,
    display_value(fin_value, $per_unit, $decimals) AS fin_shown,
    display_value(difference, $per_unit, $decimals) AS difference_shown
FROM reference_comparison
ORDER BY state = 'differs' DESC, abs(difference) >= $limit DESC NULLS LAST,
    abs(difference) DESC NULLS LAST, value_id
"""


def threshold(root: Path, company: str) -> tuple[Decimal, bool]:
    """(the open engagement's query threshold, else `[diligence] query_threshold`; whether an
    engagement is open, which a query or an adjustment needs)."""
    with connect(company_db_path(root, company)) as con:
        row = con.execute("SELECT query_threshold FROM engagement WHERE status = 'open'").fetchone()
    return (row[0], True) if row else (load_config(root).query_threshold, False)


def comparison(root: Path, company: str, display: Display | None = None) -> dict[str, Any]:
    """{threshold (rupees) and threshold_text, unit_label, engagement_open, rows (rupees;
    `*_shown` in the display unit, `*_text` as shown; `above`), counts by state, files}. E001
    for a company with no database."""
    path = company_db_path(root, company)
    if not path.is_file():
        raise DependencyMissing(no_company_message(company))
    display = display or display_settings(root, company)
    limit, engagement_open = threshold(root, company)
    with connect(path) as con:
        add_macro(con)
        rows = fetch_dicts(con, _ROWS_SQL, {"limit": limit, **sql_params(display)})
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
    for row in rows:
        for column in SHOWN:
            row[f"{column}_text"] = number(row[f"{column}_shown"], display, blank=MISSING)
    return {
        "threshold": limit,
        "threshold_text": money(limit, display),
        "unit_label": display.unit_label,
        "engagement_open": engagement_open,
        "rows": rows,
        "counts": {state: counts.get(state, 0) for state in STATES},
        "files": files,
    }


def format_row(r: dict[str, Any], unit_label: str) -> str:
    """One figure: id, state, period, scope, line, both figures and the difference in the
    display unit, and both sources."""
    flag = ", at or above the threshold" if r["above"] else ""
    target = f"{r['target_kind']} {r['target']}" if r["target"] else "not mapped"
    text = f"#{r['value_id']} [{r['state']}{flag}] {r['period']} {r['scope']} {r['label']!r} "
    text += f"({target}): fin {r['fin_text']}, reference {r['reference_text']}"
    if r["difference"] is not None:
        text += f", difference {r['difference_text']}"
    text += f" {unit_label}\n    fin: {r['fin_source'] or 'no figure'}; "
    return text + f"{r['source']}: {r['file_name']} {r['reference_ref']}"


def differences(data: dict[str, Any]) -> list[dict[str, Any]]:
    """The rows of `comparison` whose figures differ."""
    return [r for r in data["rows"] if r["state"] == "differs"]


def format_comparison(company: str, data: dict[str, Any], all_rows: bool) -> str:
    counts = data["counts"]
    if not data["files"]:
        return f"{company} has no reference file; add one with fin reference add"
    shown = data["rows"] if all_rows else differences(data)
    out = [
        f"{company}: {counts['differs']} differences (threshold {data['threshold_text']}), "
        f"{counts['agrees']} agree, {counts['no_fin_figure']} with no fin figure, "
        f"{counts['unmapped']} not mapped (fin reference lines {company})"
    ]
    out += [format_row(r, data["unit_label"]) for r in shown]
    return "\n".join(out)
