[![Licence](https://img.shields.io/badge/GPL--3.0-orange?label=Licence)](https://git.sysmd.uk/guardutils/chguard/src/branch/main/LICENCE)
[![Gitea Release](https://img.shields.io/gitea/v/release/guardutils/chguard?gitea_url=https%3A%2F%2Fgit.sysmd.uk%2F&style=flat&color=orange&logo=gitea)](https://git.sysmd.uk/guardutils/chguard/releases)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-blue?logo=pre-commit&style=flat)](https://git.sysmd.uk/guardutils/chguard/src/branch/main/.pre-commit-config.yaml)

# chguard

<div align="center">
  <img src="https://git.sysmd.uk/guardutils/chguard/raw/branch/main/chguard.png" alt="chguard logo" width="256" />
</div>


**chguard** is a safety-first command-line tool that snapshots and restores
filesystem ownership and permissions.

Think of it as a guardrail around `chmod` and `chown`:
it records the current state, shows you exactly what would change, and only
applies changes after explicit confirmation.

## Features

### Snapshots ownership and permissions
Records numeric `uid`, `gid`, and file mode for files and directories.

### Preview before restore
Always shows a clear, readable table of differences before applying changes.

### Interactive confirmation
A single confirmation prompt at the end of a restore (default: **No**).

### Dry-run mode
Preview restore operations without prompting or applying changes.

### Wrapper mode (automatic snapshots)

`chguard` can also run as a wrapper around ownership and permission commands.
In this mode, `chguard` automatically saves a snapshot before the command runs, so the user can easily restore the previous state if needed.

#### Supported commands

Wrapper mode is intentionally limited to commands that modify filesystem metadata only:

* `chown`
* `chmod`
* `chgrp`

Other commands are rejected to avoid giving a _false sense of protection_.

#### Automatic snapshot names

Snapshots created in wrapper mode are named automatically, for example:

```
auto-20251230-161301
```

Auto-generated snapshots are visually distinguished in the output so they are easy to identify.

### Scope control
Restore:
* both ownership and permissions (default)
* permissions only
* ownership only

### Safe by design
* Never creates, deletes, or moves files
* Missing files are ignored
* New files are ignored
* Symbolic links are skipped entirely
* Requires sudo **only when necessary**

## Non-Goals

`chguard` deliberately does **not**:

* restore deleted files
* remove newly created files
* track file contents or checksums
* manage ACLs or extended attributes
* provide full “undo” semantics

It only concerns itself with **ownership** and **permissions**.

## Installation

### From GuardUtils package repo

This is the preferred method of installation.

### Debian/Ubuntu

#### 1) Import the GPG key

```bash
sudo mkdir -p /usr/share/keyrings
curl -fsSL https://repo.sysmd.uk/guardutils/guardutils.gpg | sudo gpg --dearmor -o /usr/share/keyrings/guardutils.gpg
```

The GPG fingerprint is `0032C71FA6A11EF9567D4434C5C06BD4603C28B1`.

#### 2) Add the APT source

```bash
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/guardutils.gpg] https://repo.sysmd.uk/guardutils/debian stable main" | sudo tee /etc/apt/sources.list.d/guardutils.list
```

#### 3) Update and install

```
sudo apt update
sudo apt install chguard
```

### Fedora/RHEL

#### 1) Import the GPG key

```
sudo rpm --import https://repo.sysmd.uk/guardutils/guardutils.gpg
```

#### 2) Add the repository configuration

```
sudo tee /etc/yum.repos.d/guardutils.repo > /dev/null << 'EOF'
[guardutils]
name=GuardUtils Repository
baseurl=https://repo.sysmd.uk/guardutils/rpm/$basearch
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=https://repo.sysmd.uk/guardutils/guardutils.gpg
EOF
```

#### 4) Update and install

```
sudo dnf upgrade --refresh
sudo dnf install chguard
```

### From PyPI
```
pip install chguard
```

### From this repository

```bash
git clone https://git.sysmd.uk/guardutils/chguard.git
cd chguard/
poetry install
```

This installs the chguard CLI into the Poetry environment.

## Usage

### Save a state
```
chguard --save /srv/app --name app-baseline
```

If the path contains root-owned files, saving requires sudo.

### List saved states
```
chguard --list
```

Example output:
```
app-baseline   /srv/app   2025-12-20 18:11:08 +00:00
```

### Restore a state (preview only)
```
chguard --restore app-baseline
```

This shows a table of ownership and permission differences.

### Restore with confirmation
```
chguard --restore app-baseline
```

You will be prompted:
```
Do you want to restore this state? (y/N)
```

The default answer is No.

### Dry-run
```
chguard --restore app-baseline --dry-run
```

### Restore only permissions or only ownership
```
chguard --restore app-baseline --permissions
chguard --restore app-baseline --owner
```

### Wrapper mode

Use `--` to separate `chguard` arguments from the wrapped command:

```
chguard -- chown user:group file
chguard -- chmod 755 file
chguard -- chgrp staff file
```

## Privilege model

`chguard` never escalates privileges automatically

* Saving fails if root-owned files are present and the user is not root
* Restoring fails if changes require elevated privileges
* Preview and dry-run operations never require sudo

## Storage

Snapshots are stored in a local SQLite database containing:

* relative path
* file type (file or directory)
* numeric uid / gid
* numeric mode

Usernames and permission strings are resolved only for display.

### TAB completion
Add this to your `.bashrc`
```
eval "$(register-python-argcomplete chguard)"
```
And then
```
source ~/.bashrc
```

## pre-commit
This project uses [**pre-commit**](https://pre-commit.com/) to run automatic formatting and security checks before each commit (Black, Bandit, and various safety checks).

To enable it:
```
poetry install
poetry run pre-commit install
```
This ensures consistent formatting, catches common issues early, and keeps the codebase clean.
