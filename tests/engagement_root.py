"""A data folder with company acme and an open engagement for FY25 (golden evaluation tests)."""

import json
from pathlib import Path

from fin.audit.logger import Run
from fin.config import write_default_config
from fin.db.conn import connect, utcnow
from fin.db.schema import ensure_database
from fin.map.dictionary import load_seed
from fin.paths import audit_db_path, company_db_path, dictionary_db_path, review_dir


def engagement_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    write_default_config(root)
    ensure_database(audit_db_path(root), "audit")
    ensure_database(dictionary_db_path(root), "dictionary")
    load_seed(root, Run("init", {}, root))
    path = company_db_path(root, "acme")
    ensure_database(path, "company")
    with connect(path, write=True) as con:
        con.execute(
            "INSERT INTO engagement (periods, query_threshold, status, opened_run_id, opened_at) "
            "VALUES ('FY25', 100000, 'open', 'r0', ?)",
            [utcnow()],
        )
    return root


def with_proposal(tmp_path: Path) -> Path:
    """A data folder with one proposed mapping item for acme."""
    root = engagement_root(tmp_path)
    item = {
        "id": 1,
        "status": "proposed",
        "statement": "balance_sheet",
        "section": "",
        "label": "Debtors",
        "facts": 0,
        "where": [],
        "account": "trade_receivables",
        "confidence": 0.9,
        "reason": "by name",
    }
    folder = review_dir(root, "acme")
    folder.mkdir(parents=True)
    pending = {"company": "acme", "next_id": 2, "items": [item]}
    (folder / "pending.json").write_text(json.dumps(pending), encoding="utf-8")
    return root


def mapping_decisions(root: Path) -> list[tuple[str, str, bool]]:
    with connect(audit_db_path(root)) as con:
        return con.execute(
            "SELECT decision, actor, on_request FROM mapping_decisions ORDER BY recorded_at"
        ).fetchall()
