"""Saved metrics: `fin metric add|list|remove` (docs/product.md "Saved metrics").

A saved metric is a formula over accounts and metrics, such as `cfo / revenue`. It is saved for one
company (its database) or for every company (shared/dictionary.duckdb, copied into each company
database, also into one created later). fin compiles the saved formulas into the view
saved_metrics_wide (company_metrics.sql), so a saved metric is computed in SQL with the built-in
ones and is in `metrics`, `metric_definitions` (category 'user') and `metric_inputs`, and so in
everything that reads them: the Excel export, the analytics tables and dashboards, reports.

A formula has names, numbers, + - * / and brackets. A name is a built-in metric, a metric saved
before it, or an account; a name that is both a metric and an account (revenue, pat, cfo) is the
metric, which is the printed value. An account is its current_facts value. A missing input or a zero
divisor gives no value. The formula is computed in DOUBLE and rounded once: rupees to 2 places,
ratio and days to 6.
"""

import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import duckdb

from fin.audit.errors import DependencyMissing, SavedMetricInvalid, SavedMetricUnknown
from fin.audit.logger import Run
from fin.db.conn import connect, no_company_message, transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.db.schema import in_memory
from fin.paths import companies_dir, company_db_path, dictionary_db_path
from fin.query.write import backup

UNITS = ("ratio", "rupees", "days")
NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")
# A number, a name or one other character, after any spaces.
_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)?)|([a-z_][a-z0-9_]*)|(\S))")
_OPERATORS = frozenset("+-*/()")
_COLUMNS = "metric, unit, definition, formula, added_at, run_id"


@dataclass(frozen=True)
class Compiled:
    """A formula as SQL over metrics_wide (`w`) and the account values (`a`)."""

    sql: str
    periods: str
    uses: tuple[str, ...]
    operands: frozenset[tuple[str, str]]  # (name, 'account' or 'metric')


@dataclass
class Catalogue:
    metrics: dict[str, str]  # built-in metric -> its periods
    accounts: frozenset[str]
    saved: dict[str, Compiled] = field(default_factory=dict)

    def fresh(self) -> "Catalogue":
        """The same built-in metrics and accounts, no saved metrics yet."""
        return Catalogue(self.metrics, self.accounts)


def _invalid(formula: str, why: str) -> SavedMetricInvalid:
    return SavedMetricInvalid(f"the formula {formula!r} cannot be used: {why}. Nothing saved")


def _tokens(formula: str) -> list[str]:
    tokens, position, text = [], 0, formula.strip()
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None:  # cannot happen: \S matches any character
            raise _invalid(formula, f"unreadable at {text[position:]!r}")
        token = match.group(match.lastindex or 0)
        if match.lastindex == 3 and token not in _OPERATORS:
            raise _invalid(
                formula, f"{token!r} is not allowed (use names, numbers, + - * / and ())"
            )
        tokens.append(token)
        position = match.end()
    return tokens


class _Parser:
    """expr := term (+|- term)*; term := factor (*|/ factor)*; factor := -factor | (expr) | number
    | name."""

    def __init__(self, formula: str, catalogue: Catalogue) -> None:
        self.formula, self.catalogue = formula, catalogue
        self.tokens, self.i = _tokens(formula), 0
        self.operands: set[tuple[str, str]] = set()
        self.uses: list[str] = []
        self.annual = False

    def peek(self) -> str | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> str | None:
        token = self.peek()
        self.i += 1
        return token

    def expr(self) -> str:
        left = self.term()
        while self.peek() in ("+", "-"):
            operator = self.take()
            left = f"({left} {operator} {self.term()})"
        return left

    def term(self) -> str:
        left = self.factor()
        while self.peek() in ("*", "/"):
            operator, right = self.take(), self.factor()
            left = f"({left} * {right})" if operator == "*" else f"({left} / nullif({right}, 0))"
        return left

    def factor(self) -> str:
        part = self.take()
        if part is None:
            raise _invalid(self.formula, "it ends too early")
        if part == "-":
            return f"(-{self.factor()})"
        if part == "(":
            inner = self.expr()
            if self.take() != ")":
                raise _invalid(self.formula, "a bracket is not closed")
            return inner
        if part[0].isdigit():
            return f"CAST({part} AS DOUBLE)"
        if part in _OPERATORS:
            raise _invalid(self.formula, f"{part!r} where a name or number should be")
        return self.name(part)

    def name(self, token: str) -> str:
        catalogue = self.catalogue
        if token in catalogue.saved:
            saved = catalogue.saved[token]
            self.uses.append(token)
            self.operands |= saved.operands
            self.annual |= saved.periods == "annual"
            return f"CAST({saved.sql} AS DOUBLE)"
        if token in catalogue.metrics:
            self.operands.add((token, "metric"))
            self.annual |= catalogue.metrics[token] == "annual"
            return f'CAST(w."{token}" AS DOUBLE)'
        if token in catalogue.accounts:
            self.operands.add((token, "account"))
            return f'CAST(a."{token}" AS DOUBLE)'
        raise _invalid(
            self.formula,
            f"{token} is not an account or a metric (`uv run fin map accounts`, "
            "`SELECT metric FROM metric_definitions`)",
        )


def compile_formula(formula: str, unit: str, catalogue: Catalogue) -> Compiled:
    """The SQL of `formula` (E506 when it does not parse or names something unknown)."""
    parser = _Parser(formula, catalogue)
    expression = parser.expr()
    if parser.peek() is not None:
        raise _invalid(formula, f"unexpected {parser.peek()!r}")
    places = 2 if unit == "rupees" else 6
    return Compiled(
        sql=f"CAST(round({expression}, {places}) AS DECIMAL(38, 6))",
        periods="annual" if parser.annual else "all",
        uses=tuple(dict.fromkeys(parser.uses)),
        operands=frozenset(parser.operands),
    )


@cache
def builtin_metrics() -> dict[str, str]:
    """Every built-in metric and its periods (metric_definitions of an empty company database)."""
    with in_memory("company") as con:
        return dict(con.execute("SELECT metric, periods FROM metric_definitions").fetchall())


def _catalogue(root: Path) -> Catalogue:
    with connect(dictionary_db_path(root)) as con:
        accounts = frozenset(a for (a,) in con.execute("SELECT account FROM accounts").fetchall())
    return Catalogue(builtin_metrics(), accounts)


def compile_all(rows: list[dict[str, Any]], catalogue: Catalogue) -> dict[str, Compiled]:
    """Compile `rows` in order; each may use the ones before it."""
    for row in rows:
        catalogue.saved[row["metric"]] = compile_formula(row["formula"], row["unit"], catalogue)
    return dict(catalogue.saved)


def view_sql(compiled: dict[str, Compiled]) -> str:
    """saved_metrics_wide: metrics_wide plus one column per saved metric."""
    head = "CREATE OR REPLACE VIEW saved_metrics_wide AS "
    if not compiled:
        return head + "SELECT * FROM metrics_wide"
    columns = ", ".join(f'{c.sql} AS "{name}"' for name, c in compiled.items())
    accounts = sorted({n for c in compiled.values() for n, kind in c.operands if kind == "account"})
    if not accounts:
        return head + f"SELECT w.*, {columns} FROM metrics_wide w"
    pivot = ", ".join(f"max(value) FILTER (WHERE account = '{n}') AS \"{n}\"" for n in accounts)
    return head + (
        f"WITH a AS (SELECT period, scope, {pivot} FROM current_facts GROUP BY period, scope) "
        f"SELECT w.*, {columns} FROM metrics_wide w "
        "LEFT JOIN a ON a.period = w.period AND a.scope = w.scope"
    )


def _apply(
    con: duckdb.DuckDBPyConnection, rows: list[dict[str, Any]], catalogue: Catalogue
) -> None:
    """Rewrite a company's saved_metrics, saved_metric_operands and saved_metrics_wide from `rows`
    (shared first, then the company's), in the caller's transaction."""
    compiled = compile_all(rows, catalogue)
    con.execute("DELETE FROM saved_metric_operands")
    con.execute("DELETE FROM saved_metrics")
    for position, row in enumerate(rows, 1):
        c = compiled[row["metric"]]
        con.execute(
            "INSERT INTO saved_metrics (metric, unit, periods, shared, definition, formula, uses, "
            "position, added_at, run_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [row["metric"], row["unit"], c.periods, row["shared"], row["definition"],
             row["formula"], list(c.uses), position, row["added_at"], row["run_id"]],
        )  # fmt: skip
        con.executemany(
            "INSERT INTO saved_metric_operands VALUES (?, ?, ?)",
            [[row["metric"], name, kind] for name, kind in sorted(c.operands)],
        )
    con.execute(view_sql(compiled))
    con.execute("SELECT count(*) FROM metrics").fetchall()  # binds the new view


def _shared_rows(root: Path) -> list[dict[str, Any]]:
    path = dictionary_db_path(root)
    with connect(path) as con:
        rows = fetch_dicts(con, f"SELECT {_COLUMNS} FROM saved_metrics ORDER BY added_at, metric")
    return [{**row, "shared": True} for row in rows]


def _company_rows(path: Path) -> list[dict[str, Any]]:
    with connect(path) as con:
        return fetch_dicts(
            con,
            f"SELECT {_COLUMNS}, false AS shared FROM saved_metrics WHERE NOT shared "
            "ORDER BY position",
        )


def _plans(root: Path, company: str | None) -> dict[Path, list[dict[str, Any]]]:
    """The company databases a change touches, each with its own saved metrics."""
    if company is not None:
        path = company_db_path(root, company)
        if not path.is_file():
            raise DependencyMissing(no_company_message(company))
        return {path: _company_rows(path)}
    return {path: _company_rows(path) for path in sorted(companies_dir(root).glob("*.duckdb"))}


def _write(run: Run, plans: dict[Path, list[dict[str, Any]]], shared: list[dict[str, Any]]) -> None:
    catalogue = _catalogue(run.root)
    for path, own in plans.items():
        backup(run.root, path, run)
        with connect(path, write=True) as con, transaction(con):
            _apply(con, shared + own, catalogue.fresh())


def add_metric(
    run: Run, name: str, formula: str, unit: str, definition: str, company: str | None
) -> dict[str, Any]:
    """Save a metric for `company`, or for every company when it is None. E506 when the name is
    taken or invalid, the unit unknown, the definition empty or the formula unusable."""
    if not NAME.fullmatch(name):
        raise SavedMetricInvalid(f"{name!r} is not a metric name (lower-case letters, digits, '_')")
    if unit not in UNITS:
        raise SavedMetricInvalid(f"unknown unit {unit!r}; use one of: {', '.join(UNITS)}")
    if not definition.strip():
        raise SavedMetricInvalid("give a definition: what the metric means, in words")
    root = run.root
    catalogue = _catalogue(root)
    shared = _shared_rows(root)
    plans = _plans(root, company)
    taken = set(catalogue.metrics) | catalogue.accounts | {r["metric"] for r in shared}
    taken |= {r["metric"] for own in plans.values() for r in own}
    if name in taken:
        raise SavedMetricInvalid(f"{name} is already a metric or an account; choose another name")
    row = {"metric": name, "unit": unit, "definition": definition.strip(), "formula": formula,
           "added_at": utcnow(), "run_id": run.run_id, "shared": company is None}  # fmt: skip
    if company is None:
        shared = [*shared, row]
    else:
        plans = {path: [*own, row] for path, own in plans.items()}
    # Every check before anything is written; the last compile has the new metric.
    compiled = compile_all(shared, catalogue.fresh())
    for own in plans.values():
        compiled = compile_all(shared + own, catalogue.fresh())
    if company is None:
        dictionary = dictionary_db_path(root)
        backup(root, dictionary, run)
        with connect(dictionary, write=True) as con:
            con.execute(
                f"INSERT INTO saved_metrics ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)",
                [row[c] for c in _COLUMNS.split(", ")],
            )
    _write(run, plans, shared)
    saved = compiled[name]
    run.event("metrics", f"saved metric {name} for {company or 'every company'}: {formula}")
    return {"metric": name, "company": company, "shared": company is None, "unit": unit,
            "periods": saved.periods, "formula": formula, "uses": list(saved.uses),
            "companies": [p.stem for p in plans]}  # fmt: skip


def remove_metric(run: Run, name: str, company: str | None) -> dict[str, Any]:
    """Remove a saved metric of `company`, or a shared one when it is None. E507 when there is no
    such saved metric or another saved metric uses it."""
    root = run.root
    shared = _shared_rows(root)
    plans = _plans(root, company)
    pool = shared if company is None else next(iter(plans.values()))
    if name not in {r["metric"] for r in pool}:
        where = "saved for every company" if company is None else f"saved for {company}"
        raise SavedMetricUnknown(f"no metric {name} {where} (`uv run fin metric list`)")
    catalogue = _catalogue(root)
    users = set()
    for own in [*plans.values(), []]:
        compiled = compile_all(shared + own, catalogue.fresh())
        users |= {m for m, c in compiled.items() if name in c.uses}
    if users:
        raise SavedMetricUnknown(
            f"{name} is used by {', '.join(sorted(users))}; remove those first"
        )
    if company is None:
        shared = [r for r in shared if r["metric"] != name]
        dictionary = dictionary_db_path(root)
        backup(root, dictionary, run)
        with connect(dictionary, write=True) as con:
            con.execute("DELETE FROM saved_metrics WHERE metric = ?", [name])
    else:
        plans = {path: [r for r in own if r["metric"] != name] for path, own in plans.items()}
    _write(run, plans, shared)
    run.event("metrics", f"removed saved metric {name} of {company or 'every company'}")
    return {
        "metric": name,
        "company": company,
        "removed": True,
        "companies": [p.stem for p in plans],
    }


def list_metrics(root: Path, company: str | None) -> list[dict[str, Any]]:
    """The metrics saved for `company` (its own and the shared ones), or the shared ones."""
    if company is None:
        return _shared_rows(root)
    path = company_db_path(root, company)
    if not path.is_file():
        raise DependencyMissing(no_company_message(company))
    with connect(path) as con:
        return fetch_dicts(
            con,
            "SELECT metric, unit, periods, shared, definition, formula, uses, added_at, run_id "
            "FROM saved_metrics ORDER BY position",
        )


def install_shared(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    """Copy the shared saved metrics into the company database just created at `path` (`con`).
    E506 when a shared formula no longer compiles."""
    root = path.parent.parent
    if path.parent != companies_dir(root) or not dictionary_db_path(root).is_file():
        return
    shared = _shared_rows(root)
    if shared:
        with transaction(con):
            _apply(con, shared, _catalogue(root))
