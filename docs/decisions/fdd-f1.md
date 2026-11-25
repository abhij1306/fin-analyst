# F1: contract terms and deal walkthroughs

Builder readings of `docs/fdd.md` F1; the owner confirms or changes them. Format: [README.md](README.md).

## 1. Contracts load through `fin recast contracts`, vendors included

- **Decision**: the existing command takes the new columns (party kind, terms) instead of a second `fin contracts load`. `customer` is still
  read as the counterparty column. A vendor contract is checked against the purchase register's vendors and invoice numbers, a customer
  contract against the sales register's. The diligence report's "contracts read" table lists customer contracts only.
- **Why**: one loader, no second command to keep in step; the plan allowed either. The recast keeps naming only customer contracts, so a vendor
  contract never appears beside a sales invoice.
- **How to change it**: `reconcile/contracts.py` (`REGISTER`, `load`), `report/dd_data.py` (`_CONTRACTS`).

## 2. A term that was not read is NULL, not "no clause"

- **Decision**: an empty term cell stores NULL and shows as "not read". "No" is stored only when the reading says no. Coverage shows
  auto-renew, change of control and lock-in as "not read" for NULL.
- **Why**: CLAUDE.md forbids filling gaps with assumptions; a missing clause and an absent one are different findings in diligence.
- **How to change it**: `reconcile/contract_terms.py` (`parse_terms`), `reconcile/contract_coverage.py`.

## 3. How coverage is read

- **Decision**: "latest period" is the engagement's last annual period (not its stub); customers are ranked by core revenue (Amount in Rs,
  pass-through customers left out) and vendors by purchases, within that period. If a party has several contracts, the one that ends last
  is shown, and the count. "Expired" means it ended before the period end; "expires within 12 months" means it ends from the period end
  to 12 months after it (a contract that ended earlier is expired, not "expiring"). Lock-in left is whole months from the period end to
  start + lock-in months (0 when over). The period end follows the engagement's fiscal start month. `[fdd] coverage_top_n` (default 10) is
  how many of each are shown.
- **Why**: the plan says "latest period" and "after the last period end" without defining either; the last annual period is the one the
  audited statements and the other diligence views use. A period with no register lines gives no rows (the command says to load the registers).
- **How to change it**: `db/schema/fdd_contracts.sql` (view `contract_coverage`), `config.toml [fdd] coverage_top_n`.

## 4. How the sample is drawn

- **Decision**: the threshold is the engagement's query threshold, compared with the line's Amount in Rs without its sign (a credit note counts by
  size). Lines at or above it come first, largest first (ties by register position), up to n; if more than n are at or above it, the largest n
  are the sample and the sample says how many were left out. Only then are lines below the threshold drawn at random until the sample has n lines. The
  seed is made at random when not given, and stored, so the same register and seed give the same lines.
- **Why**: plan F1 ("every line at or above the threshold (largest first, up to n), then the rest drawn at random with a seed stored").
- **How to change it**: `sampling/draw.py` (`choose`).

## 5. Walkthrough checks and what becomes a query

- **Decision**: five fixed checks (the line is in the register; a contract covers it and its rate, dates and value agree; the service period
  is inside the contract; a bank receipt or payment is matched; the ledger posting is found), each pass, exception or not_available, with
  evidence text required. Claude does the checking; fin stores it and does not verify it. One reconciliation line (`walkthrough`) per sample line
  with at least one exception, with the line's Amount in Rs and the failed checks in its note: at or above the threshold it is grouped into a
  query by Claude, below it it is listed. Recording the same line and check again replaces it. An exception is the company's difference, so it is
  queried, never fixed. Because it is a registered reconciliation, gate G2 requires every such line to be in a query once a walkthrough is recorded
  for the period.
- **Why**: plan F1 ("an exception at or above the threshold is a recon item for a query") and the diligence rule that differences are queried.
  The amount of a line with two exceptions is counted once. `not_available` is neither pass nor exception: it is counted and shown.
- **How to change it**: `sampling/walkthrough.py` (`CHECKS`), `reconcile/walkthrough.py` (`compute`); to leave G2 alone, take `walkthrough` out of `RECONCILIATIONS`.

## 6. Golden eval kinds

- **Decision**: `contract_value` (a contract's value as Claude read it) and `sample_exception_amount` (the Amount in Rs of a sample's lines with
  an exception) are the two figures a person can check against the documents. Coverage and sample picks are not golden figures: coverage depends on
  the user's `coverage_top_n` and a random pick has no right answer.
- **Why**: the plan asks for a golden kind per figure a person can check.
- **How to change it**: `evals/fdd_contracts.py`.
