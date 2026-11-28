"""The next step of each ingest failure (docs/specs/SPEC-03 PR 2) and the ingest service's progress
lines (PR 1, criterion 3)."""

from pathlib import Path

from typer.testing import CliRunner

from fin.actor import USER
from fin.audit import errors
from fin.cli import app
from fin.services.ingest import ingest_inbox
from fin.services.next_steps import NEXT_STEPS, OTHER, next_step, reading_prompt

runner = CliRunner()


def failure_codes() -> set[str]:
    found, todo = set(), [errors.DocumentFailure]
    while todo:
        cls = todo.pop()
        todo += cls.__subclasses__()
        if cls is not errors.DocumentFailure:
            found.add(cls.code)
    return found


def test_every_ingest_error_code_has_a_next_step_and_nothing_else_does() -> None:
    assert set(NEXT_STEPS) == failure_codes()
    assert all(step.text for step in NEXT_STEPS.values())


def test_the_steps_offer_the_buttons_the_spec_names() -> None:
    assert next_step("E203").text == "Which unit is this file in?"
    assert next_step("E203").offers == ("unit",)
    assert next_step("E104").offers == ("reading", "addon")
    assert next_step("E201").offers == ("reading", "addon")
    assert next_step("E103").offers == ()
    assert next_step("E003") is OTHER
    assert next_step(None) is OTHER


def test_the_reading_message_names_the_file_and_the_command_that_loads_it() -> None:
    first = reading_prompt("acme", "review/acme/scan.pdf", "E104 no text layer", force=False)
    assert "Claude reading of review/acme/scan.pdf (in the data folder) for acme" in first
    assert "fin could not read it: E104 no text layer." in first
    assert "`uv run fin ingest acme review/acme/scan.pdf --reading <csv>`" in first
    again = reading_prompt("acme", "inbox/acme/a.pdf", "pages", force=True)
    assert "`uv run fin ingest acme inbox/acme/a.pdf --reading <csv> --force`" in again


def test_ingest_tells_which_file_it_is_reading(data_root: Path, samples: Path) -> None:
    assert runner.invoke(app, ["init"]).exit_code == 0
    inbox = data_root / "inbox" / "kestrel"
    inbox.mkdir(parents=True)
    name = "Kestrel_Forgings_Financials_FY23-FY25.xlsx"
    (inbox / name).write_bytes((samples / "clean" / "kestrel" / name).read_bytes())
    told: list[str] = []

    result = ingest_inbox(data_root, "kestrel", via="ui", actor=USER, progress=told.append)

    assert result.ok, result.lines
    assert told == [f"reading 1 of 1: kestrel/{name}"]
    assert result.lines[0].startswith(f"[1/1] kestrel/{name}: loaded as doc_id 1 version 1: ")
    assert result.lines[-1].startswith("metrics: shared/analytics.duckdb refreshed, 1 companies")
