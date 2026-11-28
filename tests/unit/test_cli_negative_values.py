"""Every option that takes a value accepts one starting with a minus, in both forms
(`--opt -1` and `--opt=-1`): a negative amount is never read as an unknown flag (client run,
2026-11-27, `fin adjust propose --revenue-effect -1000000`). Only the command line is parsed;
no command runs."""

from collections.abc import Iterator
from typing import Any

import pytest
import typer.main

from fin.cli import app


def value_options() -> Iterator[tuple[str, Any, Any]]:
    """(command path, command, option) for every --option that takes a value."""

    def walk(command: Any, path: str) -> Iterator[tuple[str, Any, Any]]:
        for name, sub in getattr(command, "commands", {}).items():
            yield from walk(sub, f"{path} {name}".strip())
        for param in getattr(command, "params", []):
            if getattr(param, "is_flag", True) is False and param.opts[0].startswith("--"):
                yield path, command, param

    yield from walk(typer.main.get_command(app), "")


OPTIONS = list(value_options())


def test_the_amount_options_are_among_those_checked() -> None:
    names = {(path, param.opts[0]) for path, _, param in OPTIONS}
    assert {
        ("adjust propose", "--amount"),
        ("adjust propose", "--revenue-effect"),
        ("adjust propose", "--cost-effect"),
        ("adjust propose", "--moved-amount"),
        ("query raise", "--amount"),
    } <= names


@pytest.mark.parametrize(
    ("path", "command", "param"), OPTIONS, ids=[f"{p} {o.opts[0]}" for p, _, o in OPTIONS]
)
def test_a_negative_value_is_taken_as_the_value(path: str, command: Any, param: Any) -> None:
    option = param.opts[0]
    for form in ([option, "-1000000"], [f"{option}=-1000000"]):
        parser = command.make_parser(command.context_class(command))
        values, extra, _ = parser.parse_args(form)
        expected = ["-1000000"] if param.multiple else "-1000000"
        assert (values[param.name], extra) == (expected, []), form
