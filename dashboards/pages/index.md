---
title: Companies
---

Every company loaded into fin, from `shared/analytics.duckdb`. Standalone and consolidated figures are
always shown separately.

```sql companies
select company, documents, facts, last_ingested_at from analytics.companies order by company
```

<DataTable data={companies} />

- [One company](/company): metrics by category over the years, quarters, standard statements, findings and
  coverage
- [Companies side by side](/compare): one metric, picked by category, across companies
- [Data quality](/quality): findings, held documents, coverage, unread pages
