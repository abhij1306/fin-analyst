"""A data folder with company acme and an open engagement for FY25 (golden evaluation tests)."""

from pathlib import Path

from fin.audit.logger import Run
from fin.config import write_default_config
from fin.db.conn import connect, utcnow
from fin.db.schema import ensure_database
from fin.map.dictionary import load_seed
from fin.paths import audit_db_path, company_db_path, dictionary_db_path


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
