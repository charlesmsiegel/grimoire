"""PC Profile — campaign-scoped character overlay stored as markdown.

Each PC in a campaign can have a profile with description, goals, and
player notes. Profiles are markdown files with YAML frontmatter at:
    data/campaigns/{campaign_id}/characters/{character_id}/profile.md

Revisions are timestamped copies under a sibling revisions/ directory.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from grimoire.files.frontmatter import ParsedDocument, read_markdown, write_markdown
from grimoire.state_store.paths import pc_profile_path, pc_profile_revisions_dir


class PCProfile(BaseModel):
    character_ref: str = ""
    goals: list[str] = Field(default_factory=list)
    player_notes: str = ""
    description: str = ""
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PCProfileRevision(BaseModel):
    timestamp: str
    character_ref: str = ""
    goals: list[str] = Field(default_factory=list)
    player_notes: str = ""
    description: str = ""


def read_pc_profile(
    data_root: Path, campaign_id: str, character_id: str
) -> PCProfile | None:
    target = pc_profile_path(data_root, campaign_id, character_id)
    if not target.exists():
        return None
    doc = read_markdown(target)
    fm = doc.frontmatter
    return PCProfile(
        character_ref=fm.get("character_ref", ""),
        goals=fm.get("goals", []),
        player_notes=fm.get("player_notes", ""),
        description=doc.body.strip(),
        updated_at=fm.get("updated_at", datetime.now(UTC)),
    )


def write_pc_profile(
    data_root: Path, campaign_id: str, character_id: str, profile: PCProfile
) -> None:
    target = pc_profile_path(data_root, campaign_id, character_id)
    if target.exists():
        _snapshot_revision(data_root, campaign_id, character_id, target)
    profile.updated_at = datetime.now(UTC)
    fm: dict = {
        "character_ref": profile.character_ref,
        "goals": profile.goals,
        "player_notes": profile.player_notes,
        "updated_at": profile.updated_at.isoformat(),
    }
    doc = ParsedDocument(frontmatter=fm, body=profile.description + "\n")
    write_markdown(target, doc)


def list_pc_profile_revisions(
    data_root: Path, campaign_id: str, character_id: str
) -> list[PCProfileRevision]:
    rev_dir = pc_profile_revisions_dir(data_root, campaign_id, character_id)
    if not rev_dir.exists():
        return []
    revisions: list[PCProfileRevision] = []
    for path in sorted(rev_dir.glob("*.md")):
        ts = path.stem
        doc = read_markdown(path)
        fm = doc.frontmatter
        revisions.append(
            PCProfileRevision(
                timestamp=ts,
                character_ref=fm.get("character_ref", ""),
                goals=fm.get("goals", []),
                player_notes=fm.get("player_notes", ""),
                description=doc.body.strip(),
            )
        )
    return revisions


def read_pc_profile_revision(
    data_root: Path, campaign_id: str, character_id: str, timestamp: str
) -> PCProfileRevision | None:
    rev_dir = pc_profile_revisions_dir(data_root, campaign_id, character_id)
    path = rev_dir / f"{timestamp}.md"
    if not path.exists():
        return None
    doc = read_markdown(path)
    fm = doc.frontmatter
    return PCProfileRevision(
        timestamp=timestamp,
        character_ref=fm.get("character_ref", ""),
        goals=fm.get("goals", []),
        player_notes=fm.get("player_notes", ""),
        description=doc.body.strip(),
    )


def _snapshot_revision(
    data_root: Path, campaign_id: str, character_id: str, current_path: Path
) -> None:
    rev_dir = pc_profile_revisions_dir(data_root, campaign_id, character_id)
    rev_dir.mkdir(parents=True, exist_ok=True)
    doc = read_markdown(current_path)
    fm = doc.frontmatter
    ts_raw = fm.get("updated_at", datetime.now(UTC).isoformat())
    ts = str(ts_raw).replace(":", "-").replace("+", "_")
    dest = rev_dir / f"{ts}.md"
    shutil.copy2(current_path, dest)
