from __future__ import annotations

import os
import stat
from pathlib import Path

from chguard.restore import apply_restore, plan_restore


def test_plan_restore_reports_mode_owner_missing_and_type_changes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config = root / "config.txt"
    config.write_text("config", encoding="utf-8")
    config.chmod(0o644)
    logs = root / "logs"
    logs.write_text("not a directory", encoding="utf-8")

    current = config.lstat()
    rows = [
        ("config.txt", "file", 0o600, current.st_uid + 1, current.st_gid),
        ("missing.txt", "file", 0o644, current.st_uid, current.st_gid),
        ("logs", "dir", 0o755, current.st_uid, current.st_gid),
    ]

    changes = plan_restore(
        root=root,
        rows=rows,
        restore_permissions=True,
        restore_owner=True,
    )

    kinds_by_path = {(change.path.name, change.kind) for change in changes}
    assert ("config.txt", "mode") in kinds_by_path
    assert ("config.txt", "owner") in kinds_by_path
    assert ("missing.txt", "missing") in kinds_by_path
    assert ("logs", "type") in kinds_by_path


def test_plan_restore_marks_unselected_scope_as_not_applicable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config = root / "config.txt"
    config.write_text("config", encoding="utf-8")
    config.chmod(0o644)
    st = config.lstat()

    changes = plan_restore(
        root=root,
        rows=[("config.txt", "file", 0o600, st.st_uid, st.st_gid)],
        restore_permissions=False,
        restore_owner=True,
    )

    assert len(changes) == 1
    assert changes[0].kind == "mode"
    assert not changes[0].will_apply


def test_apply_restore_changes_mode_without_changing_contents(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config = root / "config.txt"
    config.write_text("config", encoding="utf-8")
    config.chmod(0o644)
    st = config.lstat()

    apply_restore(
        root=root,
        rows=[("config.txt", "file", 0o600, st.st_uid, st.st_gid)],
        restore_permissions=True,
        restore_owner=False,
    )

    assert stat.S_IMODE(config.lstat().st_mode) == 0o600
    assert config.read_text(encoding="utf-8") == "config"


def test_apply_restore_skips_missing_and_type_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config = root / "config.txt"
    config.write_text("config", encoding="utf-8")
    config.chmod(0o644)
    st = config.lstat()

    apply_restore(
        root=root,
        rows=[
            ("missing.txt", "file", 0o600, st.st_uid, st.st_gid),
            ("config.txt", "dir", 0o600, st.st_uid, st.st_gid),
        ],
        restore_permissions=True,
        restore_owner=False,
    )

    assert not (root / "missing.txt").exists()
    assert stat.S_IMODE(config.lstat().st_mode) == 0o644
    assert os.listdir(root) == ["config.txt"]
