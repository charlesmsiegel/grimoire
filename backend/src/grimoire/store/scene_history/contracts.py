"""Immutable projection values; no private tracker fields cross this boundary."""

from dataclasses import dataclass


@dataclass(frozen=True)
class VNCursor:
    scene_identity: str
    post_id: str
    beat_id: str | None = None


@dataclass(frozen=True)
class ActorAtPost:
    ref: str
    version_id: str
    visible_mood: str | None = None
    clothing: str | None = None
    expression_outfit: str | None = None
    expression_mood: str | None = None


@dataclass(frozen=True)
class HistoricalPresentation:
    cursor: VNCursor
    actors: tuple[ActorAtPost, ...]
    location_id: str | None = None
    speaker_ref: str | None = None
