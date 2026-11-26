# Decisions: final-fixes (the verified audit of main at 5ac8b49)

Builder readings, not owner decisions. Owner source for the workstream: the owner's brief of 2026-11-25 (ship the
FIX items of the verified audit plan; client-level choices go to the client list below).

## Names learned from a later file are masked in the free text already stored

- **Superseded 2026-11-26** by [masking.md](masking.md): free text is no longer masked, so nothing is masked again.

- **Decision:** When a load adds names to `mask_values`, the same transaction masks those names in the free text
  already stored: bank remarks and reference, Ledger Vouchers particulars, register text fields (location, order
  type, product, service type) and contract billing terms. Only values that change are updated. The load summary
  says how many earlier values changed ("N earlier free-text values masked with the new names").
- **Why:** A salary remark naming an employee stayed readable if the bank statement loaded before the payroll file.
  The masking rule is "no real name goes to Claude", so the result must not depend on load order.
- **How to change it:** `src/fin/diligence/mask.py: FREE_TEXT_COLUMNS, Masker.remask`; the call sites in
  `diligence/bank.py: ingest_bank` and `diligence/sources.py: ingest_source`.

## An employee code is never searched for in free text

- **Superseded 2026-11-26** by [masking.md](masking.md): free text is no longer searched for names at all.

- **Decision:** Free text is searched for known names (customers, vendors, users, employee names, ledgers), not for
  employee codes. Payroll still stores each code as a token (`ECODE-0001`), and a cell that is exactly a known code
  is still looked up with `Masker.known`.
- **Why:** Codes can be short numbers ("101", "7"); searching free text for them rewrote amounts and dates in
  remarks and particulars.
- **How to change it:** `src/fin/diligence/mask.py: Masker.text` (`self.codes`).

## `fin sql` cannot read files, and `--write` cannot change the schema

- **Decision:** `fin sql` (read and write) runs with file access closed: `read_csv`, `read_blob`, `glob`, COPY of
  a file and ATTACH of a file fail with E504, so the original files, logs and exports cannot be read through SQL.
  `fin sql --write` also refuses CREATE, DROP, ALTER, PRAGMA, SET, EXPORT and VACUUM (E504, before the backup).
  Data changes (INSERT, UPDATE, DELETE) work as before.
- **Why:** Names are masked in the tables, but the archived originals hold the real ones; a statement such as
  `read_blob` would put them in the output. One `DROP VIEW` made every later command report a wrong version error.
- **How to change it:** `src/fin/db/conn.py: lock_files` (called in `query/run.py: run_select` and
  `query/write.py: write_sql`); `query/write.py: SCHEMA_CHANGE`.

## A figure of 10^16 rupees or more fails its file

- **Decision:** A document with a figure of 10^16 rupees (after the unit conversion) or more is not loaded: it goes
  to `review/` with E890 naming the sheet!cell or page, and the next file loads. Rows the database refuses for any
  other reason fail the same way (E891).
- **Why:** A figure that large is a wrong unit or a misread cell. It cannot be stored (`facts.value` holds up to
  10^18 with two decimals) and one bad file used to abort the whole batch with a traceback. The plan named 10^16.
- **How to change it:** `src/fin/ingest.py: MAX_RUPEES, check_range`.

## `fin delete` also removes exports, evals and saved parses

- **Decision:** `fin delete` removes (after the backup) the company's `exports/<company>/`, `evals/<company>/` and
  the saved PDF parses of its files (`cache/<engine>/<sha256>*`, found through the sha256 of its documents and source
  files; a parse of a file another company has loaded too stays, because it is keyed by content alone). Company-scope label mappings stay in the shared dictionary; the output says how many.
- **Why:** The exports and evals hold masked figures and queries of the company; a parse is the company's page text
  (a LlamaParse response costs money to make again, so it is in the backup).
- **How to change it:** `src/fin/delete.py: company_paths, _cache_files`.


## Ledger names are not masked again when a later file learns a name

- **Superseded 2026-11-26** by [masking.md](masking.md): ledger names are stored as filed.

- **Decision:** `remask` covers free text only. A trial balance or Ledger Vouchers ledger name that holds a name
  fin learns later (a ledger "Loan - <employee>" loaded before payroll) stays as it was stored.
- **Why:** Ledger names are join keys: ledger mappings the user confirmed, reconciliation lines and gate input hashes
  name them. Rewriting them after the fact would orphan confirmed mappings and reopen gates. Load the payroll and
  registers before the trial balance when the ledger names hold people's names, or see D-7 and D-10.
- **How to change it:** `src/fin/diligence/mask.py: FREE_TEXT_COLUMNS` (with the ledger mapping and recon tables
  rewritten in the same transaction).

## A withdrawn query no longer covers a trial balance difference (G1)

- **Decision:** In G1's checklist, a `tb_vs_statements` difference at or above the threshold counts as queried only
  while its query is not withdrawn. Withdrawing the query reopens the line, and the withdrawn flag is part of G1's
  inputs, so an approved G1 reopens.
- **Why:** A withdrawn query is not a question to the company; the difference is unexplained again.
- **How to change it:** `src/fin/diligence/tb_checks.py: _recon_line, inputs`.

## A partly paid invoice is not counted as received

- **Decision:** `customer_collections.received_inr` and `collection_days` use invoices matched in full only; an
  invoice in a `difference` group shows its invoice amount as `partly_paid_inr` (invoiced = received + partly paid
  + open). The diligence report's collections tables show the new column. This replaces "counts as received in full"
  in [d5-bank-match.md](d5-bank-match.md) 8.
- **Why:** A short payment counted as received overstated collections and shortened collection days.
- **How to change it:** `src/fin/db/schema/sources_views.sql` view `customer_collections`.

# Client decisions (NEEDS CLIENT CONFIRMATION)

Product-level choices the audit found where fin does something the client may not expect. Each is marked NEEDS CLIENT
CONFIRMATION: the client's agent asks about it ([client-handoff.md](../client-handoff.md), step 5). Until the client
answers, fin keeps the behaviour in "Currently".

## D-1 The first period's recast has no opening deferral (NEEDS CLIENT CONFIRMATION)

- **Decision:** When the registers start at the first engagement period, the recast of that period has no deferral
  from invoices before it, so its proposed adjustment is understated. Currently: it is proposed silently. Options:
  (a) no recast proposed for the first period; (b) propose it with a printed note "no invoices before <date> loaded";
  (c) require a register that goes back one service span.
- **Why:** The recast spreads revenue over service dates; the revenue of earlier invoices that falls in the first
  period is not in the registers.
- **How to change it:** `src/fin/reconcile/recast.py` and `recast_run.py` (the period recast and its proposal); `docs/decisions/d6-recast.md` 2.

## D-2 `bank_vs_tb` compares bank statements with cash in hand and deposits too (NEEDS CLIENT CONFIRMATION)

- **Decision:** The books side takes every ledger confirmed to `cash` and `other_bank_balances` (cash in hand,
  deposits, margin money), but statements cover current accounts only, so the line nearly always differs. Currently:
  kept as is. Options: compare only the ledgers the client names as bank accounts; list ledgers with no statement as
  "not compared"; keep.
- **Why:** fin cannot tell a current account from a deposit by its ledger name alone.
- **How to change it:** `src/fin/reconcile/bank.py` (the books side of `bank_vs_tb`).

## D-3 An omitted nil line makes EBITDA and debt NULL (NEEDS CLIENT CONFIRMATION)

- **Decision:** A statement that omits "Other income", "Finance costs" or "Short-term borrowings" gives NULL for EBITDA
  and debt metrics, while gross margin counts absent purchases as nil. Currently: NULL. Option: count an omitted line
  as nil when the document prints PBT and totals, and show it as "counted as nil".
- **Why:** An absent line is not the same as a printed zero; a gap is not filled with an assumption.
- **How to change it:** `src/fin/db/schema/company_metric_definitions.sql` and `company_metrics.sql` (the `when_missing` rules of `ebitda`, debt and
  related metrics in `metrics_wide`).

## D-4 One validation tolerance of 1 lakh rupees (NEEDS CLIENT CONFIRMATION)

- **Decision:** Every validation check (subtotals, balance sheet, comparatives) allows a difference of Rs 1,00,000,
  which is wide for a small company reporting in lakh. Currently: one tolerance for all. Options: a tolerance per
  reporting unit; a lower default; one per engagement.
- **Why:** A figure printed in lakh is rounded to Rs 1,000 at best, but a small company's statement can sit entirely
  inside the tolerance.
- **How to change it:** `src/fin/config.py` (the validation tolerance); `src/fin/validate/gates.py` (where it is applied).

## D-5 Many small differences never roll up into a query (NEEDS CLIENT CONFIRMATION)

- **Decision:** The query threshold applies to each reconciliation line, so hundreds of below-threshold differences
  pass G2 though their total is large. Currently: only listed. Options: also query a month or party roll-up at or
  above the threshold; or print the unqueried total in the G2 checklist.
- **Why:** The threshold was read as a per-line rule ([d4-recon.md](d4-recon.md) 1).
- **How to change it:** `src/fin/reconcile/base.py` (`needs_query`); `src/fin/reconcile/checks.py` (the G2 checklist).

## D-6 Amounts print with thousands grouping, not lakh/crore grouping (NEEDS CLIENT CONFIRMATION)

- **Decision:** Output reads Rs 12,500,000.00 while the diligence docs write 1,25,00,000.00. Currently: thousands
  grouping. Option: Indian digit grouping in printed amounts.
- **Why:** Formatting only; changing it touches many tests, so it is the client's call whether accountants expect it.
- **How to change it:** `src/fin/diligence/engagement.py: rupees` (and `src/fin/statements.py: amount`).

## D-7 Masking matches exact full names only (NEEDS CLIENT CONFIRMATION)

- **Superseded 2026-11-26** by [masking.md](masking.md): free text is no longer masked; no longer a client question.

- **Decision:** A known name is replaced only when it appears in full (ignoring case and repeated spaces), as whole
  words. "Rao Asha" for "Asha Rao", "ASHA" alone, or "Acme Pvt Ltd" against "Acme Pvt. Ltd." pass through. Currently:
  exact only. Options: match name parts of 4+ letters in any order; normalise legal-form words; mask UPI and NEFT
  beneficiary segments by pattern; only report the gap. See also [masking.md](masking.md).
- **Why:** A looser match risks masking ordinary words and hiding real text.
- **How to change it:** `src/fin/diligence/mask.py: Masker.text`.

## D-8 Typed free text in query answers, reasons and .eml files is stored unmasked (NEEDS CLIENT CONFIRMATION)

- **Superseded 2026-11-26** by [masking.md](masking.md): all free text, reply files too, is stored as written; no longer a client question.

- **Decision:** Query wording is checked for tokens, but answers, reasons, notes typed by the user and the text of a
  reply .eml are stored, printed and logged as typed (a .txt, .eml or .md reply file is read with known names masked).
  Currently: kept. Option: mask known names, emails and phone numbers on store.
- **Why:** The user types these; masking them could change what the accountant said.
- **How to change it:** `src/fin/diligence/cli.py` (query answer, reason and note options); `src/fin/diligence/answers.py`.

## D-9 A bank account number is printed in full (NEEDS CLIENT CONFIRMATION)

- **Decision:** Bank account numbers appear in full in labels, findings, query text and the audit log
  ([d3-bank.md](d3-bank.md)). Currently: kept. Options: last 4 digits only; a token.
- **Why:** The account number identifies the statement and is not a name; the accountant's wording needs it.
- **How to change it:** `src/fin/diligence/bank.py` (`Statement.label` and the finding labels).

## D-10 Only "Sundry Debtors" and "Sundry Creditors" are party groups (NEEDS CLIENT CONFIRMATION)

- **Superseded 2026-11-26** by [masking.md](masking.md): Tally party ledgers are no longer masked, so fin has no party groups; no longer a client question.

- **Decision:** Ledgers under these two groups (exact name) are masked as customers and vendors. Variants such as
  "Sundry Debtors (Export)" or "Trade Receivables", and person-named ledgers in other groups (director loan, staff
  advance), stay real. The trial balance load now lists the groups it treated as party groups and the others, so the
  client can see the gap. Which groups should count?
- **Why:** fin does not guess which group holds people or companies.
- **How to change it:** `templates/profiles/tally_trial_balance.toml` (the `groups` table of the party groups).

## D-13 Permissions for the client's agent (NEEDS CLIENT CONFIRMATION)

- **Decision:** The docs say Claude works without approval prompts, but `.claude/settings.json` has no permission
  rules and `~/FinAnalysis` is outside the project, so each read and command may prompt. Which mode or allow rules
  should the handoff prescribe (for example `Bash(uv run fin:*)` plus the data folder as an additional directory)?
  The gate approval prompt must remain.
- **Why:** An unattended agent stalls on prompts; allowing too much weakens the guard.
- **How to change it:** `.claude/settings.json` (`permissions`); `docs/client-handoff.md` step 1.

## D-14 PDF layout heuristics against "keep extraction simple" (NEEDS CLIENT CONFIRMATION)

- **Decision:** The PDF reader holds about 1,150 lines of layout heuristics (`src/fin/extract/pdf*.py`), against the
  rule to keep extraction simple. Currently: frozen, none added or removed (the file is only split by size). Decide
  after the eval results which to drop in favour of the PDF add-on.
- **Why:** Dropping one without real documents to score it against could lose figures.
- **How to change it:** `src/fin/extract/pdf.py`, `pdf_tables.py`; the `[pdf] parser` add-on path `src/fin/extract/fallback.py`.
