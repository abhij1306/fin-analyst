"""The draft summary email (text) and the one-issue Word brief of a query export.

Both are written from the approved queries as they are: the wording Claude drafted and fin's own
figures (count and value per kind of difference), with real names in place of tokens. They describe
the differences and ask; they judge nothing. The user sends from their own email.
"""

from pathlib import Path

from docx import Document
from docx.shared import Pt

from fin.audit.errors import ExportFileError
from fin.diligence.engagement import rupees
from fin.diligence.export_data import Issue, Unmasker

BRIEF_LINES = 20  # lines a brief lists, so it stays within two pages; the rest are counted


def email_text(company: str, issues: list[Issue], u: Unmasker) -> str:
    """Subject and body of the covering email; the workbook has a sheet per query."""
    periods = ", ".join(dict.fromkeys(i.query["period"] for i in issues if i.query["period"]))
    count = f"{len(issues)} query" if len(issues) == 1 else f"{len(issues)} queries"
    out = [
        f"Subject: {company}: {count}" + (f" ({periods})" if periods else ""),
        "",
        "Hello,",
        "",
        f"We have {count} on {company}'s records. The attached workbook has a Summary sheet, a "
        "sheet per query (Q<number>) listing the lines, and copies of the cited source sheets "
        "(S1, S2, ...) with the rows concerned highlighted.",
        "",
    ]
    for issue in issues:
        out.append(f"{issue.query_id}. {issue.title}, {rupees(issue.query['amount'])}")
        out.append(f"   {u(issue.query['question'])}")
        if issue.kinds:
            out.append(
                f"   Lines: {len(issue.lines)}: {issue.difference_text()} (sheet Q{issue.query_id})"
            )
        else:
            out.append(f"   Sources: {u(issue.query['sources'])}")
        out.append("")
    out += [
        "Please reply with what explains each, or the document that shows it.",
        "",
        "Thank you,",
        "",
    ]
    return "\n".join(out)


def write_brief(issue: Issue, u: Unmasker, path: Path) -> None:
    """A brief for one query: the question, the count and value per kind of difference, the lines
    (the first BRIEF_LINES) and the sources."""
    doc = Document()
    doc.styles["Normal"].font.size = Pt(10)  # type: ignore[attr-defined]
    doc.add_heading(f"Query {issue.query_id}: {issue.title}", level=1)
    doc.add_paragraph(f"Amount at issue: {rupees(issue.query['amount'])}")
    doc.add_paragraph(u(issue.query["question"]))
    if issue.kinds:
        doc.add_heading("What differs", level=2)
        table = doc.add_table(rows=1, cols=3, style="Light Grid Accent 1")
        for cell, head in zip(table.rows[0].cells, ("Kind", "Lines", "Value"), strict=True):
            cell.text = head
        for kind in issue.kinds:
            row = table.add_row().cells
            row[0].text, row[1].text, row[2].text = (
                kind["label"],
                str(kind["count"]),
                rupees(kind["value"]),
            )
        doc.add_heading("Lines", level=2)
        lines = doc.add_table(rows=1, cols=4, style="Light Grid Accent 1")
        for cell, head in zip(
            lines.rows[0].cells, ("Line", "Kind", "Difference", "Source"), strict=True
        ):
            cell.text = head
        for line in issue.lines[:BRIEF_LINES]:
            row = lines.add_row().cells
            source = "; ".join(s for s in (line["left_source"], line["right_source"]) if s)
            row[0].text = u(line["key"])
            row[1].text = line["kind"].replace("_", " ")
            row[2].text = rupees(line["difference"])
            row[3].text = u(source)
        if len(issue.lines) > BRIEF_LINES:
            doc.add_paragraph(f"and {len(issue.lines) - BRIEF_LINES} more lines of these kinds.")
    doc.add_heading("Sources", level=2)
    doc.add_paragraph(u(issue.query["sources"]))
    try:
        doc.save(str(path))
    except OSError as exc:
        raise ExportFileError(f"cannot write {path}: {exc}") from exc
