---
title: Compare
---

Companies side by side for one metric and scope, per fiscal year. Companies are listed alphabetically.

```sql scope_list
select distinct scope from analytics.metrics order by scope
```

```sql metric_list
select distinct metric from analytics.metrics order by metric
```

<Dropdown name=scope data={scope_list} value=scope title="Scope" />
<Dropdown name=metric data={metric_list} value=metric title="Metric" />

```sql compare
select company, fiscal_year, value, unit from analytics.metrics
where scope = '${inputs.scope.value}' and metric = '${inputs.metric.value}'
order by fiscal_year, company
```

<BarChart data={compare} x=fiscal_year y=value series=company type=grouped title="{inputs.metric.value} ({inputs.scope.value})" />

<DataTable data={compare} rows=all />
