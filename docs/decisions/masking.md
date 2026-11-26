# Decisions: masking scope

## Masking scope

- **Decision (the owner, 2026-11-26):** "the client has ZDR enabled so there is no concern of claude reading the data
  or cloud", then "whatever the client said in his instructions stays only wherever u masked for claude goes away".
  So fin masks only what the client asked for:
  - **payroll:** employee name and code (`EMP-0001`, `ECODE-0001`); designation, department and the like stay as
    filed;
  - **the starred register fields:** sales Customer Name, handler and submitted-by; purchase Vendor Name
    (`CUST-0001`, `USER-0001`, `VEND-0001`). The orderbook's customer gets the same token as the registers, so the
    orderbook and the registers stay one customer.

  Names are stored once in `mask_values`; these fields carry the token in the data tables, `fin` output and logs,
  and their real values come back only in `fin query export`, for the accountant. The same names can still appear as
  filed or written where masking was dropped (below): the books, bank text and free text. `fin sql` refuses `mask_values` (E810).
- **Why it stays:** the client's own instruction (answers of 2026-11-24): anonymise payroll before any third-party
  processing (mask name and code at minimum), and mask the identifier fields they starred.
- **What was dropped, and why:** our own reading that Claude counts as third-party processing, which made fin mask
  far more than the starred fields. The client has zero data retention with Anthropic, so the owner dropped it. No
  longer masked, stored and printed as filed or written:
  - Tally party ledgers (the ledgers under Sundry Debtors and Sundry Creditors) and Ledger Vouchers ledgers and
    particulars; a Ledger Vouchers ledger not in its trial balance is no longer `LEDG-0001`;
  - bank remarks and reference (no more replacing known names inside them);
  - the free-text register columns (location, order type, product, service type);
  - the free text fin stores from Claude or the accountant: FDD finding text, query answers and finding responses,
    contract terms, walkthrough evidence, disclosure nature and relationship, net debt item text and the reasons
    stored with proposals (the old `FREE_TEXT_COLUMNS` and the re-masking when a later load learned a name);
  - disclosure counterparties no loaded file had (no more `PRTY-0001`).
- **Party matching:** the registers carry tokens and the books and bank statements carry names, so fin matches them
  through `mask_values` (`fin.diligence.mask.Parties`: by the name ignoring case and spaces; in bank text, as whole
  words): the register vs ledger reconciliations (a voucher's particulars to the register's party), bank vs invoices
  (a receipt's customer named in its reference or remarks, a Ledger Vouchers receipt's ledger), contracts (a
  counterparty given by name), disclosures (a counterparty that is a register customer or vendor gets its token; one
  that is a loaded ledger's name becomes that ledger) and the view `related_party_matches` (a ledger meets a token
  through the masked name it is). Customer revenue, concentration and retention read the registers only (tokens).
  Because a matched line shows the token beside the ledger's name, the token of a matched party can be read off fin's
  output. This follows from the decision above (the books stay as filed); it is a reading of that decision, not an
  owner quote, so the owner or the client may still ask for it to change ([client question 2](../client-questions.md)).
- **Still anonymous: the eval bundle.** `fin eval bundle` goes to the fin developers, whom the client's ZDR does not
  cover: companies as `company-N`, ledgers as `ledger-N`, no file names; E833 now also refuses a ledger name or a
  related party name, since ledgers are no longer tokens.
- **Open with the client:** [Client question 1](../client-questions.md) (mask more: locations, product names, account numbers; option 3 below)
  and [client question 2](../client-questions.md) (a token shown beside a ledger name). LlamaParse, the optional cloud PDF
  add-on, sees pages unmasked; the owner allowed it with a warning in the README; Docling and LiteParse run locally.

## Options

1. **As decided above** (the client's fields only).
2. **Mask nothing for Claude; only before other third parties.** `Masker.token` in `src/fin/diligence/mask.py` would
   return the real value and the E810 refusal in `src/fin/query/run.py: refuse_masked` would go. Against the client's
   instruction.
3. **Mask more** (locations, product names, account numbers). Registers: `MASKED` in
   `src/fin/diligence/registers.py`; payroll: `payroll.rows` in `src/fin/diligence/payroll.py`; bank account number:
   `bank.py: _insert`; a new kind needs a prefix in `mask.py: PREFIX`. Masking a name in the books again would need
   party matching to look up both sides by token.

## How to change it

The files named in each option; party matching is `mask.py: Parties` and its callers (`reconcile/registers.py`,
`reconcile/bank_lines.py`, `disclosures/load.py`, `reconcile/contracts.py: _token`) and the view
`related_party_matches` in `db/schema/fdd_disclosures.sql`. Existing data must be loaded again (no migrations).
