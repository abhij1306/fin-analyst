"""The diligence actions the CLI and the web UI share (SPEC-09): open and close an engagement,
import adjustments from a walk sheet, and raise, edit, answer and export queries. Each runs as an
audited run with its CLI command's name; the run's arguments record `via` ("cli" or "ui") and
`actor`, as in `fin.services.decisions`, which holds the decisions only the user takes."""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from fin.audit.logger import Run
from fin.diligence import adjust_import, adjustments, answers, engagement, export, queries
from fin.services.result import ActionResult, perform


@dataclass(frozen=True)
class Terms:
    """What an engagement is opened with (`fin engagement open`)."""

    periods: list[str]
    threshold: Decimal | None = None
    stub: str | None = None
    due_days: int | None = None


@dataclass(frozen=True)
class QueryDraft:
    """A query raised by hand (`fin query raise` with --amount, --period and --source)."""

    amount: Decimal
    period: str
    scope: str | None
    source: str
    question: str


def open_engagement(
    root: Path, company: str, terms: Terms, *, via: str, actor: str
) -> ActionResult:
    """`fin engagement open`: E803 when one is open already; E808, E846 as the command."""

    def body(run: Run) -> list[str]:
        row = engagement.open_engagement(
            run, company, terms.periods, terms.threshold, terms.stub, terms.due_days
        )
        return [f"{company}: {engagement.format_engagement(row)}"]

    args = {
        "company": company,
        "periods": ",".join(terms.periods),
        "threshold": terms.threshold,
        "stub": terms.stub,
        "due_days": terms.due_days,
        "via": via,
        "actor": actor,
    }
    return perform(root, "engagement open", args, body)


def close_engagement(root: Path, company: str, *, via: str, actor: str) -> ActionResult:
    """`fin engagement close`: E801 unless gate G4 is approved (when there is work to lose)."""

    def body(run: Run) -> list[str]:
        row = engagement.close_engagement(run, company)
        return [f"{company}: {engagement.format_engagement(row)}"]

    return perform(root, "engagement close", {"company": company, "via": via, "actor": actor}, body)


def import_adjustments(
    root: Path, company: str, path: Path, sheet: str | None, *, via: str, actor: str
) -> ActionResult:
    """`fin adjust import`: one proposed adjustment per row of the walk sheet, all or none
    (E847 names the bad row)."""

    def body(run: Run) -> list[str]:
        rows = adjust_import.import_file(run, company, path, sheet)
        lines = [adjustments.format_adjustment(row) for row in rows]
        return [*lines, f"{company}: {len(rows)} adjustments proposed from {path.name}"]

    args = {"company": company, "file": path.name, "sheet": sheet, "via": via, "actor": actor}
    return perform(root, "adjust import", args, body)


def raise_query(
    root: Path, company: str, draft: QueryDraft, *, via: str, actor: str
) -> ActionResult:
    """`fin query raise` with an amount, period and source: a draft query (E881 for a period
    that is not the engagement's)."""

    def body(run: Run) -> list[str]:
        row = queries.raise_manual(
            run,
            company,
            amount=draft.amount,
            period=draft.period,
            scope=draft.scope,
            sources=draft.source,
            question=draft.question,
        )
        return [queries.format_query(row)]

    args = {
        "company": company,
        "question": draft.question,
        "amount": draft.amount,
        "period": draft.period,
        "scope": draft.scope,
        "source": draft.source,
        "via": via,
        "actor": actor,
    }
    return perform(root, "query raise", args, body)


def edit_query(
    root: Path, company: str, query_id: int, question: str, *, via: str, actor: str
) -> ActionResult:
    """`fin query edit`: new wording for a draft (E805 otherwise)."""

    def body(run: Run) -> list[str]:
        return [queries.format_query(queries.edit(run, company, query_id, question))]

    args = {"company": company, "id": query_id, "question": question, "via": via, "actor": actor}
    return perform(root, "query edit", args, body)


@dataclass(frozen=True)
class Answer:
    """The accountant's answer (`fin query answer`): text, where it is, and/or the reply file."""

    text: str | None
    source: str | None = None
    file: Path | None = None


def answer_query(
    root: Path, company: str, query_id: int, answer: Answer, *, via: str, actor: str
) -> ActionResult:
    """`fin query answer`: record the answer to a sent query (E805 otherwise; E860 for a reply
    file fin cannot read)."""

    def body(run: Run) -> list[str]:
        text, source, reply = answer.text, answer.source, None
        if answer.file is not None:
            reply = answers.read_reply(company, answer.file, answer.text)
            text = reply.text
            source = f"{answer.source}; {reply.source}" if answer.source else reply.source
        rows = queries.set_status(
            run,
            company,
            [query_id],
            "answered",
            answer=text,
            answer_source=source,
            # archived in the same transaction: a refused answer leaves no file
            on_change=None if reply is None else lambda: answers.archive_reply(run, company, reply),
        )
        return [queries.format_query(row) for row in rows]

    args = {
        "company": company,
        "id": query_id,
        "text": answer.text,
        "source": answer.source,
        "file": answer.file,
        "via": via,
        "actor": actor,
    }
    return perform(root, "query answer", args, body)


def export_queries(root: Path, company: str, *, brief: bool, via: str, actor: str) -> ActionResult:
    """`fin query export`: the approved queries written for the accountant; the lines are the
    files' paths. fin sends nothing (E858 when no query is approved)."""

    def body(run: Run) -> list[str]:
        return [str(path) for path in export.export_queries(run, company, brief=brief)]

    args = {"company": company, "brief": brief, "via": via, "actor": actor}
    return perform(root, "query export", args, body)
