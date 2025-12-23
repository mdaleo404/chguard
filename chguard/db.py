from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from platformdirs import user_data_dir


APP_NAME = "chguard"


def default_db_path() -> Path:
    base = Path(user_data_dir(APP_NAME))
    base.mkdir(parents=True, exist_ok=True)
    return base / "states.db"


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or default_db_path()
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS states (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            root_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            created_by_uid INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS entries (
            state_id INTEGER NOT NULL,
            path TEXT NOT NULL,
            type TEXT NOT NULL,
            mode INTEGER NOT NULL,
            uid INTEGER NOT NULL,
            gid INTEGER NOT NULL,
            PRIMARY KEY (state_id, path),
            FOREIGN KEY (state_id) REFERENCES states(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_entries_state_id ON entries(state_id);
        """
    )
    conn.commit()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute("SELECT 1 FROM states WHERE name = ? LIMIT 1", (name,))
    return cur.fetchone() is not None


def create_state(
    conn: sqlite3.Connection,
    name: str,
    root_path: str,
    created_by_uid: int,
    *,
    commit: bool = True,
) -> int:
    cur = conn.execute(
        "INSERT INTO states (name, root_path, created_at, created_by_uid) VALUES (?, ?, ?, ?)",
        (name, root_path, utc_now_iso(), created_by_uid),
    )
    if commit:
        conn.commit()
    return int(cur.lastrowid)


def delete_state(
    conn: sqlite3.Connection, name: str, commit: bool = True
) -> int:
    cur = conn.execute("DELETE FROM states WHERE name = ?", (name,))
    if commit:
        conn.commit()
    return cur.rowcount


@dataclass(frozen=True)
class State:
    id: int
    name: str
    root_path: str
    created_at: str
    created_by_uid: int


def get_state(conn: sqlite3.Connection, name: str) -> State | None:
    cur = conn.execute(
        "SELECT id, name, root_path, created_at, created_by_uid FROM states WHERE name = ?",
        (name,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return State(*row)
