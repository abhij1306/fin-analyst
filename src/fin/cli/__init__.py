"""`fin` command-line entry point (`fin.cli:app`). Every command runs through the audit logger
(`fin.audit.logger.execute`). One module per command group; importing it registers its commands.
The import order below is the order of commands in `fin --help`: keep it."""

from fin.cli.core import app

# isort: off
from fin.cli import init, ingest, findings, statements, reconcile, payroll, data, evals, analytics  # noqa: F401, E402
from fin.cli import facts  # noqa: F401, E402
from fin.cli import documents  # noqa: F401, E402
from fin.cli import recast  # noqa: F401, E402
from fin.cli import fdd_contracts  # noqa: F401, E402
from fin.cli import provisions  # noqa: F401, E402
from fin.cli import orderbook  # noqa: F401, E402
from fin.cli import tax  # noqa: F401, E402
from fin.cli import disclosures  # noqa: F401, E402
from fin.map import proposal_commands  # noqa: F401, E402
from fin.cli import netdebt  # noqa: F401, E402
from fin.cli import fdd  # noqa: F401, E402
from fin.cli import ui  # noqa: F401, E402
from fin.cli import dataroom  # noqa: F401, E402
from fin.cli import reference  # noqa: F401, E402
from fin.cli import questions  # noqa: F401, E402
# isort: on

__all__ = ["app"]
