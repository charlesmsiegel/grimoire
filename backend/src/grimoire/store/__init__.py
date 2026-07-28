"""Filesystem-as-database for grimoire: markdown files under ~/.grimoire/."""

from __future__ import annotations

from . import (
    absorb, appearances, assets, audit, campaigns, cards, changes, characters, checks, chronicle,
    chub, climates, context, dice, dossiers, entities, entity_schema, epub, export, fence, fetch, greetings, groupstate,
    image_subjects, length_drift, lengths, llm_connections, localize, locks, lorebook, migrations, module_edit, modules, overlay, pcs, playing,
    playstate, plot, proposals, relationships, response_presets, rolls, scene_ids, scene_refs, scenes, sheets,
    styles, suggest,
    sync, tags, taglines, thumbs, worlds,
)
from .appearances import AppearError
from .campaigns import CampaignNotFound
from .cards import CardParseError
from .characters import CharacterNotFound, VersionNotFound
from .chub import ChubFetchError, ChubParseError
from .dice import DiceError
from .rolls import RollNotFound
from .llm_connections import ConnectionNotFound
from .styles import BuiltInStyleImmutable, StyleNotFound
from .greetings import GreetingNotFound
from .lorebook import LorebookError
from .playing import PlayError
from .pcs import PCNotFound, PCVersionNotFound
from .tags import TagNotFound
from .config import DEFAULT_MODEL, DEFAULT_THEME, read_config, write_config
from .entities import EntityNotFound, UnknownKind
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import (
    data_dir_info, ensure_home, home, now_iso, set_data_dir, slugify, uniquify,
)
from .scenes import SceneNotFound
from .worlds import WorldNotFound

__all__ = [
    "absorb",
    "parse_frontmatter",
    "dump_frontmatter",
    "home",
    "ensure_home",
    "set_data_dir",
    "data_dir_info",
    "now_iso",
    "slugify",
    "uniquify",
    "read_config",
    "write_config",
    "DEFAULT_MODEL",
    "DEFAULT_THEME",
    "entities",
    "entity_schema",
    "epub",
    "export",
    "EntityNotFound",
    "UnknownKind",
    "worlds",
    "WorldNotFound",
    "campaigns",
    "CampaignNotFound",
    "changes",
    "chronicle",
    "assets",
    "fetch",
    "localize",
    "cards",
    "CardParseError",
    "characters",
    "CharacterNotFound",
    "VersionNotFound",
    "chub",
    "ChubParseError",
    "ChubFetchError",
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
    "groupstate",
    "image_subjects",
    "playing",
    "PlayError",
    "playstate",
    "plot",
    "relationships",
    "dice",
    "DiceError",
    "rolls",
    "RollNotFound",
    "llm_connections",
    "ConnectionNotFound",
    "styles",
    "StyleNotFound",
    "BuiltInStyleImmutable",
    "suggest",
    "lorebook",
    "LorebookError",
    "locks",
    "modules",
    "sheets",
    "checks",
    "fence",
    "proposals",
    "lengths",
    "length_drift",
    "response_presets",
]
