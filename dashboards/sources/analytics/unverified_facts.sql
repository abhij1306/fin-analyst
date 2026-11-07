-- One placeholder row (company NULL) when empty: see findings.sql.
select
    company, file_name, scope, period, statement, label, account, cast(value as double) as value,
    source
from unverified_facts
union all
select null, null, null, null, null, null, null, null, null
where not exists (select 1 from unverified_facts)
