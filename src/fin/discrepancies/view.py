"""The temporary view `discrepancies`: one row per difference of the latest run of each
reconciliation of the latest engagement, at one of four levels, each pointing to its parent.

- headline: tb_vs_statements lines, the trial balance rolled up by confirmed mapping (the rebuilt
  line of SPEC-14) against the reported statement line;
- account: gl_vs_tb ledger lines (Ledger Vouchers against the trial balance) and the payroll_vs_gl
  line;
- party: the register reconciliations' customer and vendor lines (register against Ledger
  Vouchers);
- transaction: the register reconciliations' invoice lines, bank_vs_invoices' match, receipt
  and invoice lines, and purchase_register_vs_gstr2b's invoice lines (SPEC-20).

The service-period recast (`service_recast`, SPEC-17 item 5) adds a headline row per period: the
recognition difference, revenue in the books (the sales register by invoice date) - the recast
revenue, with its invoices (the recast's schedule lines) as transaction rows under it. The recast
stores recast - booked, so its sides are swapped here (`swapped`) and the row is labelled
(`label`); its month roll-ups and undated invoices are not rows. A period with no register line
dated in it has no books side: the row shows it missing, with no difference (missing is not zero).

A headline row also comes from each reference figure that differs from fin's
(`reference_comparison`, SPEC-10, state differs: fin - the reference). It has no `item_id` and
no parent or children; its `value_id` goes to `fin reference query`. Its query is the one that
command linked (`reference_values.query_id`) when it is of the latest engagement; it can be queried
as a line can: at or above the latest engagement's query threshold, of one of its periods and in no
query but a withdrawn one (as `fin reference query` checks, E983 and E881). A figure fin does not
hold (no_fin_figure) is not a difference and is not a row. `row_id` names every row: the line's item
id, or ref-<value_id>.

Only lines that differ are rows (kinds difference, left_only, right_only and fx); month roll-ups,
matched, unmapped and not reconciled lines are not. Every row is in the statements' sign: the
lower rung minus the rung above it (register - books, Ledger Vouchers - trial balance, trial
balance - statements); gl_vs_tb lines, stored as debit - credit, are multiplied by their
account's side. `difference` is in rupees: a foreign-currency bank line's rupee difference, NULL
when it has none (missing is not zero); its amounts stay in `currency`. A bank line stored with
currency INR is in rupees and has no `currency`, so it is shown in the display unit like the rest.

Parents: an account or party row is under the headline row of the same period whose account is
its own or the nearest above it in the dictionary tree (a party row's account is the register
reconciliation's books account: revenue, or the one purchase account the ledgers are confirmed
to). An invoice row is under its party row, else under the headline row of its own run (a recast
invoice), else under that headline. A row with no parent row is
a root. `explained` is the children's differences added up (0 with no children; NULL when a child
has no rupee value) and `unexplained` = difference - explained: nothing is spread or hidden.
"""

from typing import Any

import duckdb

HEADLINE, ACCOUNT, PARTY, TRANSACTION = "headline", "account", "party", "transaction"
LEVELS = (HEADLINE, ACCOUNT, PARTY, TRANSACTION)
KINDS = ("difference", "left_only", "right_only", "fx")
RECOGNITION = "recognition difference"


def sources() -> list[tuple[Any, ...]]:
    """The rows of `discrepancy_sources`: (reconciliation, line level or "" for none, level, books
    accounts, left side, right side, whether the amounts are debit - credit, whether the stored
    sides are swapped to show them, the label of its rows or None for their kind)."""
    # Imported here: fin.reconcile imports fin.diligence, which imports `fin sql` (this module).
    from fin.diligence.recon import NAME as TB_VS_STATEMENTS
    from fin.reconcile import bank, gl_vs_tb, gstr2b, payroll_vs_gl, recast
    from fin.reconcile.registers import PURCHASE, SALES

    return [
        (TB_VS_STATEMENTS, "", HEADLINE, [], "trial balance", "statements", False, False, None),
        (gl_vs_tb.NAME, "ledger", ACCOUNT, [], "Ledger Vouchers", "trial balance", True, False,
         None),
        (payroll_vs_gl.NAME, "payroll", ACCOUNT, list(payroll_vs_gl.ACCOUNTS), "payroll", "books",
         False, False, None),
        *(
            (spec.name, level, tier, list(spec.accounts), "register", "books", False, False, None)
            for spec in (SALES, PURCHASE)
            for level, tier in (("party", PARTY), ("invoice", TRANSACTION))
        ),
        *(
            (bank.INVOICES, level, TRANSACTION, [], "bank", "invoices", False, False, None)
            for level in ("match", "receipt", "invoice")
        ),
        *(
            (recast.NAME, level, tier, [], "books", "recast", False, True, RECOGNITION)
            for level, tier in (("period", HEADLINE), ("schedule", TRANSACTION))
        ),
        (gstr2b.NAME, "invoice", TRANSACTION, [], "GSTR-2B", "register", False, False, None),
    ]  # fmt: skip


VIEW_SQL = """
CREATE OR REPLACE TEMP VIEW discrepancies AS
WITH runs AS (
    SELECT r.* FROM recon_runs r
    WHERE r.engagement_id = (SELECT max(engagement_id) FROM engagement)
        AND r.recon_id = (
            SELECT max(x.recon_id) FROM recon_runs x
            WHERE x.engagement_id = r.engagement_id AND x.name = r.name AND x.period = r.period
        )
),
picked AS (
    SELECT i.*, r.name AS reconciliation, r.period, r.threshold, s.tier, s.accounts,
        s.left_name, s.right_name, s.swapped, s.label,
        CASE WHEN s.debit_credit THEN (
            SELECT CASE WHEN count(DISTINCT m.side) = 1 THEN min(m.side) END
            FROM ledger_accounts m WHERE m.status = 'confirmed' AND m.account = i.account
        ) ELSE 1 END AS sign,
        coalesce(i.account, (
            SELECT CASE WHEN count(DISTINCT m.account) = 1 THEN min(m.account) END
            FROM ledger_accounts m
            WHERE m.status = 'confirmed' AND list_contains(s.accounts, m.account)
        )) AS books_account
    FROM recon_items i
    JOIN runs r USING (recon_id)
    JOIN discrepancy_sources s ON s.name = r.name AND s.level = coalesce(i.level, '')
    WHERE i.kind IN ('difference', 'left_only', 'right_only', 'fx')
),
lines AS (
    SELECT
        p.item_id, p.recon_id, p.reconciliation, p.period, p.tier AS level,
        -- the recast's period line is keyed by its period; its row is about revenue
        CASE WHEN p.label IS NOT NULL AND p.tier = 'headline' THEN 'revenue'
            ELSE coalesce(p.key, p.account) END AS key,
        p.label, p.books_account AS account, p.party,
        p.left_name, p.right_name, nullif(p.currency, 'INR') AS currency,
        -- a swapped source (the recast: stored recast - booked) is shown books - recast
        CASE WHEN NOT p.swapped THEN p.kind WHEN p.kind = 'left_only' THEN 'right_only'
            WHEN p.kind = 'right_only' THEN 'left_only' ELSE p.kind END AS kind,
        CASE WHEN p.swapped THEN p.right_amount ELSE p.left_amount END * p.sign AS left_amount,
        CASE WHEN p.swapped THEN p.left_amount ELSE p.right_amount END * p.sign AS right_amount,
        -- the line's rupees as fin.reconcile.items.line_rupees reads them; the recast against
        -- books with a side missing (no register line dated in the period) has no difference
        CASE WHEN NOT p.swapped THEN coalesce(p.amount_inr,
                CASE WHEN coalesce(p.currency, 'INR') = 'INR' THEN p.difference END) * p.sign
            WHEN p.left_amount IS NOT NULL AND p.right_amount IS NOT NULL
                THEN -p.difference * p.sign END AS difference,
        CASE WHEN p.swapped THEN p.right_source ELSE p.left_source END AS left_source,
        CASE WHEN p.swapped THEN p.left_source ELSE p.right_source END AS right_source,
        p.note, p.threshold, p.needs_query, p.query_id
    FROM picked p
),
headline_parent AS (
    SELECT l.item_id, arg_min(h.item_id, CASE WHEN h.account = l.account THEN 0
        ELSE len(a."between") + 1 END) AS parent_id
    FROM lines l
    JOIN lines h ON h.level = 'headline' AND h.period = l.period
    LEFT JOIN statement_paths a ON a.ancestor = h.account AND a.descendant = l.account
    WHERE l.level <> 'headline' AND (h.account = l.account OR a.ancestor IS NOT NULL)
    GROUP BY l.item_id
),
run_parent AS (
    -- in the same run: the party row of an invoice's party, or the run's headline row (a recast
    -- invoice under its period); no run has both
    SELECT l.item_id, min(p.item_id) AS parent_id
    FROM lines l
    JOIN lines p ON p.recon_id = l.recon_id
        AND (p.level = 'party' AND p.key = l.party OR p.level = 'headline')
    WHERE l.level = 'transaction'
    GROUP BY l.item_id
),
linked AS (
    SELECT l.*, CASE WHEN l.level = 'headline' THEN NULL
        WHEN l.level = 'transaction' THEN coalesce(rp.parent_id, hp.parent_id)
        ELSE hp.parent_id END AS parent_id
    FROM lines l
    LEFT JOIN headline_parent hp USING (item_id)
    LEFT JOIN run_parent rp USING (item_id)
),
children AS (
    SELECT parent_id, count(*) AS children, count(difference) AS known,
        sum(difference) AS explained
    FROM linked WHERE parent_id IS NOT NULL GROUP BY parent_id
),
joined AS (
    SELECT k.*, coalesce(c.children, 0) AS children,
        CASE WHEN c.children IS NULL THEN CAST(0 AS DECIMAL(20, 2))
            WHEN c.known = c.children THEN c.explained END AS explained
    FROM linked k LEFT JOIN children c ON c.parent_id = k.item_id
)
SELECT
    j.item_id, NULL::BIGINT AS value_id, CAST(j.item_id AS VARCHAR) AS row_id, j.parent_id,
    j.level, j.reconciliation, j.period, j.key, j.kind, j.label, j.account,
    j.party, j.left_name, j.right_name, j.currency, j.left_amount, j.right_amount,
    j.difference, j.children, j.explained, j.difference - j.explained AS unexplained,
    j.left_source, j.right_source, j.note, j.threshold,
    abs(j.difference) >= j.threshold AS over_threshold,
    j.query_id, q.status AS query_status,
    j.needs_query AND (j.query_id IS NULL OR q.status = 'withdrawn') AS queryable
FROM joined j
LEFT JOIN accountant_queries q ON q.query_id = j.query_id
UNION ALL BY NAME
SELECT
    c.value_id, 'ref-' || c.value_id AS row_id, 'headline' AS level,
    'reference_comparison' AS reconciliation, c.period, c.target AS key, 'difference' AS kind,
    CASE WHEN c.target_kind = 'account' THEN c.target END AS account, 'fin' AS left_name,
    c.source AS right_name, c.fin_value AS left_amount, c.reference_value AS right_amount,
    c.difference, 0 AS children, CAST(0 AS DECIMAL(20, 2)) AS explained,
    c.difference AS unexplained, c.fin_source AS left_source,
    c.file_name || ' ' || c.reference_ref AS right_source, c.scope || ': ' || c.label AS note,
    e.query_threshold AS threshold, abs(c.difference) >= e.query_threshold AS over_threshold,
    q.query_id, q.status AS query_status,
    -- as `fin reference query` checks: over the threshold, a period of the engagement (E881), in no
    -- query but a withdrawn one
    coalesce(abs(c.difference) >= e.query_threshold, false)
        AND (list_contains(string_split(e.periods, ','), c.period)
            OR c.period IS NOT DISTINCT FROM e.stub)
        AND (q.query_id IS NULL OR q.status = 'withdrawn') AS queryable
FROM reference_comparison c
LEFT JOIN engagement e ON e.engagement_id = (SELECT max(engagement_id) FROM engagement)
LEFT JOIN accountant_queries q ON q.query_id = c.query_id AND q.engagement_id = e.engagement_id
WHERE c.state = 'differs'
"""


def add_view(con: duckdb.DuckDBPyConnection) -> None:
    """Create the temporary view `discrepancies` (and its table `discrepancy_sources`). Needs the
    temporary table `statement_paths` (`fin.statements.add_view`)."""
    con.execute(
        "CREATE OR REPLACE TEMP TABLE discrepancy_sources (name VARCHAR, level VARCHAR, "
        "tier VARCHAR, accounts VARCHAR[], left_name VARCHAR, right_name VARCHAR, "
        "debit_credit BOOLEAN, swapped BOOLEAN, label VARCHAR)"
    )
    con.executemany("INSERT INTO discrepancy_sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", sources())
    con.execute(VIEW_SQL)
