"""fin dataroom scan|plan (SPEC-13 PR 1): a synthetic data room scans to the checked-in plan."""

import json
import shutil
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fin.cli import app
from fin.dataroom.kinds import REASONS
from fin.dataroom.scan import read_plan
from fin.paths import dataroom_dir
from tests.register_fixtures import write_registers
from tests.synthetic_pdf import BALANCE_SHEET, PROFIT_AND_LOSS, WIDTH, write_pdf
from tests.tally_fixtures import write_trial_balance, write_unknown_layout

EXPECTED = Path(__file__).parents[1] / "fixtures" / "dataroom" / "expected_plan.json"
SHOWN = ("path", "kind", "profile", "period", "reason", "next_step", "duplicates")
runner = CliRunner()

LEDGER_PDF = [
    (50, 50, "Ledger: Rent"),
    (50, 65, "1-Apr-2023 to 31-Mar-2024"),
    (50, 85, "Date"),
    (120, 85, "Particulars"),
    (400, 85, "Debit"),
    (480, 85, "Credit"),
    (50, 105, "01-05-2023"),
    (120, 105, "Axis Bank"),
    (400, 105, "10,000.00"),
]


def make_room(tmp_path: Path) -> Path:
    """Nested folders, one archive, two duplicate files (one inside the archive), one PDF ledger,
    one unknown file and a hidden file."""
    room = tmp_path / "room"
    (room / "books" / "fy25").mkdir(parents=True)
    (room / "misc").mkdir()
    (room / "statements").mkdir()
    tb = write_trial_balance(room / "books" / "fy25" / "trial_balance.xlsx")
    write_pdf(room / "books" / "fy25" / "ledger_fy24.pdf", LEDGER_PDF)
    write_unknown_layout(room / "misc" / "notes.xlsx")
    write_pdf(room / "statements" / "annual.pdf", BALANCE_SHEET, PROFIT_AND_LOSS, width=WIDTH)
    (room / ".DS_Store").write_bytes(b"hidden")
    registers = write_registers(tmp_path / "billing.xlsx")
    with zipfile.ZipFile(room / "registers.zip", "w") as archive:
        archive.write(registers, "billing/registers.xlsx")
        archive.write(tb, "tb_copy.xlsx")
    return room


@pytest.fixture
def root(data_root: Path) -> Path:
    assert runner.invoke(app, ["init"]).exit_code == 0
    return data_root


def scan(*args: str) -> str:
    result = runner.invoke(app, ["dataroom", "scan", "acme", *args])
    assert result.exit_code == 0, result.output
    return result.output


def shown(root: Path) -> list[dict[str, object]]:
    return [{k: getattr(r, k) for k in SHOWN} for r in read_plan(root, "acme")]


def test_a_data_room_scans_to_the_expected_plan(root: Path, tmp_path: Path) -> None:
    room = make_room(tmp_path)
    scan(str(room))
    assert shown(root) == json.loads(EXPECTED.read_text(encoding="utf-8"))
    archive_dirs = list(dataroom_dir(root, "acme").iterdir())
    assert len(archive_dirs) == 1
    assert (archive_dirs[0] / "billing" / "registers.xlsx").is_file()


def test_the_plan_text_lists_files_duplicates_and_what_is_skipped(
    root: Path, tmp_path: Path
) -> None:
    output = scan(str(make_room(tmp_path)))
    assert "data-room plan for acme: 3 to load (0 approved), 2 skipped" in output
    assert "      duplicate: registers.zip/tb_copy.xlsx" in output
    assert "registers.zip: archive, unpacked" in output


def test_a_second_scan_adds_only_new_files(root: Path, tmp_path: Path) -> None:
    room = make_room(tmp_path)
    scan(str(room))
    before = read_plan(root, "acme")
    assert "scanned" in scan(str(room)) and read_plan(root, "acme") == before
    shutil.copy(room / "misc" / "notes.xlsx", room / "misc" / "notes_again.xlsx")
    write_unknown_layout(room / "misc" / "other.xlsx", title="Other balances")
    output = scan(str(room))
    assert "2 new in the plan" not in output
    assert "1 new in the plan, 1 new duplicate paths" in output
    after = read_plan(root, "acme")
    assert [r.path for r in after[len(before) :]] == ["misc/other.xlsx"]
    notes = next(r for r in after if r.path == "misc/notes.xlsx")
    assert notes.duplicates == ["misc/notes_again.xlsx"]


def test_every_reason_has_a_next_step() -> None:
    for reason, step in REASONS.values():
        assert reason.strip() and step.strip()


def test_every_unusable_file_in_the_plan_has_a_reason_and_a_next_step(
    root: Path, tmp_path: Path
) -> None:
    room = make_room(tmp_path)
    (room / "misc" / "contract.docx").write_bytes(b"not a workbook")
    (room / "misc" / "old.xls").write_bytes(b"old")
    (room / "misc" / "broken.xlsx").write_bytes(b"not a zip")
    (room / "misc" / "more.rar").write_bytes(b"rar")
    scan(str(room))
    skipped = {r.path: (r.reason, r.next_step) for r in read_plan(root, "acme") if r.reason}
    assert skipped == {
        "books/fy25/ledger_fy24.pdf": REASONS["pdf_no_statement"],
        "misc/broken.xlsx": REASONS["unreadable"],
        "misc/contract.docx": REASONS["file_type"],
        "misc/more.rar": REASONS["archive_type"],
        "misc/notes.xlsx": REASONS["no_layout"],
        "misc/old.xls": REASONS["old_excel"],
    }


def _archive_with(room: Path, *entries: tuple[zipfile.ZipInfo | str, bytes]) -> Path:
    room.mkdir()
    with zipfile.ZipFile(room / "data.zip", "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return room


def _refused(root: Path, room: Path) -> str | None:
    scan(str(room))
    (row,) = read_plan(root, "acme")
    assert (row.path, row.kind) == ("data.zip", "archive")
    assert not dataroom_dir(root, "acme").exists() or not any(dataroom_dir(root, "acme").iterdir())
    return row.reason


@pytest.mark.parametrize("name", ["../evil.txt", "a/../../evil.txt", "/evil.txt", "C:/evil.txt"])
def test_an_entry_outside_the_archive_folder_is_refused(
    root: Path, tmp_path: Path, name: str
) -> None:
    room = _archive_with(tmp_path / "room", ("ok.txt", b"fine"), (name, b"bad"))
    assert _refused(root, room) == REASONS["archive_unsafe"][0]
    assert not (tmp_path / "evil.txt").exists()
    assert not (root / "dataroom" / "evil.txt").exists()
    assert not (root / "evil.txt").exists()


def test_a_link_entry_is_refused(root: Path, tmp_path: Path) -> None:
    link = zipfile.ZipInfo("link.txt")
    link.external_attr = 0o120777 << 16
    room = _archive_with(tmp_path / "room", (link, b"/etc/passwd"))
    assert _refused(root, room) == REASONS["archive_unsafe"][0]


def test_an_archive_over_the_limit_is_refused(root: Path, tmp_path: Path) -> None:
    config = root / "config.toml"
    text = config.read_text(encoding="utf-8").replace(
        "archive_limit_mb = 2048", "archive_limit_mb = 1"
    )
    config.write_text(text, encoding="utf-8")
    room = _archive_with(tmp_path / "room", ("big.bin", b"0" * (1024 * 1024 + 1)))
    assert _refused(root, room) == REASONS["archive_too_large"][0]


def test_a_damaged_archive_is_refused(root: Path, tmp_path: Path) -> None:
    room = tmp_path / "room"
    room.mkdir()
    (room / "data.zip").write_bytes(b"PK not really")
    assert _refused(root, room) == REASONS["archive_unreadable"][0]


def test_a_missing_folder_is_e814(root: Path, tmp_path: Path) -> None:
    result = runner.invoke(app, ["dataroom", "scan", "acme", str(tmp_path / "none")])
    assert result.exit_code == 2
    assert "E814" in result.output


def test_a_folder_holding_the_data_folder_is_e814(root: Path) -> None:
    result = runner.invoke(app, ["dataroom", "scan", "acme", str(root.parent)])
    assert result.exit_code == 2
    assert "E814" in result.output


def test_plan_without_a_scan_is_e815(root: Path) -> None:
    result = runner.invoke(app, ["dataroom", "plan", "acme"])
    assert result.exit_code == 2
    assert "E815: acme has no data-room plan" in result.output
