-- Evidence cannot load a source with no rows, so an empty table gives one placeholder row with company NULL;
-- pages filter on `company is not null`. Same in unread_pages.sql.
select
    company, finding_id, status, document_status, file_name, code, statement, scope, period, label,
    cast(fin_value as double) as fin_value, cast(other_value as double) as other_value,
    fin_source, other_source, other_file, finding
from findings
union all
select
    null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null
where not exists (select 1 from findings)
