"""Golden checks for the FDD contracts and walkthroughs (docs/fdd.md F1), in rupees.

- `contract_value`: the value of a contract Claude read (`contracts.value`); `ledger` is the
  contract id. The source a person checks is the contract document, file + page;
- `sample_exception_amount`: Amount in Rs (without signs) of the sample's lines with an exception
  recorded (`walkthrough_summary.exception_amount`); period is the sample's period, `ledger` the
  sample id. The source is the sampled register lines and the evidence of each exception.
"""

from collections.abc import Callable

import duckdb

KINDS = ("contract_value", "sample_exception_amount")
NEEDS = {"contract_value": ("ledger",), "sample_exception_amount": ("period", "ledger")}
_LATEST = "(SELECT max(engagement_id) FROM engagement)"
FIN_VALUE = {
    "contract_value": (
        f"SELECT value FROM contracts WHERE contract_id::VARCHAR = ? AND engagement_id = {_LATEST}",
        ("ledger",),
    ),
    "sample_exception_amount": (
        "SELECT exception_amount FROM walkthrough_summary WHERE sample_id::VARCHAR = ? "
        f"AND period = ? AND engagement_id = {_LATEST}",
        ("ledger", "period"),
    ),
}
_CONTRACTS = f"""
SELECT contract_id, file_name || ' p.' || page FROM contracts
WHERE value IS NOT NULL AND engagement_id = {_LATEST} ORDER BY contract_id
"""
_SAMPLES = f"""
SELECT sample_id, period FROM walkthrough_summary
WHERE exception_lines > 0 AND engagement_id = {_LATEST} ORDER BY sample_id
"""


def candidates(
    con: duckdb.DuckDBPyConnection, row: Callable[..., dict[str, str]]
) -> list[dict[str, str]]:
    """Every contract value and every sample with an exception fin has, with its source."""
    rows = [
        row("contract_value", "", source, ledger=str(contract_id), unit="rupees")
        for contract_id, source in con.execute(_CONTRACTS).fetchall()
    ]
    rows += [
        row(
            "sample_exception_amount", period, f"sample #{sample_id} lines with an exception",
            ledger=str(sample_id), unit="rupees",
        )
        for sample_id, period in con.execute(_SAMPLES).fetchall()
    ]  # fmt: skip
    return rows
