"""`fin` command-line entry point (`fin.cli:app`). Every command runs through the audit logger
(`fin.audit.logger.execute`). One module per command group; importing it registers its commands.
The import order below is the order of commands in `fin --help`: keep it."""

from fin.cli.core import app

# isort: off
from fin.cli import init, ingest, findings, statements, data  # noqa: F401, E402
# isort: on

__all__ = ["app"]
