from __future__ import annotations

import argparse
import argcomplete
import grp
import importlib.metadata
import os
import pwd
import sqlite3
import stat
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table

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
from chguard.restore import PlannedChange, apply_restore, plan_restore
from chguard.scan import scan_tree
from chguard.util import normalize_root

PRUNE_STATES_FROM_ENV = "__CHGUARD_PRUNE_STATES_FROM_ENV__"


def get_version():
    try:
        return importlib.metadata.version("chguard")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _uid_to_name(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def _gid_to_name(gid: int) -> str:
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return str(gid)


def _format_owner(uid: int, gid: int) -> str:
    return f"{_uid_to_name(uid)}:{_gid_to_name(gid)}"


def _mode_to_rwx(mode: int) -> str:
    bits = (
        stat.S_IRUSR,
        stat.S_IWUSR,
        stat.S_IXUSR,
        stat.S_IRGRP,
        stat.S_IWGRP,
        stat.S_IXGRP,
        stat.S_IROTH,
        stat.S_IWOTH,
        stat.S_IXOTH,
    )
    out = []
    for b in bits:
        if mode & b:
            out.append(
                "r"
                if b in (stat.S_IRUSR, stat.S_IRGRP, stat.S_IROTH)
                else (
                    "w"
                    if b in (stat.S_IWUSR, stat.S_IWGRP, stat.S_IWOTH)
                    else "x"
                )
            )
        else:
            out.append("-")
    return "".join(out)


def _is_root() -> bool:
    return os.geteuid() == 0


def _parse_prune_states_value(value: str | None) -> int | str:
    if value is None:
        value = os.environ.get("CHGUARD_STATES_LIFE")

    if not value:
        raise SystemExit(
            "Missing prune age. Use --prune-states=N or set "
            "CHGUARD_STATES_LIFE."
        )

    value = value.strip().lower()

    if value == "all":
        return "all"

    try:
        days = int(value)
    except ValueError as exc:
        raise SystemExit(
            "Invalid prune age. Use an integer number of days or 'all'."
        ) from exc

    if days < 0:
        raise SystemExit("Invalid prune age. Value must be >= 0.")

    return days


def _confirm_or_abort(*, yes: bool, prompt: str) -> None:
    if yes:
        return

    if not sys.stdin.isatty():
        raise SystemExit(
            "Refusing to continue without confirmation (no TTY).\n"
            "Use --yes to force."
        )

    answer = input(f"\n{prompt} (y/N) ").strip().lower()
    if answer not in ("y", "yes"):
        raise SystemExit("Aborted.")


def complete_state_names(prefix, parsed_args, **kwargs):
    try:
        conn = connect(
            Path(parsed_args.db).expanduser().resolve()
            if parsed_args.db
            else None
        )
        rows = conn.execute("SELECT name FROM states").fetchall()
        return [name for (name,) in rows if name.startswith(prefix)]
    except Exception:
        return []


def _extract_paths_from_command(cmd: list[str]) -> list[Path]:
    paths = []
    for arg in cmd:
        if arg.startswith("-"):
            continue
        p = Path(arg)
        if p.exists():
            paths.append(p.resolve())
    return paths


def _common_snapshot_root(paths: list[Path]) -> Path:
    if len(paths) == 1:
        return paths[0].resolve()

    return Path(os.path.commonpath([str(p.resolve()) for p in paths]))


def _create_auto_state(
    conn, root_path: Path, created_by_uid: int
) -> tuple[int, str]:
    base_name = f"auto-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    suffix = 0

    while True:
        auto_name = base_name if suffix == 0 else f"{base_name}-{suffix}"
        try:
            state_id = create_state(
                conn,
                auto_name,
                str(root_path),
                created_by_uid,
                commit=False,
            )
        except sqlite3.IntegrityError:
            suffix += 1
        else:
            return state_id, auto_name


def _type_for_mode(mode: int) -> str | None:
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISLNK(mode):
        return "symlink"
    return None


def _iter_entries_for_target(path: Path, snapshot_root: Path):
    """Yield Entry-like tuples for one wrapper-mode target.

    The paths yielded here are relative to the single snapshot root for the
    whole wrapped command, not relative to each individual command argument.
    This lets one auto-snapshot cover commands such as:

        chmod 700 foo1 foo2

    without inserting multiple entries with the empty relative path.
    """

    def entry_for(p: Path):
        try:
            st = p.lstat()
        except FileNotFoundError:
            return None

        typ = _type_for_mode(st.st_mode)
        if typ is None:
            return None

        rel = "" if p == snapshot_root else str(p.relative_to(snapshot_root))
        return rel, typ, stat.S_IMODE(st.st_mode), st.st_uid, st.st_gid

    first = entry_for(path)
    if first is not None:
        yield first

    if not path.is_dir():
        return

    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for name in list(dirnames) + list(filenames):
            entry = entry_for(Path(dirpath) / name)
            if entry is not None:
                yield entry


def _entry_is_under(rel: str, root: str) -> bool:
    return rel == root or rel.startswith(root.rstrip("/") + "/")


def _root_entry_summary(entry_type: str) -> str:
    if entry_type == "dir":
        return "directory tree"
    if entry_type == "file":
        return "file"
    if entry_type == "symlink":
        return "symlink"
    return entry_type


def _captured_paths_summary(
    conn, state_id: int, root_path: str, limit: int = 5
) -> str:
    root_entry = conn.execute(
        """
        SELECT type
        FROM entries
        WHERE state_id = ? AND (path = '' OR path = ?)
        LIMIT 1
        """,
        (state_id, root_path),
    ).fetchone()
    if root_entry is not None:
        return _root_entry_summary(root_entry[0])

    roots: list[str] = []
    truncated = False

    rows = conn.execute(
        "SELECT path FROM entries WHERE state_id = ? ORDER BY path",
        (state_id,),
    )
    for (rel,) in rows:
        if any(_entry_is_under(rel, root) for root in roots):
            continue

        if len(roots) >= limit:
            truncated = True
            break

        roots.append(rel)

    if not roots:
        return "—"

    summary = ", ".join(roots)
    if truncated:
        summary += ", …"
    return summary


def _display_restore_path(path: Path, target_root: Path) -> Path:
    try:
        return path.relative_to(target_root)
    except ValueError:
        return path


def _format_skipped_restore_change(change: PlannedChange) -> str:
    if change.kind == "missing":
        return "missing path"

    if change.kind == "type":
        got, expected = change.detail.split(" -> ", 1)
        return f"found {got}, expected {expected}"

    return change.detail


def _restore_preview_rows(
    changes: list[PlannedChange], target_root: Path, current_uid: int
) -> tuple[dict[Path, dict[str, str]], Counter, bool]:
    per_path: dict[Path, dict[str, str]] = defaultdict(dict)
    counts = Counter()
    needs_root = False

    for ch in changes:
        rel = _display_restore_path(ch.path, target_root)

        if ch.kind == "owner" and ch.will_apply:
            before, after = ch.detail.split(" -> ")
            bu, bg = map(int, before.split(":"))
            au, ag = map(int, after.split(":"))

            owner_change = f"{_format_owner(bu, bg)} → {_format_owner(au, ag)}"
            per_path[rel]["owner"] = owner_change
            counts["owner"] += 1

            try:
                if ch.path.lstat().st_uid != current_uid:
                    needs_root = True
            except FileNotFoundError:
                pass

        elif ch.kind == "mode" and ch.will_apply:
            before, after = ch.detail.split(" -> ")
            per_path[rel]["mode"] = (
                f"{_mode_to_rwx(int(before, 8))} → "
                f"{_mode_to_rwx(int(after, 8))}"
            )
            counts["mode"] += 1

            try:
                if ch.path.lstat().st_uid != current_uid:
                    needs_root = True
            except FileNotFoundError:
                pass

        elif ch.kind in ("missing", "type"):
            skipped = _format_skipped_restore_change(ch)
            existing = per_path[rel].get("skipped")
            per_path[rel]["skipped"] = (
                f"{existing}; {skipped}" if existing else skipped
            )
            counts["skipped"] += 1

    return per_path, counts, needs_root


def main() -> None:
    wrapper_cmd = None
    if "--" in sys.argv:
        idx = sys.argv.index("--")
        wrapper_cmd = sys.argv[idx + 1 :]
        sys.argv = sys.argv[:idx]

    parser = argparse.ArgumentParser(
        prog="chguard",
        description="Snapshot and restore filesystem ownership and permissions.",
        epilog=(
            "Wrapper mode:\n"
            "  chguard -- chown [OPTIONS] PATH...\n"
            "  chguard -- chmod [OPTIONS] PATH...\n"
            "  chguard -- chgrp [OPTIONS] PATH...\n\n"
            "In wrapper mode, chguard automatically saves a snapshot of ownership\n"
            "and permissions for the affected paths before running the command.\n"
            "Only chown, chmod, and chgrp are supported."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    actions = parser.add_mutually_exclusive_group(required=wrapper_cmd is None)

    parser.add_argument(
        "--version",
        action="version",
        version=f"chguard {get_version()}",
    )

    actions.add_argument(
        "--save", metavar="PATH", help="Save state for PATH"
    ).completer = argcomplete.FilesCompleter()

    actions.add_argument(
        "--restore", action="store_true", help="Restore a saved state"
    )
    actions.add_argument(
        "--list", action="store_true", help="List saved states"
    )

    actions.add_argument(
        "--delete", metavar="STATE", help="Delete a saved state"
    ).completer = complete_state_names

    actions.add_argument(
        "--prune-states",
        nargs="?",
        const=PRUNE_STATES_FROM_ENV,
        metavar="N",
        help=(
            "Delete states older than N days. If N is omitted, "
            "CHGUARD_STATES_LIFE is used. Use 'all' to delete all states."
        ),
    )

    parser.add_argument("state", nargs="?", help="State name").completer = (
        complete_state_names
    )
    parser.add_argument("--name", help="State name")
    parser.add_argument(
        "--overwrite", action="store_true", help="Overwrite existing state"
    )
    parser.add_argument(
        "--permissions", action="store_true", help="Restore MODE only"
    )
    parser.add_argument(
        "--owner", action="store_true", help="Restore OWNER only"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview only; do not apply"
    )
    parser.add_argument(
        "--yes", action="store_true", help="Apply without confirmation"
    )

    parser.add_argument(
        "--root", metavar="PATH", help="Override restore root"
    ).completer = argcomplete.FilesCompleter()

    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Exclude path prefix",
    ).completer = argcomplete.FilesCompleter()

    parser.add_argument(
        "--db", metavar="PATH", help="Override database path"
    ).completer = argcomplete.FilesCompleter()

    argcomplete.autocomplete(parser)
    args = parser.parse_args()

    if wrapper_cmd is not None:
        if not wrapper_cmd:
            raise SystemExit("No command provided after '--'")

        cmd = Path(wrapper_cmd[0]).name
        if cmd not in ("chown", "chmod", "chgrp"):
            raise SystemExit(
                "Wrapper mode only supports chown, chmod, and chgrp"
            )

    console = Console()

    conn = connect(Path(args.db).expanduser().resolve() if args.db else None)
    init_db(conn)

    if wrapper_cmd:
        paths = _extract_paths_from_command(wrapper_cmd)

        if paths:
            root_path = _common_snapshot_root(paths)

            with conn:
                state_id, auto_name = _create_auto_state(
                    conn, root_path, os.getuid()
                )

                seen_paths: set[str] = set()
                for path in paths:
                    for rel, typ, mode, uid, gid in _iter_entries_for_target(
                        path, root_path
                    ):
                        # A command may name the same path more than once, or
                        # name overlapping trees such as "foo" and "foo/bar".
                        # Store the pre-command state once per path.
                        if rel in seen_paths:
                            continue
                        seen_paths.add(rel)

                        if uid == 0 and not _is_root():
                            raise SystemExit(
                                "This command affects root-owned files.\n"
                                "Please re-run with sudo."
                            )

                        conn.execute(
                            """
                            INSERT INTO entries
                                (state_id, path, type, mode, uid, gid)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (state_id, rel, typ, mode, uid, gid),
                        )

            console.print(
                f"Saved pre-command snapshot: [cyan]{auto_name}[/cyan]"
            )

        proc = subprocess.run(wrapper_cmd)
        sys.exit(proc.returncode)

    if args.prune_states is not None:
        value = (
            None
            if args.prune_states == PRUNE_STATES_FROM_ENV
            else args.prune_states
        )
        life = _parse_prune_states_value(value)

        if life == "all":
            rows = conn.execute("""
                SELECT name, root_path, created_at
                FROM states
                ORDER BY created_at
                """).fetchall()
            cutoff_iso = None
        else:
            cutoff = datetime.now(timezone.utc) - timedelta(days=life)
            cutoff_iso = cutoff.isoformat(timespec="seconds")
            rows = conn.execute(
                """
                SELECT name, root_path, created_at
                FROM states
                WHERE created_at < ?
                ORDER BY created_at
                """,
                (cutoff_iso,),
            ).fetchall()

        if not rows:
            console.print("No states matched.")
            return

        console.print(
            f"\nThe following {len(rows)} state(s) will be deleted:\n"
        )

        table = Table(box=box.SIMPLE, header_style="bold")
        table.add_column("State")
        table.add_column("Root path")
        table.add_column("Created")

        for name, root, created in rows:
            state_name = (
                f"[bright_cyan]{name}[/bright_cyan]"
                if name.startswith("auto-")
                else name
            )
            table.add_row(
                state_name,
                f"[bright_magenta]{root}[/bright_magenta]",
                f"[bright_cyan]{created}[/bright_cyan]",
            )

        console.print(table)

        if args.dry_run:
            console.print(
                "\n[yellow]Dry-run only. No states were deleted.[/yellow]"
            )
            return

        _confirm_or_abort(yes=args.yes, prompt="Delete these states?")

        if life == "all":
            deleted = prune_all_states(conn)
        else:
            deleted = prune_states_before(conn, cutoff_iso)

        console.print(f"\nDeleted {deleted} state(s).")
        return

    if args.list:
        rows = conn.execute("""
            SELECT id, name, root_path, created_at
            FROM states
            ORDER BY created_at DESC
            """).fetchall()

        if not rows:
            console.print("No saved states.")
            return

        table = Table(box=box.SIMPLE, header_style="bold")
        table.add_column("State")
        table.add_column("Snapshot root")
        table.add_column("Captured paths")
        table.add_column("Created")

        for state_id, name, root, created in rows:
            state_name = (
                f"[bright_cyan]{name}[/bright_cyan]"
                if name.startswith("auto-")
                else name
            )
            table.add_row(
                state_name,
                f"[bright_magenta]{root}[/bright_magenta]",
                _captured_paths_summary(conn, state_id, root),
                f"[bright_cyan]{created}[/bright_cyan]",
            )

        console.print(table)
        return

    if args.delete:
        if delete_state(conn, args.delete) == 0:
            raise SystemExit(f"No such state: {args.delete}")
        console.print(f"Deleted state '{args.delete}'")
        return

    if args.save:
        if not args.name:
            parser.error("--name is required with --save")

        root = normalize_root(args.save)

        with conn:
            if state_exists(conn, args.name):
                if not args.overwrite:
                    raise SystemExit(
                        f"State '{args.name}' already exists (use --overwrite)"
                    )
                delete_state(conn, args.name, commit=False)

            state_id = create_state(
                conn, args.name, str(root), os.getuid(), commit=False
            )

            for entry in scan_tree(root, excludes=args.exclude):
                if entry.uid == 0 and not _is_root():
                    raise SystemExit(
                        "This path contains root-owned files.\n"
                        "Saving this state requires sudo."
                    )

                conn.execute(
                    """
                    INSERT INTO entries
                        (state_id, path, type, mode, uid, gid)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state_id,
                        entry.path,
                        entry.type,
                        entry.mode,
                        entry.uid,
                        entry.gid,
                    ),
                )

        console.print(f"Saved state '{args.name}' for {root}")
        return

    if args.restore:
        if not args.state:
            parser.error("STATE is required with --restore")

        state = get_state(conn, args.state)
        if not state:
            raise SystemExit(f"No such state: {args.state}")

        snapshot_root = Path(state.root_path)
        target_root = normalize_root(args.root) if args.root else snapshot_root

        restore_permissions = args.permissions or (
            not args.permissions and not args.owner
        )
        restore_owner = args.owner or (not args.permissions and not args.owner)

        rows = conn.execute(
            "SELECT path, type, mode, uid, gid FROM entries WHERE state_id = ?",
            (state.id,),
        ).fetchall()

        changes = plan_restore(
            root=target_root,
            rows=rows,
            restore_permissions=restore_permissions,
            restore_owner=restore_owner,
        )

        per_path, counts, needs_root = _restore_preview_rows(
            changes, target_root, os.geteuid()
        )

        if not changes:
            console.print("No differences found.")
            return

        if not per_path:
            console.print(
                "No differences found for the selected restore scope."
            )
            return

        console.print(f"\nRestoring under: {target_root}\n")

        table = Table(box=box.SIMPLE, header_style="bold")
        table.add_column("Path")
        table.add_column("Owner change", style="cyan")
        table.add_column("Mode change", style="green")
        table.add_column("Skipped", style="yellow")

        for path in sorted(per_path):
            row = per_path[path]
            table.add_row(
                str(path),
                row.get("owner", "—"),
                row.get("mode", "—"),
                row.get("skipped", "—"),
            )

        console.print(table)
        console.print(
            f"\nSummary: {counts['mode']} mode change(s), "
            f"{counts['owner']} owner change(s), "
            f"{counts['skipped']} skipped item(s)"
        )

        if counts["mode"] == 0 and counts["owner"] == 0:
            console.print(
                "\n[yellow]No applicable changes. "
                "Skipped items were not restored.[/yellow]"
            )
            return

        if args.dry_run:
            console.print(
                "\n[yellow]Dry-run only. No changes were applied.[/yellow]"
            )
            return

        if needs_root and not _is_root():
            raise SystemExit(
                "This restore requires elevated privileges.\n"
                "Please re-run the command with sudo."
            )

        _confirm_or_abort(
            yes=args.yes, prompt="Do you want to restore this state?"
        )

        apply_restore(
            root=target_root,
            rows=rows,
            restore_permissions=restore_permissions,
            restore_owner=restore_owner,
        )
        console.print("\n[green]Restore complete.[/green]")
