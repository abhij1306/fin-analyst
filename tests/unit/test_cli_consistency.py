"""Every `fin` command (SPEC-00): its help works, a usage error exits 2, an option its mode
ignores is refused, and docs/product.md names every command and option (and no command that does
not exist)."""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from fin.cli import app

ROOT = Path(__file__).resolve().parents[2]
# `fin a b|c` in the docs names `fin a b` and `fin a c`; Markdown tables escape the bar.
MENTION = re.compile(r"`(?:uv run )?fin ([^`]*)`")
WORD = re.compile(r"[a-z][a-z0-9-]*(?:\|[a-z][a-z0-9-]*)*$")


def leaves(command: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    if hasattr(command, "commands"):
        for name, sub in command.commands.items():
            yield from leaves(sub, (*path, name))
    else:
        yield path, command


TREE = typer.main.get_command(app)
COMMANDS = dict(leaves(TREE))
NAMES = [" ".join(path) for path in COMMANDS]


def options(command: Any) -> list[str]:
    return sorted(
        flag
        for param in command.params
        if type(param).__name__.endswith("Option")
        for flag in param.opts
        if flag.startswith("--") and flag != "--help"
    )


def documented(doc: str) -> tuple[set[str], set[str]]:
    """(commands the docs name, names the docs use as commands that do not exist)."""
    found: set[str] = set()
    unknown: set[str] = set()
    for mention in MENTION.finditer(doc.replace("\\|", "|")):
        nodes: list[tuple[tuple[str, ...], Any]] = [((), TREE)]
        for word in mention.group(1).split():
            groups = [(path, node) for path, node in nodes if hasattr(node, "commands")]
            if not groups or not WORD.match(word):
                break
            nodes = []
            for path, node in groups:
                for name in word.split("|"):
                    if name in node.commands:
                        nodes.append(((*path, name), node.commands[name]))
                    else:
                        unknown.add(" ".join((*path, name)))
        found |= {" ".join(path) for path, node in nodes if not hasattr(node, "commands")}
    return found, unknown


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("FIN_DATA_DIR", str(tmp_path / "data"))
    return lambda *args: CliRunner().invoke(app, list(args))


@pytest.mark.parametrize("name", NAMES)
def test_help_works(run: Any, name: str) -> None:
    result = run(*name.split(), "--help")

    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("name", NAMES)
def test_a_usage_error_exits_2(run: Any, name: str) -> None:
    result = run(*name.split(), "--no-such-option")

    assert result.exit_code == 2, result.output


@pytest.mark.parametrize(
    ("args", "said"),
    [
        (["ingest", "acme", "--profile", "gstr1"], "--profile needs --kind"),
        (["ingest", "acme", "--unit", "lakhs"], "--unit needs PATH"),
        (["ingest", "acme", "{file}", "--kind", "bank", "--force"], "does not take --force"),
        (["report", "acme", "--diligence", "--fdd"], "not both"),
    ],
)
def test_an_option_the_mode_ignores_is_refused(
    run: Any, tmp_path: Path, args: list[str], said: str
) -> None:
    (tmp_path / "b.xlsx").write_bytes(b"")

    result = run(*(a.format(file=tmp_path / "b.xlsx") for a in args))

    assert result.exit_code == 2
    # Typer draws a usage error in a coloured box that wraps the message: the words, in order
    text = re.sub(r"\x1b\[[0-9;]*m", "", result.output).replace(
        "\N{BOX DRAWINGS LIGHT VERTICAL}", " "
    )
    assert said in " ".join(text.split())


def test_the_docs_name_every_command_and_only_real_ones() -> None:
    found, unknown = documented((ROOT / "docs" / "product.md").read_text(encoding="utf-8"))

    assert sorted(set(NAMES) - found) == []
    assert sorted(unknown) == []


def test_the_docs_name_every_option() -> None:
    doc = (ROOT / "docs" / "product.md").read_text(encoding="utf-8")
    missing = {
        name: [o for o in options(COMMANDS[tuple(name.split())]) if o not in doc] for name in NAMES
    }

    assert {name: flags for name, flags in missing.items() if flags} == {}


def test_the_docs_check_finds_a_missing_and_an_unknown_command() -> None:
    found, unknown = documented("`fin ingest` and `fin query raise|nope <co>`")

    assert "query list" not in found
    assert found >= {"ingest", "query raise"}
    assert unknown == {"query nope"}
