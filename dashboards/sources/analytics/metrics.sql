select
    company,
    fiscal_year,
    scope,
    metric,
    unit,
    cast(value as double) as value
from metrics
