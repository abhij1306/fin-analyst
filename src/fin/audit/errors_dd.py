"""The diligence error classes from golden evaluation to the diligence report (E830-E874; the
table is in docs/errors.md), split from `fin.audit.errors` for size. They are re-exported by
`fin.audit.errors`, so import them from there like every other error."""

from fin.audit.errors import FinError

# E83x: golden evaluation (fin.evals, docs/client-handoff.md). Stop the command.


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
    """E833. `fin eval bundle`: the results would contain a real name (a masked party, a ledger,
    a related party, a company or a file name). The name is not printed. Nothing written."""

    code = "E833"


class PassthroughInvalid(FinError):
    """E845. `fin engagement passthrough`: a customer token that no loaded sales register line has
    (or no sales register is loaded, so there is nothing to check it against). Nothing changed."""

    code = "E845"


class AdjustmentImportInvalid(FinError):
    """E847. `fin adjust import`: the walk sheet cannot be read (not CSV or Excel, no such sheet,
    a missing or unknown column, no rows), or a row is not a valid adjustment. The message names
    the row. Nothing imported."""

    code = "E847"


class CreditTermsInvalid(FinError):
    """E846. `fin engagement open --due-days` or `fin engagement terms`: the credit terms are not
    whole days from 0 to 3650. Nothing changed."""

    code = "E846"


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
    """E861. A query's wording or a line names a token (CUST-0001, ...) that has no real value in
    the company's masked names: refused when the query is raised or edited (nothing raised), and by
    `fin query export` (nothing written). The token is named."""

    code = "E861"


class QueryPeriodInvalid(FinError):
    """E881. `fin query raise --period`: a period that is not a year of the open engagement nor
    its stub. Nothing raised."""

    code = "E881"


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
