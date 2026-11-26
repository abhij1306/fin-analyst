# FDD F4: net debt and debt-like items

Builder's readings of [fdd.md](../fdd.md) F4, not owner decisions. Scope readings are in
[fdd-scope.md](fdd-scope.md).

## One class per ledger, for every period end

- **Decision**: a ledger has one net debt class (cash, debt, debt_like or none) that applies at every period end of
  the engagement, not one class per period.
- **Why**: the plan says "each trial balance ledger at a period end gets a class". A ledger's nature (a term loan, a
  gratuity provision) does not change between year ends, and one class per ledger keeps the review list to one line
  per ledger. A ledger whose nature did change can be split by the company into two ledgers, or handled as an item.
- **How to change it**: `db/schema/fdd_netdebt.sql`: `netdebt_classes` (add a period column) and
  `netdebt_ledger_classes`.

## Default classes

- **Decision**: a ledger confirmed to `lt_borrowings`, `st_borrowings`, `borrowings`, `debt_securities`,
  `subordinated_liabilities` or a lease liability account defaults to debt; one confirmed to `cash` or
  `cash_and_bank_balances` defaults to cash; one confirmed to a profit and loss account
  defaults to none. Every other ledger (other balance sheet accounts, and ledgers with no confirmed account) is
  unclassified until the user confirms a class. A confirmed class wins over the default; rejecting it restores the
  default. Other bank balances (fixed deposits, earmarked or margin money) have no default: they stay unclassified
  until the user confirms a class ([fdd-review-fixes.md](fdd-review-fixes.md), orchestrator's reading). NEEDS
  CLIENT CONFIRMATION (whether restricted bank balances are cash).
- **Why**: the plan names borrowings and leases as debt and cash and bank as cash. Profit and loss ledgers carry no
  period-end balance to classify, so leaving them unclassified would only add review noise. Every other balance sheet
  ledger may be debt-like (provisions, statutory dues, tax payable, capex creditors, advances), so it is left for
  Claude to propose and the user to decide rather than defaulted to none.
- **How to change it**: `db/schema/fdd_netdebt.sql`: `netdebt_default_accounts`, `netdebt_ledger_classes`.

## Signs and the net debt figure

- **Decision**: cash is a ledger's closing debit less credit; debt and debt-like are credit less debit, so each reads
  positive in the usual case (an overdrawn bank ledger is negative cash, a debit balance in a debt-like ledger
  lowers debt-like). Net debt = debt + debt-like − cash, where debt-like is the debt-like ledgers plus the accepted
  items. An item's amount is signed: positive raises net debt.
- **Why**: the usual FDD definition; an overdraft netted in a cash ledger stays in it rather than being moved to debt
  (moving it is the user's call: confirm the bank ledger as debt).
- **How to change it**: `db/schema/fdd_netdebt.sql`: `net_debt_lines`, `net_debt`.

## Missing data and incomplete classification

- **Decision**: a period of the engagement with no loaded trial balance has NULL cash, debt and debt-like (shown as
  "no trial balance loaded; net debt not computed"), never 0. Net debt is computed while some ledgers are still
  unclassified, with their count (and how many have a proposal waiting) shown beside it as "net debt may be
  incomplete".
- **Why**: CLAUDE.md: never fill gaps. Hiding the figure until every ledger is classified would block the view on
  ledgers that do not matter; showing the count keeps the gap visible, as `nwc_year_end` does with unmapped
  ledgers.
- **How to change it**: `db/schema/fdd_netdebt.sql`: `net_debt`.

## Items: no gate, no recon item, evidence as a reference

- **Decision**: a debt-like item is proposed with a period (a year of the open engagement or its stub), a non-zero
  amount, a description and an evidence reference (free text: a query id, a fin output, file + page), with known
  names replaced by their tokens. The user accepts or rejects it (a reason is required to reject). Unlike adjustments,
  accepting or rejecting does not need gate G2. Since the review fixes, G4 needs no proposed item and no proposed
  class, and a change to them reopens it ([fdd-review-fixes.md](fdd-review-fixes.md)). Net debt is not a comparison of two
  sources, so it writes no `recon_runs` / `recon_items` and drafts no query; a difference Claude finds while
  classifying is raised as a query the usual way.
- **Why**: the plan says items are accepted or rejected "as for adjustments" (Claude proposes, the user decides),
  but adds no gate; G2 protects EBITDA adjustments from draft queries, which net debt items do not depend on. The
  rule on recon items applies to new checks that compare two sources.
- **How to change it**: `src/fin/diligence/netdebt_items.py`: `propose`, `decide`; gates in
  `src/fin/diligence/gates.py`.

## Review through `fin review --netdebt`; the plan's `fin netdebt <co>` is `fin netdebt show <co>`

- **Decision**: class proposals are listed, confirmed and rejected with `fin review list|confirm|reject <co> --netdebt`
  (as the plan says); `fin review confirm --netdebt` takes no other class: to change one, Claude proposes again (or
  the user rejects and Claude re-proposes). The plan's `fin netdebt <co> [--json]` is `fin netdebt show <co>
  [--period] [--json]`, since `fin netdebt` is a command group (propose, item ...).
- **Why**: a group cannot also take a company argument without clashing with its subcommands; F3 did the same
  (`fin provisions show`).
- **How to change it**: `src/fin/cli/netdebt.py`; `src/fin/map/commands.py`.

## Golden kind

- **Decision**: golden kind `net_debt` with `ledger` cash, debt, debt_like or net_debt, per period with a trial
  balance. A person checks cash and debt against the balance sheet and the trial balance.
- **Why**: the figures a buyer reads first; each is a sum of trial balance rows the person can see.
- **How to change it**: `src/fin/evals/netdebt.py`.
