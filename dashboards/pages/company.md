---
title: Company
---

One company's metrics over the fiscal years. Pick the scope: standalone and consolidated are never
combined.

```sql company_list
select distinct company from analytics.metrics order by company
```

```sql scope_list
select distinct scope from analytics.metrics order by scope
```

<Dropdown name=company data={company_list} value=company title="Company" />
<Dropdown name=scope data={scope_list} value=scope title="Scope" />

```sql metric_list
select distinct metric from analytics.metrics
where company = '${inputs.company.value}' and scope = '${inputs.scope.value}'
order by metric
```

<Dropdown name=metric data={metric_list} value=metric title="Metric" />

```sql one_metric
select fiscal_year, value, unit from analytics.metrics
where company = '${inputs.company.value}' and scope = '${inputs.scope.value}'
  and metric = '${inputs.metric.value}'
order by fiscal_year
```

<LineChart data={one_metric} x=fiscal_year y=value title="{inputs.company.value} {inputs.scope.value}: {inputs.metric.value}" />

## All metrics

```sql all_metrics
select metric, fiscal_year, value, unit from analytics.metrics
where company = '${inputs.company.value}' and scope = '${inputs.scope.value}'
order by metric, fiscal_year
```

<DataTable data={all_metrics} rows=all />

A blank value means an input to that metric is missing from the loaded documents. Rupee values are in
rupees; ratios are fractions (0.18 is 18%); days are days.
