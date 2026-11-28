-- Evidence cannot load a source with no rows: an empty table gives one placeholder row with company NULL.
select company, period, granularity, as_of, basis, cast(trade_receivables as double) as trade_receivables, cast(inventories as double) as inventories, cast(other_current_assets as double) as other_current_assets, cast(trade_payables as double) as trade_payables, cast(other_current_liabilities as double) as other_current_liabilities, cast(net_working_capital as double) as net_working_capital, unmapped_ledgers, ledgers_included, ledgers_excluded, missing_components
from qoe_nwc
union all
select null, null, null, null, null, null, null, null, null, null, null, null, null, null, null
where not exists (select 1 from qoe_nwc)
