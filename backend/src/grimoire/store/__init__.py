"""Filesystem-as-database for grimoire: markdown files under ~/.grimoire/.

The imports below are ordered for readability, not correctness: the module
graph under ``store/`` is acyclic and every import is at module scope,
enforced by ``backend/tests/test_import_guard.py``, so nothing here depends
on a sibling having already run first. This file itself is exempt from that
guard's stricter rule on cross-package imports -- re-exporting names like
``CampaignNotFound`` below is exactly a facade's job -- but every other
module under ``store/`` is held to it: a cross-package import there must
bind a *submodule*, not a name, e.g. ``from ..campaigns import read`` and
then ``read.world_refs(...)`` at the call site, never ``from ..campaigns
import world_refs``. Binding a name off a package that may still be
initializing raises at import time, and binding a function by value from a
submodule silently defeats any test that tries to patch it -- both survive
the acyclic check untouched, which is why the import-form rule has to be
checked, and recorded, separately.
"""

from __future__ import annotations

from . import (
    absorb, appearances, assets, atomic, audit, campaign_climate, campaigns, cards, changes, characters, checks, chronicle, commits,
    chub, climates, config, context, dice, dossiers, entities, entity_schema, epub, export, fence, fetch, greetings, groupstate,
    image_subjects, length_drift, lengths, llm_connections, localize, locks, lorebook, migrations, module_edit, modules, overlay, pcs, playing,
    playstate, plot, proposals, relationships, response_presets, rolls, scene_ids, scene_refs, scenes, sheets,
    styles, suggest,
    sync, tags, taglines, thumbs, worlds,
)
# `module_display` was never exported here: it was bound as an attribute of
# this package only as a side effect of the flat `modules.py` importing it.
# Folding it into `modules/display.py` removes that side effect, so the name
# is aliased explicitly. Deliberately absent from `__all__` -- it was not
# there before either, and the facade's public list is frozen.
from .modules import display as module_display
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
    "config",
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
    "campaign_climate",
    "CampaignNotFound",
    "changes",
    "chronicle",
    "commits",
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
