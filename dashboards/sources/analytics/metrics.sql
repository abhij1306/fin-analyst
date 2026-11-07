select
    company,
    fiscal_year,
    period,
    period_months,
    period_end,
    scope,
    category,
    metric,
    unit,
    cast(value as double) as value
from metrics
