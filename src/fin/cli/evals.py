"""`fin eval init|run|bundle`: the golden evaluation (fin.evals, docs/EVAL.md)."""

from typing import Annotated

import typer

from fin.audit.logger import Run, execute
from fin.cli.core import app, check_company, check_optional_company
from fin.evals.bundle import make_bundle
from fin.evals.scaffold import init_golden
from fin.evals.score import run_eval

eval_app = typer.Typer(
    no_args_is_help=True,
    help="Golden evaluation: score fin against values a person checked (docs/EVAL.md).",
)
app.add_typer(eval_app, name="eval")

Company = Annotated[str, typer.Argument(callback=check_company, help="Company name, e.g. kestrel.")]


@eval_app.command("init")
def eval_init(company: Company) -> None:
    """Write or extend evals/<company>/golden.csv: one line per figure fin has now, with where it is
    printed. expected and checked stay empty: a person fills them from the document. Lines already
    there are never changed."""

    def body(run: Run) -> None:
        result = init_golden(run, company)
        typer.echo(f"{result['path']}: {result['added']} lines added, {result['kept']} kept")

    execute("eval init", {"company": company}, body)


@eval_app.command("run")
def eval_run(company: Company) -> None:
    """Score fin's current outputs against the company's golden set: pass, fail, missing in fin, or
    unchecked (not scored). Writes the report to evals/<company>/."""

    def body(run: Run) -> None:
        result = run_eval(run, company)
        s = result["summary"]
        score = "no checked lines" if s["score_percent"] is None else f"{s['score_percent']}%"
        typer.echo(
            f"{company}: score {score}: {s['pass']} pass, {s['fail']} fail, {s['missing']} "
            f"missing in fin, {s['unchecked']} unchecked (not scored)"
        )
        typer.echo(str(result["report_md"]))

    execute("eval run", {"company": company}, body)


@eval_app.command("bundle")
def eval_bundle(
    company: Annotated[
        str | None,
        typer.Argument(callback=check_optional_company, help="Only this company (default: all)."),
    ] = None,
    with_figures: Annotated[
        bool,
        typer.Option("--with-figures", help="Include expected and fin values in rupees."),
    ] = False,
) -> None:
    """A results file to share: version, doctor summary, scores, failures and counts. No real names;
    no absolute figures unless --with-figures. Prints the file paths only."""

    def body(run: Run) -> None:
        for path in make_bundle(run, company, with_figures):
            typer.echo(str(path))

    execute("eval bundle", {"company": company, "with_figures": with_figures}, body)
