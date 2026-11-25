"""`fin` command-line entry point (`fin.cli:app`). Every command runs through the audit logger
(`fin.audit.logger.execute`). One module per command group; importing it registers its commands.
The import order below is the order of commands in `fin --help`: keep it."""

from fin.cli.core import app

# isort: off
from fin.cli import init, ingest, findings, statements, reconcile, payroll, data, evals, analytics  # noqa: F401, E402
from fin.cli import recast  # noqa: F401, E402
from fin.cli import fdd_contracts  # noqa: F401, E402
from fin.cli import provisions  # noqa: F401, E402
# isort: on

__all__ = ["app"]
