"""PDF bake-off: score pdfplumber against pdf-inspector on exact-value match with an answer key.

    uv run python scripts/pdf_bakeoff.py [ANSWER_KEY] [--company tarang]

ANSWER_KEY defaults to tests/fixtures/sample-financials/answer_key.json; document paths in it are
relative to its folder. To score real annual reports, write a key in the same shape (documents,
labels, facts, rupees_per_document_unit) next to them and pass its path.

Expected values are (label, fiscal year, rupees) for every statement line and every note-table row
in the key. A value scores when an extracted row has the same label and fiscal year and exactly the
same rupee amount. Labels are compared after lower-casing and normalising spaces around hyphens.

- pdfplumber: `fin.extract.pdf.extract_pdf` (word positions; unit and periods read from the page).
- pdf-inspector: rows of the Markdown tables from `extract_pages_markdown`. It is given the help it
  lacks: the last numeric cells of a row are mapped to the document's fiscal years from the key, and
  the key's unit is applied.
"""

import argparse
import json
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pdf_inspector

from fin.audit.errors import FinError
from fin.extract.pdf import extract_pdf, parse_number

DEFAULT_KEY = Path(__file__).parent.parent / "tests/fixtures/sample-financials/answer_key.json"
_NUMBER = re.compile(r"^\(?-?[\d,]*\d(\.\d+)?\)?$")
_NOTE_REF = re.compile(r"^\d{1,3}$")

Fact = tuple[str, str, Decimal]  # normalised label, fiscal year, rupees


def norm(label: str) -> str:
    return re.sub(r"\s*-\s*", "-", " ".join(label.split())).lower()


def expected_facts(company: dict, document: dict) -> Counter[Fact]:
    """Every statement line and note-table row of one document, from the key."""
    labels = {account: label for label, account in company["labels"].items()}
    # The key's labels map is keyed by label, so the second "Borrowings" line (non-current) is lost.
    labels.setdefault("lt_borrowings", "(a) Financial liabilities - Borrowings")
    facts: Counter[Fact] = Counter()
    for fy in document["fiscal_years"]:
        for account, rupees in company["facts"][document["scope"]][fy].items():
            facts[(norm(labels[account]), fy, Decimal(rupees))] += 1
        notes = company.get("notes", {})
        for row in notes.get("related_party_rupees", []):
            label = f"{row['party']} {row['relationship']} {row['nature']}"
            facts[(norm(label), fy, Decimal(row["amounts"][fy]))] += 1
        for row in notes.get("contingent_liabilities_rupees", []):
            facts[(norm(row["item"]), fy, Decimal(row["amounts"][fy]))] += 1
    return facts


def pdfplumber_facts(path: Path) -> Counter[Fact]:
    found = extract_pdf(path)
    return Counter((norm(v.label), v.fiscal_year, v.rupees) for v in found.values)


def pdf_inspector_facts(path: Path, fiscal_years: list[str], rupees_per_unit: int) -> Counter[Fact]:
    facts: Counter[Fact] = Counter()
    for page in pdf_inspector.extract_pages_markdown(str(path)).pages:
        for line in page.markdown.splitlines():
            if not line.startswith("|") or set(line) <= set("|-: "):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            numbers = [c for c in cells if _NUMBER.match(c) and not _NOTE_REF.match(c)]
            if len(numbers) < len(fiscal_years):
                continue
            values = numbers[-len(fiscal_years) :]
            label = " ".join(c for c in cells if c and c not in values and not _NOTE_REF.match(c))
            for fy, raw in zip(fiscal_years, values, strict=True):
                facts[(norm(label), fy, parse_number(raw) * rupees_per_unit)] += 1
    return facts


def score(expected: Counter[Fact], found: Counter[Fact]) -> tuple[int, int, list[Fact]]:
    missed = expected - found
    return expected.total() - missed.total(), expected.total(), sorted(missed.elements())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("key", nargs="?", type=Path, default=DEFAULT_KEY)
    parser.add_argument("--company", default="tarang")
    parser.add_argument("--show-misses", action="store_true")
    args = parser.parse_args()

    key = json.loads(args.key.read_text(encoding="utf-8"))
    company = key["companies"][args.company]
    out = sys.stdout
    out.write("| Document | Table style | pdfplumber | pdf-inspector |\n|---|---|---|---|\n")
    totals = {"pdfplumber": [0, 0], "pdf-inspector": [0, 0]}
    misses: dict[str, list[Fact]] = {}
    for document in company["documents"]:
        path = args.key.parent / document["file"]
        expected = expected_facts(company, document)
        cells = []
        for tool in totals:
            try:
                if tool == "pdfplumber":
                    found = pdfplumber_facts(path)
                else:
                    found = pdf_inspector_facts(
                        path, document["fiscal_years"], company["rupees_per_document_unit"]
                    )
            except (FinError, ValueError) as exc:
                cells.append(f"error: {exc}")
                totals[tool][1] += expected.total()
                continue
            hit, total, missed = score(expected, found)
            totals[tool][0] += hit
            totals[tool][1] += total
            misses[f"{tool} {path.name}"] = missed
            cells.append(f"{hit}/{total} ({100 * hit / total:.1f}%)")
        out.write(f"| {path.name} | {document.get('table_style', '')} | {' | '.join(cells)} |\n")
    summary = [f"{h}/{t} ({100 * h / t:.1f}%)" for h, t in totals.values()]
    out.write(f"| **All** | | {' | '.join(summary)} |\n")
    if args.show_misses:
        for name, missed in misses.items():
            out.write(f"\nMissed by {name}: {len(missed)}\n")
            for fact in missed:
                out.write(f"  {fact}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
