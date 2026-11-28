"""`config.toml` in the data folder: tolerances, display unit, defaults, diligence, data room.

`fin init` writes `DEFAULT_CONFIG_TOML` once and never overwrites it. After that the file is the
only source of these values: `load_config` reads it strictly and raises a coded error when it is
missing (E001) or invalid (E002). There are no fallback values, except that a file without the
`[pdf]` table (written before the PDF add-on) has chosen none, and a file without the `[fdd]`
table (or one of its keys) has the plan's stated FDD assumptions (`FDD_DEFAULTS`, docs/fdd.md).
The one line fin changes is `[pdf] parser`, through `fin parser use <name>` (`set_parser`).
"""

import re
import tomllib
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from fin.audit.errors import (
    DataFolderNotWritable,
    DependencyMissing,
    DependencyVersionMismatch,
)
from fin.paths import config_path

CONFIG_VERSION = 1
# [display] unit -> rupees in one unit, and the caption a unit gets when no unit_label is given.
DISPLAY_UNITS = {
    "rupees": 1,
    "thousands": 1_000,
    "lakhs": 100_000,
    "millions": 1_000_000,
    "crores": 10_000_000,
}
UNIT_LABELS = {
    "rupees": "INR",
    "thousands": "INR '000",
    "lakhs": "INR lakh",
    "millions": "INR Mn",
    "crores": "INR Cr",
}
NEGATIVES = ("minus", "brackets")
DISPLAY_KEYS = ("unit", "decimals", "negatives", "unit_label")
SCOPES = ("standalone", "consolidated")
TOLERANCE_CHECKS = ("balance_sheet", "subtotal", "cash_flow", "cross_document")
PARSERS = ("none", "docling", "liteparse", "llamaparse")  # [pdf] parser: the PDF add-on
# [fdd]: each key's value when config.toml has none (the plan's stated assumptions, docs/fdd.md)
FDD_DEFAULTS: dict[str, Any] = {
    "coverage_top_n": 10,
    "gratuity_min_years": 5,
    "gratuity_cap": Decimal("2000000.00"),
    "basic_share": Decimal("0.5"),
    "cutoff_days": 15,
    "unrecorded_days": 60,
    "inquiry_topics": (
        "revenue_recognition",
        "cut_off",
        "mis_kpi",
        "accounting_policies",
        "capitalisation",
        "provisions",
        "related_parties",
        "tax",
        "contingencies",
        "other",
    ),
}

DEFAULT_CONFIG_TOML = """\
# fin-analyst configuration. `fin init` writes this file once and never overwrites it. Every value
# here is required: fin stops with E001 if this file is missing and E002 if it is invalid.

config_version = 1

[display]
# How money is shown in fin statements, the Statements screen, the Excel export, reports, evidence
# pages and dashboards. Values are always stored in rupees; fin converts them in SQL.
# unit: rupees, thousands, lakhs, millions or crores. decimals: 0 to 4. negatives: "minus"
# (-1,234.5) or "brackets" ((1,234.5), and a zero shows as a dash). unit_label: the caption every
# table names its unit with.
unit = "millions"
decimals = 1
negatives = "brackets"
unit_label = "INR Mn"
# One company shown differently: a table named after it with any of the keys above, e.g.
# [display.companies.acme]
# unit = "lakhs"

[defaults]
# Statement scope used when a command is not told which one: standalone or consolidated.
scope = "standalone"
# First month of the fiscal year (4 = April, the Indian fiscal year Apr-Mar).
fiscal_year_start_month = 4

[tolerances]
# Largest difference, in rupees, that a validation check accepts.
balance_sheet = 100000.00
subtotal = 100000.00
cash_flow = 100000.00
cross_document = 100000.00

[diligence]
# Accountant queries: a difference at or above this many rupees becomes a query.
query_threshold = 100000.00
# Categories an adjustment may have.
adjustment_categories = [
    "management", "normalisation", "reclassification", "recast", "error",
]
# Bank matching (fin reconcile): a receipt matches an invoice when their dates are at most
# match_window_days apart (passes 2 and 3); a foreign-currency receipt takes its booked rupee
# value from a Ledger Vouchers receipt at most fx_window_days from its bank date; an unmatched
# invoice is overdue after invoice_due_days (credit terms; the client, 2026-11-27: 90 days). An
# engagement copies it when it opens; `fin engagement open --due-days` or `fin engagement terms`
# sets another value for one engagement.
match_window_days = 60
fx_window_days = 7
invoice_due_days = 90

[fdd]
# FDD extension (docs/fdd.md). coverage_top_n: how many of the top customers (core revenue) and
# the top vendors (purchases) `fin contracts coverage` checks for a contract on file.
coverage_top_n = 10
# FDD (docs/fdd.md). Indicative gratuity (F3): employees with at least gratuity_min_years of
# service; 15/26 x monthly wages x completed years, capped at gratuity_cap rupees; wages = gross
# salary x basic_share (an assumption: the payroll has no basic pay column). Labelled indicative.
gratuity_min_years = 5
gratuity_cap = 2000000.00
basic_share = 0.5
# Cut-off (F3): sales invoiced within cutoff_days of a year end whose service starts on the other
# side; purchases invoiced within unrecorded_days after a year end whose service started before it.
cutoff_days = 15
unrecorded_days = 60
# Topics of management inquiries (F2): `fin query raise --topic` takes one of these.
inquiry_topics = [
    "revenue_recognition", "cut_off", "mis_kpi", "accounting_policies", "capitalisation",
    "provisions", "related_parties", "tax", "contingencies", "other",
]

[dataroom]
# Data-room intake (`fin dataroom scan`): an archive that would unpack to more than this many
# megabytes is refused and listed, never unpacked.
archive_limit_mb = 2048

[pdf]
# The optional PDF add-on that reads the statement pages pdfplumber cannot (and scanned results
# filings): "none", "docling", "liteparse" or "llamaparse". `uv run fin parser use <name>` (or
# `./install.sh --parser <name>`) installs it and sets this.
parser = "none"
"""


@dataclass(frozen=True)
class Display:
    """How money is shown ([display]): the unit, decimals, the sign of a negative and the caption.
    The conversion itself is SQL (`fin.display`)."""

    unit: str
    decimals: int
    negatives: str
    unit_label: str

    @property
    def rupees_per_unit(self) -> int:
        return DISPLAY_UNITS[self.unit]

    def in_unit(self, unit: str | None) -> "Display":
        """These settings with another unit (a picker's), captioned with that unit's label."""
        if unit is None or unit == self.unit:
            return self
        return replace(self, unit=unit, unit_label=UNIT_LABELS[unit])


@dataclass(frozen=True)
class Config:
    display: Display
    default_scope: str
    fiscal_year_start_month: int
    tolerances: dict[str, Decimal]
    query_threshold: Decimal  # [diligence]: a difference at or above it becomes a query
    adjustment_categories: tuple[str, ...]  # [diligence]: the categories an adjustment may have
    archive_limit_mb: int  # [dataroom]: the most an archive may unpack to, in megabytes
    pdf_parser: str = "none"  # [pdf] parser: the PDF add-on for the pages pdfplumber skips
    match_window_days: int = 60  # [diligence]: date window of bank matching passes 2 and 3
    fx_window_days: int = 7  # [diligence]: window for a receipt's booked rupee value
    invoice_due_days: int = 90  # [diligence]: an unmatched invoice is overdue after this
    coverage_top_n: int = 10  # [fdd]: the top customers and vendors checked for a contract
    fdd: dict[str, Any] = field(default_factory=lambda: dict(FDD_DEFAULTS))  # [fdd], FDD_DEFAULTS
    display_companies: dict[str, Display] = field(default_factory=dict)  # [display.companies.*]

    def display_for(self, company: str | None) -> Display:
        """The company's display settings: its [display.companies.<name>] table, else [display]."""
        return self.display_companies.get(company or "", self.display)


# The value only, so indentation, a quoted key and a trailing comment are kept.
_PARSER_LINE = re.compile(
    r"""^([ \t]*)(["']?)parser\2[ \t]*=[ \t]*(["'])[a-z]*\3(?=[ \t]*(?:#|$))""", re.M
)
_PDF_TABLE = """
[pdf]
# The optional PDF add-on: "none", "docling", "liteparse" or "llamaparse" (`fin parser use`).
parser = "{value}"
"""


def set_parser(root: Path, name: str) -> None:
    """Record the chosen PDF add-on: `[pdf] parser` in config.toml (`fin parser use`). Every other
    line is kept as written. The new text is checked (it must parse, with only that value changed)
    before it replaces the file, so a failure leaves config.toml as it was. Raises E001 if
    config.toml is missing, E002 if it is invalid, `name` is not a parser, or its `[pdf]` table is
    written in a form this cannot edit."""
    load_config(root)
    path = config_path(root)
    if name not in PARSERS:
        raise _invalid(path, f"cannot set [pdf] parser = {name!r}; expected one of {PARSERS}")
    text = path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    if "pdf" in data:
        candidate = _PARSER_LINE.sub(lambda m: f'{m[1]}parser = "{name}"', text, count=1)
    else:
        candidate = text.rstrip("\n") + "\n" + _PDF_TABLE.format(value=name)
    expected = {**data, "pdf": {"parser": name}}
    try:
        ok = tomllib.loads(candidate) == expected
    except tomllib.TOMLDecodeError:
        ok = False
    if not ok:
        raise _invalid(path, 'cannot set [pdf] parser; write it as `parser = "none"`')
    temp = path.with_name(path.name + ".tmp")
    try:
        with temp.open("w", encoding="utf-8") as handle:
            handle.write(candidate)
        temp.replace(path)
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot write {path}: {exc}") from exc


def write_default_config(root: Path) -> bool:
    """Write the default config.toml if none exists. Returns True if written. Never overwrites."""
    path = config_path(root)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(DEFAULT_CONFIG_TOML)
    except FileExistsError:
        return False
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot write {path}: {exc}") from exc
    return True


def load_config(root: Path) -> Config:
    """Read and validate config.toml. Raises E001 if it is missing, E002 if it is invalid."""
    path = config_path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DependencyMissing(f"{path} is missing; run `uv run fin init` to create it") from exc
    except OSError as exc:
        raise DependencyVersionMismatch(f"cannot read {path}: {exc}") from exc
    try:
        data = tomllib.loads(text, parse_float=Decimal)
    except tomllib.TOMLDecodeError as exc:
        raise _invalid(path, f"not valid TOML ({exc})") from exc
    return _parse(data, path)


def _invalid(path: Path, problem: str) -> DependencyVersionMismatch:
    return DependencyVersionMismatch(f"{path} is invalid: {problem}")


def _keys(table: dict[str, Any], expected: tuple[str, ...], where: str, path: Path) -> None:
    missing = [key for key in expected if key not in table]
    unknown = sorted(set(table) - set(expected))
    if missing:
        raise _invalid(path, f"{where} is missing {', '.join(missing)}")
    if unknown:
        raise _invalid(path, f"{where} has unknown key(s) {', '.join(unknown)}")


def _table(data: dict[str, Any], key: str, path: Path) -> dict[str, Any]:
    value = data[key]
    if not isinstance(value, dict):
        raise _invalid(path, f"[{key}] must be a table")
    return value


def _choice(
    table: dict[str, Any], key: str, choices: tuple[str, ...], where: str, path: Path
) -> str:
    value = table[key]
    if value not in choices:
        raise _invalid(path, f"{where}.{key} = {value!r}; expected one of {', '.join(choices)}")
    return value


def _parse(data: dict[str, Any], path: Path) -> Config:
    # [pdf] and [fdd] are optional: a config.toml written before the PDF add-on or the FDD
    # extension has none. The add-on is opt-in, so no [pdf] means none chosen; no [fdd] means the
    # defaults of the default file.
    tables = ("config_version", "display", "defaults", "tolerances", "diligence", "dataroom")
    optional = tuple(name for name in ("pdf", "fdd") if name in data)
    _keys(data, (*tables, *optional), "the file", path)
    pdf = _table(data, "pdf", path) if "pdf" in data else {"parser": "none"}
    version = data["config_version"]
    if version != CONFIG_VERSION or isinstance(version, bool):
        raise _invalid(
            path, f"config_version = {version!r}; this fin reads version {CONFIG_VERSION}"
        )

    display, display_companies = _display(_table(data, "display", path), path)

    defaults = _table(data, "defaults", path)
    _keys(defaults, ("scope", "fiscal_year_start_month"), "[defaults]", path)
    scope = _choice(defaults, "scope", SCOPES, "defaults", path)
    month = defaults["fiscal_year_start_month"]
    if isinstance(month, bool) or not isinstance(month, int) or not 1 <= month <= 12:
        raise _invalid(
            path, f"defaults.fiscal_year_start_month = {month!r}; expected an integer 1-12"
        )

    table = _table(data, "tolerances", path)
    _keys(table, TOLERANCE_CHECKS, "[tolerances]", path)
    tolerances = {
        check: _rupees(table[check], f"tolerances.{check}", path) for check in TOLERANCE_CHECKS
    }

    diligence = _table(data, "diligence", path)
    days = ("match_window_days", "fx_window_days", "invoice_due_days")
    _keys(diligence, ("query_threshold", "adjustment_categories", *days), "[diligence]", path)
    for key in days:
        value = diligence[key]
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3650:
            raise _invalid(path, f"diligence.{key} = {value!r}; expected whole days, 0 to 3650")
    threshold = _rupees(diligence["query_threshold"], "diligence.query_threshold", path)
    categories = diligence["adjustment_categories"]
    if (
        not isinstance(categories, list)
        or not categories
        or not all(isinstance(c, str) and _CATEGORY.match(c) for c in categories)
        or len(set(categories)) != len(categories)
    ):
        raise _invalid(
            path,
            f"diligence.adjustment_categories = {categories!r}; expected a list of distinct "
            "names in lower-case letters, digits and '_'",
        )

    limit = _archive_limit(_table(data, "dataroom", path), path)
    _keys(pdf, ("parser",), "[pdf]", path)
    parser = _choice(pdf, "parser", PARSERS, "pdf", path)

    fdd = _fdd(_table(data, "fdd", path) if "fdd" in data else {}, path)

    return Config(
        display=display,
        default_scope=scope,
        fiscal_year_start_month=month,
        tolerances=tolerances,
        query_threshold=threshold,
        adjustment_categories=tuple(categories),
        archive_limit_mb=limit,
        pdf_parser=parser,
        match_window_days=diligence["match_window_days"],
        fx_window_days=diligence["fx_window_days"],
        invoice_due_days=diligence["invoice_due_days"],
        coverage_top_n=fdd["coverage_top_n"],
        fdd=fdd,
        display_companies=display_companies,
    )


def _display(table: dict[str, Any], path: Path) -> tuple[Display, dict[str, Display]]:
    """[display] and its [display.companies.<name>] tables (each with any of DISPLAY_KEYS)."""
    companies = table.get("companies", {})
    if not isinstance(companies, dict) or not all(isinstance(t, dict) for t in companies.values()):
        raise _invalid(path, "[display.companies] must hold one table per company")
    own = {key: value for key, value in table.items() if key != "companies"}
    base = _display_values(own, "display", path)
    return base, {
        name: _display_values(values, f"display.companies.{name}", path, base)
        for name, values in companies.items()
    }


def _display_values(
    table: dict[str, Any], where: str, path: Path, base: Display | None = None
) -> Display:
    """One display table. With `base` (a company's table) every key is optional: a missing one is
    the base's, and a unit without a unit_label gets the unit's label (UNIT_LABELS)."""
    if base is None:
        _keys(table, DISPLAY_KEYS, f"[{where}]", path)
    elif unknown := sorted(set(table) - set(DISPLAY_KEYS)):
        raise _invalid(path, f"[{where}] has unknown key(s) {', '.join(unknown)}")
    if "unit" in table:
        _choice(table, "unit", tuple(DISPLAY_UNITS), where, path)
    if "negatives" in table:
        _choice(table, "negatives", NEGATIVES, where, path)
    decimals = table.get("decimals", 0)
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 4:
        raise _invalid(path, f"{where}.decimals = {decimals!r}; expected a whole number, 0 to 4")
    label = table.get("unit_label", "-")
    if not isinstance(label, str) or not label.strip() or len(label) > 20:
        raise _invalid(
            path, f"{where}.unit_label = {label!r}; expected a caption of 1 to 20 characters"
        )
    if base is None:
        return Display(**table)
    own = dict(table)
    if "unit" in own and "unit_label" not in own:
        own["unit_label"] = UNIT_LABELS[own["unit"]]
    return Display(**{**asdict(base), **own})


def _archive_limit(table: dict[str, Any], path: Path) -> int:
    """[dataroom] archive_limit_mb: whole megabytes, 1 to 1,000,000."""
    _keys(table, ("archive_limit_mb",), "[dataroom]", path)
    limit = table["archive_limit_mb"]
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1_000_000:
        raise _invalid(
            path, f"dataroom.archive_limit_mb = {limit!r}; expected whole megabytes, 1 to 1000000"
        )
    return limit


def _fdd(table: dict[str, Any], path: Path) -> dict[str, Any]:
    """[fdd] with FDD_DEFAULTS for the keys it does not have. E002 for an unknown key or a value
    out of range."""
    unknown = sorted(set(table) - set(FDD_DEFAULTS))
    if unknown:
        raise _invalid(path, f"[fdd] has unknown key(s) {', '.join(unknown)}")
    out = {**FDD_DEFAULTS, **table}
    top_n = out["coverage_top_n"]
    if isinstance(top_n, bool) or not isinstance(top_n, int) or not 1 <= top_n <= 1000:
        raise _invalid(path, f"fdd.coverage_top_n = {top_n!r}; expected a whole number, 1 to 1000")
    for key, top in (("gratuity_min_years", 50), ("cutoff_days", 3650), ("unrecorded_days", 3650)):
        value = out[key]
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= top:
            raise _invalid(path, f"fdd.{key} = {value!r}; expected a whole number, 0 to {top}")
    out["gratuity_cap"] = _rupees(out["gratuity_cap"], "fdd.gratuity_cap", path)
    share = out["basic_share"]
    is_number = isinstance(share, int | Decimal) and not isinstance(share, bool)
    number = Decimal(share) if is_number else None
    if number is None or not number.is_finite() or not 0 < number <= 1:
        raise _invalid(path, f"fdd.basic_share = {share!r}; expected a number above 0, up to 1")
    out["basic_share"] = number
    topics = out["inquiry_topics"]
    ok = (
        isinstance(topics, list | tuple)
        and topics
        and all(isinstance(t, str) and _CATEGORY.match(t) for t in topics)
        and len(set(topics)) == len(topics)
    )
    if not ok:
        raise _invalid(
            path,
            f"fdd.inquiry_topics = {topics!r}; expected a list of different lower-case names "
            "such as revenue_recognition",
        )
    out["inquiry_topics"] = tuple(topics)
    return out


_CATEGORY = re.compile(r"^[a-z][a-z0-9_]*$")


def _rupees(value: Any, where: str, path: Path) -> Decimal:
    """A number of rupees >= 0 (an integer or a decimal, not a string or a bool)."""
    number = (
        Decimal(value) if isinstance(value, int | Decimal) and not isinstance(value, bool) else None
    )
    if number is None or not number.is_finite() or number < 0:
        raise _invalid(path, f"{where} = {value!r}; expected a number of rupees >= 0")
    return number
