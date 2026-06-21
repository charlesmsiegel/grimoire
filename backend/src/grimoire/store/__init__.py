"""Filesystem-as-database for grimoire: markdown files under ~/.grimoire/."""

from __future__ import annotations

from . import campaigns, entities, scenes, sync, worlds
from .campaigns import CampaignNotFound
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
    "sync",
    "scenes",
    "SceneNotFound",
]
