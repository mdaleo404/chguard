from __future__ import annotations

from pathlib import Path

import pytest

from chguard.cli import (
    _common_snapshot_root,
    _extract_paths_from_command,
    _parse_prune_states_value,
)


def test_parse_prune_states_value_accepts_days_and_all(monkeypatch) -> None:
    assert _parse_prune_states_value("14") == 14
    assert _parse_prune_states_value(" all ") == "all"

    monkeypatch.setenv("CHGUARD_STATES_LIFE", "30")
    assert _parse_prune_states_value(None) == 30


@pytest.mark.parametrize("value", ["", "abc", "-1"])
def test_parse_prune_states_value_rejects_invalid_values(
    value: str, monkeypatch
) -> None:
    monkeypatch.delenv("CHGUARD_STATES_LIFE", raising=False)

    with pytest.raises(SystemExit):
        _parse_prune_states_value(value)


def test_extract_paths_from_command_returns_existing_non_options(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "file.txt"
    target.write_text("data", encoding="utf-8")

    paths = _extract_paths_from_command(
        ["chmod", "-R", "644", "file.txt", "missing.txt"]
    )

    assert paths == [target.resolve()]


def test_common_snapshot_root_uses_single_path_or_common_parent(
    tmp_path: Path,
) -> None:
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()

    assert _common_snapshot_root([one]) == one.resolve()
    assert _common_snapshot_root([one, two]) == tmp_path.resolve()
