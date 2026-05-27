"""Data-source protocols the Export module reads from.

Concrete services (Scene Manager, Characters, World, Continuity, ImageGen)
implement supersets of these; tests can also supply lightweight in-memory
stubs without pulling in the full SQLite stack. Keeping this surface
narrow means an adapter never reaches into the State Store directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from grimoire.scenes.types import Post, Scene
from grimoire.types.characters import Character
from grimoire.types.common import (
    CampaignId,
    CharacterRef,
    LocationRef,
)
from grimoire.types.composition import (
    Greeting,
    LibraryEntity,
    WorldMeta,
)
from grimoire.types.continuity import Commitment, Fact
from grimoire.types.imagegen import ImageMetadata


@runtime_checkable
class SceneSource(Protocol):
    async def list_scenes(self, campaign_id: CampaignId) -> list[Scene]: ...

    async def get_posts(self, scene_id: str) -> list[Post]: ...


@runtime_checkable
class CharacterSource(Protocol):
    async def list_for_campaign(self, campaign_id: CampaignId) -> list[Character]: ...


@runtime_checkable
class WorldSource(Protocol):
    async def get_composition_worlds(self, campaign_id: CampaignId) -> list[WorldMeta]: ...

    async def list_in_world(self, world_id: str, kind: str) -> list[LibraryEntity]: ...

    async def list_greetings(self, world_id: str) -> list[Greeting]: ...

    async def get_location(
        self,
        location_ref: LocationRef,
        campaign_id: CampaignId,
    ) -> LibraryEntity | None: ...


@runtime_checkable
class ContinuitySource(Protocol):
    async def list_facts(self, campaign_id: CampaignId) -> list[Fact]: ...

    async def list_commitments(self, campaign_id: CampaignId) -> list[Commitment]: ...


@runtime_checkable
class ImageSource(Protocol):
    async def list_images(
        self, campaign_id: CampaignId, scene_id: str | None = None
    ) -> list[ImageMetadata]: ...

    async def image_bytes(self, image_id: str) -> tuple[bytes, str] | None:
        """Return ``(payload, media_type)`` or ``None`` when the file is missing."""
        ...


@runtime_checkable
class CoverGenerator(Protocol):
    """Optional cover-image renderer for EPUB auto-cover (§6).

    Implementations should return ``None`` when the backend is unavailable
    or the prompt produced no usable image; callers then fall back to the
    plain title page.
    """

    async def generate_cover(self, campaign_id: CampaignId, prompt: str) -> bytes | None: ...


@runtime_checkable
class PCSource(Protocol):
    async def pc_names(self, campaign_id: CampaignId) -> dict[CharacterRef, str]: ...


# Lightweight default stubs ------------------------------------------------ #


class _NullCharacterSource:
    async def list_for_campaign(self, campaign_id: CampaignId) -> list[Character]:
        return []


class _NullWorldSource:
    async def get_composition_worlds(self, campaign_id: CampaignId) -> list[WorldMeta]:
        return []

    async def list_in_world(self, world_id: str, kind: str) -> list[LibraryEntity]:
        return []

    async def list_greetings(self, world_id: str) -> list[Greeting]:
        return []

    async def get_location(
        self, location_ref: LocationRef, campaign_id: CampaignId
    ) -> LibraryEntity | None:
        return None


class _NullContinuitySource:
    async def list_facts(self, campaign_id: CampaignId) -> list[Fact]:
        return []

    async def list_commitments(self, campaign_id: CampaignId) -> list[Commitment]:
        return []


class _NullImageSource:
    async def list_images(
        self, campaign_id: CampaignId, scene_id: str | None = None
    ) -> list[ImageMetadata]:
        return []

    async def image_bytes(self, image_id: str) -> tuple[bytes, str] | None:
        return None


class _NullPCSource:
    async def pc_names(self, campaign_id: CampaignId) -> dict[CharacterRef, str]:
        return {}


# Convenience for callers that only have a Scene source handy: returns a
# data-sources bundle with no-op stubs filled in for the rest.
class DataSources:
    """Bundle of injectable sources, with default no-op stubs."""

    def __init__(
        self,
        scenes: SceneSource,
        *,
        characters: CharacterSource | None = None,
        world: WorldSource | None = None,
        continuity: ContinuitySource | None = None,
        images: ImageSource | None = None,
        pcs: PCSource | None = None,
        cover_generator: CoverGenerator | None = None,
        data_root: Path | None = None,
    ) -> None:
        self.scenes = scenes
        self.characters = characters or _NullCharacterSource()
        self.world = world or _NullWorldSource()
        self.continuity = continuity or _NullContinuitySource()
        self.images = images or _NullImageSource()
        self.pcs = pcs or _NullPCSource()
        self.cover_generator = cover_generator
        self.data_root = data_root


__all__ = [
    "CharacterSource",
    "ContinuitySource",
    "CoverGenerator",
    "DataSources",
    "ImageSource",
    "PCSource",
    "SceneSource",
    "WorldSource",
]
