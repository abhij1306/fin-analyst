"""`fin sample draw <company> --kind sales|purchase --period P [--n 25] [--seed S]`.

The sample is every register line of the kind and period at or above the engagement threshold
(by Amount in Rs without its sign, largest first, up to n), then the rest drawn at random from the
lines below the threshold until the sample has n lines. The seed (given, or made at random) is
stored on the sample, so the draw can be repeated: the same register and seed give the same lines.
When more than n lines are at or above the threshold, the largest n are taken and the sample says
how many were left out. Lines are of the loaded registers files of the open engagement.
"""

import random
import secrets
from typing import Any

import duckdb

from fin.audit.errors import SampleCannotDraw, SampleUnknown
from fin.audit.logger import Run
from fin.db.conn import transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database, require_open, rupees
from fin.reconcile import periods_of

PARTY = {"sales": "customer", "purchase": "vendor"}
_POPULATION = """
SELECT r.file_id, r.sheet, r."row", r.{party} AS party, r.invoice_no, r.invoice_date, r.amount_inr
FROM register_lines r JOIN source_files f USING (file_id)
WHERE f.engagement_id = ? AND f.kind = 'registers' AND f.status = 'loaded' AND r.kind = ?
  AND r.period = ?
ORDER BY r.file_id, r.sheet, r."row"
"""
_LINES = """
SELECT l.*, f.file_name FROM sample_lines l JOIN source_files f USING (file_id)
WHERE l.sample_id = ? ORDER BY l.line_no
"""


def choose(
    population: list[dict[str, Any]], n: int, threshold: Any, seed: int
) -> tuple[list[dict[str, Any]], int]:
    """(the sample's lines in the order drawn, each with its selection; how many lines are at or
    above the threshold)."""
    above = sorted(
        (x for x in population if abs(x["amount_inr"]) >= threshold),
        key=lambda x: (-abs(x["amount_inr"]), x["file_id"], x["sheet"], x["row"]),
    )
    chosen = [{**x, "selection": "threshold"} for x in above[:n]]
    below = [x for x in population if abs(x["amount_inr"]) < threshold]
    take = min(n - len(chosen), len(below))
    # A seeded draw the user can repeat, not a secret: S311 does not apply.
    drawn = random.Random(seed).sample(below, take) if take > 0 else []  # noqa: S311
    return chosen + [{**x, "selection": "random"} for x in drawn], len(above)


def draw(
    run: Run, company: str, kind: str, period: str, n: int, seed: int | None
) -> dict[str, Any]:
    """Draw and store a sample. E901 for a period not in the engagement, n below 1, or no loaded
    register line of the kind in the period."""
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        if period not in periods_of(engagement):
            raise SampleCannotDraw(
                f"{period} is not a period of {company}'s open engagement "
                f"({', '.join(periods_of(engagement))}); nothing recorded"
            )
        if n < 1:
            raise SampleCannotDraw("--n must be at least 1; nothing recorded")
        population = fetch_dicts(
            con,
            _POPULATION.format(party=PARTY[kind]),
            [engagement["engagement_id"], kind, period],
        )
        if not population:
            raise SampleCannotDraw(
                f"{company} {period}: no loaded {kind} register line (`fin ingest {company} <file> "
                "--kind registers`); nothing recorded"
            )
        seed = secrets.randbelow(2**31) if seed is None else seed
        threshold = engagement["query_threshold"]
        lines, above = choose(population, n, threshold, seed)
        sample = con.execute(
            "INSERT INTO samples (engagement_id, kind, period, size, threshold, seed, population, "
            "above_threshold, run_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "RETURNING sample_id",
            [
                engagement["engagement_id"], kind, period, n, threshold, seed, len(population),
                above, run.run_id, utcnow(),
            ],
        ).fetchone()  # fmt: skip
        assert sample is not None
        for number, line in enumerate(lines, start=1):
            con.execute(
                "INSERT INTO sample_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    sample[0], number, line["file_id"], line["sheet"], line["row"],
                    line["selection"], line["party"], line["invoice_no"], line["invoice_date"],
                    line["amount_inr"],
                ],
            )  # fmt: skip
        return load_sample(con, engagement["engagement_id"], sample[0])


def load_sample(
    con: duckdb.DuckDBPyConnection, engagement_id: int, sample_id: int
) -> dict[str, Any]:
    """The sample row with its lines (file_name added). E903 when it is not of the engagement."""
    rows = fetch_dicts(
        con,
        "SELECT * FROM samples WHERE sample_id = ? AND engagement_id = ?",
        [sample_id, engagement_id],
    )
    if not rows:
        raise SampleUnknown(
            f"no sample #{sample_id} in the open engagement; list them with `fin sample show`"
        )
    return {**rows[0], "lines": fetch_dicts(con, _LINES, [sample_id])}


def list_samples(run: Run, company: str, sample_id: int | None) -> list[dict[str, Any]]:
    """One sample with its lines, or every sample of the open engagement (without lines)."""
    with company_database(run, company) as con:
        engagement_id = require_open(con, company)["engagement_id"]
        if sample_id is not None:
            return [load_sample(con, engagement_id, sample_id)]
        return [
            {**row, "lines": []}
            for row in fetch_dicts(
                con,
                "SELECT * FROM samples WHERE engagement_id = ? ORDER BY sample_id",
                [engagement_id],
            )
        ]


def format_sample(sample: dict[str, Any]) -> str:
    left = sample["above_threshold"] - sum(
        1 for line in sample["lines"] if line["selection"] == "threshold"
    )
    head = (
        f"sample #{sample['sample_id']}: {sample['kind']} {sample['period']}, size "
        f"{sample['size']}, seed {sample['seed']}, {sample['population']} lines in the register, "
        f"{sample['above_threshold']} at or above the threshold {rupees(sample['threshold'])}"
    )
    if sample["lines"] and left > 0:
        head += f" ({left} of them not sampled: n is too small)"
    out = [head]
    for line in sample["lines"]:
        out.append(
            f"  line {line['line_no']} [{line['selection']}] {line['party']} invoice "
            f"{line['invoice_no']} {line['invoice_date']} {rupees(line['amount_inr'])} "
            f"({line['file_name']} {line['sheet']}!{line['row']})"
        )
    return "\n".join(out)
