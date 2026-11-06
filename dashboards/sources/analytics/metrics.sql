select
    company,
    fiscal_year,
    period,
    period_months,
    period_end,
    scope,
    metric,
    unit,
    cast(value as double) as value
from metrics
