"""Error classes with their codes (the table is in README.md, "Error codes"), and exit codes.

Every failure path raises a subclass of `FinError`. Each class carries its code and the exit code
a command returns when the error ends it:

- E0xx (setup), E5xx (database), E601-E603 (outputs) and E406 (accepting a finding that is not
  open) stop the command: exit 2.
- E1xx-E3xx are per-document failures and E604 is flagged in the evidence page. The command
  completes and reports them: exit 1.
- E302 (a low-confidence mapping proposal) is a warning event, not an error: it has no class.
- E401-E405 are not errors: they are the codes of validation checks whose failures are findings
  (`fin.findings`), shown to the user; the document is held, not rejected.
- E701 (action needs user confirmation; the CLI refused without it): exit 3.
- E801-E802 (diligence gates: a command needs a gate that is not approved, or a gate cannot be
  approved yet) are refusals: exit 3.
- E803-E806 (diligence: no open engagement or one already open, an invalid adjustment, an unknown
  id or a status change not allowed, `fin sql --write` on a diligence table) stop the command:
  exit 2.
"""

# Exit codes
EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_COMMAND_ERROR = 2
EXIT_BLOCKED = 3  # the CLI refused: the action needs the user's confirmation


class FinError(Exception):
    """Base error. Every failure path raises a subclass carrying its code."""

    code: str = ""
    exit_code: int = EXIT_COMMAND_ERROR

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


# E0xx: environment and setup. Stop the command.


class DependencyMissing(FinError):
    """E001. A required program or data-folder file (config.toml, audit database) is missing."""

    code = "E001"


class DependencyVersionMismatch(FinError):
    """E002. A program, config.toml or database is not the version or shape this code expects."""

    code = "E002"


class DataFolderNotWritable(FinError):
    code = "E003"


class UpdateFailed(FinError):
    """E005. `fin update` stopped: local changes in the repo, or a git, uv or npm step failed."""

    code = "E005"


# E1xx: file intake. The file is skipped and moved to review/; the command completes with failures.


class DocumentFailure(FinError):
    """Base for per-document failures (E1xx-E3xx): the command completes with failures."""

    exit_code = EXIT_FAILURES
    # The statement pages fin could not read (page, statement, code, reason), when every one failed.
    pages: tuple[dict[str, object], ...] = ()


class FileUnreadable(DocumentFailure):
    code = "E101"


class FileEncrypted(DocumentFailure):
    code = "E102"


class UnsupportedFormat(DocumentFailure):
    code = "E103"


class ScannedPdf(DocumentFailure):
    code = "E104"


class DuplicateFile(DocumentFailure):
    code = "E105"


# E2xx: extraction. File to the review queue, nothing loaded.


class NoTablesFound(DocumentFailure):
    code = "E201"


class PeriodsNotDetected(DocumentFailure):
    code = "E202"


class UnitNotDetected(DocumentFailure):
    code = "E203"


class ParseError(DocumentFailure):
    code = "E204"


class ReadingInvalid(DocumentFailure):
    """E205. A reading file (figures Claude read from the document, `fin ingest --reading`,
    `fin verify`) cannot be used: missing columns, an unknown statement or scope, a bad source,
    or no lines."""

    code = "E205"


class DoclingFailed(DocumentFailure):
    """E206. Docling (the optional PDF add-on) failed on the pages pdfplumber skipped, or could not
    download its models. Logged as a warning: the pages stay unread, the document still loads."""

    code = "E206"


# E3xx: mapping. Loaded as unconfirmed and queued for review.


class UnmappedLabel(DocumentFailure):
    code = "E301"


class ConflictingMapping(DocumentFailure):
    code = "E303"


class InvalidMapping(FinError):
    """E304. A mapping proposal or review request that cannot be applied: unknown account, an
    account of another statement, an unknown pending id or label, or unreadable proposals."""

    code = "E304"


# E4xx: validation. E401-E405 are the codes of the checks (fin.validate.gates); a failed check is a
# finding, not an error.


class FindingNotOpen(FinError):
    """E406: `fin findings accept` named a finding that does not exist or is not open. Nothing is
    accepted."""

    code = "E406"


# E5xx: database. Stop the command.


class DatabaseLocked(FinError):
    code = "E501"


class ReadOnlyViolation(FinError):
    """E502. `fin sql` was given something other than one SELECT without --write."""

    code = "E502"


class SchemaFailed(FinError):
    """E503. A database could not be created from its schema file."""

    code = "E503"


class SqlFailed(FinError):
    """E504. A `fin sql` statement failed (syntax, unknown column, constraint);
    nothing was changed."""

    code = "E504"


# E6xx: outputs.


class ReportRenderFailed(FinError):
    code = "E601"


class NarrativeHasTypedNumbers(FinError):
    code = "E602"


class DashboardBuildFailed(FinError):
    code = "E603"


class EvidenceSourceMissing(FinError):
    """E604. Flagged in the evidence page; the page is still produced."""

    code = "E604"
    exit_code = EXIT_FAILURES


# E7xx: guard.


class GuardBlocked(FinError):
    """E701: action needs user confirmation (guard). Raised when the CLI refuses without it."""

    code = "E701"
    exit_code = EXIT_BLOCKED


# E8xx: diligence (fin.diligence). E801-E802 (gates) refuse: exit 3. E803-E806 stop the command.


class GateNotApproved(FinError):
    """E801. The command needs a gate (fin.diligence.gates) that is not approved: never approved
    (open), or approved before and its data changed since (reopened). Nothing changed."""

    code = "E801"
    exit_code = EXIT_BLOCKED


class GateNotReady(FinError):
    """E802. `fin gate approve` refused: the gate's checklist fails, or an earlier gate is not
    approved. Nothing changed."""

    code = "E802"
    exit_code = EXIT_BLOCKED


class EngagementState(FinError):
    """E803. The company has no open engagement (every diligence command but `fin engagement open`
    needs one), or `fin engagement open` found one already open. Nothing changed."""

    code = "E803"


class AdjustmentInvalid(FinError):
    """E804. An adjustment cannot be proposed: no evidence (query, finding or source), a category
    not in config.toml [diligence], a period not in the engagement, or an account not in the
    dictionary. Nothing changed."""

    code = "E804"


class DiligenceChangeRefused(FinError):
    """E805. An unknown query, adjustment or finding id, or a status change not allowed (for example
    approving a query that is not a draft). Nothing changed."""

    code = "E805"


class DiligenceTableWrite(FinError):
    """E806. `fin sql --write` was given a statement that writes to a diligence table (engagement,
    accountant_queries, adjustments, gate_approvals): those change only through `fin engagement`,
    `fin query`, `fin adjust` and `fin gate`. Nothing was run."""

    code = "E806"
