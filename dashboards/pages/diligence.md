---
title: Diligence figures
---

The figures of a diligence engagement, per company: adjusted EBITDA, revenue split into core and pass-through, the
EBITDA-to-cash walk and working capital. Standalone and consolidated figures are shown separately. Money is in rupees;
margins and shares are fractions (0.18 is 18%). The current-year stub (when there is one) is unaudited management
accounts. Only accepted adjustments change adjusted EBITDA; proposed ones are shown beside it, not applied.

```sql company_list
select distinct company from analytics.qoe_ebitda where company is not null order by company
```

<Dropdown name=company data={company_list} value=company title="Company" />

## Adjusted EBITDA

```sql ebitda
select period, scope, revenue, adjusted_revenue, reported_ebitda, accepted_adjustments, adjusted_ebitda,
    reported_ebitda_margin, adjusted_ebitda_margin, proposed_adjustments
from analytics.qoe_ebitda
where company = '${inputs.company.value}'
order by scope, period
```

<DataTable data={ebitda} rows=all emptySet=pass emptyMessage="None loaded.">
    <Column id=period />
    <Column id=scope />
    <Column id=revenue fmt=num0 />
    <Column id=adjusted_revenue fmt=num0 />
    <Column id=reported_ebitda fmt=num0 />
    <Column id=accepted_adjustments fmt=num0 />
    <Column id=adjusted_ebitda fmt=num0 />
    <Column id=reported_ebitda_margin fmt=pct1 />
    <Column id=adjusted_ebitda_margin fmt=pct1 />
    <Column id=proposed_adjustments fmt=num0 title="Proposed, not applied" />
</DataTable>

## Core and pass-through revenue

Pass-through revenue is shown as project revenue; growth is on core revenue only.

```sql revenue
select period, basis, core_revenue, passthrough_revenue, passthrough_share, core_revenue_growth
from analytics.qoe_revenue
where company = '${inputs.company.value}'
order by period
```

<DataTable data={revenue} rows=all emptySet=pass emptyMessage="None loaded.">
    <Column id=period />
    <Column id=basis />
    <Column id=core_revenue fmt=num0 />
    <Column id=passthrough_revenue fmt=num0 />
    <Column id=passthrough_share fmt=pct1 />
    <Column id=core_revenue_growth fmt=pct1 />
</DataTable>

## EBITDA to cash

```sql walk
select period, scope, ebitda, adjustments, adjusted_ebitda, non_cash_and_other, working_capital_change,
    taxes_paid, other_operating, cfo, capex, free_cash_flow, cash_conversion
from analytics.qoe_walk
where company = '${inputs.company.value}'
order by scope, period
```

<DataTable data={walk} rows=all emptySet=pass emptyMessage="None loaded.">
    <Column id=period />
    <Column id=scope />
    <Column id=ebitda fmt=num0 />
    <Column id=adjustments fmt=num0 />
    <Column id=adjusted_ebitda fmt=num0 />
    <Column id=non_cash_and_other fmt=num0 />
    <Column id=working_capital_change fmt=num0 />
    <Column id=taxes_paid fmt=num0 />
    <Column id=other_operating fmt=num0 />
    <Column id=cfo fmt=num0 />
    <Column id=capex fmt=num0 />
    <Column id=free_cash_flow fmt=num0 />
    <Column id=cash_conversion fmt=pct1 />
</DataTable>

## Working capital

At each year-end (and the stub's end) from the trial balance; by month from the Ledger Vouchers, on the previous
year-end's closing balances, for the working capital ledgers whose movements tie to the trial balance. A month with
excluded ledgers is not the complete working capital.

```sql nwc
select period, granularity, as_of, basis, trade_receivables, inventories, other_current_assets, trade_payables,
    other_current_liabilities, net_working_capital, missing_components, unmapped_ledgers, ledgers_included,
    ledgers_excluded
from analytics.qoe_nwc
where company = '${inputs.company.value}'
order by as_of, granularity
```

<LineChart data={nwc.filter(d => d.granularity === 'month_end')} x=as_of y=net_working_capital />

<DataTable data={nwc} rows=all emptySet=pass emptyMessage="None loaded.">
    <Column id=period />
    <Column id=granularity />
    <Column id=as_of />
    <Column id=basis />
    <Column id=trade_receivables fmt=num0 />
    <Column id=inventories fmt=num0 />
    <Column id=other_current_assets fmt=num0 />
    <Column id=trade_payables fmt=num0 />
    <Column id=other_current_liabilities fmt=num0 />
    <Column id=net_working_capital fmt=num0 />
    <Column id=missing_components title="Left out of the net (no ledger)" />
    <Column id=unmapped_ledgers />
    <Column id=ledgers_included />
    <Column id=ledgers_excluded />
</DataTable>
