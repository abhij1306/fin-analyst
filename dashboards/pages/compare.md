---
title: Compare
---

Companies side by side for one metric and scope, per fiscal year (annual figures only). Pick a category,
then a metric in it. Companies are listed alphabetically. Ratios are fractions (0.18 is 18%); money metrics
are in rupees; days are days.

```sql scope_list
select distinct scope from analytics.metrics order by scope
```

```sql category_list
select distinct category from analytics.metrics
where period_months = 12 and category <> 'headline'
order by category
```

<Dropdown name=scope data={scope_list} value=scope title="Scope" />
<Dropdown name=category data={category_list} value=category title="Category" />

```sql metric_list
select distinct metric from analytics.metrics
where period_months = 12 and category = '${inputs.category.value}'
order by metric
```

<Dropdown name=metric data={metric_list} value=metric title="Metric" />

```sql definition
select metric, unit, definition, formula from analytics.metric_definitions
where metric = '${inputs.metric.value}'
```

<DataTable data={definition} emptySet=pass emptyMessage="None loaded." />

```sql compare
select company, fiscal_year, value, unit from analytics.metrics
where scope = '${inputs.scope.value}' and metric = '${inputs.metric.value}'
  and period_months = 12
order by fiscal_year, company
```

<BarChart data={compare} x=fiscal_year y=value series=company type=grouped sort=false title="{inputs.metric.value} ({inputs.scope.value})" emptySet=pass emptyMessage="None loaded." />

<DataTable data={compare} rows=all emptySet=pass emptyMessage="None loaded." />
