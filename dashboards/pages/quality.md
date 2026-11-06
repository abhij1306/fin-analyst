---
title: Data quality
---

What is not yet trustworthy, per company. Unconfirmed facts (unmapped labels or mappings still pending
review) are left out of every metric. Failed validations are all-time counts from the audit log.

```sql quality
select company, documents, facts, unconfirmed_facts, failed_validations, last_ingested_at, refreshed_at
from analytics.companies
order by company
```

<DataTable data={quality} rows=all />

Resolve unconfirmed items with `fin review`; see `fin audit` for the failed validations.
