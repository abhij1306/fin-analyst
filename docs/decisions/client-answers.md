# Client answers to the standing questions, 2026-11-27

The client answered the 14 questions in [client-questions.md](../client-questions.md) on 2026-11-27; the owner passed
the file on 2026-11-28. The answers are quoted as written, with one third party's name replaced by "[the adviser]".
"Reading" is the builder's reading of an answer, not the client's or the owner's decision. Where an answer changes
fin, the work is in [SPEC-17](../specs/SPEC-17-client-answers.md) or the spec named.

| # | Question | Answer | Reading | Where it goes |
|---|---|---|---|---|
| 1 | Is anything else to be masked? | "Bank account numbers too." | A change: account numbers are stored and printed in full today. | SPEC-17 item 1 |
| 2 | May a token be shown beside the ledger name it matched? | "Yes." | Default confirmed. | none |
| 3 | Basic pay share for gratuity; part years | "Irrelevant question." | No value given; the default stays. The client may not use the gratuity estimate at all. | none; asked again at the second run |
| 4 | Are fixed deposits, margin money and lien-marked balances cash? | "Can be sometimes." | Default confirmed: the user classifies each such ledger. | none |
| 5 | Receivable ageing before GST? | "Yes, books are always before GST." | Default confirmed. The second half differs from fin's note that receivables in the books include GST; to check on the client's ledger, not to assume. | SPEC-17 open issue 1 |
| 6 | Usual credit terms | "90 days." | A change: fin's default (30) becomes 90, and an engagement can set its own. | SPEC-17 item 4 |
| 7 | One combined receipt voucher for several part payments? | "Can and can not, varies." | Default stays ("no booked value"). | none |
| 8 | How are GST set-offs, credit notes and reverse charge booked; are the GST summaries a fixed export? | "Software maintains it; Tally has a clean GST against invoice module." | The set-off question is not answered; the default stays (credits only). The summaries come from the accounting software, so one read profile should cover them once its headings are known. | human task H4 |
| 9 | Is service revenue earned evenly over the service days? | "It should be, but these guys book it on billing." | Default confirmed for the recast (straight line). The company's books recognise on billing, so books and recast differ in every period by design; that difference is a diligence figure, not an error. The prior-year register was not answered. | SPEC-17 item 5 |
| 10 | Orderbook currency, open or all orders, order number in the register | "INR or USD." | A change: only INR orders are valued today. The other two parts were not answered; the defaults stay. | SPEC-17 item 2 |
| 11 | What difference is worth a query? | "1,00,000 is good." | Default confirmed. Whether a month or party total over the threshold is also queried was not answered; it stays listed only. | none |
| 12 | Which standard questions per topic? | "The FDD list from [the adviser] should be shared here." | A change: the client will supply a list; fin reads only the list in the repo today. A third party's list stays in the data folder. | SPEC-17 item 3, human task H6 |
| 13 | Which FDD figures may the narrative quote? | "Should use default FDD output." | Default confirmed. | none |
| 14 | Lakh/crore digit grouping? | "INR Mn." | Amounts are shown in INR million; Indian digit grouping is not wanted. Same as observation 11 of the client run. | SPEC-06 |

**How to change it**: each row's spec names the code. A confirmed default needs no change.
