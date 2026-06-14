"""Concrete Library service.

Wraps :class:`grimoire.state_store.StateStore` to provide the spec 18 surface.
The State Store owns SQLite mutations and file mediation; this service
translates between the file-shaped writes the store understands and the
typed values (``LibraryEntity``, ``WorldMeta``, ``Greeting``,
``ResolvedEntity``) the rest of the system consumes.
"""

from __future__ import annotations

from typing import Any

from grimoire.event_bus import Event, EventBus
from grimoire.files import slugify
from grimoire.library.config import LibraryConfig
from grimoire.library.errors import (
    LibraryConflictError,
    LibraryError,
    LibraryNotFoundError,
)
from grimoire.library.reclassify import (
    ReclassificationResult,
)
from grimoire.state_store import StateStore
from grimoire.state_store.errors import NotFoundError
from grimoire.state_store.indexers import make_library_id
from grimoire.state_store.paths import parse_library_id
from grimoire.types.common import EntityKind
from grimoire.types.composition import (
    CampaignRef,
    CharacterVariant,
    Composition,
    Greeting,
    LibraryEntity,
    ResolutionLayer,
    ResolutionSource,
    ResolvedEntity,
    UpgradePreview,
    UpgradeReport,
    WorldMeta,
)
from grimoire.types.world import LoreEntry
from grimoire.util import json_equal, maybe_json, parse_iso_datetime

# Entity kinds that live inside a world directory.
_World_ENTITY_KINDS: frozenset[str] = frozenset(
    {"character", "item", "location", "lore", "faction", "greeting", "monster"}
)

_DIR_TO_KIND: dict[str, str] = {
    "characters": "character",
    "items": "item",
    "locations": "location",
    "lore": "lore",
    "factions": "faction",
    "greetings": "greeting",
    "monsters": "monster",
}


def _normalize_kind(kind: EntityKind | str) -> str:
    if isinstance(kind, EntityKind):
        return kind.value
    if kind in _DIR_TO_KIND:
        return _DIR_TO_KIND[kind]
    return kind


def _include_to_kinds(include: list[str] | None) -> set[str] | None:
    """Translate a ``WorldRef.include`` list (directory names) into kinds.

    Returns ``None`` when the include is missing (``None``), meaning "include
    every kind" per spec 18. An empty list ``[]`` is preserved as an empty
    set, meaning "include nothing" — distinct from "all kinds", so a wizard
    that uncheck-all-kinds excludes the world rather than (silently)
    including everything.
    """
    if include is None:
        return None
    out: set[str] = set()
    for entry in include:
        out.add(_DIR_TO_KIND.get(entry, entry))
    return out


def _deep_merge_frontmatter(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Merge ``patch`` into ``base``, recursing into nested dicts.

    Used by :meth:`LibraryService.update_entity` so a partial patch like
    ``{"image": {"base_prompt": "..."}}`` updates only the named subkey of
    the existing ``image:`` block instead of clobbering the whole section.
    Non-dict values (including lists) replace wholesale, matching how spec
    14 §Backend contract describes patches: lists are atomic, scalars are
    atomic, only dicts merge.
    """
    out = dict(base)
    for key, value in patch.items():
        existing = out.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            out[key] = _deep_merge_frontmatter(existing, value)
        else:
            out[key] = value
    return out


def _entity_from_row(row: dict) -> LibraryEntity:
    """Project a ``library_index`` row dict into a :class:`LibraryEntity`."""
    frontmatter = row.get("frontmatter") or {}
    try:
        kind = EntityKind(row["kind"])
    except ValueError as exc:
        raise LibraryError(f"unknown library kind {row['kind']!r}") from exc
    return LibraryEntity(
        id=row["id"],
        world_id=row.get("world_id"),
        kind=kind,
        asset_id=row["asset_id"],
        name=row.get("name") or row["asset_id"],
        path=row.get("path") or "",
        frontmatter=frontmatter,
        body=row.get("body") or "",
        body_compressed=row.get("body_compressed"),
        tags=list(row.get("tags") or []),
        keywords=list(row.get("keywords") or []),
        file_mtime=parse_iso_datetime(row.get("file_mtime")),
        content_hash=row.get("content_hash") or "",
        indexed_at=parse_iso_datetime(row.get("indexed_at")),
        version=int(row.get("version") or 0),
    )


def _world_meta_from_row(row: dict) -> WorldMeta:
    fm = row.get("frontmatter") or {}
    return WorldMeta(
        id=fm.get("id") or row.get("asset_id") or "",
        name=fm.get("name") or row.get("name") or row.get("asset_id") or "",
        description=fm.get("description") or "",
        tags=[str(t) for t in (fm.get("tags") or row.get("tags") or [])],
        genre=fm.get("genre") or "",
        calendar=fm.get("calendar") or {},
        calendar_ids=list(fm.get("calendar_ids") or []),
        holiday_set_ids=list(fm.get("holiday_set_ids") or []),
        display_calendar_id=fm.get("display_calendar_id") or None,
        atmosphere=fm.get("atmosphere") or {},
        defaults=fm.get("defaults") or {},
        version=int(fm.get("version") or row.get("version") or 0),
        pc_role_tags=[str(t) for t in (fm.get("pc_role_tags") or [])],
    )


def _greeting_from_row(row: dict) -> Greeting:
    fm = row.get("frontmatter") or {}
    return Greeting(
        # The canonical id is the filename-stem asset_id — the value the
        # classifier keys on and the only one get_greeting can resolve. A
        # divergent frontmatter ``id`` is advisory and must not be reported
        # here, or list/get stops round-tripping (greeting handoff failure).
        id=row.get("asset_id") or fm.get("id") or "",
        world_id=row.get("world_id") or "",
        name=fm.get("name") or row.get("name") or row.get("asset_id") or "",
        starting_location=fm.get("starting_location"),
        starting_time=fm.get("starting_time"),
        present_characters=list(fm.get("present_characters") or []),
        pov_character=fm.get("pov_character"),
        mood=fm.get("mood") or "",
        body=row.get("body") or "",
        tags=[str(t) for t in (fm.get("tags") or row.get("tags") or [])],
        role_tags=[str(t) for t in (fm.get("role_tags") or [])],
    )


class LibraryService:
    """Concrete Library implementing the spec 18 surface.

    Construct with an initialized :class:`StateStore`. The watcher (task #9)
    is responsible for keeping the index in sync with the filesystem; this
    service only does file-mediated writes through the store.
    """

    def __init__(
        self,
        store: StateStore,
        config: LibraryConfig | None = None,
        *,
        event_bus: EventBus | None = None,
    ) -> None:
        self.store = store
        self.config = config or LibraryConfig()
        self._event_bus = event_bus

        from .composition import CompositionManager
        from .scanner import LibraryScanner
        from .validator import LibraryValidator

        self._composition = CompositionManager(
            store=store, config=self.config, event_bus=event_bus, service=self
        )
        self._scanner = LibraryScanner(store=store, service=self)
        self._validator = LibraryValidator(
            store=store, config=self.config, event_bus=event_bus, service=self
        )

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Best-effort event emission; no-op when no bus is wired."""
        if self._event_bus is None:
            return
        await self._event_bus.emit(Event(type=event_type, payload=payload))

    @property
    def default_track_latest(self) -> bool:
        """Spec 18 ``library.version_pinning.default`` projected to a bool.

        API endpoints / wizards that create a new ``WorldRef`` without an
        explicit ``track_latest`` should consult this to honor user-configured
        defaults rather than hard-coding the pydantic field default (``False``).
        """
        return self.config.default_track_latest

    # ------------------------------------------------------------------ #
    # Discovery / listing
    # ------------------------------------------------------------------ #

    async def list_worlds(self) -> list[WorldMeta]:
        rows = await self.store.list_library_by_kind("world")
        return [_world_meta_from_row(row) for row in rows]

    async def get_world(self, world_id: str) -> WorldMeta:
        library_id = make_library_id(world_id, "world", world_id)
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(f"world {world_id!r} not found")
        return _world_meta_from_row(row)

    async def list_in_world(self, world_id: str, kind: EntityKind | str) -> list[LibraryEntity]:
        normalized = _normalize_kind(kind)
        rows = await self.store.list_library_in_world(world_id, normalized)
        return [_entity_from_row(row) for row in rows]

    async def get_entity(
        self, world_id: str, kind: EntityKind | str, entity_id: str
    ) -> LibraryEntity:
        normalized = _normalize_kind(kind)
        library_id = make_library_id(world_id, normalized, entity_id)
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(f"entity {kind}/{entity_id} not found in world {world_id!r}")
        return _entity_from_row(row)

    # ------------------------------------------------------------------ #
    # Top-level assets
    # ------------------------------------------------------------------ #

    async def list_style_guides(self) -> list[LibraryEntity]:
        rows = await self.store.list_library_by_kind("style_guide")
        return [_entity_from_row(row) for row in rows]

    async def list_image_presets(self) -> list[LibraryEntity]:
        rows = await self.store.list_library_by_kind("image_preset")
        return [_entity_from_row(row) for row in rows]

    async def get_style_guide(self, id: str) -> LibraryEntity:
        library_id = f"style-guides/{id}"
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(f"style guide {id!r} not found")
        return _entity_from_row(row)

    async def create_style_guide(
        self,
        id: str,
        *,
        name: str,
        description: str = "",
        tags: list[str] | None = None,
        pacing: list[str] | None = None,
        voice: list[str] | None = None,
        themes: list[str] | None = None,
        avoid: list[str] | None = None,
        source: str = "user",
    ) -> LibraryEntity:
        library_id = f"style-guides/{id}"
        existing = await self.store.get_library_entity(library_id)
        if existing is not None:
            raise LibraryConflictError(f"style guide {id!r} already exists")
        frontmatter: dict[str, Any] = {"id": id, "name": name or id}
        if description:
            frontmatter["description"] = description
        if tags:
            frontmatter["tags"] = list(tags)
        body = _render_style_guide_body(
            name=name or id,
            pacing=pacing or [],
            voice=voice or [],
            themes=themes or [],
            avoid=avoid or [],
        )
        await self.store.write_library_file(
            library_id=library_id,
            frontmatter=frontmatter,
            body=body,
            source=source,
        )
        return await self.get_style_guide(id)

    async def parse_style_guide(self, id: str) -> dict[str, Any]:
        """Return the editable shape of a style guide for the edit form."""
        entity = await self.get_style_guide(id)
        parsed = _parse_style_guide_body(entity.body)
        fm = entity.frontmatter or {}
        return {
            "id": entity.asset_id,
            "name": entity.name,
            "description": fm.get("description") or "",
            "tags": list(entity.tags),
            "intro": parsed["intro"],
            "pacing": parsed["pacing"],
            "voice": parsed["voice"],
            "themes": parsed["themes"],
            "avoid": parsed["avoid"],
            "extra_sections": parsed["extra_sections"],
        }

    async def update_style_guide(
        self,
        id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        pacing: list[str] | None = None,
        voice: list[str] | None = None,
        themes: list[str] | None = None,
        avoid: list[str] | None = None,
        source: str = "user",
    ) -> LibraryEntity:
        existing = await self.get_style_guide(id)
        parsed = _parse_style_guide_body(existing.body)
        fm = dict(existing.frontmatter or {})
        new_name = name if name is not None else (fm.get("name") or existing.name or id)
        fm["id"] = id
        fm["name"] = new_name
        if description is not None:
            if description:
                fm["description"] = description
            else:
                fm.pop("description", None)
        if tags is not None:
            if tags:
                fm["tags"] = list(tags)
            else:
                fm.pop("tags", None)
        body = _render_style_guide_body(
            name=new_name,
            pacing=pacing if pacing is not None else parsed["pacing"],
            voice=voice if voice is not None else parsed["voice"],
            themes=themes if themes is not None else parsed["themes"],
            avoid=avoid if avoid is not None else parsed["avoid"],
            intro=parsed["intro"],
            extra_sections=parsed["extra_sections"],
        )
        await self.store.write_library_file(
            library_id=f"style-guides/{id}",
            frontmatter=fm,
            body=body,
            source=source,
        )
        return await self.get_style_guide(id)

    async def delete_style_guide(self, id: str, *, source: str = "user") -> None:
        library_id = f"style-guides/{id}"
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(f"style guide {id!r} not found")
        await self.store.delete_library_file(library_id=library_id, source=source)

    async def get_image_preset(self, id: str) -> LibraryEntity:
        library_id = f"image-presets/{id}"
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(f"image preset {id!r} not found")
        return _entity_from_row(row)

    async def create_image_preset(
        self,
        id: str,
        *,
        name: str,
        description: str = "",
        tags: list[str] | None = None,
        style_preamble: str = "",
        default_negative_prompt: str = "",
        default_params: dict[str, Any] | None = None,
        source: str = "user",
    ) -> LibraryEntity:
        library_id = f"image-presets/{id}"
        existing = await self.store.get_library_entity(library_id)
        if existing is not None:
            raise LibraryConflictError(f"image preset {id!r} already exists")
        frontmatter: dict[str, Any] = {"id": id, "name": name or id}
        if description:
            frontmatter["description"] = description
        if tags:
            frontmatter["tags"] = list(tags)
        if style_preamble:
            frontmatter["style_preamble"] = style_preamble
        if default_negative_prompt:
            frontmatter["default_negative_prompt"] = default_negative_prompt
        if default_params:
            frontmatter["default_params"] = dict(default_params)
        await self.store.write_library_file(
            library_id=library_id,
            frontmatter=frontmatter,
            body="",
            source=source,
        )
        return await self.get_image_preset(id)

    async def parse_image_preset(self, id: str) -> dict[str, Any]:
        """Return the editable shape of an image preset for the edit form."""
        entity = await self.get_image_preset(id)
        fm = entity.frontmatter or {}
        return {
            "id": entity.asset_id,
            "name": entity.name,
            "description": fm.get("description") or "",
            "tags": list(entity.tags),
            "style_preamble": fm.get("style_preamble") or "",
            "default_negative_prompt": fm.get("default_negative_prompt") or "",
            "default_params": dict(fm.get("default_params") or {}),
        }

    async def update_image_preset(
        self,
        id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        style_preamble: str | None = None,
        default_negative_prompt: str | None = None,
        default_params: dict[str, Any] | None = None,
        source: str = "user",
    ) -> LibraryEntity:
        existing = await self.get_image_preset(id)
        fm = dict(existing.frontmatter or {})
        fm["id"] = id
        if name is not None:
            fm["name"] = name or id
        if description is not None:
            if description:
                fm["description"] = description
            else:
                fm.pop("description", None)
        if tags is not None:
            if tags:
                fm["tags"] = list(tags)
            else:
                fm.pop("tags", None)
        if style_preamble is not None:
            if style_preamble:
                fm["style_preamble"] = style_preamble
            else:
                fm.pop("style_preamble", None)
        if default_negative_prompt is not None:
            if default_negative_prompt:
                fm["default_negative_prompt"] = default_negative_prompt
            else:
                fm.pop("default_negative_prompt", None)
        if default_params is not None:
            if default_params:
                fm["default_params"] = dict(default_params)
            else:
                fm.pop("default_params", None)
        await self.store.write_library_file(
            library_id=f"image-presets/{id}",
            frontmatter=fm,
            body=existing.body or "",
            source=source,
        )
        return await self.get_image_preset(id)

    async def delete_image_preset(self, id: str, *, source: str = "user") -> None:
        library_id = f"image-presets/{id}"
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(f"image preset {id!r} not found")
        await self.store.delete_library_file(library_id=library_id, source=source)

    # ------------------------------------------------------------------ #
    # Custom calendars + holiday sets
    #
    # Built-in calendars/holiday-sets are owned by the calendar service
    # (see grimoire.world.calendar_service) and never touch the
    # library_index. These methods only handle user-created custom
    # entries persisted as YAML files in data/library/calendars/ and
    # data/library/holiday-sets/.
    # ------------------------------------------------------------------ #

    async def list_custom_calendars(self) -> list[LibraryEntity]:
        rows = await self.store.list_library_by_kind("calendar")
        return [_entity_from_row(row) for row in rows]

    async def get_custom_calendar(self, id: str) -> LibraryEntity:
        library_id = f"calendars/{id}"
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(f"calendar {id!r} not found")
        return _entity_from_row(row)

    async def write_custom_calendar(
        self,
        id: str,
        *,
        frontmatter: dict[str, Any],
        source: str = "user",
    ) -> LibraryEntity:
        library_id = f"calendars/{id}"
        fm = dict(frontmatter or {})
        fm["id"] = id
        await self.store.write_library_file(
            library_id=library_id,
            frontmatter=fm,
            body="",
            source=source,
        )
        return await self.get_custom_calendar(id)

    async def delete_custom_calendar(self, id: str, *, source: str = "user") -> None:
        library_id = f"calendars/{id}"
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(f"calendar {id!r} not found")
        await self.store.delete_library_file(library_id=library_id, source=source)

    async def list_custom_holiday_sets(self) -> list[LibraryEntity]:
        rows = await self.store.list_library_by_kind("holiday_set")
        return [_entity_from_row(row) for row in rows]

    async def get_custom_holiday_set(self, id: str) -> LibraryEntity:
        library_id = f"holiday-sets/{id}"
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(f"holiday set {id!r} not found")
        return _entity_from_row(row)

    async def write_custom_holiday_set(
        self,
        id: str,
        *,
        frontmatter: dict[str, Any],
        source: str = "user",
    ) -> LibraryEntity:
        library_id = f"holiday-sets/{id}"
        fm = dict(frontmatter or {})
        fm["id"] = id
        await self.store.write_library_file(
            library_id=library_id,
            frontmatter=fm,
            body="",
            source=source,
        )
        return await self.get_custom_holiday_set(id)

    async def delete_custom_holiday_set(self, id: str, *, source: str = "user") -> None:
        library_id = f"holiday-sets/{id}"
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(f"holiday set {id!r} not found")
        await self.store.delete_library_file(library_id=library_id, source=source)

    # ------------------------------------------------------------------ #
    # Greetings
    # ------------------------------------------------------------------ #

    async def list_greetings(self, world_id: str) -> list[Greeting]:
        rows = await self.store.list_library_in_world(world_id, "greeting")
        return [_greeting_from_row(row) for row in rows]

    async def get_greeting(self, world_id: str, id: str) -> Greeting:
        library_id = make_library_id(world_id, "greeting", id)
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(f"greeting {id!r} not found in world {world_id!r}")
        return _greeting_from_row(row)

    # ------------------------------------------------------------------ #
    # Character variants (in-world diff overlays on a base character)
    # ------------------------------------------------------------------ #

    async def list_character_variants(
        self, world_id: str, character_id: str
    ) -> list[CharacterVariant]:
        await self.get_entity(world_id, "character", character_id)
        rows = await self.store.list_character_variants(world_id, character_id)
        return [CharacterVariant.model_validate(row) for row in rows]

    async def get_character_variant(
        self, world_id: str, character_id: str, variant_id: str
    ) -> CharacterVariant:
        row = await self.store.get_character_variant(world_id, character_id, variant_id)
        if row is None:
            raise LibraryNotFoundError(
                f"variant {variant_id!r} of character {character_id!r} "
                f"not found in world {world_id!r}"
            )
        return CharacterVariant.model_validate(row)

    async def upsert_character_variant(
        self,
        world_id: str,
        character_id: str,
        variant_id: str,
        *,
        label: str | None = None,
        frontmatter: dict | None = None,
        body: str = "",
        source: str = "user",
    ) -> CharacterVariant:
        """Create or replace a variant overlay of an existing base character.

        ``frontmatter`` holds only the fields that differ from the base; the
        reserved ``id`` key is dropped so a variant can never change the
        character's identity. ``label`` is stored as the ``label`` key.
        """
        await self.get_entity(world_id, "character", character_id)
        fm = dict(frontmatter or {})
        fm.pop("id", None)
        if label is not None:
            fm["label"] = label
        row = await self.store.write_character_variant(
            world_id=world_id,
            base_id=character_id,
            variant_id=variant_id,
            frontmatter=fm,
            body=body or "",
            source=source,
        )
        return CharacterVariant.model_validate(row)

    async def delete_character_variant(
        self,
        world_id: str,
        character_id: str,
        variant_id: str,
        *,
        source: str = "user",
    ) -> None:
        try:
            await self.store.delete_character_variant(
                world_id=world_id,
                base_id=character_id,
                variant_id=variant_id,
                source=source,
            )
        except NotFoundError as exc:
            raise LibraryNotFoundError(str(exc)) from exc

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #

    async def create_world(self, id: str, meta: dict, *, source: str = "user") -> WorldMeta:
        frontmatter = dict(meta or {})
        frontmatter.setdefault("id", id)
        frontmatter.setdefault("name", id)
        frontmatter.setdefault("version", int(frontmatter.get("version") or 1))
        library_id = make_library_id(id, "world", id)
        await self.store.write_library_file(
            library_id=library_id,
            frontmatter=frontmatter,
            body="",
            source=source,
        )
        return await self.get_world(id)

    async def create_entity(
        self,
        world_id: str,
        kind: EntityKind | str,
        entity_id: str,
        frontmatter: dict,
        body: str,
        *,
        source: str = "user",
    ) -> LibraryEntity:
        normalized = _normalize_kind(kind)
        if normalized not in _World_ENTITY_KINDS:
            raise LibraryError(
                f"create_entity does not handle kind {normalized!r}; "
                "use create_world / write top-level assets via store"
            )
        fm = dict(frontmatter or {})
        fm.setdefault("id", entity_id)
        fm.setdefault("name", fm.get("name") or entity_id)
        library_id = make_library_id(world_id, normalized, entity_id)
        await self.store.write_library_file(
            library_id=library_id,
            frontmatter=fm,
            body=body or "",
            source=source,
        )
        return await self.get_entity(world_id, normalized, entity_id)

    async def update_entity(
        self,
        world_id: str,
        kind: EntityKind | str,
        entity_id: str,
        frontmatter_patch: dict | None = None,
        body: str | None = None,
        *,
        source: str = "user",
    ) -> LibraryEntity:
        normalized = _normalize_kind(kind)
        library_id = make_library_id(world_id, normalized, entity_id)
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(
                f"cannot update missing entity {kind}/{entity_id} in {world_id!r}"
            )
        new_frontmatter = dict(row.get("frontmatter") or {})
        if frontmatter_patch:
            new_frontmatter = _deep_merge_frontmatter(new_frontmatter, frontmatter_patch)
        new_body = body if body is not None else (row.get("body") or "")
        await self.store.write_library_file(
            library_id=library_id,
            frontmatter=new_frontmatter,
            body=new_body,
            source=source,
        )
        return await self.get_entity(world_id, normalized, entity_id)

    async def delete_entity(
        self,
        world_id: str,
        kind: EntityKind | str,
        entity_id: str,
        *,
        source: str = "user",
    ) -> None:
        normalized = _normalize_kind(kind)
        library_id = make_library_id(world_id, normalized, entity_id)
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(
                f"cannot delete missing entity {kind}/{entity_id} in {world_id!r}"
            )
        await self.store.delete_library_file(library_id=library_id, source=source)

    # ------------------------------------------------------------------ #
    # Reclassification (delegated to validator)
    # ------------------------------------------------------------------ #

    async def preview_reclassification(
        self, world_id: str, source_id: str, *, target_kind: EntityKind | str
    ) -> dict[str, Any]:
        return await self._validator.preview_reclassification(
            world_id, source_id, target_kind=target_kind
        )

    async def reclassify_entity(
        self,
        world_id: str,
        source_id: str,
        *,
        target_kind: EntityKind | str,
        overrides: dict[str, Any] | None = None,
        actor: str = "user",
    ) -> ReclassificationResult:
        return await self._validator.reclassify_entity(
            world_id, source_id, target_kind=target_kind, overrides=overrides, actor=actor
        )

    async def _collision_suffix(self, world_id: str, kind: EntityKind, base_id: str) -> str:
        return await self._validator._collision_suffix(world_id, kind, base_id)

    async def list_reclassifications(self, world_id: str) -> list[dict[str, Any]]:
        return await self._validator.list_reclassifications(world_id)

    async def undo_reclassification(
        self, world_id: str, timestamp: str, *, actor: str = "user"
    ) -> dict[str, Any]:
        return await self._validator.undo_reclassification(world_id, timestamp, actor=actor)

    def _coerce_reclassify_target(self, target_kind: EntityKind | str) -> EntityKind:
        return self._validator._coerce_reclassify_target(target_kind)

    # ------------------------------------------------------------------ #
    # Promotion (delegated to composition)
    # ------------------------------------------------------------------ #

    async def promote_to_library(
        self,
        campaign_id: str,
        entity_kind: EntityKind | str,
        campaign_entity_id: str,
        target_world_id: str,
        *,
        source: str = "user",
    ) -> str:
        return await self._composition.promote_to_library(
            campaign_id, entity_kind, campaign_entity_id, target_world_id, source=source
        )

    async def _cleanup_promoted_emergent(self, **kwargs: Any) -> tuple[str, bool]:
        return await self._composition._cleanup_promoted_emergent(**kwargs)

    async def _rekey_embeddings_after_promotion(self, **kwargs: Any) -> None:
        return await self._composition._rekey_embeddings_after_promotion(**kwargs)

    async def demote(
        self,
        world_id: str,
        kind: EntityKind | str,
        entity_id: str,
        *,
        copy_down_to: list[str] | None = None,
        source: str = "user",
    ) -> list[CampaignRef]:
        return await self._composition.demote(
            world_id, kind, entity_id, copy_down_to=copy_down_to, source=source
        )

    async def preview_save_override_to_library(
        self, campaign_id: str, library_id: str
    ) -> dict[str, Any]:
        return await self._composition.preview_save_override_to_library(campaign_id, library_id)

    async def save_override_to_library(
        self, campaign_id: str, library_id: str, *, source: str = "user"
    ) -> dict[str, Any]:
        return await self._composition.save_override_to_library(
            campaign_id, library_id, source=source
        )

    # ------------------------------------------------------------------ #
    # Delegated composition methods
    # ------------------------------------------------------------------ #

    async def get_composition(self, campaign_id: str) -> Composition:
        return await self._composition.get_composition(campaign_id)

    async def set_composition(self, campaign_id: str, composition: Composition) -> None:
        return await self._composition.set_composition(campaign_id, composition)

    # ------------------------------------------------------------------ #
    # Delegated scanner methods
    # ------------------------------------------------------------------ #

    async def world_diff(
        self, world_id: str, from_version: int, to_version: int | None = None
    ) -> dict[str, Any]:
        return await self._scanner.world_diff(world_id, from_version, to_version)

    async def preview_upgrade_world_ref(self, campaign_id: str, world_id: str) -> UpgradePreview:
        return await self._scanner.preview_upgrade_world_ref(campaign_id, world_id)

    async def upgrade_world_ref(self, campaign_id: str, world_id: str) -> UpgradeReport:
        return await self._scanner.upgrade_world_ref(campaign_id, world_id)

    async def resolve(self, entity_id: str, campaign_id: str) -> ResolvedEntity:
        return await self._scanner.resolve(entity_id, campaign_id)

    # ------------------------------------------------------------------ #
    # Dependents + composition-aware listing (delegated)
    # ------------------------------------------------------------------ #

    async def dependents(
        self, world_id: str, kind: EntityKind | str, entity_id: str
    ) -> list[CampaignRef]:
        return await self._composition.dependents(world_id, kind, entity_id)

    async def list_for_composition(
        self, campaign_id: str, kind: EntityKind | str
    ) -> list[LibraryEntity]:
        return await self._composition.list_for_composition(campaign_id, kind)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_row(row: Any) -> dict:
    """Make ``store.db.fetchall`` rows look like ``store.get_library_entity`` dicts.

    ``StateStore.get_library_entity`` returns a fully-decoded dict (lists, JSON
    parsed); ``db.fetchall`` returns ``aiosqlite.Row`` with raw text. This
    helper normalizes so the projection helpers can handle either.
    """
    if isinstance(row, dict):
        return row
    raw = dict(row)
    raw["frontmatter"] = maybe_json(raw.get("frontmatter"))
    raw["tags"] = maybe_json(raw.get("tags")) or []
    raw["keywords"] = maybe_json(raw.get("keywords")) or []
    return raw


def _render_style_guide_body(
    *,
    name: str,
    pacing: list[str],
    voice: list[str],
    themes: list[str],
    avoid: list[str],
    intro: str = "",
    extra_sections: list[tuple[str, str]] | None = None,
) -> str:
    parts: list[str] = [f"# {name}"]
    intro_text = (intro or "").strip()
    if intro_text:
        parts.append(intro_text)
    for heading, items in (
        ("Pacing", pacing),
        ("Voice", voice),
        ("Themes", themes),
        ("Avoid", avoid),
    ):
        bullets = [item.strip() for item in (items or []) if item and item.strip()]
        if not bullets:
            continue
        section = f"## {heading}\n" + "\n".join(f"- {b}" for b in bullets)
        parts.append(section)
    for heading, raw in extra_sections or []:
        body_text = (raw or "").strip()
        if not body_text:
            continue
        parts.append(f"## {heading}\n{body_text}")
    return "\n\n".join(parts) + "\n"


_KNOWN_SECTIONS: tuple[str, ...] = ("Pacing", "Voice", "Themes", "Avoid")


def _parse_style_guide_body(body: str) -> dict[str, Any]:
    """Split a style-guide markdown body into the structured shape the form edits.

    Returns ``{intro, pacing, voice, themes, avoid, extra_sections}`` where the
    four named sections are bullet-item lists and ``extra_sections`` preserves
    any other ``## Heading`` blocks verbatim so a round-trip edit doesn't
    silently drop hand-authored prose.
    """
    lines = (body or "").splitlines()
    cursor = 0
    # Skip a leading H1; we re-emit it from the entity name.
    while cursor < len(lines) and not lines[cursor].strip():
        cursor += 1
    if cursor < len(lines) and lines[cursor].lstrip().startswith("# "):
        cursor += 1

    intro_lines: list[str] = []
    while cursor < len(lines) and not lines[cursor].lstrip().startswith("## "):
        intro_lines.append(lines[cursor])
        cursor += 1
    intro = "\n".join(intro_lines).strip()

    sections: dict[str, list[str]] = {h: [] for h in _KNOWN_SECTIONS}
    extras: list[tuple[str, str]] = []
    while cursor < len(lines):
        heading_line = lines[cursor].lstrip()
        if not heading_line.startswith("## "):
            cursor += 1
            continue
        heading = heading_line[3:].strip()
        cursor += 1
        block: list[str] = []
        while cursor < len(lines) and not lines[cursor].lstrip().startswith("## "):
            block.append(lines[cursor])
            cursor += 1
        canonical = next((h for h in _KNOWN_SECTIONS if h.lower() == heading.lower()), None)
        if canonical is not None:
            bullets: list[str] = []
            for raw in block:
                stripped = raw.lstrip()
                if stripped.startswith("- ") or stripped.startswith("* "):
                    bullets.append(stripped[2:].strip())
            sections[canonical] = bullets
        else:
            extras.append((heading, "\n".join(block).strip()))
    return {
        "intro": intro,
        "pacing": sections["Pacing"],
        "voice": sections["Voice"],
        "themes": sections["Themes"],
        "avoid": sections["Avoid"],
        "extra_sections": extras,
    }


def _parse_resolve_ref(entity_id: str) -> tuple[str, str | None, str, bool]:
    """Return ``(kind, world_id, asset_id, emergent_only)`` for resolve()."""
    if not entity_id:
        raise LibraryError("empty entity_id")
    parts = entity_id.strip("/").split("/")
    # Campaign-local emergent shorthand: 'emergent/<kind>/<id>'.
    if parts[0] == "emergent" and len(parts) >= 3:
        kind = _normalize_kind(parts[1])
        return kind, None, parts[2], True
    ref = parse_library_id(entity_id)
    if ref.kind in {"world", "style_guide", "image_preset"}:
        raise LibraryError(
            f"resolve() does not handle top-level kind {ref.kind!r}; "
            "use get_world / get_style_guide / get_image_preset"
        )
    return ref.kind, ref.world_id, ref.asset_id, False


def _build_resolved(
    kind: str,
    world_id: str | None,
    asset_id: str,
    data: dict,
) -> ResolvedEntity:
    fm = data.get("frontmatter") or {}
    source = data.get("source") or "library-live"
    layer = _LAYER_BY_SOURCE.get(source, ResolutionLayer.LIBRARY_LIVE)
    chain = [
        ResolutionSource(
            layer=layer,
            scope=_SCOPE_BY_SOURCE.get(source, "library"),
            library_id=data.get("library_id"),
            world_id=world_id,
            version=data.get("version"),
            override_applied=bool(data.get("override")),
        )
    ]
    applied: list[str] = []
    if data.get("variant"):
        applied.append(f"variant:{data['variant']}")
    if data.get("variant_error"):
        # Variant state is broken (unreadable campaign.yaml, dangling or
        # unparseable overlay): resolution fell back to the base, and the
        # marker keeps "broken" distinguishable from "no selection".
        applied.append(f"variant-error:{data['variant_error']}")
    if data.get("override"):
        applied.append("override")
    return ResolvedEntity(
        kind=EntityKind(kind),
        asset_id=asset_id,
        world_id=world_id,
        name=fm.get("name") or fm.get("title") or asset_id,
        frontmatter=fm,
        body=data.get("body") or "",
        source_chain=chain,
        overrides_applied=applied,
    )


_LAYER_BY_SOURCE = {
    "campaign-emergent": ResolutionLayer.EMERGENT,
    "campaign-override": ResolutionLayer.OVERRIDE,
    "library-snapshot": ResolutionLayer.LIBRARY_SNAPSHOT,
    "library-live": ResolutionLayer.LIBRARY_LIVE,
    "library-fallback": ResolutionLayer.LIBRARY_LIVE,
}

_SCOPE_BY_SOURCE = {
    "campaign-emergent": "campaign-local",
    "campaign-override": "campaign-file",
    "library-snapshot": "library",
    "library-live": "library",
    "library-fallback": "library",
}


def _slugify(value: str) -> str:
    return slugify(value, fallback="entity")


def _lore_from_entity(entity: LibraryEntity) -> LoreEntry:
    """Construct a `LoreEntry` from a freshly-read library entity row."""
    fm = dict(entity.frontmatter or {})
    fm.setdefault("world_id", entity.world_id or "")
    fm.setdefault("id", entity.asset_id)
    fm.setdefault("title", fm.get("name") or entity.asset_id)
    fm["body"] = entity.body or fm.get("body", "")
    # Drop keys LoreEntry doesn't know about (the file may carry extra
    # frontmatter that survives at write time but isn't part of the model).
    known = set(LoreEntry.model_fields.keys())
    fm = {k: v for k, v in fm.items() if k in known}
    return LoreEntry.model_validate(fm)


def _frontmatter_diff(current: dict, baseline: dict) -> dict:
    """Return the frontmatter keys in ``current`` that differ from ``baseline``.

    Used after promotion when the emergent's frontmatter has drifted from
    what we wrote to the library: the differences become the campaign-side
    override patch. Keys present in ``current`` but missing in ``baseline``
    are included; keys removed from ``current`` are written as ``None`` so
    the merge in :meth:`StateStore.resolve_entity` can null them out.
    """
    out: dict[str, Any] = {}
    for key, value in current.items():
        if not json_equal(value, baseline.get(key)):
            out[key] = value
    for key in baseline:
        if key not in current:
            out[key] = None
    return out
