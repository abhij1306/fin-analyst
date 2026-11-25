---
title: Customers and sectors
---

Customer, sector and pricing analytics from a diligence engagement's sales register (Amount in Rs, before GST),
per company. Customers are tokens. Core and pass-through revenue are always shown separately: retention and
price/volume/mix are core only and annual only (the current-year stub is a part year and is never included).
Shares and retention are fractions (0.18 is 18%).

```sql company_list
select distinct company from analytics.customer_concentration where company is not null order by company
```

<Dropdown name=company data={company_list} value=company title="Company" />

## Customer concentration

Share of each segment's revenue from its largest 1, 5 and 10 customers.

```sql concentration
select period, segment, customers, revenue, top1_share, top5_share, top10_share
from analytics.customer_concentration
where company = '${inputs.company.value}'
order by period, segment
```

<DataTable data={concentration} rows=all emptySet=pass emptyMessage="None loaded.">
    <Column id=period />
    <Column id=segment />
    <Column id=customers />
    <Column id=revenue fmt=num0 />
    <Column id=top1_share fmt=pct1 />
    <Column id=top5_share fmt=pct1 />
    <Column id=top10_share fmt=pct1 />
</DataTable>

## Retention of core customers

Net revenue retention is the revenue this year from last year's customers over their revenue last year; gross
leaves out what retained customers spend more. Logo retention counts customers.

```sql retention
select period, prior_period, prior_customers, retained_customers, lost_customers, new_customers,
    prior_revenue, retained_revenue, lost_revenue, new_revenue, net_retention, gross_retention, logo_retention
from analytics.customer_retention
where company = '${inputs.company.value}'
order by period
```

<DataTable data={retention} rows=all emptySet=pass emptyMessage="None loaded.">
    <Column id=period />
    <Column id=prior_period />
    <Column id=prior_customers />
    <Column id=retained_customers />
    <Column id=lost_customers />
    <Column id=new_customers />
    <Column id=prior_revenue fmt=num0 />
    <Column id=retained_revenue fmt=num0 />
    <Column id=lost_revenue fmt=num0 />
    <Column id=new_revenue fmt=num0 />
    <Column id=net_retention fmt=pct1 />
    <Column id=gross_retention fmt=pct1 />
    <Column id=logo_retention fmt=pct1 />
</DataTable>

## Sector mix

A customer's sector is the one the user confirmed; the others are shown as unmapped.

```sql sectors
select period, segment, sector, customers, revenue, share
from analytics.sector_mix
where company = '${inputs.company.value}'
order by period, segment, revenue desc
```

<DataTable data={sectors} rows=all emptySet=pass emptyMessage="None loaded.">
    <Column id=period />
    <Column id=segment />
    <Column id=sector />
    <Column id=customers />
    <Column id=revenue fmt=num0 />
    <Column id=share fmt=pct1 />
</DataTable>

## Price, volume and mix

Core products with a quantity and a rate in two consecutive years: the change in their revenue split into price,
volume and mix, plus the revenue of products new or discontinued.

```sql pvm
select period, prior_period, price_effect, volume_effect, mix_effect, new_revenue, discontinued_revenue, change
from analytics.price_volume_mix
where company = '${inputs.company.value}'
order by period
```

<DataTable data={pvm} rows=all emptySet=pass emptyMessage="None loaded.">
    <Column id=period />
    <Column id=prior_period />
    <Column id=price_effect fmt=num0 />
    <Column id=volume_effect fmt=num0 />
    <Column id=mix_effect fmt=num0 />
    <Column id=new_revenue fmt=num0 />
    <Column id=discontinued_revenue fmt=num0 />
    <Column id=change fmt=num0 />
</DataTable>
