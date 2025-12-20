from __future__ import annotations

from pathlib import Path


def normalize_root(path: str) -> Path:
    return Path(path).expanduser().resolve()
