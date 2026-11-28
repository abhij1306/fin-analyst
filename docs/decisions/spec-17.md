# SPEC-17: decisions from the client's answers

Builder's readings ([README](README.md)); the owner confirms or changes them.

## 1. Bank account numbers (PR 1)

### Which numbers are masked
- **Decision:** the account number a statement's header gives (the profile's `[header] account` pattern) is stored
  once in `mask_values` (kind `bank_account`) and replaced by `ACCT-0001` in `bank_statements`, `bank_lines`,
  findings, output and logs. The same number, found as a whole number inside any bank line's remarks or reference
  (a transfer between the company's own accounts), is replaced too, also in lines loaded before that account's
  statement was. Other digits in bank text (cheque numbers, a third party's account) stay as filed.
- **Why:** the client asked for "bank account numbers too" (answer 1). fin knows an account number only when a
  statement names it; guessing which other digit runs are account numbers would be a parsing heuristic.
- **How to change it:** `bank_mask.py: mask_text, remask_lines`; `bank.py: mask_statement`.

### An account number is its own kind
- **Decision:** a number masked as `bank_account` never shares a token with a name or code of another kind with the same
  text (a payroll code with the same digits keeps its `ECODE-` token, and the account gets `ACCT-`).
- **Why:** a customer who is also a vendor is one party, so the name kinds share tokens; an account number and an
  employee code with the same digits are not one thing.
- **How to change it:** `mask.py: Masker.token`.

### The saved parse of a PDF
- **Decision:** `cache/bank/<company>/<sha256>.json` is written with tokens. It is kept per company (a token means
  something only in its company's `mask_values`) and `fin delete` removes the folder, also the parses of failed loads; a
  saved parse holding a token the company has no number for is refused (E818) rather than read with another account's
  number. A number a later statement makes known is replaced in the earlier saved parses too, after the load commits. The numbers are found with the bank profiles' account
  patterns and made tokens in their own short transaction just before the parse is saved, so a load that fails later
  (E808, for example) still leaves a saved parse fin can read again without calling the parser. Such a token has no
  `first_file_id` (NULL): it was made from a saved parse, before any file loaded.
- **Why:** the spec asks that the saved parse hold the token; parsing again after every failed load would cost a
  LlamaParse call each time.
- **How to change it:** `bank_pdf.py: read_parse`, `bank_mask.py: mask_parse, unmask_parse, remask_parses`.

### What still shows the number
- **Decision:** the original file (archive, inbox), a PDF evidence crop, and a file name that contains the number show it
  as filed; `fin query export` writes the real number for the accountant, like the other masked fields.
- **Why:** the spec states this limit (item 1); the export follows "like the fields masked today".
- **How to change it:** `export_data.py: Unmasker` (to keep the token in the export).

### Earlier data
- **Decision:** no migration and no `--force` path for bank files. Statements loaded before hold the number; the docs say
  to re-ingest (`fin delete <co>`, then load again, which also removes their saved parses).
- **Why:** the owner, 2026-11-28: "this is a greenfield build. No migrations, no compatibility code, no fallbacks or
  special cases for earlier users or earlier data". This replaces the spec's "loaded again with `--force`".
- **How to change it:** `cli/ingest.py: _check_usage` and `loading.py: refuse_duplicate`, if a reload is wanted.

## 2. Orders in another currency (USD) at a rate the user states

Readings by the builder (2026-11-28), not owner decisions.

- **Which rate applies**: the rate stated for the order's currency, the open engagement and the orderbook's as-at date
  exactly. A rate as at another date does not apply to that orderbook (`fin engagement rate` says so when no loaded
  order matches): picking the nearest date would be an assumption.
- **Which currencies**: any three-letter code other than INR, as the spec says ("any other currency stays not converted
  until a rate is stated the same way").
- **Stating it again**: rates are appended, never changed; the latest for a currency and date is used. Each one is in
  `diligence_events` (kind `rate`, status `stated`) with the actor and run, so earlier rates stay in the audit log.
- **Rounding**: value and unbilled value are converted and rounded to the paisa first; the backlog spreads the rupee
  amount as it spreads an INR order.
- **What it feeds**: the backlog and revenue visibility, and the orderbook reconciliation (a converted order without
  billing can reach the query threshold). A run of that reconciliation made before the rate was stated is stale (its
  input hash changes), so the gate over it asks for a new run.
- **Who may state it**: the user. Claude states a rate only when the user gives it; the audit row then says `claude`.

## 3. The user's own inquiry list

Readings by the builder (2026-11-28), not owner decisions.

- **Where**: `<data folder>/templates/inquiries.toml`, as the spec says. It replaces fin's list whole; the two are not
  merged. A topic the user's list leaves out shows no questions (its inquiries still show).
- **Checks**: the same as fin's list (topics from `[fdd] inquiry_topics`, a non-empty list of questions each). A list
  that cannot be read or fails a check stops `fin query checklist` with E910 naming the file; fin does not fall back
  to its own list.
- **Naming it**: the output's first line, and `list` and `path` in `--json`.

## 4. Credit terms on the engagement

Readings by the builder (2026-11-28), not owner decisions.

- **Changing them**: the spec says "changing it reopens the gate", so the open engagement's terms can change after it
  opens: `fin engagement terms <co> --due-days N` (recorded in `diligence_events`). `--due-days` on
  `fin engagement open` sets them at the start.
- **Which gate**: G2. The terms are part of the bank_vs_invoices input hash, so a run made with other terms is stale
  even when no invoice changes side; the ageing view reads the run's stored terms.
- **Range**: whole days from 0 to 3650, the range `config.toml` already accepts (E846 otherwise).
- **The engagement screen**: SPEC-09 PR 2 builds it. Until then the Gates screen header shows the terms with the
  engagement number.
- **Existing data folders**: `config.toml` is the user's file; one written before keeps the value written there
  (re-run setup or edit it). New data folders get 90.

## 5. The recast against the books

Readings by the builder (2026-11-28), not owner decisions.

- **Revenue in the books**: the sales register's amount (before GST) by invoice date, which is what `fin recast run`
  already compares the recast with. The client said the company books revenue on billing (answer 9), so revenue by
  invoice date is the books' recognition. The trial balance's revenue line is not used: it is already the
  `tb_vs_statements` headline, and a second comparison would be a second computation of the recast.
- **One computation**: the row reads the latest `service_recast` run of the period (`recon_items`), as SPEC-15
  reads every reconciliation; no recast runs in the view. A period with no recast run has no row (not a zero).
- **Sign and sides**: books - recast, as the spec says; the run stores recast - booked, so the view swaps the sides and
  the sign (and left_only and right_only) for this source only.
- **Its children**: the recast's schedule lines (one per invoice whose recast and booked amounts differ in the period),
  under the period row of the same run; they add up to it. Month roll-ups and undated invoices are not rows (an
  undated invoice is recognised on its invoice date, so it is no part of the difference).
- **Key and label**: the headline row is keyed `revenue` (the stored key is the period) and every row of it carries
  `label` "recognition difference", shown instead of the kind. It is not under or over the trial balance's revenue
  row: the two compare different things.
- **No query from the list**: these rows are not marked "can be queried"; the difference is by design and becomes a
  `recast` adjustment with evidence (`fin recast run`, gate G3).
- **How to change it**: `sources()` and the `lines` and `run_parent` steps of `VIEW_SQL` in
  `src/fin/discrepancies/view.py`.
