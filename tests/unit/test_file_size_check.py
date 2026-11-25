"""tools/check_file_size.mjs flags an authored source file over 6,000 tokens (bytes / 4) and skips
tests, lockfiles and non-source files."""

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_file_size.mjs"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def check(*files: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", str(SCRIPT), *map(str, files)], capture_output=True, text=True, check=False
    )


def test_a_file_over_the_limit_fails_and_one_at_the_limit_passes(tmp_path: Path) -> None:
    big, edge = tmp_path / "big.py", tmp_path / "edge.py"
    big.write_bytes(b"x = 1\n" * 4001)  # 24,006 bytes
    edge.write_bytes(b"#" * 23999 + b"\n")  # 24,000 bytes: 6,000 tokens

    failed, passed = check(big), check(edge)

    assert failed.returncode == 1
    assert "big.py: 6002 tokens (24006 bytes)" in failed.stderr
    assert passed.returncode == 0


def test_tests_lockfiles_and_other_files_are_not_authored_source(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    skipped = [tmp_path / "tests" / "test_big.py", tmp_path / "uv.lock", tmp_path / "notes.md"]
    for path in skipped:
        path.write_text("x" * 30000, encoding="utf-8")

    assert check(*skipped).returncode == 0
