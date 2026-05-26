"""Tests for PC profile path helpers, I/O, and rendering."""

from __future__ import annotations

from pathlib import Path

from grimoire.state_store.paths import pc_profile_path, pc_profile_revisions_dir


def test_pc_profile_path(tmp_path: Path) -> None:
    result = pc_profile_path(tmp_path, "camp-1", "alistair")
    assert result == tmp_path / "campaigns" / "camp-1" / "characters" / "alistair" / "profile.md"


def test_pc_profile_revisions_dir(tmp_path: Path) -> None:
    result = pc_profile_revisions_dir(tmp_path, "camp-1", "alistair")
    assert result == tmp_path / "campaigns" / "camp-1" / "characters" / "alistair" / "revisions"


# ---------------------------------------------------------------------------
# PCProfile model and I/O
# ---------------------------------------------------------------------------

from grimoire.characters.pc_profile import (
    PCProfile,
    PCProfileRevision,
    read_pc_profile,
    write_pc_profile,
    list_pc_profile_revisions,
    read_pc_profile_revision,
)


def test_pc_profile_defaults() -> None:
    profile = PCProfile(character_ref="library:worlds/wod/characters/alistair")
    assert profile.goals == []
    assert profile.player_notes == ""
    assert profile.description == ""
    assert profile.updated_at is not None


def test_write_and_read_profile(tmp_path: Path) -> None:
    profile = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        goals=["Find the lost artifact", "Protect the chantry"],
        player_notes="Lean into the mentor archetype.",
        description="A Tremere elder, calm and clinical.",
    )
    write_pc_profile(tmp_path, "camp-1", "alistair", profile)
    target = pc_profile_path(tmp_path, "camp-1", "alistair")
    assert target.exists()

    loaded = read_pc_profile(tmp_path, "camp-1", "alistair")
    assert loaded is not None
    assert loaded.character_ref == "library:worlds/wod/characters/alistair"
    assert loaded.goals == ["Find the lost artifact", "Protect the chantry"]
    assert loaded.player_notes == "Lean into the mentor archetype."
    assert loaded.description == "A Tremere elder, calm and clinical."


def test_read_missing_profile(tmp_path: Path) -> None:
    result = read_pc_profile(tmp_path, "camp-1", "nobody")
    assert result is None


def test_write_creates_revision(tmp_path: Path) -> None:
    profile_v1 = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        goals=["Original goal"],
        description="Version one.",
    )
    write_pc_profile(tmp_path, "camp-1", "alistair", profile_v1)

    profile_v2 = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        goals=["Updated goal"],
        description="Version two.",
    )
    write_pc_profile(tmp_path, "camp-1", "alistair", profile_v2)

    revisions = list_pc_profile_revisions(tmp_path, "camp-1", "alistair")
    assert len(revisions) == 1
    assert revisions[0].description == "Version one."
    assert revisions[0].goals == ["Original goal"]


def test_read_specific_revision(tmp_path: Path) -> None:
    profile_v1 = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        goals=["Original goal"],
        description="Version one.",
    )
    write_pc_profile(tmp_path, "camp-1", "alistair", profile_v1)

    profile_v2 = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        goals=["Updated goal"],
        description="Version two.",
    )
    write_pc_profile(tmp_path, "camp-1", "alistair", profile_v2)

    revisions = list_pc_profile_revisions(tmp_path, "camp-1", "alistair")
    assert len(revisions) == 1

    loaded = read_pc_profile_revision(
        tmp_path, "camp-1", "alistair", revisions[0].timestamp
    )
    assert loaded is not None
    assert loaded.description == "Version one."


def test_first_write_no_revision(tmp_path: Path) -> None:
    profile = PCProfile(
        character_ref="library:worlds/wod/characters/alistair",
        goals=["First goal"],
        description="First version.",
    )
    write_pc_profile(tmp_path, "camp-1", "alistair", profile)
    revisions = list_pc_profile_revisions(tmp_path, "camp-1", "alistair")
    assert len(revisions) == 0
