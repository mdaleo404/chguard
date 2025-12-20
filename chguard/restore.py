from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class PlannedChange:
    path: Path
    kind: str  # mode | owner | missing | type
    detail: str
    will_apply: bool


def plan_restore(
    *,
    root: Path,
    rows: Iterable[tuple[str, str, int, int, int]],
    restore_permissions: bool,
    restore_owner: bool,
) -> list[PlannedChange]:
    changes: list[PlannedChange] = []

    for rel, expected_type, want_mode, want_uid, want_gid in rows:
        p = root / rel

        if not p.exists() and expected_type != "symlink":
            changes.append(
                PlannedChange(p, "missing", "path does not exist", False)
            )
            continue

        try:
            st = p.lstat()
        except FileNotFoundError:
            changes.append(
                PlannedChange(p, "missing", "path does not exist", False)
            )
            continue

        if stat.S_ISDIR(st.st_mode):
            got_type = "dir"
        elif stat.S_ISREG(st.st_mode):
            got_type = "file"
        elif stat.S_ISLNK(st.st_mode):
            got_type = "symlink"
        else:
            got_type = "other"

        if got_type != expected_type:
            changes.append(
                PlannedChange(
                    p, "type", f"{got_type} -> {expected_type}", False
                )
            )
            continue

        got_mode = stat.S_IMODE(st.st_mode)
        if got_mode != want_mode:
            changes.append(
                PlannedChange(
                    p,
                    "mode",
                    f"{oct(got_mode)} -> {oct(want_mode)}",
                    restore_permissions,
                )
            )

        if st.st_uid != want_uid or st.st_gid != want_gid:
            changes.append(
                PlannedChange(
                    p,
                    "owner",
                    f"{st.st_uid}:{st.st_gid} -> {want_uid}:{want_gid}",
                    restore_owner,
                )
            )

    return changes


def apply_restore(
    *,
    root: Path,
    rows: Iterable[tuple[str, str, int, int, int]],
    restore_permissions: bool,
    restore_owner: bool,
) -> None:
    for rel, expected_type, want_mode, want_uid, want_gid in rows:
        p = root / rel
        try:
            st = p.lstat()
        except FileNotFoundError:
            continue

        if stat.S_ISDIR(st.st_mode):
            got_type = "dir"
        elif stat.S_ISREG(st.st_mode):
            got_type = "file"
        elif stat.S_ISLNK(st.st_mode):
            got_type = "symlink"
        else:
            continue

        if got_type != expected_type:
            continue

        if restore_owner:
            try:
                os.chown(p, want_uid, want_gid, follow_symlinks=False)
            except (PermissionError, NotImplementedError):
                pass

        if restore_permissions:
            try:
                os.chmod(p, want_mode, follow_symlinks=False)
            except (PermissionError, NotImplementedError):
                pass
