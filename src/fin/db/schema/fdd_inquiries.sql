-- Company database: management inquiries (docs/fdd.md F2). Run after diligence.sql (accountant_queries).

-- A query that is an inquiry (`fin query raise --topic`): a question for management with no amount or source document.
-- It is an ordinary accountant_queries row (origin 'manual', no amount, sources a fixed note) and follows the same
-- life cycle; this row says which topic it is on. topic is one of config.toml [fdd] inquiry_topics (checked by fin,
-- not here, so the list can change). `fin sql --write` refuses this table (E806).
CREATE TABLE inquiries (
    query_id INTEGER PRIMARY KEY,
    topic VARCHAR NOT NULL
);
