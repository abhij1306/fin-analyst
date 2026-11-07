---
title: Data quality
---

What is not yet trustworthy, per company. Unconfirmed facts (unmapped labels or mappings still pending
review) are left out of every metric. Unverified Claude-read facts were read by Claude from pages fin could
not read (`fin ingest --reading`) and no second, independent reading has agreed with them yet (`fin verify`).

Findings are figures in what a company filed that do not add up, or that differ between documents or from a
reading. A held document has open findings and is left out of the metrics until they are accepted (kept as
filed) or fixed. Accepted findings stay counted. Failed validations are all-time counts of failed checks
from the audit log.

```sql quality
select company, documents, held_documents, open_findings, accepted_findings, facts, unconfirmed_facts,
    unverified_claude_facts, failed_validations, last_ingested_at, refreshed_at
from analytics.companies
order by company
```

<DataTable data={quality} rows=all />

Resolve unconfirmed items with `fin review`; see each finding with `fin findings list`.
