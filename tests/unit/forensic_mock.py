"""The mock set (mock/) loaded for the forensic tests: the trial balances and Ledger Vouchers of
every period, ledgers mapped as `mock.generator.data.LEDGERS` says, an engagement over the four
periods and the approval limit the mock company states."""

import shutil
from pathlib import Path

from fin.actor import USER
from fin.audit.logger import Run
from fin.config import load_config, write_default_config
from fin.db.conn import connect, utcnow
from fin.db.schema import ensure_database
from fin.diligence import ledgers
from fin.diligence.sources import ingest_source
from fin.forensic import settings
from fin.map.dictionary import load_seed
from fin.paths import audit_db_path, company_db_path, dictionary_db_path
from mock.generator import data

COMPANY = "corvane"
ROOM = Path(__file__).resolve().parents[2] / "mock" / "dataroom" / "01_company"
TRIAL = {
    "FY23": "corvane_tb_fy23_2023-05-10.xlsx",
    "FY24": "corvane_tb_fy24_2024-05-12.xlsx",
    "FY25": "corvane_tb_fy25_v2_2025-06-20.xlsx",
    data.STUB: "corvane_tb_fy26_h1_2025-10-15.xlsx",
}
VOUCHERS = {
    "FY23": "corvane_ledger_vouchers_fy23.xlsx",
    "FY24": "corvane_ledger_vouchers_fy24.xlsx",
    "FY25": "corvane_ledger_vouchers_fy25.xlsx",
    data.STUB: "corvane_ledger_vouchers_fy26_h1.xlsx",
}


def run(root: Path) -> Run:
    return Run("test", {}, root)


def build(root: Path, periods: tuple[str, ...] = tuple(data.PERIODS)) -> Path:
    """A data folder with the mock company loaded for `periods`."""
    root.mkdir(parents=True)
    write_default_config(root)
    ensure_database(audit_db_path(root), "audit")
    (root / "logs").mkdir()
    ensure_database(dictionary_db_path(root), "dictionary")
    load_seed(root, Run("init", {}, root))
    ensure_database(company_db_path(root, COMPANY), "company")
    with connect(company_db_path(root, COMPANY), write=True) as con:
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, stub, stub_start, stub_end, status, "
            "opened_run_id, opened_at) VALUES (?, ?, ?, ?, ?, 'open', 'r0', ?)",
            [
                ",".join(data.ANNUAL),
                data.QUERY_THRESHOLD,
                data.STUB,
                *data.PERIODS[data.STUB],
                utcnow(),
            ],
        )
    config = load_config(root)
    for period in periods:
        trial = ROOM / "trial_balances" / TRIAL[period]
        ingest_source(run(root), config, COMPANY, trial, "trial_balance", None)
        vouchers = ROOM / "ledgers" / VOUCHERS[period]
        ingest_source(run(root), config, COMPANY, vouchers, "ledger_vouchers", None)
    found = ledgers.list_ledgers(run(root), COMPANY)
    loaded = {r["ledger"] for r in found}
    proposals = [
        {"ledger": ledger, "account": account, "confidence": 0.9, "reason": "by name"}
        for ledger, (_, account) in data.LEDGERS.items()
        if ledger in loaded  # a ledger of a period not loaded has no trial balance row yet
    ]
    ledgers.propose(run(root), COMPANY, proposals)
    ledgers.confirm(run(root), COMPANY, [r["ledger_id"] for r in found], actor=USER)
    settings.set_value(run(root), root, COMPANY, "approval_limits", str(data.APPROVAL_LIMIT), USER)
    return root


def copy(template: Path, to: Path) -> Path:
    return Path(shutil.copytree(template, to))
