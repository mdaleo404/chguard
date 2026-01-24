from __future__ import annotations

import argparse
import argcomplete
import importlib.metadata
import os
import sys
import stat
import pwd
import grp
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich import box

from chguard.db import (
    connect,
    init_db,
    create_state,
    delete_state,
    get_state,
    state_exists,
)
from chguard.scan import scan_tree
from chguard.restore import plan_restore, apply_restore
from chguard.util import normalize_root


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


def main() -> None:
    """
    Entry point for the CLI.

    Behavior summary:
    - --save snapshots ownership and permissions
    - --restore previews changes, then asks for confirmation
    - Root privileges are required only when necessary
    - Symlinks are skipped during scanning
    """

    wrapper_cmd = None
    if "--" in sys.argv:
        idx = sys.argv.index("--")
        wrapper_cmd = sys.argv[idx + 1 :]
        sys.argv = sys.argv[:idx]

    parser = argparse.ArgumentParser(
        prog="chguard",
        description="Snapshot and restore filesystem ownership and permissions.",
    )

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
        "--save",
        metavar="PATH",
        help="Save state for PATH",
    ).completer = argcomplete.FilesCompleter()

    actions.add_argument(
        "--restore",
        action="store_true",
        help="Restore a saved state",
    )

    actions.add_argument(
        "--list",
        action="store_true",
        help="List saved states",
    )

    actions.add_argument(
        "--delete",
        metavar="STATE",
        help="Delete a saved state",
    ).completer = complete_state_names

    # positional STATE
    parser.add_argument(
        "state",
        nargs="?",
        help="State name (required with --restore)",
    ).completer = complete_state_names

    parser.add_argument(
        "--name",
        help="State name (required with --save)",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing state",
    )

    parser.add_argument(
        "--permissions",
        action="store_true",
        help="Restore MODE only",
    )

    parser.add_argument(
        "--owner",
        action="store_true",
        help="Restore OWNER only",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only; do not apply",
    )

    parser.add_argument(
        "--yes",
        action="store_true",
        help="Apply without confirmation",
    )

    parser.add_argument(
        "--root",
        metavar="PATH",
        help="Override restore root",
    ).completer = argcomplete.FilesCompleter()

    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Exclude path prefix",
    ).completer = argcomplete.FilesCompleter()

    parser.add_argument(
        "--db",
        metavar="PATH",
        help="Override database path",
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
            auto_name = f"auto-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            with conn:
                root_path = str(Path(paths[0]).resolve())
                state_id = create_state(
                    conn, auto_name, root_path, os.getuid(), commit=False
                )

                for path in paths:
                    if path.is_dir():
                        for entry in scan_tree(path):
                            if entry.uid == 0 and not _is_root():
                                raise SystemExit(
                                    "This command affects root-owned files.\n"
                                    "Please re-run with sudo."
                                )
                            conn.execute(
                                """
                                INSERT INTO entries (state_id, path, type, mode, uid, gid)
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
                    else:
                        st = path.lstat()
                        if st.st_uid == 0 and not _is_root():
                            raise SystemExit(
                                "This command affects root-owned files.\n"
                                "Please re-run with sudo."
                            )
                        conn.execute(
                            """
                            INSERT INTO entries (state_id, path, type, mode, uid, gid)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                state_id,
                                str(path),
                                "file",
                                stat.S_IMODE(st.st_mode),
                                st.st_uid,
                                st.st_gid,
                            ),
                        )

            console.print(
                f"Saved pre-command snapshot: [cyan]{auto_name}[/cyan]"
            )

        proc = subprocess.run(wrapper_cmd)
        sys.exit(proc.returncode)

    if args.list:
        rows = conn.execute(
            "SELECT name, root_path, created_at FROM states ORDER BY created_at DESC"
        ).fetchall()

        if not rows:
            console.print("No saved states.")
            return

        table = Table(box=box.SIMPLE, header_style="bold")

        table.add_column("State")
        table.add_column("Root path")
        table.add_column("Created")

        for name, root, created in rows:
            dt = datetime.fromisoformat(created)
            ts = dt.strftime("%Y-%m-%d %H:%M:%S %z")

            state_name = (
                f"[bright_cyan]{name}[/bright_cyan]"
                if name.startswith("auto-")
                else name
            )
            root = f"[bright_magenta]{root}[/bright_magenta]"
            ts = f"[bright_cyan]{created}[/bright_cyan]"

            table.add_row(
                state_name,
                root,
                ts,
            )

        console.print(table)

    if args.delete:
        if delete_state(conn, args.delete) == 0:
            raise SystemExit(f"No such state: {args.delete}")
        console.print(f"Deleted state '{args.delete}'")
        return

    if args.save:
        if not args.name:
            parser.error("--name is required with --save")

        root = normalize_root(args.save)

        try:
            with conn:  # start transaction
                if state_exists(conn, args.name):
                    if not args.overwrite:
                        raise SystemExit(
                            f"State '{args.name}' already exists (use --overwrite)"
                        )
                    # if the new save fails, this delete_state step will also roll back
                    delete_state(conn, args.name, commit=False)

                state_id = create_state(
                    conn, args.name, str(root), os.getuid(), commit=False
                )

                # Abort early if root-owned files exist and user is not root.
                # This prevents creating snapshots that cannot be meaningfully restored.
                for entry in scan_tree(root, excludes=args.exclude):
                    if entry.uid == 0 and not _is_root():
                        raise SystemExit(
                            "This path contains root-owned files.\n"
                            "Saving this state requires sudo."
                        )

                    conn.execute(
                        """
                        INSERT INTO entries (state_id, path, type, mode, uid, gid)
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

        except SystemExit:
            raise

    if args.restore:
        if not args.state:
            parser.error("STATE is required with --restore")

        state = get_state(conn, args.state)
        if not state:
            raise SystemExit(f"No such state: {args.state}")

        snapshot_root = Path(state.root_path)
        target_root = normalize_root(args.root) if args.root else snapshot_root

        # Default restore behavior is OWNER + MODE unless narrowed explicitly.
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

        per_path: dict[Path, dict[str, str]] = defaultdict(dict)
        counts = Counter()
        needs_root = False
        current_uid = os.geteuid()

        # Build a per-path view of owner/mode changes and detect privilege needs.
        for ch in changes:
            if ch.kind not in ("owner", "mode"):
                continue

            try:
                rel = ch.path.relative_to(target_root)
            except ValueError:
                rel = ch.path

            if ch.kind == "owner" and restore_owner:
                before, after = ch.detail.split(" -> ")
                bu, bg = map(int, before.split(":"))
                au, ag = map(int, after.split(":"))

                per_path[rel][
                    "owner"
                ] = f"{_format_owner(bu, bg)} → {_format_owner(au, ag)}"
                counts["owner"] += 1

                if ch.path.stat().st_uid != current_uid:
                    needs_root = True

            elif ch.kind == "mode" and restore_permissions:
                b, a = ch.detail.split(" -> ")
                per_path[rel][
                    "mode"
                ] = f"{_mode_to_rwx(int(b, 8))} → {_mode_to_rwx(int(a, 8))}"
                counts["mode"] += 1

                try:
                    if ch.path.stat().st_uid != current_uid:
                        needs_root = True
                except FileNotFoundError:
                    pass

        if not per_path:
            console.print("No differences found.")
            return

        console.print(f"\nRestoring under: {target_root}\n")

        table = Table(box=box.SIMPLE, header_style="bold")
        table.add_column("Path")
        table.add_column("Owner change", style="cyan")
        table.add_column("Mode change", style="green")

        for path in sorted(per_path):
            row = per_path[path]
            table.add_row(
                str(path),
                row.get("owner", "—"),
                row.get("mode", "—"),
            )

        console.print(table)
        console.print(
            f"\nSummary: {counts['mode']} mode change(s), "
            f"{counts['owner']} owner change(s)"
        )

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

        if not args.yes:
            if not sys.stdin.isatty():
                raise SystemExit(
                    "Refusing to apply changes without confirmation (no TTY).\n"
                    "Use --yes to force."
                )

            answer = (
                input("\nDo you want to restore this state? (y/N) ")
                .strip()
                .lower()
            )
            if answer not in ("y", "yes"):
                console.print("\nAborted.")
                return

        apply_restore(
            root=target_root,
            rows=rows,
            restore_permissions=restore_permissions,
            restore_owner=restore_owner,
        )
        console.print("\n[green]Restore complete.[/green]")
