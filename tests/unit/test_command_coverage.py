"""The command coverage table (docs/product.md, "Where each command is"; SPEC-11): every `fin`
command is on a screen of the web UI, done through chat, or terminal only with its reason. A command
added without a row, or a row for a command that is gone, fails here."""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import typer.main

from fin.cli import app
from fin.ui.nav import SCREENS

PRODUCT = Path(__file__).parents[2] / "docs" / "product.md"
HEADING = "### Where each command is"
ROW = re.compile(r"\| `fin ([a-z -]+)` \| (.+) \|")
TERMINAL_ONLY = "terminal only: "


def commands(command: Any, prefix: list[str]) -> Iterator[str]:
    """Every command a user can run: each leaf, and a group that runs on its own (`fin ui`)."""
    subcommands = getattr(command, "commands", None)
    if subcommands is None:
        yield " ".join(prefix)
        return
    if prefix and command.invoke_without_command:
        yield " ".join(prefix)
    for name, sub in subcommands.items():
        yield from commands(sub, [*prefix, name])


def table() -> list[tuple[str, str]]:
    section = PRODUCT.read_text(encoding="utf-8").split(HEADING, 1)[1].split("\n#", 1)[0]
    lines = [line for line in section.splitlines() if line.startswith("| `fin ")]
    rows = [ROW.fullmatch(line) for line in lines]
    assert all(rows), [line for line, row in zip(lines, rows, strict=True) if row is None]
    return [(row[1], row[2]) for row in rows if row]


def test_every_command_has_one_row_and_every_row_is_a_command() -> None:
    names = [name for name, _ in table()]
    assert len(names) == len(set(names)), "a command has two rows"
    assert sorted(names) == sorted(commands(typer.main.get_command(app), []))


def test_each_row_names_a_screen_chat_or_terminal_only_with_its_reason() -> None:
    screens = {label for _, label in SCREENS}
    for name, where in table():
        if where.startswith(TERMINAL_ONLY):
            assert len(where.removeprefix(TERMINAL_ONLY)) > 15, f"fin {name}: say why"
        else:
            assert where in screens | {"chat"}, f"fin {name}: {where}"
