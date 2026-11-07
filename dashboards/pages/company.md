---
title: Company
---

One company over its fiscal years, by metric category, then its quarters, its standard statements, and
what is not yet settled in its data. Pick the scope: standalone and consolidated are never combined.
Percentages are ratios (18% is 0.18 in `fin sql`); money metrics are in ₹ crore here; days are days.
A missing point means an input to that metric is not in the loaded documents.

```sql company_list
select distinct company from analytics.metrics order by company
```

```sql scope_list
select distinct scope from analytics.metrics
where company = '${inputs.company.value}'
order by scope
```

<Dropdown name=company data={company_list} value=company title="Company" />
<Dropdown name=scope data={scope_list} value=scope title="Scope" />

```sql annual
select
    fiscal_year,
    category,
    metric,
    case when unit = 'rupees' then value / 10000000 else value end as value
from analytics.metrics
where company = '${inputs.company.value}' and scope = '${inputs.scope.value}'
  and period_months = 12
order by fiscal_year, metric
```

## Profitability

```sql profitability
select * from ${annual}
where metric in ('gross_margin', 'ebitda_margin', 'ebit_margin', 'pat_margin')
```

<LineChart data={profitability} x=fiscal_year y=value series=metric yFmt=pct1 title="Margins" emptySet=pass emptyMessage="None loaded." />

## Returns

```sql returns
select * from ${annual} where metric in ('roe', 'roce', 'roa')
```

<LineChart data={returns} x=fiscal_year y=value series=metric yFmt=pct1 title="Return on equity, capital employed and assets (closing balances)" emptySet=pass emptyMessage="None loaded." />

## Liquidity

```sql liquidity
select * from ${annual} where metric in ('current_ratio', 'quick_ratio', 'cash_ratio')
```

<LineChart data={liquidity} x=fiscal_year y=value series=metric yFmt=num2 title="Liquidity ratios (times)" emptySet=pass emptyMessage="None loaded." />

## Solvency

```sql solvency
select * from ${annual}
where metric in ('debt_to_equity', 'debt_to_ebitda', 'net_debt_to_ebitda', 'interest_cover')
```

<LineChart data={solvency} x=fiscal_year y=value series=metric yFmt=num2 title="Leverage and interest cover (times)" emptySet=pass emptyMessage="None loaded." />

## Efficiency

```sql efficiency
select * from ${annual}
where metric in ('receivable_days', 'inventory_days', 'payable_days', 'cash_conversion_cycle')
```

<LineChart data={efficiency} x=fiscal_year y=value series=metric yFmt=num1 title="Working capital days" emptySet=pass emptyMessage="None loaded." />

## Cash flow

```sql cash_flow
select * from ${annual} where metric in ('cfo', 'free_cash_flow')
```

<BarChart data={cash_flow} x=fiscal_year y=value series=metric type=grouped sort=false yFmt=num2 title="Cash from operations and free cash flow (₹ crore)" emptySet=pass emptyMessage="None loaded." />

## Growth

```sql growth
select * from ${annual} where metric in ('revenue_growth', 'ebitda_growth', 'pat_growth')
```

<LineChart data={growth} x=fiscal_year y=value series=metric yFmt=pct1 title="Growth on the year before" emptySet=pass emptyMessage="None loaded." />

## Earnings quality

```sql earnings_quality
select * from ${annual} where category = 'earnings_quality'
```

<LineChart data={earnings_quality} x=fiscal_year y=value series=metric yFmt=pct1 title="Other income and exceptional items as a share of PBT, effective tax rate" emptySet=pass emptyMessage="None loaded." />

## Any metric

Every annual metric by category, including per share and lender metrics (lender metrics only for NBFCs and
banks).

```sql category_list
select distinct category from ${annual} order by category
```

<Dropdown name=category data={category_list} value=category title="Category" />

```sql category_metrics
select fiscal_year, metric, value from ${annual}
where category = '${inputs.category.value}'
```

<LineChart data={category_metrics} x=fiscal_year y=value series=metric title="{inputs.category.value}" emptySet=pass emptyMessage="None loaded." />

<DataTable data={category_metrics} rows=all emptySet=pass emptyMessage="None loaded." />

## Quarters

Revenue, EBITDA margin and PAT for each quarter loaded from results statements (fiscal year April to
March: Q1 ends in June), with trailing twelve months. Empty when no quarterly results are loaded.

```sql quarters
select
    period,
    period_end,
    max(value) filter (where metric = 'revenue') / 10000000 as revenue,
    max(value) filter (where metric = 'ebitda_margin') as ebitda_margin,
    max(value) filter (where metric = 'pat') / 10000000 as pat,
    max(value) filter (where metric = 'ttm_revenue') / 10000000 as ttm_revenue,
    max(value) filter (where metric = 'ttm_pat') / 10000000 as ttm_pat
from analytics.metrics
where company = '${inputs.company.value}' and scope = '${inputs.scope.value}' and period_months = 3
group by period, period_end
order by period_end
```

<BarChart data={quarters} x=period y=revenue sort=false title="Revenue by quarter (₹ crore)" emptySet=pass emptyMessage="None loaded." />

<LineChart data={quarters} x=period y=ebitda_margin sort=false yFmt=pct1 title="EBITDA margin by quarter" emptySet=pass emptyMessage="None loaded." />

<DataTable data={quarters} rows=all emptySet=pass emptyMessage="None loaded." />

## Standard statements

The profit and loss, balance sheet and cash flow in the standard account order, in the unit the company's
documents use. "Unmapped / other" is the part of a printed subtotal its mapped lines do not explain.

```sql period_list
select distinct period, period_months, period_end from analytics.statements
where company = '${inputs.company.value}' and scope = '${inputs.scope.value}'
order by period_months <> 12, period_end desc
```

<Dropdown name=period data={period_list} value=period order="period_end desc" title="Period" />

```sql statement_lines
select
    statement,
    line_no,
    repeat('· ', cast(depth as integer)) || label as line,
    value,
    unit
from analytics.statements
where company = '${inputs.company.value}' and scope = '${inputs.scope.value}'
  and period = '${inputs.period.value}'
order by statement, line_no
```

### Profit and loss

```sql profit_and_loss
select line, value, unit from ${statement_lines} where statement = 'profit_and_loss' order by line_no
```

<DataTable data={profit_and_loss} rows=all emptySet=pass emptyMessage="None loaded." />

### Balance sheet

```sql balance_sheet
select line, value, unit from ${statement_lines} where statement = 'balance_sheet' order by line_no
```

<DataTable data={balance_sheet} rows=all emptySet=pass emptyMessage="None loaded." />

### Cash flow

```sql cash_flow_statement
select line, value, unit from ${statement_lines} where statement = 'cash_flow' order by line_no
```

<DataTable data={cash_flow_statement} rows=all emptySet=pass emptyMessage="None loaded." />

## Findings and coverage

Findings are figures in what the company filed that do not add up, or that differ between documents or from
a reading: open ones wait for a decision, accepted ones are kept as filed. Coverage is the share of each
statement's figures that is mapped to an account (the rest is left out of every metric).

```sql company_findings
select status, document_status, file_name, code, period, label, finding, fin_source, other_source
from analytics.findings
where company = '${inputs.company.value}'
order by status, file_name, period
```

<DataTable data={company_findings} rows=all emptySet=pass emptyMessage="None loaded." />

```sql company_coverage
select scope, statement, figures, mapped, mapped_share, claude_unverified
from analytics.coverage
where company = '${inputs.company.value}'
order by scope, statement
```

<DataTable data={company_coverage} rows=all emptySet=pass emptyMessage="None loaded.">
    <Column id=scope />
    <Column id=statement />
    <Column id=figures />
    <Column id=mapped />
    <Column id=mapped_share fmt=pct1 title="Mapped share" />
    <Column id=claude_unverified title="Claude-read, unverified" />
</DataTable>
