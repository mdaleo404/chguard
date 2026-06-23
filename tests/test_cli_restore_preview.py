from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

from chguard.cli import _restore_preview_rows, main
from chguard.db import connect, create_state, init_db
from chguard.restore import PlannedChange


class FakePath:
    def relative_to(self, root: Path) -> Path:
        return Path("link")

    def lstat(self):
        return SimpleNamespace(st_uid=123)

    def stat(self):
        raise AssertionError("restore preview must not follow symlinks")


def test_missing_and_type_changes_are_reported_as_skipped() -> None:
    root = Path("/snapshot")
    changes = [
        PlannedChange(
            root / "deleted.conf",
            "missing",
            "path does not exist",
            False,
        ),
        PlannedChange(root / "logs", "type", "file -> dir", False),
    ]

    rows, counts, needs_root = _restore_preview_rows(
        changes, root, current_uid=999
    )

    assert not needs_root
    assert counts["skipped"] == 2
    assert rows[Path("deleted.conf")]["skipped"] == "missing path"
    assert rows[Path("logs")]["skipped"] == "found file, expected dir"


def test_unselected_scope_changes_are_not_reported() -> None:
    root = Path("/snapshot")
    changes = [
        PlannedChange(root / "file.txt", "mode", "0o644 -> 0o600", False)
    ]

    rows, counts, needs_root = _restore_preview_rows(
        changes, root, current_uid=999
    )

    assert rows == {}
    assert counts["mode"] == 0
    assert not needs_root


def test_applicable_changes_use_lstat_for_privilege_check() -> None:
    changes = [PlannedChange(FakePath(), "mode", "0o644 -> 0o600", True)]

    rows, counts, needs_root = _restore_preview_rows(
        changes, Path("/snapshot"), current_uid=999
    )

    assert needs_root
    assert counts["mode"] == 1
    assert "rw-r--r--" in rows[Path("link")]["mode"]
    assert "rw-------" in rows[Path("link")]["mode"]


def test_restore_with_only_skipped_items_does_not_prompt(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = tmp_path / "root"
    root.mkdir()

    conn = connect(tmp_path / "states.db")
    init_db(conn)
    with conn:
        state_id = create_state(
            conn, "baseline", str(root), os.getuid(), commit=False
        )
        conn.execute(
            """
            INSERT INTO entries (state_id, path, type, mode, uid, gid)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (state_id, "deleted.conf", "file", 0o644, os.getuid(), 0),
        )
    conn.close()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "chguard",
            "--db",
            str(tmp_path / "states.db"),
            "--restore",
            "baseline",
        ],
    )

    main()

    output = capsys.readouterr().out
    assert "missing path" in output
    assert "No applicable changes" in output
