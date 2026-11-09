"""A diligence command that changes data backs up the company database first, to
backups/<run_id>/companies/<company>.duckdb."""

from decimal import Decimal
from pathlib import Path

from fin.audit.logger import Run
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.diligence.engagement import engagement_status, open_engagement
from fin.paths import company_db_path


def engagements(path: Path) -> list[tuple[int, str]]:
    with connect(path) as con:
        return con.execute("SELECT engagement_id, periods FROM engagement").fetchall()


def test_opening_an_engagement_backs_up_the_database_as_it_was(tmp_path: Path) -> None:
    path = company_db_path(tmp_path, "acme")
    ensure_database(path, "company")
    run = Run("engagement open", {}, tmp_path)

    open_engagement(run, "acme", ["FY24", "FY25"], Decimal(100000))

    backup = tmp_path / "backups" / run.run_id / "companies" / "acme.duckdb"
    assert engagements(backup) == []
    assert engagements(path) == [(1, "FY24,FY25")]


def test_reading_makes_no_backup(tmp_path: Path) -> None:
    path = company_db_path(tmp_path, "acme")
    ensure_database(path, "company")
    open_engagement(Run("engagement open", {}, tmp_path), "acme", ["FY24"], Decimal(1))
    run = Run("engagement status", {}, tmp_path)

    assert engagement_status(run, "acme")["engagement"]["periods"] == "FY24"
    assert not (tmp_path / "backups" / run.run_id).exists()
