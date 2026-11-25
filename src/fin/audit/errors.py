"""Error classes with their codes (the table is in docs/errors.md), and exit codes.

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
- E807-E811 (diligence source files: no column profile for the layout, a period not in the
  engagement, a trial balance with group rows only, `fin sql` naming `mask_values`, Ledger
  Vouchers before its period's trial balance) stop the command: exit 2.
- E816-E818 (bank statements: a PDF with no PDF add-on or with pages the add-on could not read, no
  account number in a statement's header, a saved parse that cannot be read) stop the command:
  exit 2.
- E812 (a reconciliation cannot run: a period not annual in the engagement, no loaded trial balance,
  or no audited statement figures for the period) stops the command: exit 2.
- E840-E842 (diligence reconciliations: one cannot run for lack of loaded inputs; a recon line id is
  unknown or stale; a line cannot be raised as a query) stop the command: exit 2.
- E845 (`fin engagement passthrough`: a customer token that is not on a loaded sales register)
  stops the command: exit 2.
- E868-E869 (D9 analytics: a customer sector proposal or review that is not valid; `fin analytics`
  with no engagement or no loaded sales register) stop the command: exit 2.
- E863-E865 (service-period recast: it cannot run for lack of a loaded sales register or a period of
  the engagement; a contract reading that cannot be used; a contract naming a customer or invoice
  no loaded sales register has) stop the command: exit 2.
- E873-E874 (the diligence report, fin.report.dd_render: the company has no engagement; a narrative
  reference to a figure or period the report does not have) stop the command: exit 2.
- E820-E821 (payroll: `fin payroll confirm-unit` names no loaded payroll file; a payroll row with no
  employee name or code, or an unreadable date) stop the command: exit 2.
- E858-E861 (the query export, fin.diligence.export: no approved query, a file that cannot be read
  or written, a reply file that cannot be recorded, a token with no real value) stop the command:
  exit 2.
- E854-E855 (bank matching: a match proposal file that is not valid, an unknown match proposal id or
  a status change not allowed) stop the command: exit 2.
- E830-E833 (golden evaluation, fin.evals: no golden set, a golden set that cannot be read, nothing
  to bundle, a bundle that would contain a real name) stop the command: exit 2.
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
    """E005. `fin update` stopped: local changes in the repo, or a git, uv or npm step failed; or
    the `uv sync` of `fin parser use` failed."""

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


class PdfAddonFailed(DocumentFailure):
    """E206. The optional PDF add-on (Docling, LiteParse or LlamaParse; the message names it) failed
    on the pages pdfplumber skipped, could not download its models, or (LlamaParse) has no API key
    in LLAMA_CLOUD_API_KEY. Logged as a warning: the pages stay unread, the document still loads."""

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
    accountant_queries, adjustments, gate_approvals, source_files, trial_balance, ledger_entries,
    ledger_blocks, register_lines, payroll_lines, payroll_units, ledger_accounts, recon_runs,
    recon_items, passthrough_customers, recon_match_lines, match_proposals, contracts): those change
    only through `fin engagement`, `fin query`, `fin adjust`, `fin gate`, `fin ingest --kind`,
    `fin map ledgers` with `fin review --ledgers`, `fin reconcile` (with `fin review --matches`) and
    `fin recast`. Nothing was run."""

    code = "E806"


class LayoutUnknown(FinError):
    """E807. `fin ingest --kind`: the column profile named is unknown or invalid, or no profile of
    the kind matches the file's headers (the message lists the header lines found, to write a
    profile from). Nothing loaded."""

    code = "E807"


class SourcePeriodInvalid(FinError):
    """E808. `fin ingest --kind`: the export's header lines give no period, or the period is not an
    annual period of the open engagement nor exactly its stub. Nothing loaded. Also `fin engagement
    open --stub`: the stub is not a year-to-date period of the fiscal year after the last annual
    period. Nothing opened."""

    code = "E808"


class GroupsOnlyTrialBalance(FinError):
    """E809. A trial balance with group rows only (Tally's Condensed export): ledgers are needed, so
    export it again in Detailed mode. Nothing loaded."""

    code = "E809"


class MaskedTableRead(FinError):
    """E810. `fin sql` was given SQL naming `mask_values` (the real values behind masked tokens),
    or calling query, query_table or read_duckdb, which could reach it: fin never prints them.
    Nothing was run."""

    code = "E810"


class TrialBalanceNeeded(FinError):
    """E811. Ledger Vouchers were ingested before the trial balance of their period: the trial
    balance says which ledgers are parties, whose names are masked. Nothing loaded."""

    code = "E811"


class ReconNotPossible(FinError):
    """E812. `fin reconcile`: the period is not an annual period of the engagement (the stub has
    no audited statement), or it has no loaded trial balance or no audited standalone statement
    figures. Nothing recorded."""

    code = "E812"


class RegisterLineInvalid(FinError):
    """E813. A registers workbook row cannot be loaded as an invoice line: no invoice number, date,
    party or Amount in Rs, a date that is not in the profile's date format, or a row that is not a
    `skip` row (a total). Nothing loaded: fix the cell, or add the row's name to [rows] skip in the
    profile."""

    code = "E813"


class BankPdfUnreadable(FinError):
    """E816. A PDF bank statement cannot be read: `[pdf] parser` is none (a bank PDF needs Docling,
    LiteParse or LlamaParse), the parser could not read a page, or no table on it has the columns
    of a bank profile's header. Nothing loaded."""

    code = "E816"


class BankHeaderMissing(FinError):
    """E817. A bank statement's header lines give no account number (the profile's `[header]
    account` pattern finds none), or no opening balance when the first row has no balance to
    stand in for it. Nothing loaded."""

    code = "E817"


class BankParseUnreadable(FinError):
    """E818. The saved parse of a bank PDF (`cache/bank/<sha256>.json`) cannot be read. Nothing
    loaded; delete the file to parse the PDF again."""

    code = "E818"


class ReconCannotRun(FinError):
    """E840. A reconciliation (`fin reconcile`: sales_register_vs_gl, purchase_register_vs_gl,
    gl_vs_tb, payroll_vs_gl) cannot run: a period not in the engagement, or an input is not loaded
    (Ledger Vouchers, registers covering the period, a trial balance, a payroll file with its unit
    confirmed annual) or no ledger is confirmed to the account the books side reads. Nothing
    recorded."""

    code = "E840"


class ReconItemUnknown(FinError):
    """E841. `fin query raise --items` names a reconciliation line that does not exist, belongs
    to another engagement, or is not in the latest run of its reconciliation (the data has been
    reconciled again since). Nothing raised."""

    code = "E841"


class ReconItemNotQueryable(FinError):
    """E842. `fin query raise --items` names a line that is not a difference at or above the
    threshold (matched, fx, not reconciled, unmapped, a month or party roll-up or below the
    threshold), a line already in a query, or lines of different periods. Nothing raised."""

    code = "E842"


class PayrollFileNotFound(FinError):
    """E820. `fin payroll confirm-unit` names no loaded payroll file of the open engagement (an
    unknown name or id, not a payroll file, or one a later snapshot superseded). Nothing
    recorded."""

    code = "E820"


class PayrollRowInvalid(FinError):
    """E821. A payroll snapshot row has amounts or other cells but no employee name or code, or a
    joining or resign date that is not a date in the profile's format. Nothing loaded: fin cannot
    mask or trace a row it cannot identify, and never drops one."""

    code = "E821"


# E83x: golden evaluation (fin.evals, docs/EVAL.md). Stop the command.


class GoldenSetMissing(FinError):
    """E830. `fin eval run`: the company has no golden set (`evals/<company>/golden.csv`); make one
    with `fin eval init <company>`."""

    code = "E830"


class GoldenSetInvalid(FinError):
    """E831. The golden set cannot be read: a missing column, an unknown kind, scope, account or
    unit, a figure that is not a number, a checked line with no expected value or source, or the
    same check twice. The message lists every line at fault. Nothing scored or changed."""

    code = "E831"


class NothingToBundle(FinError):
    """E832. `fin eval bundle`: no company database to evaluate (or the company named has none).
    Nothing written."""

    code = "E832"


class BundleNotAnonymous(FinError):
    """E833. `fin eval bundle`: the results would contain a real name (a masked party, a company
    or a file name). The name is not printed. Nothing written."""

    code = "E833"


class PassthroughInvalid(FinError):
    """E845. `fin engagement passthrough`: a customer token that no loaded sales register line has
    (or no sales register is loaded, so there is nothing to check it against). Nothing changed."""

    code = "E845"


# E85x: the query export (fin.diligence.export). Stop the command.


class NothingToExport(FinError):
    """E858. `fin query export`: the open engagement has no approved query. Nothing written."""

    code = "E858"


class ExportFileError(FinError):
    """E859. `fin query export`: a source workbook an issue cites cannot be read from the archive,
    or the export folder cannot be written. Nothing is left in the export folder."""

    code = "E859"


class ReplyFileError(FinError):
    """E860. `fin query answer --file`: the file is missing or cannot be read or archived, or it is
    not plain text (.txt, .eml, .md) and no --text says what it answers. Nothing recorded."""

    code = "E860"


class UnknownToken(FinError):
    """E861. `fin query export`: a query's wording or a line names a token (CUST-0001, ...) that
    has no real value in the company's masked names, so it cannot be written as a name. The token
    is named; nothing written. Edit the draft (or re-raise it) with the right token."""

    code = "E861"


# E854-E855: bank matching (fin.diligence.bank_proposals). Stop the command.


class MatchProposalInvalid(FinError):
    """E854. `fin reconcile match --propose`: the file is not a JSON list of matches, or a match
    has no bank lines or invoices, names a line that is not a loaded bank credit or sales invoice
    line, or lacks a reason. Nothing stored."""

    code = "E854"


class MatchChangeRefused(FinError):
    """E855. `fin review confirm|reject --matches`: no match proposal with that id, or it is no
    longer waiting. Nothing changed."""

    code = "E855"


class CustomerMappingInvalid(FinError):
    """E868. `fin map customers` or `fin review ... --customers`: a proposal that is not an object
    with customer, sector, confidence and reason, a customer that is on no loaded sales register, a
    sector that is empty or 'unmapped', a confirmation with no sector, or an unknown id. Nothing
    changed."""

    code = "E868"


class AnalyticsUnavailable(FinError):
    """E869. `fin analytics`: the company has no engagement, or no sales register loaded for it, so
    there is nothing to analyse. Nothing changed."""

    code = "E869"


# E863-E865: the service-period recast (fin.reconcile.recast, `fin recast`). Stop the command.


class RecastCannotRun(FinError):
    """E863. `fin recast run`: a period that is not an annual period of the open engagement, or no
    loaded sales register line to recast. Nothing recorded."""

    code = "E863"


class ContractReadingInvalid(FinError):
    """E864. `fin recast contracts`: the reading CSV cannot be used (missing column, bad date,
    amount or page, end before start, no rows). The message names each CSV line at fault. Nothing
    recorded."""

    code = "E864"


class ContractNotOnRegister(FinError):
    """E865. `fin recast contracts`: a contract names a customer or an invoice number that no
    loaded sales register has (the name is not printed). Nothing recorded."""

    code = "E865"


# E873-E874: the diligence report (fin.report.dd_render). Stop the command.


class DiligenceReportUnavailable(FinError):
    """E873. `fin report --diligence`: the company has no engagement, so there is nothing to
    report. Nothing written."""

    code = "E873"


class FigureReferenceUnknown(FinError):
    """E874. `fin report --diligence`: the narrative has a `{{figure:name:period}}` reference to a
    figure the report does not have, or in a period outside the engagement. Nothing rendered."""

    code = "E874"
