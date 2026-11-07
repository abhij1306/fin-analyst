"""`config.toml` in the data folder: tolerances, display unit, defaults (PLAN.md §6, §9).

`fin init` writes `DEFAULT_CONFIG_TOML` once and never overwrites it. After that the file is the
only source of these values: `load_config` reads it strictly and raises a coded error when it is
missing (E001) or invalid (E002). There are no fallback values, except that a file without the
`[pdf]` table (written before the Docling add-on) has not opted in to it. The one line fin changes
is `[pdf] docling`, through `fin init --docling` / `--no-docling` (run by install.sh).
"""

import re
import tomllib
from dataclasses import dataclass
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
DISPLAY_UNITS = ("rupees", "thousands", "lakhs", "crores")
SCOPES = ("standalone", "consolidated")
TOLERANCE_CHECKS = ("balance_sheet", "subtotal", "cash_flow", "cross_document")

DEFAULT_CONFIG_TOML = """\
# fin-analyst configuration. `fin init` writes this file once and never overwrites it. Every value
# here is required: fin stops with E001 if this file is missing and E002 if it is invalid.

config_version = 1

[display]
# Unit used to display money in reports and dashboards: rupees, thousands, lakhs or crores.
# Values are always stored in rupees.
unit = "crores"

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

[pdf]
# Docling, the optional PDF add-on: reads the statement pages pdfplumber cannot (and scanned results
# filings). `./install.sh --docling` installs it and sets this to true; `--no-docling` sets false.
docling = false
"""


@dataclass(frozen=True)
class Config:
    display_unit: str
    default_scope: str
    fiscal_year_start_month: int
    tolerances: dict[str, Decimal]
    docling: bool = False  # [pdf] docling: run the Docling fallback on pages pdfplumber skips


# The value only, so a trailing comment ("docling = false  # off") is kept.
_DOCLING_LINE = re.compile(r"^docling\s*=\s*(?:true|false)(?=[ \t]*(?:#.*)?$)", re.M)
_PDF_TABLE = """
[pdf]
# Docling, the optional PDF add-on (`./install.sh --docling` / `--no-docling`).
docling = {value}
"""


def set_docling(root: Path, on: bool) -> None:
    """Record whether the Docling add-on is installed: `[pdf] docling` in config.toml (`fin init
    --docling` / `--no-docling`, run by install.sh). Every other line is kept as written. Raises
    E001 if config.toml is missing, E002 if it is invalid."""
    load_config(root)
    path = config_path(root)
    text = path.read_text(encoding="utf-8")
    value = "true" if on else "false"
    if _DOCLING_LINE.search(text):
        text = _DOCLING_LINE.sub(f"docling = {value}", text, count=1)
    else:
        text = text.rstrip("\n") + "\n" + _PDF_TABLE.format(value=value)
    try:
        with path.open("w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError as exc:
        raise DataFolderNotWritable(f"cannot write {path}: {exc}") from exc
    load_config(root)


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
    # [pdf] is optional: a config.toml written before the Docling add-on has none, and the add-on
    # is opt-in, so no table means "not opted in".
    tables = ("config_version", "display", "defaults", "tolerances")
    _keys(data, (*tables, "pdf") if "pdf" in data else tables, "the file", path)
    pdf = _table(data, "pdf", path) if "pdf" in data else {"docling": False}
    version = data["config_version"]
    if version != CONFIG_VERSION or isinstance(version, bool):
        raise _invalid(
            path, f"config_version = {version!r}; this fin reads version {CONFIG_VERSION}"
        )

    display = _table(data, "display", path)
    _keys(display, ("unit",), "[display]", path)
    unit = _choice(display, "unit", DISPLAY_UNITS, "display", path)

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
    tolerances: dict[str, Decimal] = {}
    for check in TOLERANCE_CHECKS:
        value = table[check]
        number = (
            Decimal(value)
            if isinstance(value, int | Decimal) and not isinstance(value, bool)
            else None
        )
        if number is None or not number.is_finite() or number < 0:
            raise _invalid(
                path, f"tolerances.{check} = {value!r}; expected a number of rupees >= 0"
            )
        tolerances[check] = number

    _keys(pdf, ("docling",), "[pdf]", path)
    if not isinstance(pdf["docling"], bool):
        raise _invalid(path, f"pdf.docling = {pdf['docling']!r}; expected true or false")

    return Config(
        display_unit=unit,
        default_scope=scope,
        fiscal_year_start_month=month,
        tolerances=tolerances,
        docling=pdf["docling"],
    )
