"""install.sh's retry helper (SPEC-12): only a network failure is tried again; anything else stops
at once with the failing command and what to do. The helper's functions are taken from install.sh
and run in bash with a fake command and a `sleep` that does not wait."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

INSTALL_SH = Path(__file__).resolve().parents[2] / "install.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")


def _helpers() -> str:
    script = INSTALL_SH.read_text(encoding="utf-8")
    parts = []
    for name in ("NETWORK_PATTERN", "PERMISSION_PATTERN"):
        found = re.search(rf"^{name}='.*'$", script, re.MULTILINE)
        assert found, f"install.sh has no {name}"
        parts.append(found.group())
    for name in ("die", "run_retrying"):
        found = re.search(rf"^{name}\(\) \{{.*?^\}}$", script, re.MULTILINE | re.DOTALL)
        assert found, f"install.sh has no function {name}"
        parts.append(found.group())
    return "\n".join(parts)


def _run(tmp_path: Path, outputs: list[str]) -> tuple[subprocess.CompletedProcess[str], int]:
    """Run `run_retrying 60 step` where attempt n prints outputs[n-1] and fails, or succeeds
    once the list is used up. Returns the result and the number of attempts."""
    count = tmp_path / "count"
    count.write_text("", encoding="utf-8")
    for n, text in enumerate(outputs, start=1):
        (tmp_path / f"out{n}").write_text(text, encoding="utf-8")
    script = f"""set -euo pipefail
{_helpers()}
sleep() {{ echo "(slept $1)"; }}
step() {{
  echo x >> count
  n=$(wc -l < count | tr -d ' ')
  if [[ -f "out$n" ]]; then cat "out$n" >&2; return 3; fi
  echo done
}}
run_retrying 60 step --flag
"""
    result = subprocess.run(
        [str(BASH), "-c", script], cwd=tmp_path, capture_output=True, text=True, timeout=30
    )
    return result, len(count.read_text(encoding="utf-8").splitlines())


def test_a_password_prompt_is_not_retried_and_says_what_to_do(tmp_path: Path) -> None:
    result, attempts = _run(tmp_path, ["sudo: a terminal is required to read the password"])
    assert attempts == 1
    assert result.returncode == 1
    assert "(slept" not in result.stdout
    assert "'step --flag' failed (exit 3) and it is not a network failure" in result.stderr
    assert "run ./install.sh yourself in a terminal" in result.stderr
    assert "check the network" not in result.stderr


def test_another_failure_is_not_retried_and_names_the_command(tmp_path: Path) -> None:
    result, attempts = _run(tmp_path, ["Error: No such file or directory - Brewfile"])
    assert attempts == 1
    assert result.returncode == 1
    assert "'step --flag' failed (exit 3) and it is not a network failure: see the lines above" in (
        result.stderr
    )


def test_a_network_failure_is_tried_three_times(tmp_path: Path) -> None:
    lost = "curl: (6) Could not resolve host: github.com"
    result, attempts = _run(tmp_path, [lost, lost, lost])
    assert attempts == 3
    assert result.returncode == 1
    assert result.stdout.count("(slept 60)") == 2
    assert "failed 3 times with a network error; check the network" in result.stderr


def test_a_network_failure_then_success_carries_on(tmp_path: Path) -> None:
    result, attempts = _run(tmp_path, ["curl: (22) The requested URL returned error: 504"])
    assert attempts == 2
    assert result.returncode == 0
    assert "failed with a network error (attempt 1 of 3); trying again in 60 seconds" in (
        result.stdout
    )
    assert result.stdout.rstrip().endswith("done")
