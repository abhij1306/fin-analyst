"""`config.toml [forensic]`: the forensic tests' thresholds and the user's inputs (SPEC-18).

Each setting has a default and the reason for it (docs/decisions/forensic.md). The value used for a
company is its `[forensic.companies.<name>]` key, else the `[forensic]` key, else the default. A
family declares its settings in `SETTINGS` (fin.forensic.families); `min_amount` is shared.
`set_value` changes one key of the company's table (`fin forensic set`, the Settings screen): every
other line of config.toml is kept, and the new text is checked (it must parse, with only that
change) before it replaces the file. Each change is in the audit log with its actor. E990 for an
unknown key, a bad value or a table this cannot edit.
"""

import json
import re
import tomllib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fin.audit.errors import ForensicSettingInvalid
from fin.audit.logger import Run
from fin.config import load_config
from fin.paths import atomic_write, config_path

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
THRESHOLD = "query_threshold"
DEFAULT = "default"  # `fin forensic set <co> <key> default` removes the company's value


@dataclass(frozen=True)
class Setting:
    """kind: 'whole' (an integer >= minimum), 'number' (rupees > 0), 'fraction' (above 0, below 1),
    'amount' (rupees > 0 or "query_threshold"), 'names' (a list of names), 'weekdays', 'dates'
    (MM-DD every year or YYYY-MM-DD) or 'limits' (a list of rupee amounts > 0). A default of None
    is an input the user keeps: a test that needs it cannot run until it is set."""

    key: str
    kind: str
    default: Any
    reason: str
    minimum: int = 1


SHARED = (
    Setting(
        "min_amount",
        "amount",
        THRESHOLD,
        "single-entry tests flag only amounts at or above the engagement's query threshold, so "
        "a test does not flood a real ledger",
    ),
)


def all_settings() -> dict[str, Setting]:
    """Every setting: the shared ones, then each family's."""
    from fin.forensic.registry import families

    found = {s.key: s for s in SHARED}
    for module in families():
        for setting in getattr(module, "SETTINGS", ()):
            found[setting.key] = setting
    return found


def _bad(message: str, **seen: Any) -> ForensicSettingInvalid:
    return ForensicSettingInvalid(message).with_context(**seen)


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        number = (
            Decimal(str(value).replace(",", "")) if isinstance(value, int | Decimal | str) else None
        )
    except InvalidOperation:
        return None
    return number if number is not None and number.is_finite() else None


def _date(text: Any) -> str | None:
    if isinstance(text, date):
        return text.isoformat()
    if not isinstance(text, str):
        return None
    try:
        if re.fullmatch(r"\d{2}-\d{2}", text):
            date(2024, int(text[:2]), int(text[3:]))  # 2024 has 29 February
            return text
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return date.fromisoformat(text).isoformat()
    except ValueError:
        return None
    return None


def check(setting: Setting, value: Any) -> Any:
    """The value in the form the tests use. E990 when it is not of the setting's kind."""
    kind, key = setting.kind, setting.key
    if kind == "whole":
        if isinstance(value, int) and not isinstance(value, bool) and value >= setting.minimum:
            return value
        raise _bad(
            f"{key} = {value!r}; expected a whole number, at least {setting.minimum}", key=key
        )
    if kind in ("number", "amount"):
        if kind == "amount" and value == THRESHOLD:
            return value
        number = _number(value)
        if number is not None and number > 0:
            return number
        also = ' or "query_threshold"' if kind == "amount" else ""
        raise _bad(f"{key} = {value!r}; expected rupees above 0{also}", key=key)
    if kind == "fraction":
        number = _number(value)
        if number is not None and 0 < number < 1:
            return number
        raise _bad(
            f"{key} = {value!r}; expected a fraction above 0 and below 1, e.g. 0.02", key=key
        )
    if not isinstance(value, list | tuple) or not value:
        raise _bad(f"{key} = {value!r}; expected a list of one or more values", key=key)
    if kind == "names" and all(isinstance(v, str) and v.strip() for v in value):
        return tuple(dict.fromkeys(v.strip() for v in value))
    if kind == "weekdays" and all(v in WEEKDAYS for v in value):
        return tuple(dict.fromkeys(value))
    if kind == "dates" and all(_date(v) for v in value):
        return tuple(dict.fromkeys(_date(v) for v in value))
    if kind == "limits":
        numbers = [_number(v) for v in value]
        if all(n is not None and n > 0 for n in numbers):
            return tuple(sorted(set(numbers)))  # type: ignore[type-var]
    expected = {
        "names": "names",
        "weekdays": f"weekday names ({', '.join(WEEKDAYS)})",
        "dates": "dates as MM-DD (every year) or YYYY-MM-DD",
        "limits": "rupee amounts above 0",
    }[kind]
    raise _bad(f"{key} = {value!r}; expected a list of {expected}", key=key)


def _tables(config_forensic: dict[str, Any], company: str) -> tuple[dict, dict]:
    known = all_settings()
    own = {k: v for k, v in config_forensic.items() if k != "companies"}
    companies = config_forensic.get("companies", {})
    if not isinstance(companies, dict) or not all(isinstance(t, dict) for t in companies.values()):
        raise _bad("[forensic.companies] must hold one table per company")
    mine = companies.get(company, {})
    for where, table in (("[forensic]", own), (f"[forensic.companies.{company}]", mine)):
        unknown = sorted(set(table) - set(known))
        if unknown:
            raise _bad(f"{where} has unknown key(s) {', '.join(unknown)}", keys=unknown)
    return own, mine


def resolve(root: Path, company: str) -> dict[str, Any]:
    """key -> the value a company's tests use (None: an input not set)."""
    return values(load_config(root).forensic, company)


def values(config_forensic: dict[str, Any], company: str) -> dict[str, Any]:
    own, mine = _tables(config_forensic, company)
    out = {}
    for key, setting in all_settings().items():
        raw = mine.get(key, own.get(key, setting.default))
        out[key] = None if raw is None else check(setting, raw)
    return out


def rows(root: Path, company: str) -> list[dict[str, Any]]:
    """Each setting with its value, default, where the value comes from and why (`fin forensic
    settings`, the Settings screen)."""
    forensic = load_config(root).forensic
    own, mine = _tables(forensic, company)
    used = values(forensic, company)
    out = []
    for key, setting in sorted(all_settings().items()):
        origin = "company" if key in mine else "config" if key in own else "default"
        out.append(
            {
                "key": key,
                "value": show(used[key]),
                "default": show(setting.default),
                "from": origin if used[key] is not None else "not set",
                "reason": setting.reason,
                "kind": setting.kind,
            }
        )
    return out


def show(value: Any) -> str:
    """A value as the user types it back: lists comma-separated."""
    if value is None:
        return "not set"
    if isinstance(value, list | tuple):
        return ", ".join(str(v) for v in value)
    return str(value)


def parse_text(setting: Setting, text: str) -> Any:
    """What the user typed (`fin forensic set`, a Settings field) as a TOML value."""
    text = text.strip()
    if setting.kind in ("whole",):
        if re.fullmatch(r"\d+", text):
            return int(text)
        raise _bad(f"{setting.key} = {text!r}; expected a whole number", key=setting.key)
    if setting.kind in ("number", "amount", "fraction"):
        if setting.kind == "amount" and text == THRESHOLD:
            return THRESHOLD
        number = _number(text)
        if number is None:
            raise _bad(f"{setting.key} = {text!r}; expected a number", key=setting.key)
        return number
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if setting.kind == "limits":
        return [_number(p) if _number(p) is not None else p for p in parts]
    return parts


def _toml(value: Any) -> str:
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return str(int(value))
    return str(value)


def _header(company: str) -> re.Pattern[str]:
    name = re.escape(company)
    return re.compile(rf'^\[forensic\.companies\.(?:{name}|"{name}")\][ \t]*(?:#.*)?$', re.M)


def set_value(
    run: Run, root: Path, company: str, key: str, text: str, actor: str
) -> dict[str, Any]:
    """Set (or with "default", remove) one key of the company's [forensic.companies.<name>]
    table. Returns {key, value, actor}. E990 for an unknown key, a bad value or a table this cannot
    edit."""
    known = all_settings()
    if key not in known:
        raise _bad(
            f"no forensic setting {key!r}; the settings are {', '.join(sorted(known))}", key=key
        )
    config = load_config(root)
    path = config_path(root)
    data = tomllib.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    _tables(config.forensic, company)
    table = dict(config.forensic.get("companies", {}).get(company, {}))
    if text.strip() == DEFAULT:
        table.pop(key, None)
    else:
        value = parse_text(known[key], text)
        check(known[key], value)
        table[key] = value
    forensic = dict(data.get("forensic", {}))
    companies = dict(forensic.get("companies", {}))
    if table:
        companies[company] = table
    else:
        companies.pop(company, None)
    forensic["companies"] = companies
    expected = {**data, "forensic": forensic}
    values(forensic, company)  # the new table must still be valid as a whole
    candidate = _replace_table(path.read_text(encoding="utf-8"), company, table)
    try:
        ok = _normal(tomllib.loads(candidate, parse_float=Decimal)) == _normal(expected)
    except tomllib.TOMLDecodeError:
        ok = False
    if not ok:
        raise _bad(
            f"cannot edit [forensic.companies.{company}] in {path}; write it as its own table "
            f"([forensic.companies.{company}] on a line, then key = value lines)",
            company=company,
        )
    atomic_write(path, candidate)
    shown = show(values(forensic, company)[key])
    run.event(
        "forensic",
        f"{company}: forensic setting {key} = {shown} (by {actor})",
        context={"company": company, "key": key, "value": shown, "actor": actor},
    )
    return {"key": key, "value": shown, "actor": actor}


def _normal(data: dict[str, Any]) -> dict[str, Any]:
    """The parsed file with an empty [forensic.companies] (and so an empty [forensic]) as none."""
    out = dict(data)
    forensic = dict(out.get("forensic", {}))
    if forensic.get("companies") == {}:
        forensic.pop("companies")
    if forensic:
        out["forensic"] = forensic
    else:
        out.pop("forensic", None)
    return out


def _replace_table(text: str, company: str, table: dict[str, Any]) -> str:
    """config.toml with the company's table written as `table` (removed when empty)."""
    body = "".join(f"{k} = {_toml(v)}\n" for k, v in table.items())
    section = f"[forensic.companies.{company}]\n{body}" if table else ""
    found = _header(company).search(text)
    if found is None:
        return text.rstrip("\n") + "\n\n" + section if section else text
    end = re.compile(r"^[ \t]*\[", re.M).search(text, found.end())
    stop = end.start() if end else len(text)
    rest = text[stop:]
    return text[: found.start()] + section + ("\n" if section and rest else "") + rest
