"""What a service returns, and the audited run every service works in.

`Done` and `Refused` share `ok`, `lines`, `code` and `run_id`, so the UI renders either one; the
CLI echoes the lines and exits with `Refused.exit_code` (`show`)."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import typer

from fin.audit.errors import EXIT_COMMAND_ERROR, EXIT_OK, FinError, UnexpectedError
from fin.audit.logger import Run, is_defect


@dataclass(frozen=True)
class Done:
    lines: list[str]
    run_id: str
    ok: ClassVar[bool] = True
    code: ClassVar[None] = None


@dataclass(frozen=True)
class Refused:
    """A refusal with its error code; `run_id` is None when it was refused before a run started.
    `code` is None only when the work was done but the audit log could not be written: `done` holds
    what was done."""

    code: str | None
    message: str
    run_id: str | None = None
    exit_code: int = EXIT_COMMAND_ERROR
    done: tuple[str, ...] = ()
    ok: ClassVar[bool] = False

    @property
    def lines(self) -> list[str]:
        return [*self.done, self.message]

    @classmethod
    def before_run(cls, error: FinError) -> "Refused":
        return cls(error.code, error.message, None, error.exit_code)


ActionResult = Done | Refused
Progress = Callable[[str], None]  # told one line about where a long action is


def perform(
    root: Path, command: str, args: dict[str, Any], body: Callable[[Run], list[str]]
) -> ActionResult:
    """Run `body` as an audited run of `command`, like `fin.audit.logger.execute` but returning the
    result instead of exiting. A FinError, or a defect (E012, `is_defect`), is printed to stderr,
    logged with its code and returned as a Refused with the run_id; an interrupt is raised again."""
    run = Run(command, args, root)
    lines: list[str] = []
    refused: tuple[str, str] | None = None  # (code, message)
    exit_code = EXIT_OK
    try:
        run.start(required=True)
        lines = body(run)
        exit_code = run.exit_code
    except FinError as exc:
        typer.echo(str(exc), err=True)
        run.log_error(command, exc.message, exc.code, exc.context)
        refused, exit_code = (exc.code, exc.message), exc.exit_code
    except BaseException as exc:
        run.log_unexpected(command, exc)
        if not is_defect(exc):
            run.finish(EXIT_COMMAND_ERROR)
            raise
        refused = (UnexpectedError.code, run.unexpected_message(exc))
        typer.echo(f"{refused[0]}: {refused[1]}", err=True)
        exit_code = EXIT_COMMAND_ERROR
    exit_code = run.finish(exit_code)
    if refused is not None:
        return Refused(*refused, run.run_id, exit_code)
    if exit_code != EXIT_OK:
        message = f"done, but run {run.run_id} ended with exit {exit_code}: see the messages above"
        return Refused(None, message, run.run_id, exit_code, tuple(lines))
    return Done(lines, run.run_id)


def show(result: ActionResult) -> None:
    """The CLI's rendering: the lines on stdout (a refusal's message is already on stderr), then the
    exit code of a refusal."""
    for line in result.done if isinstance(result, Refused) else result.lines:
        typer.echo(line)
    if isinstance(result, Refused):
        raise typer.Exit(result.exit_code)
