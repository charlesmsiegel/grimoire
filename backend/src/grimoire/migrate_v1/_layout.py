"""Where a path sits in a V0.2.1 library, and what the scanner does with it.

Pure: a path's parts in, a `Layout` out. The walker asks before it descends or
reads, so an excluded subtree is never opened and every other entry gets
exactly one disposition. Interpreters later read every ``interpreted`` entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DIRECTORY = "directory"
INTERPRETED = "interpreted"
HASHED = "hashed"
UNSUPPORTED = "recognized_unsupported"
EXCLUDED = "excluded"
IGNORED = "ignored"
UNKNOWN = "unknown"
REFUSED = "refused"
UNREADABLE = "unreadable"

#: Never read: credentials, accounting, logs, derived caches, backups, staging.
EXCLUDED_ROOT = frozenset({"llm_connections", "usage", "logs", ".cache", "backups",
                           ".world-staging", ".module-staging"})
#: Settings, presets and user plugins (``calendars/`` holds Python: listed, never run).
UNSUPPORTED_ROOT_DIRS = frozenset({"sampler_presets", "response_presets", "styles", "climates",
                                   "modules", "calendars"})
UNSUPPORTED_ROOT_FILES = frozenset({"pricing.json", "chores.json", "regex.json", "prompt_layout.json",
                                    "inference-retired.json", ".char_macros_baked",
                                    ".synthetic-library", ".synth-library.json"})
OS_LITTER = frozenset({".ds_store", "thumbs.db", "desktop.ini"})
FLAT_KINDS = ("locations", "lore", "items", "groups", "creatures", "greetings")
ACTOR_BASES = ("characters", "pcs")
IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "webp"})
ASSET_SIDECARS = frozenset({"focus.json", "descriptions.json", "subjects.json"})
CHARACTER_SIDECARS = frozenset({"tagline.md", "voice_anchor.md"})
CAMPAIGN_LOCAL_ACTOR_FILES = frozenset({"dossier.md", "state.md", "voice_drift.md"})
WORLD_UNSUPPORTED_FILES = frozenset({"tags.md", "calendar.json", "plotmap.json"})
CAMPAIGN_LEDGERS = {"sync.md": "ledger_sync", "deleted.json": "ledger_deleted",
                    "detached.json": "ledger_detached", "appearances.json": "ledger_appearances"}
#: Campaign-local state the first importer does not carry (V1 overlay docstring).
CAMPAIGN_UNSUPPORTED_FILES = frozenset({
    "calendar.json", "climate.json", "changes.json", "journal.json", "relationship_history.json",
    "chronicle.json", "timeline.md", "sheet_baselines.json", "proposals.json", "plot.json",
    "commitments.json", "relationships.json", "knowledge.json", "facts.json", "events.json",
    "rolls.json", "responses.json", "notices.json", "pins.json", "sync_pins.json", "clock.json",
    "weather.json", "tracker.json", "scene_ideas.json", "continuity.json", "continuity_candidates.json",
    "commits.json", "provenance.json", "played.json", "attempts.json", "replay.json",
    "authors_notes.json", "quick_replies.json", "membership_review_tokens.json", "revision.txt",
    "activity.txt", "plotmap.json"})
CAMPAIGN_UNSUPPORTED_DIRS = frozenset({"response-prompts", "tracker", "rewrites", "prompts",
                                       "scene-history", "sheets"})
SCENE_SIDECARS = {".alts.json": "scene_alts", ".review.json": "scene_review",
                  ".steering.json": "scene_steering"}


@dataclass(frozen=True)
class Layout:
    disposition: str
    family: str
    coords: dict = field(default_factory=dict)


_DIR = Layout(DIRECTORY, "directory")
_UNKNOWN = Layout(UNKNOWN, "unknown")


def _unsupported(family: str, **coords) -> Layout:
    return Layout(UNSUPPORTED, family, coords)


def _interpreted(family: str, **coords) -> Layout:
    return Layout(INTERPRETED, family, coords)


def classify(parts: tuple[str, ...], is_dir: bool) -> Layout:
    """The layout of the entry at source-relative `parts`."""
    top = parts[0]
    if len(parts) == 1 and top in EXCLUDED_ROOT:
        return Layout(EXCLUDED, "excluded")
    if is_dir:
        return _DIR
    if parts[-1].casefold() in OS_LITTER:
        return Layout(IGNORED, "os_litter")
    if len(parts) == 1:
        if top == "config.md":
            return _interpreted("config")
        if top in UNSUPPORTED_ROOT_FILES:
            return _unsupported("settings")
        return _UNKNOWN
    if top in UNSUPPORTED_ROOT_DIRS:
        return _unsupported("settings")
    if top in ("worlds", "campaigns") and len(parts) >= 3:
        scope = "world" if top == "worlds" else "campaign"
        return _scoped(parts[2:], {"scope": scope, "scope_id": parts[1]})
    if top == "assets":
        return _image_store(parts[1:])
    return _UNKNOWN


def _scoped(rest: tuple[str, ...], scope: dict) -> Layout:
    head, name = rest[0], rest[-1]
    campaign = scope["scope"] == "campaign"
    if len(rest) == 1:
        if name == ("campaign.md" if campaign else "world.md"):
            return _interpreted(f"{scope['scope']}_meta", **scope)
        if campaign and name in CAMPAIGN_LEDGERS:
            return _interpreted(CAMPAIGN_LEDGERS[name], **scope)
        if campaign and name in CAMPAIGN_UNSUPPORTED_FILES:
            return _unsupported("campaign_state", **scope)
        if not campaign and name in WORLD_UNSUPPORTED_FILES:
            return _unsupported("world_state", **scope)
        return _UNKNOWN
    if campaign and head in CAMPAIGN_UNSUPPORTED_DIRS:
        return _unsupported("campaign_state", **scope)
    if not campaign and head == "sheets":
        return _unsupported("world_state", **scope)
    if campaign and head == "scenes":
        return _scene(rest, scope)
    if head == "assets":
        return _scope_assets(rest[1:], scope)
    if head in FLAT_KINDS:
        return _flat(rest, scope)
    if head in ACTOR_BASES:
        return _actor(rest, scope)
    return _UNKNOWN


def _scene(rest: tuple[str, ...], scope: dict) -> Layout:
    if len(rest) != 2:
        return _UNKNOWN
    name = rest[1]
    for suffix, family in SCENE_SIDECARS.items():
        if name.endswith(suffix) and len(name) > len(suffix):
            return _unsupported(family, **scope, sid=name[:-len(suffix)])
    if name.endswith(".md") and len(name) > 3:
        return _interpreted("scene", **scope, sid=name[:-3])
    return _UNKNOWN


def _flat(rest: tuple[str, ...], scope: dict) -> Layout:
    kind = rest[0]
    if len(rest) == 2:
        name = rest[1]
        if name.endswith(".md") and len(name) > 3:
            return _interpreted("flat_record", **scope, kind=kind, record=name[:-3])
        return _UNKNOWN
    record = rest[1]
    if rest[2] == "assets" and len(rest) >= 5:
        return _asset(rest[4:], {**scope, "base": kind, "record": record, "version": rest[3]})
    if len(rest) == 3 and rest[2] == "state.md":
        return _unsupported("record_state", **scope, kind=kind, record=record)
    return _UNKNOWN


def _actor(rest: tuple[str, ...], scope: dict) -> Layout:
    if len(rest) < 3:
        return _UNKNOWN
    base, actor, name = rest[0], rest[1], rest[2]
    coords = {**scope, "base": base, "actor": actor}
    if len(rest) == 3:
        if name == ("character.md" if base == "characters" else "pc.md"):
            return _interpreted("actor_meta", **coords)
        if base == "characters" and name.endswith(".json") and len(name) > 5:
            return _interpreted("card", **coords, version=name[:-5])
        if base == "pcs" and name.endswith(".md") and len(name) > 3 and name != "pc.md":
            return _interpreted("persona", **coords, version=name[:-3])
        if name in CHARACTER_SIDECARS:
            return _interpreted("actor_sidecar", **coords, sidecar=name)
        if name in CAMPAIGN_LOCAL_ACTOR_FILES:
            return _unsupported("actor_state", **coords)
        return _UNKNOWN
    if name == "assets" and len(rest) >= 5:
        return _asset(rest[4:], {**scope, "base": base, "record": actor, "version": rest[3]})
    if base == "pcs" and name in ("history", "play"):
        return _unsupported("actor_state", **coords)
    return _UNKNOWN


def _scope_assets(rest: tuple[str, ...], scope: dict) -> Layout:
    if rest[0] == "images" and len(rest) >= 2:
        return _asset(rest[1:], {**scope, "base": "library", "record": None, "version": None})
    if rest[0] == "image-collections" and len(rest) == 2 and scope["scope"] == "world":
        name = rest[1]
        if name.endswith(".json") and len(name) > 5:
            return _interpreted("collection", **scope, collection=name[:-5])
        return _UNKNOWN
    return _asset(rest, {**scope, "base": "cover", "record": None, "version": None})


def _asset(rest: tuple[str, ...], owner: dict) -> Layout:
    """Inside one asset directory: placements, legacy images, sidecars, expressions."""
    name = rest[-1]
    if len(rest) == 2 and rest[0] == "image-refs" and name.endswith(".json") and len(name) > 5:
        slot = name[:-5]
        if slot == ".promote":
            return _interpreted("promote_journal", **owner)
        return _interpreted("placement", **owner, area="record", name=slot)
    if len(rest) == 1:
        if name in ASSET_SIDECARS:
            return _interpreted("asset_sidecar", **owner, sidecar=name)
        stem, dot, ext = name.rpartition(".")
        if dot and stem and ext.lower() in IMAGE_EXTS:
            return Layout(HASHED, "legacy_image", {**owner, "name": stem, "ext": ext})
        return _UNKNOWN
    if rest[0] == "expressions" and owner.get("version") is not None:
        if rest[1:] == ("manifest.json",):
            return _interpreted("expression_manifest", **owner)
        if rest[1] == "pins":
            return _unsupported("expression_pins", **owner)
        if (len(rest) == 6 and rest[1] == "revisions" and rest[4] == "image-refs"
                and name.endswith(".json") and len(name) > 5):
            return _interpreted("placement", **owner, area="expression", revision=rest[2],
                                outfit=rest[3], name=name[:-5])
    return _UNKNOWN


def _image_store(rest: tuple[str, ...]) -> Layout:
    if len(rest) == 4 and rest[0] == "image-store":
        kind, shard, name = rest[1], rest[2], rest[3]
        if kind == "blobs":
            stem, dot, ext = name.rpartition(".")
            return Layout(HASHED, "blob", {"shard": shard, "sha": stem, "ext": ext if dot else ""})
        if kind == "objects" and name.endswith(".json") and len(name) > 5:
            return _interpreted("image_object", shard=shard, object=name[:-5])
    return _UNKNOWN
