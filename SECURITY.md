# chguard Threat Model and Security Scope

chguard is a command-line systems administration tool. It is designed to be
executed intentionally by an operator, sometimes with elevated privileges, to
snapshot and restore filesystem ownership and permission metadata.

Because of that design, chguard's security model is different from that of a
network service, web application, daemon, sandbox, or setuid program. chguard
does not attempt to defend against arbitrary local compromise of the account
executing it. If an attacker can control the command line, environment, working
directory, `PATH`, selected database, installed Python package, or wrapped
system command used by the operator, they may be able to influence what chguard
does. That situation is considered a local trust-boundary failure outside
chguard's intended security model.

chguard only manages filesystem metadata. It does not read, store, compare,
restore, or protect file contents.

## Core Assumptions

chguard assumes that the person running the tool understands what they are
asking it to do.

In particular:

* If chguard is run as root, the root user is assumed to control and understand
  the command line, environment, database path, restore root, and wrapped
  command being used.
* If `--db` is used, the selected SQLite database path and its contents are
  assumed to be trusted local administrative state chosen by the operator.
* If `--root` is used during restore, the alternate restore root is assumed to
  be intentionally selected by the operator.
* If `--yes` is used, the operator is intentionally bypassing the interactive
  confirmation prompt.
* Wrapper mode commands are assumed to be the trusted `chown`, `chmod`, or
  `chgrp` implementation that the operator intended to execute.
* The operator is expected to understand the impact of restoring ownership and
  permission bits, especially when restoring as root.

## What chguard Records

chguard snapshots a narrow set of filesystem metadata:

* Relative path under the snapshot root.
* Entry type: regular file, directory, or symbolic link.
* Permission bits.
* Numeric `uid`.
* Numeric `gid`.

chguard deliberately does not snapshot:

* File contents.
* File hashes.
* ACLs.
* Extended attributes.
* Capabilities.
* SELinux, AppArmor, or other MAC labels.
* Deleted files.
* Newly created files.
* Device nodes, sockets, FIFOs, or other special files.

User and group names are display-only. Numeric `uid` and `gid` values are the
source of truth.

## What Is In Scope

chguard tries to protect careful administrators from common and serious mistakes
that can occur when a privileged CLI tool records and restores filesystem
metadata.

In-scope security concerns include:

* Restore must not create, delete, move, rename, or rewrite files.
* Restore must not change file contents.
* Restore must preview applicable owner and mode changes before applying them.
* Mutating restore must require confirmation unless `--yes` is provided.
* Mutating prune operations must require confirmation unless `--yes` is
  provided.
* Preview and dry-run restore should remain usable without root.
* chguard must not automatically run sudo or otherwise escalate privileges.
* Scanning and restoring should use no-follow filesystem operations and avoid
  following symlink targets.
* Restore should re-check current filesystem state before applying chmod or
  chown operations.
* Restore should skip missing paths, unsupported file types, and type mismatches
  rather than replacing them.
* Wrapper mode should stay limited to ownership and permission commands:
  `chown`, `chmod`, and `chgrp`.
* Wrapper mode should not use a shell to execute the wrapped command.
* SQLite access should use parameterized queries for operator-provided names and
  paths.
* A state created by chguard through normal scanning should not contain relative
  paths that escape the snapshot root.

These measures are defense-in-depth. They are intended to reduce the chance of
accidental metadata changes, symlink traversal, unintended privilege changes, or
unsafe restore behavior when chguard is used normally by an administrator.

## What Is Out Of Scope

The following are generally out of scope and should not be reported as chguard
vulnerabilities unless they also bypass one of chguard's explicit hardening
mechanisms:

* A malicious local user who can already control the root user's command line,
  shell environment, working directory, `PATH`, Python environment, installed
  package, or invoked binaries.
* A root user intentionally selecting a malicious or manually edited SQLite
  database with `--db`.
* A root user intentionally restoring a snapshot that sets unsafe ownership or
  permissions.
* A root user intentionally using `--root` to apply a trusted snapshot under a
  different filesystem tree.
* A root user intentionally passing `--yes` and bypassing confirmation.
* A user intentionally wrapping a malicious binary whose basename is `chown`,
  `chmod`, or `chgrp`.
* A user relying on chguard to restore file contents, deleted files, ACLs,
  extended attributes, capabilities, MAC labels, or full undo semantics.
* A user relying on chguard as a sandbox for untrusted local users or untrusted
  command execution.
* A compromised system where an attacker already controls root-owned files,
  root's shell, root's Python packages, root's environment, or the privileged
  tools chguard invokes.
* Reports that amount to "if root runs this tool with malicious options, root
  can make the system do dangerous things."

chguard is a tool for administrators, not a sandbox for hostile local users. It
cannot make unsafe local trust decisions safe if the operator's own execution
environment is already attacker-controlled.

## Trusted Snapshot Databases

chguard snapshots are stored in a local SQLite database. By default, chguard uses
the platform-specific user data directory for the invoking account. Operators
may override this with `--db`.

Snapshot databases should be treated as trusted administrative state. They can
contain filesystem paths, ownership, group, permission, timestamp, and snapshot
root information. They do not contain file contents, but the metadata can still
reveal operational details about a system.

Before running restore, especially as root or with `--yes`, the operator should
be confident that the selected database is the intended one and has not been
tampered with.

chguard-created snapshots are expected to contain paths relative to the snapshot
root. chguard does not treat an arbitrary attacker-supplied SQLite database as
untrusted input to be safely enforced.

## Wrapper Mode

Wrapper mode exists to take an automatic pre-command snapshot before running a
metadata-changing command:

```bash
chguard -- chmod 755 path
chguard -- chown user:group path
chguard -- chgrp group path
```

Wrapper mode is intentionally limited to `chmod`, `chown`, and `chgrp` by command
basename. chguard snapshots existing non-option path arguments that it can
identify, then runs the wrapped command and returns that command's exit code.

Wrapper mode is not a full parser for every possible option accepted by those
commands. It is a guardrail for common ownership and permission changes, not a
general command supervision framework.

## Symlinks And Filesystem Races

chguard uses `lstat()` and no-follow operations while scanning and restoring. It
records symbolic link entries and attempts no-follow ownership or permission
restoration where the platform supports it. It should not follow a symbolic link
target and apply changes to the target as part of scanning or restore.

Restore is best-effort across platforms. Some operations, such as changing
symlink permissions, are not supported everywhere and may be skipped.

Because chguard operates on a live filesystem, concurrent filesystem changes can
still affect what exists at the moment restore runs. chguard mitigates this by
re-checking paths before applying changes and by skipping missing paths, special
files, and type mismatches. It does not claim to provide a transactional
filesystem restore.

## Local Compromise

chguard includes hardening against some local filesystem attack patterns because
it is often run with high privileges. For example, it avoids symlink traversal,
does not use a shell for wrapper mode, previews restore changes, and does not
automatically escalate privileges.

However, local compromise cannot be ruled out completely for a privileged CLI
tool. If an attacker can influence the administrator's shell, environment,
database, binaries, Python packages, current working directory, or command-line
arguments, they may be able to influence chguard's behavior.

Such scenarios are treated as local compromise or operator trust failures, not
as vulnerabilities in chguard by themselves.

## Security Report Guidance

Useful vulnerability reports include issues where chguard behaves unsafely
despite the documented trust model. Examples include:

* chguard follows a symlink target during save or restore in a way that causes
  unintended privileged chmod or chown operations.
* Restore creates, deletes, moves, renames, or rewrites files.
* Restore applies owner or mode changes without previewing them first.
* Restore applies changes without confirmation when `--yes` was not provided.
* Dry-run or preview applies filesystem changes.
* chguard automatically escalates privileges or invokes sudo.
* A snapshot produced by normal chguard scanning can contain paths that escape
  the snapshot root during restore.
* Wrapper mode accepts and executes unsupported command classes outside `chown`,
  `chmod`, or `chgrp`.
* Wrapper mode introduces shell injection when running ordinary operator-provided
  path names or arguments.
* SQLite operations allow operator-provided state names or paths to alter
  unintended database rows through injection.
* A failed safety check is silently ignored and chguard proceeds with a dangerous
  operation anyway.

Less useful reports, and normally out of scope, include:

* "Root can restore dangerous permissions."
* "Root can pass `--yes` and bypass the confirmation prompt."
* "Root can point `--db` at a malicious SQLite database."
* "Root can use `--root` to restore metadata into a different tree."
* "A malicious local user can compromise chguard after already controlling
  root's environment, Python packages, or binaries."
* "chguard does not restore file contents, ACLs, xattrs, capabilities, or full
  deleted-file state."

Reports about concrete bypasses of chguard's hardening are welcome. The project
does not treat intentional administrator-controlled execution as a vulnerability
by itself.
