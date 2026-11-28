"""Management inquiries (docs/fdd.md F2): a query with a topic and no amount or source document.

An inquiry is an ordinary `accountant_queries` row (origin 'manual', no amount, the note
INQUIRY_SOURCE as its sources) plus a row in `inquiries` (db/schema/fdd_inquiries.sql) that names
its topic. It follows the query life cycle (`fin.diligence.queries`) and the query export. Claude
drafts inquiries from the checklist the user keeps in the data folder (`<data folder>/templates/
inquiries.toml`, SPEC-17 item 3) when there is one, else from fin's standard checklist
(`templates/fdd/inquiries.toml`); the user approves them. `collect` is the data function the FDD
report (F8) calls.
"""

import tomllib
from pathlib import Path
from typing import Any

import duckdb

from fin.audit.errors import InquiryInvalid, QueryPeriodInvalid
from fin.audit.logger import Run
from fin.config import load_config
from fin.db.conn import transaction
from fin.db.rows import fetch_dicts
from fin.diligence import queries
from fin.diligence.engagement import company_database, require_open

TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "fdd" / "inquiries.toml"
INQUIRY_SOURCE = "management inquiry (no source document)"
OWN_LIST = Path("templates") / "inquiries.toml"  # under the data folder


def checklist_path(root: Path) -> tuple[Path, str]:
    """(path, label) of the inquiry checklist to draft from: the user's list in the data folder
    when the file is there, else fin's standard one. The label names it for the output."""
    own = root / OWN_LIST
    if own.is_file():
        return own, f"the data folder's list ({OWN_LIST.as_posix()})"
    return TEMPLATE, "fin's standard list (templates/fdd/inquiries.toml)"


def check_topic(topic: str, topics: tuple[str, ...]) -> None:
    """E910 unless `topic` is one of [fdd] inquiry_topics."""
    if topic not in topics:
        raise InquiryInvalid(
            f"{topic!r} is not an inquiry topic (config.toml [fdd] inquiry_topics: "
            f"{', '.join(topics)}); nothing raised"
        )


def standard_questions(topics: tuple[str, ...], path: Path = TEMPLATE) -> dict[str, list[str]]:
    """The standard checklist: topic -> its questions. E910 when the template cannot be read or
    names a topic that is not in `topics` or gives it no list of questions."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise InquiryInvalid(f"cannot read the inquiry checklist {path}: {exc}").with_context(
            path=str(path)
        ) from exc
    out: dict[str, list[str]] = {}
    for topic, table in data.items():
        questions = table.get("questions") if isinstance(table, dict) else None
        if topic not in topics or not (
            isinstance(questions, list)
            and questions
            and all(isinstance(q, str) and q.strip() for q in questions)
        ):
            raise InquiryInvalid(
                f"{path}: {topic!r} is not one of [fdd] inquiry_topics or has no list of "
                "questions; fix the list"
            ).with_context(path=str(path), topic=topic)
        out[topic] = questions
    return out


def raise_inquiry(
    run: Run, company: str, *, topic: str, question: str, period: str | None, scope: str | None
) -> dict[str, Any]:
    """A draft inquiry on `topic`. E910 for an unknown topic, E881 for a period that is not one
    of the engagement's."""
    check_topic(topic, load_config(run.root).fdd["inquiry_topics"])
    with company_database(run, company, write=True) as con, transaction(con):
        engagement = require_open(con, company)
        if period is not None and period not in queries.periods_of(engagement):
            raise QueryPeriodInvalid(
                f"{period} is not a period of {company}'s open engagement "
                f"({', '.join(queries.periods_of(engagement))}); nothing raised"
            )
        row = queries.draft_in(
            run,
            company,
            con,
            engagement,
            {
                "origin": "manual",
                "finding_id": None,
                "period": period,
                "scope": scope,
                "question": question,
                "amount": None,
                "sources": INQUIRY_SOURCE,
            },
        )
        con.execute("INSERT INTO inquiries VALUES (?, ?)", [row["query_id"], topic])
        return {**row, "topic": topic}


def collect(
    con: duckdb.DuckDBPyConnection, engagement_id: int, topics: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Per topic of `topics`, its inquiries of the engagement (id, status, question, answer,
    answer_source) by id, withdrawn ones included. `active` is whether any is not withdrawn."""
    rows = fetch_dicts(
        con,
        "SELECT i.topic, q.query_id, q.status, q.question, q.answer, q.answer_source "
        "FROM inquiries i JOIN accountant_queries q USING (query_id) "
        "WHERE q.engagement_id = ? ORDER BY q.query_id",
        [engagement_id],
    )
    out = []
    for topic in topics:
        mine = [{k: v for k, v in r.items() if k != "topic"} for r in rows if r["topic"] == topic]
        out.append(
            {
                "topic": topic,
                "queries": mine,
                "active": any(r["status"] != "withdrawn" for r in mine),
            }
        )
    return out


def checklist(run: Run, company: str) -> dict[str, Any]:
    """{"list": which checklist was read (label), "path": its file, "topics": `collect` for the
    open engagement, with the checklist's questions on the topics that have no active inquiry yet
    (`standard`, else empty)}. E910 when the checklist cannot be used (`standard_questions`)."""
    topics = load_config(run.root).fdd["inquiry_topics"]
    path, label = checklist_path(run.root)
    standard = standard_questions(topics, path)
    with company_database(run, company) as con:
        engagement = require_open(con, company)
        data = collect(con, engagement["engagement_id"], topics)
    return {
        "list": label,
        "path": str(path),
        "topics": [
            {**d, "standard": [] if d["active"] else standard.get(d["topic"], [])} for d in data
        ],
    }


def format_checklist(result: dict[str, Any]) -> str:
    out = [f"questions from {result['list']}"]
    for d in result["topics"]:
        count = len([q for q in d["queries"] if q["status"] != "withdrawn"])
        out.append(f"{d['topic']}: {count} inquiries" if d["active"] else f"{d['topic']}: none yet")
        for q in d["queries"]:
            out.append(f"  #{q['query_id']} {q['status']}: {q['question']}")
            if q["answer"] is not None:
                out.append(f"      answer: {q['answer']}")
        for question in d["standard"]:
            out.append(f"  standard: {question}")
    return "\n".join(out)
