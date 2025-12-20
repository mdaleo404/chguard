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


def scan_tree(root: Path, excludes: Iterable[str] = ()) -> Iterator[Entry]:
    root = root.resolve()

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
            try:
                st = p.lstat()  # never follow symlinks
            except FileNotFoundError:
                continue

            rel = str(p.relative_to(root))

            if stat.S_ISDIR(st.st_mode):
                typ = "dir"
            elif stat.S_ISREG(st.st_mode):
                typ = "file"
            elif stat.S_ISLNK(st.st_mode):
                typ = "symlink"
            else:
                # skip special files (devices, sockets, fifos) in v0.1
                continue

            mode = stat.S_IMODE(st.st_mode)
            yield Entry(
                path=rel, type=typ, mode=mode, uid=st.st_uid, gid=st.st_gid
            )
