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

Every import below is a re-export, so none of them is *used* in this file --
which is why ``docs/health.html`` reports fourteen of them as
``unused_import``. They are not: this module is the facade, ``__all__`` is what
publishes them, and ``tests/test_store_api_baseline.py`` fails if any one
stops resolving. Deleting one to clear the finding would break the callers that
do ``from grimoire.store import x``, which is the whole reason that test exists.
"""

from __future__ import annotations

from . import (
    absorb,
    aging,
    alternates,
    appearances,
    assets,
    atomic,
    attempts,
    audit,
    backups,
    birthdays,
    briefing,
    campaign_climate,
    campaign_images,
    campaigns,
    cards,
    cascade,
    casefile,
    changes,
    characters,
    checks,
    chronicle,
    chub,
    climates,
    clock,
    commitments,
    commits,
    config,
    context,
    covers,
    dice,
    dossiers,
    embed_space,
    entities,
    entity_schema,
    epub,
    events,
    export,
    external,
    facts,
    fence,
    fetch,
    fieldtext,
    fork,
    greetings,
    groupstate,
    image_descriptions,
    image_drafts,
    image_subjects,
    journal,
    length_drift,
    lengths,
    llm_connections,
    localize,
    locks,
    lorebook,
    migrations,
    module_edit,
    modules,
    overlay,
    pcs,
    pins,
    playing,
    playstate,
    plot,
    pricing,
    prompt_log,
    proposals,
    provenance,
    relationships,
    replay,
    response_presets,
    retcon,
    rolling_summary,
    rolls,
    scenario,
    scene_break,
    scene_ideas,
    scene_ids,
    scene_refs,
    scenes,
    search,
    semsearch,
    sheets,
    styles,
    suggest,
    sync,
    taglines,
    tags,
    thumbs,
    timeline,
    tokens,
    turnstate,
    undo,
    usage,
    voice_anchors,
    voice_drift,
    world_bundle,
    worlds,
    ziputil,
)
from .appearances import AppearError
from .campaigns import CampaignNotFound
from .cards import CardParseError
from .characters import CharacterNotFound, VersionNotFound
from .chub import ChubFetchError, ChubParseError
from .config import DEFAULT_MODEL, DEFAULT_THEME, read_config, write_config
from .dice import DiceError
from .entities import EntityNotFound, UnknownKind
from .frontmatter import dump_frontmatter, parse_frontmatter
from .greetings import GreetingNotFound
from .llm_connections import ConnectionNotFound
from .lorebook import LorebookError

# `module_display` was never exported here: it was bound as an attribute of
# this package only as a side effect of the flat `modules.py` importing it.
# Folding it into `modules/display.py` removes that side effect, so the name
# is aliased explicitly. Deliberately absent from `__all__` -- it was not
# there before either, and the facade's public list is frozen.
from .modules import display as module_display
from .paths import (
    data_dir_info,
    ensure_home,
    home,
    now_iso,
    set_data_dir,
    slugify,
    uniquify,
)
from .pcs import PCNotFound, PCVersionNotFound
from .playing import PlayError
from .rolls import RollNotFound
from .scenes import SceneNotFound
from .styles import BuiltInStyleImmutable, StyleNotFound
from .tags import TagNotFound
from .world_bundle import BundleError
from .worlds import WorldNotFound

__all__ = [
    "absorb",
    "alternates",
    "attempts",
    "backups",
    "briefing",
    "cascade",
    "fork",
    "casefile",
    "parse_frontmatter",
    "dump_frontmatter",
    # The one text coercion every JSON-store projection runs on its way to
    # React; `routes.campaigns` needs it too, so the facade carries it.
    "fieldtext",
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
    "external",
    "EntityNotFound",
    "UnknownKind",
    "worlds",
    "WorldNotFound",
    "world_bundle",
    "BundleError",
    "ziputil",
    "campaigns",
    "campaign_climate",
    # The campaign's own image library (#376) -- a deliberate addition to the
    # facade, not a leak: `routes.campaigns` and `store.export` both reach it
    # through `store.campaign_images`, the way they reach `store.covers`.
    "campaign_images",
    "CampaignNotFound",
    "changes",
    "chronicle",
    # The campaign clock and the roster-birthdate reads it shares with
    # `suggest` (#100) -- a deliberate addition to the facade, not a leak.
    "clock",
    # Scheduled events (#101) and the aging computation over what a campaign
    # still owes (#103) -- both read by routes, both part of the same
    # question the clock answers, so both on the facade beside it.
    "events",
    "aging",
    "birthdays",
    "commitments",
    "commits",
    "facts",
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
    "timeline",
    "turnstate",
    "appearances",
    "AppearError",
    "tags",
    "TagNotFound",
    "pcs",
    "PCNotFound",
    "PCVersionNotFound",
    "context",
    "covers",
    "greetings",
    "GreetingNotFound",
    "groupstate",
    "image_descriptions",
    "image_drafts",
    "image_subjects",
    "playing",
    "PlayError",
    "pins",
    "playstate",
    "plot",
    "pricing",
    "prompt_log",
    "provenance",
    "replay",
    "retcon",
    "journal",
    "undo",
    "relationships",
    "dice",
    "DiceError",
    "rolling_summary",
    "scene_break",
    "rolls",
    "RollNotFound",
    "search",
    "semsearch",
    "embed_space",
    "llm_connections",
    "ConnectionNotFound",
    "styles",
    "StyleNotFound",
    "BuiltInStyleImmutable",
    "suggest",
    "scene_ideas",
    "lorebook",
    "LorebookError",
    "scenario",
    "locks",
    "modules",
    "sheets",
    "checks",
    "fence",
    "proposals",
    "lengths",
    "length_drift",
    "response_presets",
    "voice_anchors",
    "voice_drift",
    "usage",
]
