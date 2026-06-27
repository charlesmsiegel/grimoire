"""Filesystem-as-database for grimoire: markdown files under ~/.grimoire/."""

from __future__ import annotations

from . import (
    appearances, assets, campaigns, cards, characters, context, entities,
    fetch, greetings, localize, lorebook, pcs, playing, scenes, sync, tags,
    worlds,
)
from .appearances import AppearError
from .campaigns import CampaignNotFound
from .cards import CardParseError
from .characters import CharacterNotFound, VersionNotFound
from .greetings import GreetingNotFound
from .lorebook import LorebookError
from .playing import PlayError
from .pcs import PCNotFound, PCVersionNotFound
from .tags import TagNotFound
from .config import DEFAULT_MODEL, DEFAULT_THEME, read_config, write_config
from .entities import EntityNotFound, UnknownKind
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import ensure_home, home, now_iso, slugify, uniquify
from .scenes import SceneNotFound
from .worlds import WorldNotFound

__all__ = [
    "parse_frontmatter",
    "dump_frontmatter",
    "home",
    "ensure_home",
    "now_iso",
    "slugify",
    "uniquify",
    "read_config",
    "write_config",
    "DEFAULT_MODEL",
    "DEFAULT_THEME",
    "entities",
    "EntityNotFound",
    "UnknownKind",
    "worlds",
    "WorldNotFound",
    "campaigns",
    "CampaignNotFound",
    "assets",
    "fetch",
    "localize",
    "cards",
    "CardParseError",
    "characters",
    "CharacterNotFound",
    "VersionNotFound",
    "sync",
    "scenes",
    "SceneNotFound",
    "appearances",
    "AppearError",
    "tags",
    "TagNotFound",
    "pcs",
    "PCNotFound",
    "PCVersionNotFound",
    "context",
    "greetings",
    "GreetingNotFound",
    "playing",
    "PlayError",
    "lorebook",
    "LorebookError",
]
