from pathlib import Path

from src.output_paths import (
    existing_version_numbers,
    next_version_name,
    versioned_dir,
)


def test_first_version_is_v1(tmp_path: Path) -> None:
    base = tmp_path / "talking_head_002"
    out = versioned_dir(base)
    assert out == base / "v1"
    assert out.is_dir()
    assert (base / "latest").resolve() == out.resolve()


def test_auto_increment_keeps_previous_versions(tmp_path: Path) -> None:
    base = tmp_path / "video1"
    first = versioned_dir(base)
    second = versioned_dir(base)
    third = versioned_dir(base)
    assert [first.name, second.name, third.name] == ["v1", "v2", "v3"]
    assert all(p.is_dir() for p in (first, second, third))
    # latest follows the newest render
    assert (base / "latest").resolve() == third.resolve()


def test_explicit_version_overwrites_in_place(tmp_path: Path) -> None:
    base = tmp_path / "video1"
    versioned_dir(base)  # v1
    versioned_dir(base)  # v2
    again = versioned_dir(base, version="v2")
    assert again == base / "v2"
    # asking for an explicit version does not bump the counter
    assert next_version_name(base) == "v3"


def test_version_accepts_bare_number(tmp_path: Path) -> None:
    base = tmp_path / "vid"
    assert versioned_dir(base, version="2").name == "v2"
    assert versioned_dir(base, version="V3").name == "v3"


def test_latest_symlink_ignored_when_counting(tmp_path: Path) -> None:
    base = tmp_path / "vid"
    versioned_dir(base)  # v1 + latest symlink
    versioned_dir(base)  # v2
    # the "latest" symlink must not be mistaken for a version
    assert existing_version_numbers(base) == [1, 2]
    assert next_version_name(base) == "v3"
