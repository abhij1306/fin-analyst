-- One placeholder row (company NULL) when empty: see findings.sql.
select company, file_name, page, statement, code, reason from unread_pages
union all
select null, null, null, null, null, null where not exists (select 1 from unread_pages)
