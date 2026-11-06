---
title: Companies
---

Every company loaded into fin, from `shared/analytics.duckdb`. Standalone and consolidated figures are
always shown separately.

```sql companies
select company, documents, facts, last_ingested_at from analytics.companies order by company
```

<DataTable data={companies} />

- [One company over the years](/company)
- [Companies side by side](/compare)
- [Data quality](/quality)
