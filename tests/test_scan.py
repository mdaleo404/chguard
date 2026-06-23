from __future__ import annotations

import stat
from pathlib import Path

from chguard.scan import scan_tree


def entries_by_path(root: Path, excludes: tuple[str, ...] = ()):
    return {entry.path: entry for entry in scan_tree(root, excludes=excludes)}


def test_scan_tree_records_root_dirs_files_and_symlinks(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    data_dir = root / "data"
    data_dir.mkdir()
    file_path = data_dir / "config.txt"
    file_path.write_text("config", encoding="utf-8")
    file_path.chmod(0o640)
    (root / "config-link").symlink_to(file_path)

    entries = entries_by_path(root)

    assert entries[""].type == "dir"
    assert entries["data"].type == "dir"
    assert entries["data/config.txt"].type == "file"
    assert entries["data/config.txt"].mode == 0o640
    assert entries["config-link"].type == "symlink"
    assert entries["config-link"].mode == stat.S_IMODE(
        (root / "config-link").lstat().st_mode
    )


def test_scan_tree_excludes_path_prefixes(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "keep").mkdir()
    (root / "keep" / "file.txt").write_text("keep", encoding="utf-8")
    (root / "cache").mkdir()
    (root / "cache" / "file.txt").write_text("cache", encoding="utf-8")
    (root / "var").mkdir()
    (root / "var" / "tmp").mkdir()
    (root / "var" / "tmp" / "file.txt").write_text("tmp", encoding="utf-8")

    entries = entries_by_path(root, excludes=("cache", "var/tmp"))

    assert "keep" in entries
    assert "keep/file.txt" in entries
    assert "cache" not in entries
    assert "cache/file.txt" not in entries
    assert "var" in entries
    assert "var/tmp" not in entries
    assert "var/tmp/file.txt" not in entries


def test_scan_tree_file_root_records_only_root_entry(tmp_path: Path) -> None:
    root_file = tmp_path / "single.txt"
    root_file.write_text("single", encoding="utf-8")

    entries = list(scan_tree(root_file))

    assert len(entries) == 1
    assert entries[0].path == ""
    assert entries[0].type == "file"
