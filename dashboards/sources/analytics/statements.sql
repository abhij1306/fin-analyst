select
    company, scope, period, period_months, period_end, statement, line_no, depth, line_type,
    label, cast(value as double) as value, unit
from statements
