"""`fin eval bundle [company] [--with-figures]`: one results file to share (Markdown and JSON).

It holds the fin version and git commit, a `fin doctor` summary, and per company the golden score,
the checks not passing (kind, period, scope, account, outcome, the kind of source location, the
related codes) and the counts (fin.evals.counts). It holds no real names: companies are
`company-1`, `company-2` (in the order of the company names), a ledger shows only as its mask token
(any other ledger as `ledger-1`, ...), and no file name, label, source text or note is included.
Before writing, every masked party name, company name and file name is looked for in it; one found
stops it (E833). By default it has no absolute figures, only the relative difference of a failing
check; `--with-figures` adds the expected and fin values in rupees."""

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from fin import __version__
from fin.audit.errors import BundleNotAnonymous, GoldenSetMissing, NothingToBundle
from fin.audit.logger import Run, git_commit
from fin.diligence.engagement import company_database
from fin.doctor import run_checks
from fin.evals.counts import counts
from fin.evals.golden import LEDGERS
from fin.evals.score import evaluate
from fin.paths import companies_dir, evals_dir

TOKEN = re.compile(r"^[A-Z]+-\d{4}$")
_CELL, _ROW = re.compile(r"![A-Z]{1,3}\d+"), re.compile(r"!\d+")
_PAGE = re.compile(r"\b(?:page|p\.?)\s*\d+", re.I)
_NAMES = (
    "SELECT real_value FROM mask_values UNION SELECT file_name FROM documents "
    "UNION SELECT file_name FROM source_files"
)


def source_kind(source: str) -> str:
    """Where a golden value is printed, without the place: sheet_cell, sheet_row, page or other."""
    kinds = [
        k
        for k, p in (("sheet_cell", _CELL), ("sheet_row", _ROW), ("page", _PAGE))
        if p.search(source)
    ]
    return "+".join(kinds) or "other"


def _percent(value: Any) -> str | None:
    """The relative difference as a percentage: two decimals, or two significant digits when that
    would show a real difference as 0.00% (a misread digit in a large figure)."""
    if value is None:
        return None
    percent = Decimal(value) * 100
    if percent and abs(percent) < Decimal("0.005"):
        return f"{percent:+.2g}%"
    return f"{percent:+.2f}%"


def _failures(checks: list[dict[str, Any]], with_figures: bool) -> list[dict[str, Any]]:
    aliases: dict[str, str] = {}
    out = []
    for check in checks:
        if check["outcome"] not in ("fail", "missing"):
            continue
        ledger = check["ledger"]
        if ledger and not TOKEN.match(ledger) and ledger not in LEDGERS.get(check["kind"], ()):
            ledger = aliases.setdefault(ledger, f"ledger-{len(aliases) + 1}")
        item = {
            "kind": check["kind"],
            "period": check["period"],
            "scope": check["scope"],
            "account": check["account"],
            "ledger": ledger,
            "outcome": check["outcome"],
            "source_kind": source_kind(check["source"]),
            "relative_difference": _percent(check["relative_difference"]),
            "codes": check["codes"],
        }
        if with_figures:
            item |= {"expected": check["expected"], "fin": check["fin"]}
        out.append(item)
    return out


def _company(run: Run, company: str, alias: str, with_figures: bool) -> dict[str, Any]:
    try:
        report = evaluate(run, company)
    except GoldenSetMissing:
        with company_database(run, company) as con:
            return {
                "company": alias,
                "golden_set": False,
                "summary": None,
                "failures": [],
                "counts": counts(run.root, company, con),
            }
    return {
        "company": alias,
        "golden_set": True,
        "summary": report["summary"],
        "failures": _failures(report["checks"], with_figures),
        "counts": report["counts"],
    }


def _doctor(root: Path) -> dict[str, Any]:
    checks = [
        {
            "name": c.name,
            "status": "fail" if not c.ok else "warn" if c.warning else "ok",
            "code": c.code,
        }
        for c in run_checks(root)
    ]
    return {s: sum(1 for c in checks if c["status"] == s) for s in ("ok", "warn", "fail")} | {
        "checks": checks
    }


def _names(run: Run, companies: list[str]) -> set[str]:
    """Every real name the bundle must not contain (read here only, never printed)."""
    names = set(companies)
    for company in companies:
        with company_database(run, company) as con:
            names |= {value for (value,) in con.execute(_NAMES).fetchall()}
    return {n.strip().lower() for n in names if len(n.strip()) >= 3}


def _check_anonymous(text: str, names: set[str]) -> None:
    lowered = text.lower()
    for name in names:
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", lowered):
            raise BundleNotAnonymous(
                "the results bundle would contain a real name (a masked party, a company or a file "
                "name); nothing written. Report this run_id: the bundle must never carry names"
            )


def format_bundle(bundle: dict[str, Any]) -> str:
    d = bundle["doctor"]
    out = [
        "# fin golden eval results",
        "",
        f"fin {bundle['fin_version']}, commit {bundle['git_commit'] or 'unknown'}; "
        f"figures {'included' if bundle['with_figures'] else 'not included'}",
        f"doctor: {d['ok']} ok, {d['warn']} warn, {d['fail']} fail"
        + "".join(f"; {c['name']} {c['status']}" for c in d["checks"] if c["status"] != "ok"),
    ]
    for c in bundle["companies"]:
        out += _company_lines(c)
    return "\n".join(out) + "\n"


def _company_lines(c: dict[str, Any]) -> list[str]:
    out = ["", f"## {c['company']}", ""]
    s = c["summary"]
    if s is None:
        out.append("no golden set")
    else:
        score = "no checked lines" if s["score_percent"] is None else f"{s['score_percent']}%"
        out.append(
            f"score {score}: {s['pass']} pass, {s['fail']} fail, {s['missing']} missing "
            f"in fin, {s['unchecked']} unchecked"
        )
    for f in c["failures"]:
        figures = f", expected {f['expected']}, fin {f['fin']}" if "expected" in f else ""
        out.append(
            f"- {f['outcome']} {f['kind']} {f['period']} {f['scope']} "
            f"{f['account'] or f['ledger']} ({f['source_kind']}; relative difference "
            f"{f['relative_difference'] or 'n/a'}{figures}; "
            f"codes {', '.join(f['codes']) or 'none'})"
        )
    return out + ["", "```json", json.dumps(c["counts"], indent=2, default=str), "```"]


def make_bundle(run: Run, company: str | None, with_figures: bool) -> tuple[Path, Path]:
    """Write `evals/bundle-<run_id>.md` and `.json`. E832 when there is no company database to
    evaluate; E833 when a real name would be in it."""
    every = sorted(p.stem for p in companies_dir(run.root).glob("*.duckdb"))
    chosen = [company] if company is not None else every
    if not chosen or any(name not in every for name in chosen):
        raise NothingToBundle(
            f"no company database for {company!r}" if company else "no company database to evaluate"
        )
    bundle = {
        "fin_version": __version__,
        "git_commit": git_commit(),
        "with_figures": with_figures,
        "doctor": _doctor(run.root),
        "companies": [
            _company(run, name, f"company-{every.index(name) + 1}", with_figures) for name in chosen
        ],
    }
    text_json = json.dumps(bundle, indent=2, default=str)
    text_md = format_bundle(bundle)
    _check_anonymous(text_json + "\n" + text_md, _names(run, every))
    folder = evals_dir(run.root)
    folder.mkdir(parents=True, exist_ok=True)
    md, js = folder / f"bundle-{run.run_id}.md", folder / f"bundle-{run.run_id}.json"
    md.write_text(text_md, encoding="utf-8")
    js.write_text(text_json, encoding="utf-8")
    run.event("eval bundle", f"results bundle {md}", context={"companies": len(chosen)})
    return md, js
