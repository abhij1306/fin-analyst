"""The forensic actions the CLI and the web UI share (SPEC-18): run the tests, read their status
and flags, and show and change the settings. Each change runs as an audited run with its CLI
command's name; the run's arguments record `via` ("cli" or "ui") and `actor`."""

from pathlib import Path
from typing import Any

from fin.audit.logger import Run
from fin.diligence.engagement import company_database
from fin.forensic import measures, settings, status
from fin.forensic import run as forensic_run
from fin.services.result import ActionResult, Progress, perform


def run_tests(
    root: Path,
    company: str,
    test: str | None = None,
    period: str | None = None,
    *,
    via: str,
    actor: str,
    progress: Progress | None = None,
) -> ActionResult:
    """`fin forensic run`: E803 without an open engagement; E990 for an invalid [forensic] table;
    E991 for an unknown test or period."""

    def body(run: Run) -> list[str]:
        if progress:
            progress(f"running the forensic tests for {company}")
        results = forensic_run.run_tests(run, root, company, test, period)
        return [forensic_run.log_line(r) for r in results]

    args = {"company": company, "test": test, "period": period, "via": via, "actor": actor}
    return perform(root, "forensic run", args, body)


def read_status(run: Run, root: Path, company: str) -> list[dict[str, Any]]:
    """`fin forensic status` and the Forensic screen: each test and period with its state."""
    return [{**row, "text": status.state_text(row)} for row in status.status(run, root, company)]


def read_flags(run: Run, root: Path, company: str, **filters: Any) -> list[dict[str, Any]]:
    """`fin forensic flags` and the Forensic screen."""
    return status.flags(run, root, company, **filters)


def read_period_end(run: Run, root: Path, company: str) -> list[str]:
    """`fin analytics` and the Forensic screen: the period-end revenue of each period, in words."""
    with company_database(run, company) as con:
        return measures.lines(measures.rows(con, root, company))


def read_settings(root: Path, company: str) -> list[dict[str, Any]]:
    """`fin forensic settings` and the Settings screen."""
    return settings.rows(root, company)


def set_setting(
    root: Path, company: str, key: str, value: str, *, via: str, actor: str
) -> ActionResult:
    """`fin forensic set` and the Settings screen: E990 for an unknown key or a bad value. The runs
    the setting feeds become stale."""

    def body(run: Run) -> list[str]:
        done = settings.set_value(run, root, company, key, value, actor)
        return [f"{company}: forensic setting {done['key']} = {done['value']}"]

    args = {"company": company, "key": key, "value": value, "via": via, "actor": actor}
    return perform(root, "forensic set", args, body)
