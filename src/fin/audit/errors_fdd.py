"""The FDD extension's error classes (E9xx, docs/fdd.md; the table is in docs/errors.md). They are
re-exported by `fin.audit.errors`, so import them from there like every other error."""

from fin.audit.errors import FinError, ReconCannotRun

# E901-E903: FDD contracts and walkthroughs (fin.sampling, docs/fdd.md F1). Stop the command.


class SampleCannotDraw(FinError):
    """E901. `fin sample draw`: a period that is not a period of the open engagement, a size below
    1, or no loaded register line of the kind in the period. Nothing recorded."""

    code = "E901"


class WalkthroughInvalid(FinError):
    """E902. `fin walkthrough record`: the file is not a JSON list of results, or a result has a
    line_no not in the sample, a check or result that is not one of the allowed ones, no evidence,
    a bad page, or repeats a line and check. The message names each result at fault. Nothing
    recorded."""

    code = "E902"


class SampleUnknown(FinError):
    """E903. `fin sample show` or `fin walkthrough record`: no sample with that id in the open
    engagement. Nothing changed."""

    code = "E903"


# E910-E919: FDD F2, management inquiries (fin.diligence.inquiries). Stop the command.


class InquiryInvalid(FinError):
    """E910. `fin query raise --topic` or `fin query checklist`: a topic that is not one of
    config.toml [fdd] inquiry_topics, or a templates/fdd/inquiries.toml that cannot be used (a
    topic not in inquiry_topics, or a topic with no list of questions). Nothing raised."""

    code = "E910"


# E920-E929: FDD F3, provisions and cut-off (fin.diligence.provisions, fin.reconcile.cutoff).


class ProvisionLedgerInvalid(FinError):
    """E920. `fin provisions propose`: the file is not a JSON list of {ledger, kind, reason}, a
    ledger is not in a loaded trial balance, the kind is not doubtful_debts or gratuity, the reason
    is empty, or the ledger is already confirmed (only the user changes it). Nothing stored."""

    code = "E920"


class ProvisionLedgerUnknown(FinError):
    """E921. `fin provisions confirm|reject`: no provision ledger with that id, or it is not
    waiting for that decision (confirm needs a proposal; reject a proposal or a confirmed one).
    Nothing changed."""

    code = "E921"


class CutoffCannotRun(ReconCannotRun):
    """E922. `fin reconcile sales_cutoff|purchase_cutoff`: the period is not an annual period of
    the open engagement, or no loaded register has lines of that kind in the window around its
    year end. Nothing recorded. A kind of E840, so `fin reconcile` without a name lists it as not
    run."""

    code = "E922"


class ProvisionsUnavailable(FinError):
    """E923. `fin provisions show`: the company has no engagement, or a period that is not one of
    the latest engagement's annual periods. Nothing shown."""

    code = "E923"


# E960-E969: FDD F7, orderbook and revenue visibility (fin.diligence.orderbook,
# fin.reconcile.visibility).


class OrderbookRowInvalid(FinError):
    """E960. `fin ingest --kind orderbook`: a row with other cells but no customer or order number,
    no order value, or a date or amount that cannot be read. Nothing loaded."""

    code = "E960"


class VisibilityUnavailable(FinError):
    """E961. `fin visibility`: no orderbook, no billed-not-earned line and no customer contract
    is loaded for the latest engagement. Nothing shown."""

    code = "E961"


# E950-E959: GST and income tax (fin.fdd, docs/fdd.md F6). Stop the command.


class GstReturnInvalid(FinError):
    """E950. `fin ingest --kind gst`: a GST return row whose month cannot be read, a month outside
    the file's period, the same month twice, or no month rows. Nothing loaded."""

    code = "E950"


class TaxLedgerInvalid(FinError):
    """E951. `fin map tax` or `fin review ... --tax`: a proposal that is not an object with
    ledger, class, confidence and reason, a ledger no trial balance fin loaded has, an unknown
    class, a confirmed ledger proposed again, a confirmation with no class, or an unknown id.
    Nothing changed."""

    code = "E951"


# E940-E949: FDD disclosures and related parties (fin.disclosures). Stop the command.


class DisclosureReadingInvalid(FinError):
    """E940. `fin disclosures load`: the reading CSV cannot be used (a missing column, an unknown
    kind or books, a period not of the open engagement, a bad amount, unit or page, a related party
    row without its counterparty, amount or relationship, no rows), or a ledger it names is in no
    loaded file. The message names each CSV line at fault. Nothing recorded."""

    code = "E940"


class RelatedPartyLinkInvalid(FinError):
    """E941. `fin disclosures related` or `fin review ... --related`: a proposal that is not an
    object with party, kind, target, confidence and reason, a party that is not a related party of
    the open engagement, a target that is not a loaded customer, vendor or ledger, a link fin
    already matched, or an unknown link id. Nothing changed."""

    code = "E941"
