from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


@dataclass(frozen=True)
class Entry:
    path: str  # relative to root
    type: str  # file|dir|symlink
    mode: int  # permission bits only (e.g. 0o644)
    uid: int
    gid: int


def _is_excluded(rel: str, excludes: Iterable[str]) -> bool:
    # Simple prefix-based excludes for MVP; can evolve to glob later.
    for ex in excludes:
        ex = ex.strip().strip("/")
        if not ex:
            continue
        if rel == ex or rel.startswith(ex + "/"):
            return True
    return False


def _entry_for_path(p: Path, root: Path) -> Entry | None:
    try:
        st = p.lstat()  # never follow symlinks
    except FileNotFoundError:
        return None

    if stat.S_ISDIR(st.st_mode):
        typ = "dir"
    elif stat.S_ISREG(st.st_mode):
        typ = "file"
    elif stat.S_ISLNK(st.st_mode):
        typ = "symlink"
    else:
        # skip special files (devices, sockets, fifos) in v0.1
        return None

    rel = "" if p == root else str(p.relative_to(root))

    return Entry(
        path=rel,
        type=typ,
        mode=stat.S_IMODE(st.st_mode),
        uid=st.st_uid,
        gid=st.st_gid,
    )


def scan_tree(root: Path, excludes: Iterable[str] = ()) -> Iterator[Entry]:
    root = root.resolve()

    root_entry = _entry_for_path(root, root)
    if root_entry is not None:
        yield root_entry

    if not root.is_dir():
        return

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # prune excluded directories early
        rel_dir = (
            str(Path(dirpath).relative_to(root))
            if Path(dirpath) != root
            else ""
        )
        if rel_dir and _is_excluded(rel_dir, excludes):
            dirnames[:] = []
            continue

        # prune excluded children
        dirnames[:] = [
            d
            for d in dirnames
            if not _is_excluded(str(Path(rel_dir, d)).strip("/"), excludes)
        ]
        files = [
            f
            for f in filenames
            if not _is_excluded(str(Path(rel_dir, f)).strip("/"), excludes)
        ]

        # record dirs and files
        for name in list(dirnames) + list(files):
            p = Path(dirpath) / name
            entry = _entry_for_path(p, root)
            if entry is not None:
                yield entry
