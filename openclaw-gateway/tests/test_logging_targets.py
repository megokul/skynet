from __future__ import annotations

from logging_targets import iter_dirs, join_windows_path, resolve_targets


def test_join_windows_path_trims_duplicate_separators() -> None:
    assert join_windows_path(r"C:\Logs\\", "runtime.log") == r"C:\Logs\runtime.log"
    assert join_windows_path("", "runtime.log") == "runtime.log"


def test_iter_dirs_deduplicates_equivalent_targets(tmp_path) -> None:
    primary = str(tmp_path)
    mirror = str(tmp_path)

    dirs = list(iter_dirs(primary, mirror))

    assert dirs == [tmp_path]


def test_resolve_targets_creates_requested_file_paths(tmp_path) -> None:
    targets = resolve_targets(str(tmp_path), None, "runtime.log")

    assert targets == [tmp_path / "runtime.log"]
    assert tmp_path.exists()
