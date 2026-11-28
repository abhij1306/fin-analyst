"""FDD F2 (docs/fdd.md): management inquiries as queries with a topic, the checklist, the
[fdd] inquiry_topics config key and the query export of an inquiry, on an empty engagement."""

import shutil
from pathlib import Path

import pytest
import typer

from fin.audit.errors import DiligenceTableWrite, FinError
from fin.audit.logger import Run
from fin.config import FDD_DEFAULTS, load_config, write_default_config
from fin.db.conn import connect
from fin.db.schema import ensure_database
from fin.diligence import export_data, export_docs, inquiries, queries
from fin.diligence.cli_checks import check_raise_options
from fin.diligence.engagement import open_engagement_row
from fin.paths import company_db_path
from fin.query.write import DILIGENCE_TABLE
from tests.fdd_bsheet_fixtures import seed


def run(root: Path) -> Run:
    return Run("test", {}, root)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    data = tmp_path_factory.mktemp("f2") / "data"
    data.mkdir()
    write_default_config(data)
    ensure_database(company_db_path(data, "acme"), "company")
    with connect(company_db_path(data, "acme"), write=True) as con:
        seed(con)
    return data


@pytest.fixture
def root(template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = Path(shutil.copytree(template, tmp_path / "data"))
    monkeypatch.setenv("FIN_DATA_DIR", str(data))
    return data


def raise_one(root: Path, topic: str = "tax", **kw: str | None) -> dict:
    return inquiries.raise_inquiry(
        run(root),
        "acme",
        topic=topic,
        question="Are there open tax notices?",
        period=kw.get("period"),
        scope=kw.get("scope"),
    )


def test_inquiry_is_a_draft_query_with_a_topic_and_no_amount(root: Path) -> None:
    row = raise_one(root, period="FY24")
    assert (row["status"], row["topic"], row["amount"], row["period"]) == (
        "draft",
        "tax",
        None,
        "FY24",
    )
    assert row["sources"] == inquiries.INQUIRY_SOURCE
    [listed] = queries.list_queries(run(root), "acme")
    assert listed["topic"] == "tax"
    assert "inquiry: tax" in queries.format_query(listed)


def test_inquiry_follows_the_query_life_cycle(root: Path) -> None:
    row = raise_one(root)
    queries.set_status(run(root), "acme", [row["query_id"]], "approved")
    queries.set_status(run(root), "acme", [row["query_id"]], "sent")
    [done] = queries.set_status(
        run(root), "acme", [row["query_id"]], "answered", answer="None open", answer_source="r.pdf"
    )
    assert (done["status"], done["answer"], done["topic"]) == ("answered", "None open", "tax")


def test_unknown_topic_and_period_are_refused(root: Path) -> None:
    with pytest.raises(FinError) as caught:
        raise_one(root, topic="weather")
    assert caught.value.code == "E910"
    with pytest.raises(FinError) as caught:
        raise_one(root, period="FY19")
    assert caught.value.code == "E881"
    assert queries.list_queries(run(root), "acme") == []


def test_token_the_company_does_not_have_is_refused(root: Path) -> None:
    with pytest.raises(FinError) as caught:
        inquiries.raise_inquiry(
            run(root), "acme", topic="tax", question="Why CUST-9999?", period=None, scope=None
        )
    assert caught.value.code == "E861"


def test_checklist_shows_topics_with_none_and_their_standard_questions(root: Path) -> None:
    first = raise_one(root, "tax")
    withdrawn = raise_one(root, "cut_off")
    queries.set_status(run(root), "acme", [withdrawn["query_id"]], "withdrawn", note="dup")
    result = inquiries.checklist(run(root), "acme")
    data = {d["topic"]: d for d in result["topics"]}
    assert result["list"] == "fin's standard list (templates/fdd/inquiries.toml)"
    assert list(data) == list(FDD_DEFAULTS["inquiry_topics"])
    assert data["tax"]["active"] and data["tax"]["standard"] == []
    assert [q["query_id"] for q in data["tax"]["queries"]] == [first["query_id"]]
    assert not data["cut_off"]["active"] and data["cut_off"]["standard"]  # withdrawn: none active
    assert data["revenue_recognition"]["queries"] == [] and data["revenue_recognition"]["standard"]
    text = inquiries.format_checklist(result)
    assert "tax: 1 inquiries" in text and "cut_off: none yet" in text
    assert text.startswith("questions from fin's standard list")


def test_a_list_in_the_data_folder_is_drafted_from_and_named(root: Path) -> None:
    own = root / "templates" / "inquiries.toml"
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text('[tax]\nquestions = ["Which tax years are still open?"]\n', encoding="utf-8")

    result = inquiries.checklist(run(root), "acme")

    data = {d["topic"]: d for d in result["topics"]}
    assert result["list"] == "the data folder's list (templates/inquiries.toml)"
    assert result["path"] == str(own)
    assert data["tax"]["standard"] == ["Which tax years are still open?"]
    assert data["cut_off"]["standard"] == []  # not on the user's list: nothing from fin's
    text = inquiries.format_checklist(result)
    assert "questions from the data folder's list" in text
    assert "  standard: Which tax years are still open?" in text


@pytest.mark.parametrize(
    "body", ['[weather]\nquestions = ["x"]\n', "[tax]\nquestions = []\n", "[tax\n"]
)
def test_a_bad_list_in_the_data_folder_stops_with_e910(root: Path, body: str) -> None:
    own = root / "templates" / "inquiries.toml"
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text(body, encoding="utf-8")

    with pytest.raises(FinError) as caught:
        inquiries.checklist(run(root), "acme")

    assert caught.value.code == "E910" and "inquiries.toml" in str(caught.value)


def test_collect_is_the_report_data_function(root: Path) -> None:
    row = raise_one(root)
    with connect(company_db_path(root, "acme")) as con:
        engagement = open_engagement_row(con)
        assert engagement is not None
        data = inquiries.collect(con, engagement["engagement_id"], ("tax", "other"))
    assert [d["topic"] for d in data] == ["tax", "other"]
    assert data[0]["queries"][0]["query_id"] == row["query_id"] and not data[1]["active"]


def test_standard_template_uses_only_configured_topics(tmp_path: Path) -> None:
    topics = FDD_DEFAULTS["inquiry_topics"]
    assert set(inquiries.standard_questions(topics)) == set(topics)  # every topic has questions
    bad = tmp_path / "t.toml"
    bad.write_text('[weather]\nquestions = ["x"]\n', encoding="utf-8")
    with pytest.raises(FinError) as caught:
        inquiries.standard_questions(topics, bad)
    assert caught.value.code == "E910"
    bad.write_text("[tax]\nquestions = []\n", encoding="utf-8")
    with pytest.raises(FinError):
        inquiries.standard_questions(topics, bad)


def test_inquiry_topics_config(tmp_path: Path) -> None:
    write_default_config(tmp_path)
    assert load_config(tmp_path).fdd["inquiry_topics"] == FDD_DEFAULTS["inquiry_topics"]
    path = tmp_path / "config.toml"
    text = path.read_text(encoding="utf-8")
    start = text.index("inquiry_topics = [")
    end = text.index("]\n", start) + 2
    path.write_text(
        text[:start] + 'inquiry_topics = ["tax", "tax"]\n' + text[end:], encoding="utf-8"
    )
    with pytest.raises(FinError) as caught:
        load_config(tmp_path)
    assert caught.value.code == "E002"
    path.write_text(
        text[:start] + 'inquiry_topics = ["tax", "legal"]\n' + text[end:], encoding="utf-8"
    )
    assert load_config(tmp_path).fdd["inquiry_topics"] == ("tax", "legal")


def test_topic_cannot_be_mixed_with_amount_or_source() -> None:
    assert check_raise_options(None, None, (None, "FY24", None, None), "tax") == []
    for manual, finding, items in (
        ((1, None, None, None), None, None),
        ((None,) * 4, 3, None),
        ((None, None, None, "s"), None, None),
        ((None,) * 4, None, "1"),
    ):
        with pytest.raises(typer.BadParameter):
            check_raise_options(items, finding, manual, "tax")  # type: ignore[arg-type]


def test_sql_write_refuses_the_inquiries_table() -> None:
    assert DILIGENCE_TABLE.search("INSERT INTO inquiries VALUES (1, 'tax')")
    assert DiligenceTableWrite.code == "E806"


def test_export_names_the_topic_and_leaves_out_the_amount(root: Path) -> None:
    row = raise_one(root, period="FY24")
    queries.set_status(run(root), "acme", [row["query_id"]], "approved")
    with connect(company_db_path(root, "acme")) as con:
        engagement = open_engagement_row(con)
        assert engagement is not None
        [issue] = export_data.approved_issues(con, "acme", engagement)
        unmask = export_data.Unmasker(con)
    assert issue.title == "FY24 tax"
    email = export_docs.email_text("acme", [issue], unmask)
    assert f"{issue.query_id}. FY24 tax\n" in email and "₹" not in email
    assert "Are there open tax notices?" in email
