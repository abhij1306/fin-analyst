---
title: Company
---

One company's metrics over the fiscal years, then its quarters. Pick the scope: standalone and
consolidated are never combined.

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
  and period_months = 12
order by metric
```

<Dropdown name=metric data={metric_list} value=metric title="Metric" />

```sql one_metric
select fiscal_year, value, unit from analytics.metrics
where company = '${inputs.company.value}' and scope = '${inputs.scope.value}'
  and metric = '${inputs.metric.value}' and period_months = 12
order by fiscal_year
```

<LineChart data={one_metric} x=fiscal_year y=value title="{inputs.company.value} {inputs.scope.value}: {inputs.metric.value}" />

## Quarters

Revenue, EBITDA margin and PAT for each quarter loaded from results statements (fiscal year April to
March: Q1 ends in June). Empty when no quarterly results are loaded.

```sql quarters
select
    period,
    period_end,
    max(value) filter (where metric = 'revenue') as revenue,
    max(value) filter (where metric = 'ebitda_margin') as ebitda_margin,
    max(value) filter (where metric = 'pat') as pat
from analytics.metrics
where company = '${inputs.company.value}' and scope = '${inputs.scope.value}' and period_months = 3
group by period, period_end
order by period_end
```

<BarChart data={quarters} x=period y=revenue sort=false title="Revenue by quarter (rupees)" />

<LineChart data={quarters} x=period y=ebitda_margin sort=false title="EBITDA margin by quarter (0.18 is 18%)" />

<BarChart data={quarters} x=period y=pat sort=false title="PAT by quarter (rupees)" />

## All metrics

Every period: fiscal years (FY25), quarters (FY26-Q1), half years (FY26-H1) and nine months (FY26-9M).

```sql all_metrics
select metric, period, value, unit from analytics.metrics
where company = '${inputs.company.value}' and scope = '${inputs.scope.value}'
order by metric, period_end, period_months desc
```

<DataTable data={all_metrics} rows=all />

A blank value means an input to that metric is missing from the loaded documents. Rupee values are in
rupees; ratios are fractions (0.18 is 18%); days are days.
