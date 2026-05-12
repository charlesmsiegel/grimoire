"""Shared test fixtures for the Export module.

We avoid pulling the full SceneManager / StateStore stack — the EPUB
adapter only sees the data through :class:`DataSources`, so we can hand
it minimal in-memory stubs that return the model objects directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from grimoire.export import DataSources
from grimoire.scenes.types import AuthorKind, Post, Scene
from grimoire.types.characters import Character, CharacterRole, VoiceAnchor
from grimoire.types.common import EntityKind
from grimoire.types.composition import LibraryEntity, SettingMeta
from grimoire.types.continuity import (
    Commitment,
    CommitmentKind,
    CommitmentStatus,
    Fact,
    FactSource,
    FactSubject,
)
from grimoire.types.imagegen import ImageMetadata


@dataclass
class StubScenes:
    scenes: list[Scene] = field(default_factory=list)
    posts: dict[str, list[Post]] = field(default_factory=dict)

    async def list_scenes(self, campaign_id, branch_id="main"):
        return [s for s in self.scenes if s.campaign_id == campaign_id and s.branch_id == branch_id]

    async def get_posts(self, scene_id):
        return list(self.posts.get(scene_id, []))


@dataclass
class StubCharacters:
    characters: list[Character] = field(default_factory=list)

    async def list_for_campaign(self, campaign_id):
        return list(self.characters)


@dataclass
class StubSetting:
    settings: list[SettingMeta] = field(default_factory=list)
    entities: dict[tuple[str, str], list[LibraryEntity]] = field(default_factory=dict)
    greetings: dict[str, list] = field(default_factory=dict)

    async def get_composition_settings(self, campaign_id):
        return list(self.settings)

    async def list_in_setting(self, setting_id, kind):
        return list(self.entities.get((setting_id, kind), []))

    async def list_greetings(self, setting_id):
        return list(self.greetings.get(setting_id, []))

    async def get_location(self, location_ref, campaign_id):
        return None


@dataclass
class StubContinuity:
    facts: list[Fact] = field(default_factory=list)
    commitments: list[Commitment] = field(default_factory=list)

    async def list_facts(self, campaign_id):
        return list(self.facts)

    async def list_commitments(self, campaign_id):
        return list(self.commitments)


@dataclass
class StubImages:
    images: list[ImageMetadata] = field(default_factory=list)
    payloads: dict[str, tuple[bytes, str]] = field(default_factory=dict)

    async def list_images(self, campaign_id, scene_id=None):
        if scene_id is None:
            return list(self.images)
        return [i for i in self.images if i.scene_id == scene_id]

    async def image_bytes(self, image_id):
        return self.payloads.get(image_id)


@dataclass
class StubPCs:
    names: dict[str, str] = field(default_factory=dict)

    async def pc_names(self, campaign_id):
        return dict(self.names)


def make_scene(
    *,
    campaign_id: str = "campaign-a",
    ordinal: int = 1,
    title: str = "Elysium Opening",
    slug: str = "elysium-opening",
    branch_id: str = "main",
    location_ref: str | None = "elysium",
    in_game_start: datetime | None = None,
    tags: list[str] | None = None,
    post_count: int = 0,
) -> Scene:
    scene_id = f"{campaign_id}:{ordinal:04d}-{slug}"
    return Scene(
        id=scene_id,
        campaign_id=campaign_id,
        branch_id=branch_id,
        ordinal=ordinal,
        slug=slug,
        title=title,
        location_ref=location_ref,
        in_game_start=in_game_start or datetime(2024, 10, 31, 22, 0, 0),
        present_character_refs=["alistair", "winifred"],
        present_pc_refs=["alistair"],
        post_count=post_count,
        tags=list(tags or []),
    )


def make_post(
    scene_id: str,
    order: int,
    body: str,
    *,
    kind: AuthorKind = AuthorKind.NARRATOR,
    pc_ref: str | None = None,
    npc_ref: str | None = None,
    is_player: bool = False,
) -> Post:
    return Post(
        id=f"{scene_id}#post-{order}",
        scene_id=scene_id,
        order_in_scene=order,
        author_kind=kind,
        body=body,
        is_player=is_player,
        created_at=datetime.now(UTC),
        turn_id=f"turn-{order}",
        author_pc_ref=pc_ref,
        author_npc_ref=npc_ref,
    )


def make_character(
    name: str = "Alistair Hyde-Smythe",
    *,
    role: CharacterRole = CharacterRole.PC,
    description: str = "A nervous antiquarian.",
) -> Character:
    return Character(
        id=name.lower().replace(" ", "-"),
        name=name,
        role=role,
        description=description,
        voice=VoiceAnchor(summary="Formal Edwardian"),
    )


def make_library_location(name: str = "Elysium", body: str = "A candlelit tower.") -> LibraryEntity:
    return LibraryEntity(
        id=f"settings/wod-london/locations/{name.lower()}",
        setting_id="wod-london",
        kind=EntityKind.LOCATION,
        asset_id=name.lower(),
        name=name,
        path=f"settings/wod-london/locations/{name.lower()}.md",
        frontmatter={},
        body=body,
    )


def make_fact(text: str = "Alistair is now sworn to House Hyde.") -> Fact:
    return Fact(
        id=f"fact_{hash(text) & 0xFFFF_FFFF:x}",
        campaign_id="campaign-a",
        branch_id="main",
        text=text,
        established_in_post=None,
        established_at_in_game=None,
        confidence=0.92,
        source=FactSource.NARRATOR,
        about=FactSubject(character_ids=["alistair"]),
    )


def make_commitment(text: str = "Return to the tower at midnight.") -> Commitment:
    return Commitment(
        id=f"com_{hash(text) & 0xFFFF_FFFF:x}",
        campaign_id="campaign-a",
        branch_id="main",
        kind=CommitmentKind.OBLIGATION,
        text=text,
        created_in_post=None,
        in_game_created_at=None,
        from_id="alistair",
        to_id="winifred",
        status=CommitmentStatus.OPEN,
    )


def make_image(
    *,
    image_id: str,
    file_path: Path,
    scene_id: str | None = None,
    prompt: str = "A figure stands at a window.",
) -> ImageMetadata:
    return ImageMetadata(
        id=image_id,
        campaign_id="campaign-a",
        file_path=str(file_path),
        scene_id=scene_id,
        prompt=prompt,
    )


def make_sources(
    *,
    scenes: list[Scene] | None = None,
    posts: dict[str, list[Post]] | None = None,
    characters: list[Character] | None = None,
    setting: SettingMeta | None = None,
    locations: list[LibraryEntity] | None = None,
    lore: list[LibraryEntity] | None = None,
    facts: list[Fact] | None = None,
    commitments: list[Commitment] | None = None,
    images: list[ImageMetadata] | None = None,
    pcs: dict[str, str] | None = None,
    data_root: Path | None = None,
) -> DataSources:
    scenes_src = StubScenes(scenes=list(scenes or []), posts=posts or {})
    chars_src = StubCharacters(characters=characters or [])
    setting_src = StubSetting(
        settings=[setting] if setting else [],
        entities={
            ("wod-london", "location"): list(locations or []),
            ("wod-london", "lore"): list(lore or []),
        },
    )
    continuity_src = StubContinuity(
        facts=list(facts or []),
        commitments=list(commitments or []),
    )
    image_src = StubImages(images=list(images or []))
    pc_src = StubPCs(names=dict(pcs or {}))
    return DataSources(
        scenes=scenes_src,
        characters=chars_src,
        setting=setting_src,
        continuity=continuity_src,
        images=image_src,
        pcs=pc_src,
        data_root=data_root,
    )
