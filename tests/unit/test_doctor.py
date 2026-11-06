import pytest

from fin.doctor import check_tool, parse_version


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("uv 0.11.28 (ebf0f43d7 2026-07-07 x86_64-apple-darwin)", (0, 11, 28)),
        ("v24.11.1", (24, 11, 1)),
        ("1.8.25", (1, 8, 25)),
        ("no digits here", None),
    ],
)
def test_parse_version(output: str, expected: tuple[int, ...] | None) -> None:
    assert parse_version(output) == expected


def test_tool_missing_is_e001(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fin.doctor.tool_version", lambda tool: None)
    check = check_tool("quarto", (1, 7))
    assert (check.ok, check.code, check.detail) == (False, "E001", "not found on PATH")


def test_tool_below_minimum_is_e002(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fin.doctor.tool_version", lambda tool: "v22.3.0")
    check = check_tool("node", (24,))
    assert (check.ok, check.code, check.detail) == (False, "E002", "found 22.3.0, need >= 24")
    assert check.fix == "run `brew upgrade node`"


def test_tool_at_or_above_minimum_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fin.doctor.tool_version", lambda tool: "v26.8.1")
    check = check_tool("node", (24,))
    assert (check.ok, check.code, check.detail) == (True, None, "26.8.1")
