"""`fin query export <co> [--brief]`: what the user sends the company's accountant.

For the open engagement's approved queries, in `exports/<company>/<run_id>/` of the data folder:

- a marked-up workbook (`queries.xlsx`: summary, a sheet per issue with the count and value per kind
  of difference, and the cited source sheets with the lines highlighted; `export_xlsx.py`) and a
  draft summary email (`email.txt`); or, with `brief`, a Word brief per issue (`query-<id>.docx`)
  instead. There is no query letter and no shared sheet.
- Claude drafts with tokens; this export is the one reader of `mask_values`: the files hold the real
  names. fin prints only the file paths, and the audit log holds paths and query ids.

Nothing changes in the database: the user sends from their own email, then runs `fin query sent`.
E858 no approved query, E859 a file that cannot be read or written, E861 a token with no real name.
"""

import shutil
from pathlib import Path

from fin.audit.errors import ExportFileError
from fin.audit.logger import Run
from fin.diligence.engagement import company_database, require_open
from fin.diligence.export_data import Unmasker, approved_issues
from fin.diligence.export_docs import email_text, write_brief
from fin.diligence.export_xlsx import write_workbook


def export_queries(run: Run, company: str, *, brief: bool = False) -> list[Path]:
    """Write the export and return its files. A failure leaves no export folder behind."""
    folder = run.root / "exports" / company / run.run_id
    with company_database(run, company) as con:
        engagement = require_open(con, company)
        issues = approved_issues(con, company, engagement)
        u = Unmasker(con)
        try:
            folder.mkdir(parents=True)
        except OSError as exc:
            raise ExportFileError(f"cannot create {folder}: {exc}") from exc
        try:
            if brief:
                paths = [folder / f"query-{i.query_id}.docx" for i in issues]
                for issue, path in zip(issues, paths, strict=True):
                    write_brief(issue, u, path)
            else:
                paths = [folder / "queries.xlsx", folder / "email.txt"]
                write_workbook(con, run.root, engagement["engagement_id"], issues, u, paths[0])
                text = email_text(company, issues, u)
                u.check()
                paths[1].write_text(text, encoding="utf-8")
            u.check()
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
    run.event(
        "query export",
        f"{company}: {len(issues)} approved queries exported to {folder}",
        context={"company": company, "queries": [i.query_id for i in issues], "brief": brief},
    )
    return paths
