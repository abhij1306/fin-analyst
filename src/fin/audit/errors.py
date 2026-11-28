"""Error classes with their codes (the table is in docs/errors.md), and exit codes.

Every failure path raises a subclass of `FinError`. Each class carries its code and the exit code
a command returns when the error ends it:

- E0xx (setup, including E006-E008 of the web UI), E5xx (database), E601-E603 (outputs), E406
  (accepting a finding that is not open), E407-E408 (a figure the user states that cannot be
  stated or withdrawn) and E409 (a figure that cannot be corrected) stop the command: exit 2.
  E505 (`fin sql --write` on facts, raw_values, stated_figures or fact_corrections) is a
  database error like the other E5xx.
- E1xx-E3xx are per-document failures and E604 is flagged in the evidence page. The command
  completes and reports them: exit 1.
- E012 is logged for an exception with no error class (`Run.log_unexpected`); it is not raised.
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
- E845-E846 (`fin engagement passthrough`: a customer token that is not on a loaded sales register;
  credit terms that are not whole days from 0 to 3650) stop the command: exit 2.
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
- E9xx (the FDD extension) stop the command: exit 2; each range is described in
  `fin.audit.errors_fdd`.
- E814, E815 and E819 (data-room intake, fin.dataroom: a folder that cannot be scanned, a company
  with no data-room plan, a plan row that cannot be approved or given that kind) stop the command:
  exit 2.
- E830-E833 (golden evaluation, fin.evals: no golden set, a golden set that cannot be read, nothing
  to bundle, a bundle that would contain a real name) stop the command: exit 2.
"""

from typing import Self

# Exit codes
EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_COMMAND_ERROR = 2
EXIT_BLOCKED = 3  # the CLI refused: the action needs the user's confirmation


class FinError(Exception):
    """Base error. Every failure path raises a subclass carrying its code."""

    code: str = ""
    exit_code: int = EXIT_COMMAND_ERROR
    context: dict[str, object] | None = None  # what was seen, for the event's context in the log

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def with_context(self, **context: object) -> Self:
        self.context = context
        return self

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


class UiUnavailable(FinError):
    """E006. `fin ui`: the web UI add-on is not installed, or its port is in use."""

    code = "E006"


class UiRequestRefused(FinError):
    """E007. The UI server refused a request: a Host that is not localhost or 127.0.0.1, a missing
    or wrong token, or a state-changing request that does not come from the UI's own page."""

    code = "E007"


class UiActionInvalid(FinError):
    """E008. The UI refused a request to change data before it ran: an unknown action, a missing
    reason, or an upload that is too large, has an unsafe or unsupported name or exists already."""

    code = "E008"


class ChatUnavailable(FinError):
    """E009. The UI chat cannot run Claude Code: `claude` is not installed or cannot start, or it
    stopped with an error."""

    code = "E009"


class ChatNotLoggedIn(FinError):
    """E010. Claude Code is installed but not logged in (the chat uses the user's own login)."""

    code = "E010"


class ChatBusy(FinError):
    """E011. A turn of this conversation is still running: wait for it or press Stop."""

    code = "E011"


class UnexpectedError(FinError):
    """E012. An exception with no error class: a defect in fin. Logged with its traceback."""

    code = "E012"


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


class ValueOutOfRange(DocumentFailure):
    """E890. A figure in the document is too large to store (10**16 rupees or more): a misread
    cell or a wrong unit. The file goes to review/ with the cell named; the next file loads."""

    code = "E890"


class LoadRejected(DocumentFailure):
    """E891. The database refused a document's rows (a value or text it cannot store). The
    document is rolled back and goes to review/; the next file loads."""

    code = "E891"


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


class FigureCannotBeStated(FinError):
    """E407: `fin fact state`: an account not in the chart of accounts, a period that is not FYnn,
    FYnn-Q1..Q4, FYnn-H1/H2 or FYnn-9M, a value that is not a number with at most two decimals or is
    10^16 or more, an empty reason, or a figure already stated for the account, period and scope.
    Nothing is stated."""

    code = "E407"


class StatedFigureUnknown(FinError):
    """E408: `fin fact unstate` named no stated figure of the company (an unknown id, or one
    already withdrawn). Nothing is changed."""

    code = "E408"


class FigureCannotBeCorrected(FinError):
    """E409: `fin fact correct`: an unknown fact, a fact of a superseded document, a value that is
    not a number with at most two decimals or is 10^16 or more, the value the fact already has, an
    empty reason, or, inside a diligence engagement, a figure under a query or an adjustment (a
    difference is queried, never fixed). Nothing is corrected."""

    code = "E409"


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


class FigureTableWrite(FinError):
    """E505. `fin sql --write` was given a statement other than a SELECT that names facts,
    raw_values or stated_figures: figures change only by loading documents, mapping their lines
    (`fin review`) or stating a figure (`fin fact state`). Nothing was run."""

    code = "E505"


class SavedMetricInvalid(FinError):
    """E506. `fin metric add`: a name that is not lower-case letters, digits and '_' or is
    taken (a built-in or saved metric, or an account), an unknown unit, an empty definition, or a
    formula that does not parse or names something that is not an account or metric. Nothing
    saved. Also a new company database whose shared saved metric no longer compiles."""

    code = "E506"


class SavedMetricUnknown(FinError):
    """E507. `fin metric remove`: no saved metric of that name where it was looked for (the
    company's, or the shared ones), or another saved metric's formula uses it. Nothing removed."""

    code = "E507"


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
    recon_items, passthrough_customers, recon_match_lines, match_proposals, contracts, samples,
    sample_lines, walkthroughs, provision_ledgers, inquiries, orderbook_lines, engagement_rates,
    gst_returns, tax_ledgers, disclosure_files, disclosures, related_party_links, netdebt_classes,
    netdebt_items, fdd_findings): those change only through `fin engagement`, `fin query`,
    `fin adjust`, `fin gate`, `fin ingest --kind`, `fin map ledgers|tax` with `fin review`,
    `fin reconcile` (with `fin review --matches`), `fin recast`, `fin sample`, `fin walkthrough`,
    `fin disclosures` (with `fin review --related`), `fin provisions`, `fin netdebt` and
    `fin fdd finding`. Nothing was run."""

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
    """E811. Ledger Vouchers were ingested before the trial balance of their period: their
    ledgers are checked against the trial balance's. Nothing loaded."""

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
    """E818. The saved parse of a bank PDF (`cache/bank/<company>/<sha256>.json`) cannot be read,
    or holds a token the company has no number for. Nothing loaded; delete it to parse again."""

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


# E830-E874: in fin.audit.errors_dd (split for size), re-exported here.
# E814, E815, E819: data-room intake, in fin.audit.errors_dataroom (split for size), re-exported.
from fin.audit.errors_dataroom import (  # noqa: E402, F401
    DataroomFolderInvalid,
    DataroomPlanMissing,
    DataroomRowInvalid,
)
from fin.audit.errors_dd import (  # noqa: E402, F401
    AnalyticsUnavailable,
    BundleNotAnonymous,
    ContractNotOnRegister,
    ContractReadingInvalid,
    CreditTermsInvalid,
    CustomerMappingInvalid,
    DiligenceReportUnavailable,
    ExportFileError,
    FigureReferenceUnknown,
    GoldenSetInvalid,
    GoldenSetMissing,
    MatchChangeRefused,
    MatchProposalInvalid,
    NothingToBundle,
    NothingToExport,
    PassthroughInvalid,
    QueryPeriodInvalid,
    RecastCannotRun,
    ReplyFileError,
    UnknownToken,
)

# E9xx: the FDD extension, in fin.audit.errors_fdd (split for size), re-exported here.
from fin.audit.errors_fdd import (  # noqa: E402, F401
    CutoffCannotRun,
    DisclosureReadingInvalid,
    FddFindingInvalid,
    FddFindingUnknown,
    GstReturnInvalid,
    InquiryInvalid,
    NarrativeJudges,
    NetDebtClassInvalid,
    NetDebtClassUnknown,
    NetDebtItemInvalid,
    NetDebtItemUnknown,
    NetDebtUnavailable,
    OrderbookRowInvalid,
    ProvisionLedgerInvalid,
    ProvisionLedgerUnknown,
    ProvisionsUnavailable,
    RateInvalid,
    RelatedPartyLinkInvalid,
    SampleCannotDraw,
    SampleUnknown,
    TaxLedgerInvalid,
    VisibilityUnavailable,
    WalkthroughInvalid,
)
