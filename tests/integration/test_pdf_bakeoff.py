import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "pdf_bakeoff.py"


def test_bakeoff_scores_both_extractors_on_the_synthetic_reports() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, encoding="utf-8", check=True
    )

    rows = result.stdout.splitlines()
    assert rows[0] == "| Document | Table style | pdfplumber | pdf-inspector |"
    assert rows[-1] == "| **All** | | 236/236 (100.0%) | 220/236 (93.2%) |"
