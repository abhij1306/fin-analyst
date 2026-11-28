"""fin.paths.inside, temp_name and atomic_write."""

from pathlib import Path

import pytest

from fin.audit.errors import DataFolderNotWritable, PathOutsideFolder
from fin.paths import atomic_write, inside, temp_name


def test_inside_returns_the_resolved_path(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()

    assert (
        inside(tmp_path, tmp_path / "a" / ".." / "a" / "f.txt")
        == (tmp_path / "a" / "f.txt").resolve()
    )


def test_inside_refuses_a_path_that_climbs_out(tmp_path: Path) -> None:
    with pytest.raises(PathOutsideFolder) as caught:
        inside(tmp_path / "root", tmp_path / "root" / ".." / "secret.txt")

    assert caught.value.code == "E013"


def test_inside_refuses_a_link_that_points_out(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "outside").mkdir()
    try:
        (root / "link").symlink_to(tmp_path / "outside", target_is_directory=True)
    except OSError:
        pytest.skip("this system does not let the test make a link")

    with pytest.raises(PathOutsideFolder):
        inside(root, root / "link" / "f.txt")


def test_temp_names_are_unique_hidden_siblings(tmp_path: Path) -> None:
    first, second = temp_name(tmp_path / "x.json"), temp_name(tmp_path / "x.json")

    assert first != second
    assert first.parent == tmp_path
    assert first.name.startswith(".x.json.")


def test_atomic_write_replaces_the_file_and_leaves_nothing_behind(tmp_path: Path) -> None:
    target = tmp_path / "x.json"
    target.write_text("old", encoding="utf-8")

    atomic_write(target, "new")
    atomic_write(tmp_path / "b.bin", b"\x00")

    assert target.read_text(encoding="utf-8") == "new"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["b.bin", "x.json"]


def test_atomic_write_fails_with_e003_and_removes_its_temporary_file(tmp_path: Path) -> None:
    folder = tmp_path / "taken"
    folder.mkdir()
    (folder / "inner").write_text("x", encoding="utf-8")

    with pytest.raises(DataFolderNotWritable):
        atomic_write(folder, "text")

    assert sorted(p.name for p in tmp_path.iterdir()) == ["taken"]
