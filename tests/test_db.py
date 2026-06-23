from __future__ import annotations

from pathlib import Path

from chguard.db import (
    connect,
    create_state,
    delete_state,
    get_state,
    init_db,
    prune_all_states,
    prune_states_before,
    state_exists,
)


def test_state_crud_and_entry_cascade_delete(tmp_path: Path) -> None:
    conn = connect(tmp_path / "states.db")
    init_db(conn)

    state_id = create_state(conn, "baseline", "/srv/app", 1000)
    conn.execute(
        """
        INSERT INTO entries (state_id, path, type, mode, uid, gid)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (state_id, "config.txt", "file", 0o644, 1000, 1000),
    )
    conn.commit()

    assert state_exists(conn, "baseline")
    state = get_state(conn, "baseline")
    assert state is not None
    assert state.id == state_id
    assert state.root_path == "/srv/app"

    assert delete_state(conn, "baseline") == 1
    assert not state_exists(conn, "baseline")
    remaining_entries = conn.execute("SELECT COUNT(*) FROM entries").fetchone()
    assert remaining_entries[0] == 0


def test_prune_states_before_deletes_only_old_states(tmp_path: Path) -> None:
    conn = connect(tmp_path / "states.db")
    init_db(conn)
    conn.execute(
        """
        INSERT INTO states (name, root_path, created_at, created_by_uid)
        VALUES (?, ?, ?, ?)
        """,
        ("old", "/old", "2024-01-01T00:00:00+00:00", 1000),
    )
    conn.execute(
        """
        INSERT INTO states (name, root_path, created_at, created_by_uid)
        VALUES (?, ?, ?, ?)
        """,
        ("new", "/new", "2024-02-01T00:00:00+00:00", 1000),
    )
    conn.commit()

    deleted = prune_states_before(conn, "2024-01-15T00:00:00+00:00")

    assert deleted == 1
    assert not state_exists(conn, "old")
    assert state_exists(conn, "new")


def test_prune_all_states_deletes_every_state(tmp_path: Path) -> None:
    conn = connect(tmp_path / "states.db")
    init_db(conn)
    create_state(conn, "one", "/one", 1000)
    create_state(conn, "two", "/two", 1000)

    assert prune_all_states(conn) == 2
    assert conn.execute("SELECT COUNT(*) FROM states").fetchone()[0] == 0
