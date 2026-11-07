---
title: Data quality
---

What is not yet settled, per company. Unconfirmed facts (unmapped labels or mappings still pending review)
are left out of every metric. Unverified Claude- or Docling-read facts were read from pages fin could not read,
by Claude (`fin ingest --reading`) or by the Docling PDF add-on, and no reading has agreed with them yet
(`fin verify`).

Findings are figures in what a company filed that do not add up, or that differ between documents or from a
reading. A held document has open findings and is left out of the metrics until they are accepted (kept as
filed) or fixed. Accepted findings stay counted. Failed validations are all-time counts of failed checks
from the audit log.

```sql quality
select company, documents, held_documents, open_findings, accepted_findings, facts, unconfirmed_facts,
    unverified_claude_facts, failed_validations, last_ingested_at, refreshed_at
from analytics.companies
order by company
```

<DataTable data={quality} rows=all emptySet=pass emptyMessage="None loaded." />

## Findings

Open and accepted findings with the figure in the document, what it was compared with, and where each was
read (page or sheet!cell).

```sql findings
select company, status, document_status, file_name, code, statement, scope, period, label,
    fin_value, other_value, fin_source, other_source, other_file, finding
from analytics.findings
where company is not null
order by company, status, file_name, period
```

<DataTable data={findings} rows=all emptySet=pass emptyMessage="None loaded." />

## Coverage

The share of each statement's figures in loaded documents that is mapped to an account.

```sql coverage
select company, scope, statement, figures, mapped, mapped_share, claude_unverified
from analytics.coverage
order by company, scope, statement
```

<DataTable data={coverage} rows=all emptySet=pass emptyMessage="None loaded.">
    <Column id=company />
    <Column id=scope />
    <Column id=statement />
    <Column id=figures />
    <Column id=mapped />
    <Column id=mapped_share fmt=pct1 title="Mapped share" />
    <Column id=claude_unverified title="Claude- or Docling-read, unverified" />
</DataTable>

## Unread pages

Statement pages fin could not read. Claude can read them (`fin ingest --reading`).

```sql unread
select company, file_name, page, statement, code, reason from analytics.unread_pages
where company is not null
order by company, file_name, page
```

<DataTable data={unread} rows=all emptySet=pass emptyMessage="None loaded." />

## Unverified Claude-read facts

```sql unverified
select company, file_name, scope, period, statement, label, account, value, source
from analytics.unverified_facts
where company is not null
order by company, file_name, period
```

<DataTable data={unverified} rows=all emptySet=pass emptyMessage="None loaded." />

Resolve unconfirmed items with `/review`; see each finding with `fin findings list`; verify Claude-read
figures with `fin verify`.
