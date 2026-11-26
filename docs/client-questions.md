# Client questions

Questions only the client can answer: how their business and books work, and what they want to see. The client's
Claude Code asks them after the eval (see [client-handoff.md](client-handoff.md), step 4) and writes each answer under
its question in the client's words, with the date. Skip a question the client's documents do not touch, and write
"skipped: not in the documents". Each one gives fin's current default and where it is used; nothing changes until
fin's maintainers act on the answer.

## Masking

1. **Is anything else to be masked?** fin replaces with tokens only the fields the client named: payroll employee
   name and code, and the starred register fields (sales Customer Name, handler, submitted-by; purchase Vendor Name).
   Everything else is stored as filed, including locations, product names and bank account numbers (printed in full).
   Default: only those fields. Used in: [masking.md](decisions/masking.md); [final-fixes.md](decisions/final-fixes.md) D-9.
   Answer:

2. **Is it acceptable that a token can be linked to a name?** When fin matches a register party to the books, the
   output shows the masked token (`CUST-0001`) beside the Tally ledger name as filed, so a reader can see which name
   the token stands for. Default: shown together. The alternative: hide the token in that output. Used in:
   [masking.md](decisions/masking.md), "Party matching".
   Answer:

## Payroll and provisions

3. **What share of gross salary is basic pay (for gratuity)?** The payroll has no basic pay column. Default: 50%
   (`config.toml [fdd] basic_share`). And should a part year over six months count as a full year of service, as the
   Gratuity Act does? Default: completed years only. Used in: [fdd-f3.md](decisions/fdd-f3.md), "Gratuity";
   [fdd-scope.md](decisions/fdd-scope.md).
   Answer:

## Cash, receivables and net debt

4. **Are fixed deposits, margin money and lien-marked balances cash?** Default: not counted as cash or debt until the
   user classifies each such ledger; the bank-to-books comparison (`bank_vs_tb`) still includes them. Used in:
   [fdd-review-fixes.md](decisions/fdd-review-fixes.md), net debt classes; [final-fixes.md](decisions/final-fixes.md) D-2.
   Answer:

5. **Should receivable ageing be before GST?** fin ages invoices at the register's Amount in Rs (before GST), while
   the books' receivables include GST. Default: before GST. Used in: [fdd-f3.md](decisions/fdd-f3.md), "Receivable ageing".
   Answer:

6. **What are your usual credit terms?** An unpaid invoice counts as overdue after this many days. Default: 30
   (`config.toml [diligence] invoice_due_days`). Used in: [d5-bank-match.md](decisions/d5-bank-match.md) 4.
   Answer:

7. **Do your books record one combined Receipt voucher for several part payments (or foreign-currency receipts) of one
   invoice?** fin then cannot find the booked rupee value of each receipt and shows "no booked value". How often does
   it happen, and how should it be shown? Default: "no booked value", no fx line. Used in:
   [test-run.md](decisions/test-run.md), the 10% band; [d5-bank-match.md](decisions/d5-bank-match.md) 5.
   Answer:

## GST

8. **How does the company book GST set-offs, credit notes and reverse charge (RCM)?** fin compares GSTR-3B with the
   credits on the output tax ledgers, so a credit note shows the books higher than GSTR-3B and becomes a query.
   And are the GST summaries you gave a fixed export (prepared by the company or its consultant, same layout every
   month)? Default: credits only; one read profile per layout. Used in: [fdd-f6.md](decisions/fdd-f6.md).
   Answer:

## Revenue and orders

9. **Is service revenue earned evenly over the service days?** fin spreads each invoice straight-line by service
   day; an invoice with no service dates counts on its invoice date. The first period has no revenue carried in from
   earlier invoices unless the sales register for the year before is loaded: can you provide it? Default: straight
   line, no opening deferral. Used in: [d6-recast.md](decisions/d6-recast.md) 2 to 4;
   [final-fixes.md](decisions/final-fixes.md) D-1.
   Answer:

10. **About the orderbook:** is it all in INR, or does it carry other currencies? Does it list open orders only or all
    orders of the year? Can the sales register carry an order number? Default: INR orders only valued; the orderbook
    read as all orders; orders matched to invoices by customer. Used in: [fdd-f7.md](decisions/fdd-f7.md) 1 and the
    orderbook-to-register test.
    Answer:

## Queries and reports

11. **What difference is worth a query?** Default: Rs 1,00,000 per reconciliation line
    (`config.toml [diligence] query_threshold`); many smaller differences are listed but never queried, however large
    their total. Should a month or party total at or above the threshold also be queried? Used in:
    [d4-recon.md](decisions/d4-recon.md) 1; [final-fixes.md](decisions/final-fixes.md) D-5.
    Answer:

12. **Which standard questions do you ask the company per topic?** fin's list is general FDD practice
    (`templates/fdd/inquiries.toml`). Default: that list. Used in: [fdd-f2.md](decisions/fdd-f2.md).
    Answer:

13. **Which FDD figures should the report narrative quote?** Default: the diligence report's figures, net debt (and
    its parts) and the effective tax rate; ageing, gratuity and visibility are shown but not quoted. Used in:
    [fdd-f8.md](decisions/fdd-f8.md), referable figures.
    Answer:

14. **Should amounts print in lakh/crore grouping (1,25,00,000.00)?** Default: thousands grouping (12,500,000.00).
    Used in: [final-fixes.md](decisions/final-fixes.md) D-6.
    Answer:
