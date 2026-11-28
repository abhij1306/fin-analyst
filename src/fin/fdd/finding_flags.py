"""The forensic flags an FDD finding rests on (SPEC-21, table `fdd_finding_flags`):
`fin fdd finding propose|update <co> --area forensic --flags 12,13`, and the Forensic screen's
"to a finding" request, which asks the assistant to run that command.

The flags are evidence, as a query's recon lines are: the finding's amount still comes from its
other links (a query raised on the flags, say), never from the flags added up, since one entry can
be flagged by two tests. A finding of area forensic rests on at least one flag, and only it has
flags. A flag given must be of the open engagement and of the latest run of its test and period
(`fin forensic flags` lists those); a link already stored stays when the test runs again.
"""

from typing import Any

import duckdb

from fin.db.rows import fetch_dicts

AREA = "forensic"

_FOUND = """
SELECT f.flag_id, r.engagement_id,
    r.forensic_run_id = (SELECT max(forensic_run_id) FROM forensic_runs x
        WHERE x.engagement_id = r.engagement_id AND x.test = r.test AND x.period = r.period)
        AS is_latest
FROM forensic_flags f JOIN forensic_runs r USING (forensic_run_id)
WHERE list_contains(?::INTEGER[], f.flag_id)
"""


def _ids(ids: list[int]) -> str:
    return ", ".join(f"#{i}" for i in ids)


def problems(
    con: duckdb.DuckDBPyConnection,
    engagement_id: int,
    area: str | None,
    flag_ids: list[int],
    *,
    look_up: bool = True,
) -> list[str]:
    """What is wrong with these flags for a finding of `area` (E970 lists them). `look_up`:
    check each flag against the open engagement's latest runs (only flags given now are)."""
    if area != AREA:
        if flag_ids:
            return [f"only a finding of area {AREA} rests on flags (--flags)"]
        return []
    if not flag_ids:
        return [f"a finding of area {AREA} rests on flags: give --flags (`fin forensic flags`)"]
    if not look_up:
        return []
    found = {r["flag_id"]: r for r in fetch_dicts(con, _FOUND, [flag_ids])}
    unknown = [i for i in flag_ids if i not in found or found[i]["engagement_id"] != engagement_id]
    old = [i for i in flag_ids if i not in unknown and not found[i]["is_latest"]]
    out = []
    if unknown:
        out.append(f"forensic flags {_ids(unknown)} are not flags of the open engagement")
    if old:
        out.append(
            f"forensic flags {_ids(old)} are not in the latest run of their test (take the ids "
            "from `fin forensic flags`)"
        )
    return out


def store(con: duckdb.DuckDBPyConnection, finding_id: int, flag_ids: list[int]) -> None:
    """Link the finding to exactly these flags."""
    con.execute("DELETE FROM fdd_finding_flags WHERE finding_id = ?", [finding_id])
    for flag_id in dict.fromkeys(flag_ids):
        con.execute(
            "INSERT INTO fdd_finding_flags (finding_id, flag_id) VALUES (?, ?)",
            [finding_id, flag_id],
        )


def flag_text(row: dict[str, Any]) -> str:
    """'#12, #13' for a finding's flags ('' when none)."""
    return _ids(row.get("flag_ids") or [])
