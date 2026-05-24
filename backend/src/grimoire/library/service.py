"""Concrete Library service.

Wraps :class:`grimoire.state_store.StateStore` to provide the spec 18 surface.
The State Store owns SQLite mutations and file mediation; this service
translates between the file-shaped writes the store understands and the
typed values (``LibraryEntity``, ``WorldMeta``, ``Greeting``,
``ResolvedEntity``) the rest of the system consumes.
"""

from __future__ import annotations

import json
import re as _re
from datetime import datetime
from typing import Any

from grimoire import events
from grimoire.event_bus import Event, EventBus
from grimoire.library.classify import suggest_kind
from grimoire.library.config import LibraryConfig
from grimoire.library.errors import (
    LibraryConflictError,
    LibraryError,
    LibraryNotFoundError,
    PromotionError,
    ReclassificationError,
)
from grimoire.library.reclassify import (
    ReclassificationResult,
    append_audit,
    apply_mapping,
    iter_audit,
    required_overrides_for,
)
from grimoire.state_store import StateStore
from grimoire.state_store.indexers import make_library_id
from grimoire.state_store.paths import parse_library_id
from grimoire.types.common import EntityKind
from grimoire.types.composition import (
    CampaignRef,
    Composition,
    Greeting,
    LibraryEntity,
    ResolutionLayer,
    ResolutionSource,
    ResolvedEntity,
    UpgradeEntityChange,
    UpgradePreview,
    UpgradeReport,
    WorldMeta,
    WorldRef,
)
from grimoire.types.world import LoreEntry

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


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


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
        file_mtime=_parse_iso(row.get("file_mtime")),
        content_hash=row.get("content_hash") or "",
        indexed_at=_parse_iso(row.get("indexed_at")),
        version=int(row.get("version") or 0),
    )


def _world_meta_from_row(row: dict) -> WorldMeta:
    fm = row.get("frontmatter") or {}
    return WorldMeta(
        id=fm.get("id") or row.get("asset_id") or "",
        name=fm.get("name") or row.get("name") or row.get("asset_id") or "",
        description=fm.get("description") or "",
        tags=list(fm.get("tags") or row.get("tags") or []),
        genre=fm.get("genre") or "",
        calendar=fm.get("calendar") or {},
        calendar_ids=list(fm.get("calendar_ids") or []),
        holiday_set_ids=list(fm.get("holiday_set_ids") or []),
        display_calendar_id=fm.get("display_calendar_id") or None,
        atmosphere=fm.get("atmosphere") or {},
        defaults=fm.get("defaults") or {},
        version=int(fm.get("version") or row.get("version") or 0),
    )


def _greeting_from_row(row: dict) -> Greeting:
    fm = row.get("frontmatter") or {}
    return Greeting(
        id=fm.get("id") or row.get("asset_id") or "",
        world_id=row.get("world_id") or "",
        name=fm.get("name") or row.get("name") or row.get("asset_id") or "",
        starting_location=fm.get("starting_location"),
        starting_time=fm.get("starting_time"),
        present_characters=list(fm.get("present_characters") or []),
        pov_character=fm.get("pov_character"),
        mood=fm.get("mood") or "",
        body=row.get("body") or "",
        tags=list(fm.get("tags") or row.get("tags") or []),
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
        rows = await self.store.db.fetchall(
            "SELECT * FROM library_index WHERE kind = 'world' ORDER BY name"
        )
        return [_world_meta_from_row(_normalize_row(row)) for row in rows]

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
        rows = await self.store.db.fetchall(
            "SELECT * FROM library_index WHERE kind = 'style_guide' ORDER BY name"
        )
        return [_entity_from_row(_normalize_row(row)) for row in rows]

    async def list_image_presets(self) -> list[LibraryEntity]:
        rows = await self.store.db.fetchall(
            "SELECT * FROM library_index WHERE kind = 'image_preset' ORDER BY name"
        )
        return [_entity_from_row(_normalize_row(row)) for row in rows]

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
        rows = await self.store.db.fetchall(
            "SELECT * FROM library_index WHERE kind = 'calendar' ORDER BY name"
        )
        return [_entity_from_row(_normalize_row(row)) for row in rows]

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
        rows = await self.store.db.fetchall(
            "SELECT * FROM library_index WHERE kind = 'holiday_set' ORDER BY name"
        )
        return [_entity_from_row(_normalize_row(row)) for row in rows]

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
    # Cross-world variants
    # ------------------------------------------------------------------ #

    async def variants_of(self, asset_id: str, kind: EntityKind | str) -> list[LibraryEntity]:
        normalized = _normalize_kind(kind)
        rows = await self.store.variants_of(asset_id, normalized)
        return [_entity_from_row(row) for row in rows]

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
    # Reclassification (spec §§1, 2, 6)
    # ------------------------------------------------------------------ #

    async def preview_reclassification(
        self,
        world_id: str,
        source_id: str,
        *,
        target_kind: EntityKind | str,
    ) -> dict[str, Any]:
        """Return the mapping result without writing — used by the Convert modal."""
        target = self._coerce_reclassify_target(target_kind)
        source_entity = await self.get_entity(world_id, "lore", source_id)
        lore = _lore_from_entity(source_entity)
        fm, body, kept, dropped, into_notes, warnings = apply_mapping(lore, target, None)
        suggestion = suggest_kind(lore, threshold=self.config.reclassification.suggestion_threshold)
        return {
            "source_id": source_id,
            "target_kind": target.value,
            "frontmatter": fm,
            "body": body,
            "kept": kept,
            "dropped": dropped,
            "into_notes": into_notes,
            "warnings": warnings,
            "required_overrides": required_overrides_for(target),
            "suggestion": {
                "kind": suggestion.kind.value,
                "confidence": suggestion.confidence,
                "reason": suggestion.reason,
            },
        }

    async def reclassify_entity(
        self,
        world_id: str,
        source_id: str,
        *,
        target_kind: EntityKind | str,
        overrides: dict[str, Any] | None = None,
        actor: str = "user",
    ) -> ReclassificationResult:
        """Convert a lore entry into a new entity of ``target_kind``.

        Order of operations: validate required overrides → read source →
        resolve target id (collision suffix) → create target → delete
        source → append audit → emit event. A failure at create surfaces
        as :class:`ReclassificationError` with the source intact; a
        failure at delete is a non-fatal warning on the result.
        """
        target = self._coerce_reclassify_target(target_kind)
        overrides = dict(overrides or {})

        missing = [
            key
            for key in required_overrides_for(target)
            if key not in overrides or overrides[key] in (None, "")
        ]
        if missing:
            raise ReclassificationError(
                f"missing required override(s) for {target.value}: {missing!r}"
            )

        source_entity = await self.get_entity(world_id, "lore", source_id)
        lore = _lore_from_entity(source_entity)
        fm, body, kept, dropped, into_notes, warnings = apply_mapping(lore, target, overrides)

        derived = _slugify(fm.get("name") or source_id)
        target_id = await self._collision_suffix(world_id, target, derived)
        fm["id"] = target_id

        try:
            await self.create_entity(
                world_id,
                target,
                target_id,
                fm,
                body,
                source=f"{actor}:reclassify",
            )
        except Exception as exc:
            raise ReclassificationError(
                f"failed to create {target.value}/{target_id}: {exc}"
            ) from exc

        try:
            await self.delete_entity(world_id, "lore", source_id, source=f"{actor}:reclassify")
        except Exception as exc:
            warnings.append(f"source not deleted: {exc}")

        # v1 always writes to <data_root>/library/imports/reclassifications.jsonl.
        # The config.reclassification.audit_log knob is reserved for a future
        # caller; not wired today.
        try:
            append_audit(
                self.store.data_root,
                world_id=world_id,
                source_id=source_id,
                source_snapshot={
                    "frontmatter": dict(source_entity.frontmatter or {}),
                    "body": source_entity.body or "",
                },
                target_id=target_id,
                target_kind=target,
                overrides=overrides,
                actor=actor,
            )
        except Exception as exc:
            warnings.append(f"audit log write failed: {exc}")

        await self._emit(
            events.LIBRARY_RECLASSIFY,
            {
                "world_id": world_id,
                "source_id": source_id,
                "target_id": target_id,
                "target_kind": target.value,
                "actor": actor,
                "warnings": warnings,
            },
        )

        return ReclassificationResult(
            source_id=source_id,
            target_id=target_id,
            target_kind=target,
            fields_kept=kept,
            fields_dropped=dropped,
            fields_into_notes=into_notes,
            warnings=warnings,
        )

    async def _collision_suffix(
        self,
        world_id: str,
        kind: EntityKind,
        base_id: str,
    ) -> str:
        """Return ``base_id`` unless it collides; otherwise ``base_id-2``, ``-3``, …, ``-99``."""
        candidate = base_id
        for n in range(1, 100):
            library_id = make_library_id(world_id, kind.value, candidate)
            existing = await self.store.get_library_entity(library_id)
            if existing is None:
                return candidate
            candidate = f"{base_id}-{n + 1}"
        raise ReclassificationError(
            f"too many id collisions for {kind.value}/{base_id} (>99); refusing to write"
        )

    async def list_reclassifications(self, world_id: str) -> list[dict[str, Any]]:
        """Return audit records for ``world_id`` in append order."""
        return list(iter_audit(self.store.data_root, world_id=world_id))

    async def undo_reclassification(
        self,
        world_id: str,
        timestamp: str,
        *,
        actor: str = "user",
    ) -> dict[str, Any]:
        """Reverse a reclassification by timestamp.

        Reads ``source_snapshot``, recreates the source under its original
        id (collision-suffixed if needed), deletes the target, and appends
        an inverse audit record stamped with ``_undo_of: <original_ts>`` in
        ``overrides``. Returns a summary dict with the restored and deleted
        ids plus warnings.
        """
        target_record: dict[str, Any] | None = None
        for record in iter_audit(self.store.data_root, world_id=world_id):
            if record.get("ts") == timestamp:
                target_record = record
                break
        if target_record is None:
            raise ReclassificationError(
                f"no audit record for world {world_id!r} at ts {timestamp!r}"
            )

        original_source_id = target_record["source_id"]
        snapshot = target_record["source_snapshot"]
        target_id = target_record["target_id"]
        target_kind = EntityKind(target_record["target_kind"])

        warnings: list[str] = []

        restored_id = await self._collision_suffix(world_id, EntityKind.LORE, original_source_id)
        snapshot_fm = dict(snapshot.get("frontmatter") or {})
        snapshot_fm["id"] = restored_id
        snapshot_body = snapshot.get("body") or ""
        await self.create_entity(
            world_id,
            "lore",
            restored_id,
            snapshot_fm,
            snapshot_body,
            source=f"{actor}:reclassify-undo",
        )

        try:
            await self.delete_entity(
                world_id,
                target_kind.value,
                target_id,
                source=f"{actor}:reclassify-undo",
            )
        except LibraryNotFoundError:
            warnings.append(f"target {target_kind.value}/{target_id} already deleted")
        except Exception as exc:
            warnings.append(f"target not deleted: {exc}")

        try:
            deps = await self.dependents(world_id, target_kind.value, target_id)
            if deps:
                warnings.append(
                    f"target was referenced by {len(deps)} campaign(s); those refs are now dangling"
                )
        except Exception:
            pass

        try:
            append_audit(
                self.store.data_root,
                world_id=world_id,
                source_id=target_id,
                source_snapshot={},
                target_id=restored_id,
                target_kind=EntityKind.LORE,
                overrides={"_undo_of": timestamp},
                actor=actor,
            )
        except Exception as exc:
            warnings.append(f"audit log write failed: {exc}")

        await self._emit(
            events.LIBRARY_RECLASSIFY_UNDO,
            {
                "world_id": world_id,
                "restored_source_id": restored_id,
                "deleted_target_id": target_id,
                "undo_of": timestamp,
                "warnings": warnings,
            },
        )

        return {
            "restored_source_id": restored_id,
            "deleted_target_id": target_id,
            "undo_of": timestamp,
            "warnings": warnings,
        }

    def _coerce_reclassify_target(self, target_kind: EntityKind | str) -> EntityKind:
        if isinstance(target_kind, EntityKind):
            value = target_kind
        else:
            try:
                value = EntityKind(_normalize_kind(target_kind))
            except ValueError as exc:
                raise ReclassificationError(f"unknown target_kind {target_kind!r}") from exc
        allowed = {
            EntityKind.CHARACTER,
            EntityKind.LOCATION,
            EntityKind.FACTION,
            EntityKind.ITEM,
            EntityKind.MONSTER,
        }
        if value not in allowed:
            raise ReclassificationError(
                "reclassify target must be character/location/faction/item/monster, "
                f"got {value.value!r}"
            )
        return value

    # ------------------------------------------------------------------ #
    # Promotion
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
        """Promote a campaign-local emergent entity into the library.

        Spec 18 §Promotion, with §4 of the remaining-design:

        1. Read the emergent.
        2. Write the library entity in ``target_world_id``.
        3. Re-read emergent after the library write. If it matches what we
           just wrote, delete the emergent file (and its index row) —
           subsequent reads will resolve through the library.
        4. If it diverges (a race or external edit between read and
           promote), write the frontmatter diff as a campaign override
           and delete the emergent. A body-only divergence is logged on
           the emitted event because overrides only carry frontmatter
           patches; the body change is dropped.
        5. Rekey embeddings from the emergent ref to the new library id.
        6. Emit ``library_entity_promoted`` for subscribers.

        Characters are routed through the Characters module (the World
        service rejects ``kind == "character"`` and the API maps that
        kind to its own promotion endpoint).
        """
        normalized = _normalize_kind(entity_kind)
        if normalized not in _World_ENTITY_KINDS:
            raise PromotionError(
                f"cannot promote kind {normalized!r}; only world-scoped kinds supported"
            )
        emergent = await self.store.get_emergent(campaign_id, normalized, campaign_entity_id)
        if emergent is None:
            raise PromotionError(
                f"no emergent {normalized}/{campaign_entity_id} in campaign {campaign_id!r}"
            )
        frontmatter = dict(emergent.get("frontmatter") or {})
        frontmatter.setdefault("id", campaign_entity_id)
        body = emergent.get("body") or ""
        library_id = make_library_id(target_world_id, normalized, campaign_entity_id)
        result = await self.store.write_library_file(
            library_id=library_id,
            frontmatter=frontmatter,
            body=body,
            source=f"{source}:promotion",
            campaign_id=campaign_id,
        )

        cleanup_action, body_diverged = await self._cleanup_promoted_emergent(
            campaign_id=campaign_id,
            kind=normalized,
            entity_id=campaign_entity_id,
            target_world_id=target_world_id,
            promoted_frontmatter=frontmatter,
            promoted_body=body,
            source=source,
        )
        await self._rekey_embeddings_after_promotion(
            campaign_id=campaign_id,
            kind=normalized,
            entity_id=campaign_entity_id,
            library_id=library_id,
        )
        await self._emit(
            events.LIBRARY_ENTITY_PROMOTED,
            {
                "campaign_id": campaign_id,
                "kind": normalized,
                "entity_id": campaign_entity_id,
                "target_world_id": target_world_id,
                "library_id": library_id,
                "library_path": str(result.path),
                "cleanup": cleanup_action,
                "body_diverged": body_diverged,
            },
        )
        return str(result.path)

    async def _cleanup_promoted_emergent(
        self,
        *,
        campaign_id: str,
        kind: str,
        entity_id: str,
        target_world_id: str,
        promoted_frontmatter: dict,
        promoted_body: str,
        source: str,
    ) -> tuple[str, bool]:
        """Finish step 5/6 of spec 18 §Promotion.

        Returns a ``(action, body_diverged)`` tuple where ``action`` is one
        of ``"deleted"``, ``"override+deleted"``, or ``"missing"``.
        """
        current = await self.store.get_emergent(campaign_id, kind, entity_id)
        if current is None:
            return ("missing", False)

        current_fm = current.get("frontmatter") or {}
        current_body = current.get("body") or ""
        # Normalize 'id' since write_library_file may have stamped one in
        # promoted_frontmatter; the on-disk emergent's 'id' is whatever the
        # campaign chose to write.
        fm_match = _json_equal(current_fm, promoted_frontmatter)
        body_match = current_body == promoted_body

        if fm_match and body_match:
            await self.store.delete_emergent(
                campaign_id=campaign_id,
                kind=kind,
                entity_id=entity_id,
                source=f"{source}:promotion-cleanup",
            )
            return ("deleted", False)

        # Mutations after the in-memory read — keep the divergence as a
        # campaign-local override on the freshly-promoted library entity.
        if not fm_match:
            diff = _frontmatter_diff(current_fm, promoted_frontmatter)
            if diff:
                library_id = make_library_id(target_world_id, kind, entity_id)
                await self.store.write_override(
                    campaign_id=campaign_id,
                    library_id=library_id,
                    patch=diff,
                    source=f"{source}:promotion-override",
                )
        await self.store.delete_emergent(
            campaign_id=campaign_id,
            kind=kind,
            entity_id=entity_id,
            source=f"{source}:promotion-cleanup",
        )
        return ("override+deleted", not body_match)

    async def _rekey_embeddings_after_promotion(
        self,
        *,
        campaign_id: str,
        kind: str,
        entity_id: str,
        library_id: str,
    ) -> None:
        """Repoint vector rows from the emergent ref to the new library id.

        Spec 18 §Promotion step 6. The watcher will eventually re-embed
        the new library file out-of-band, but the existing campaign-scoped
        embedding rows would double-count retrieval results until then —
        prefer to delete them now and let the embedder repopulate cleanly
        under the library ref.
        """
        emergent_ref = f"campaigns/{campaign_id}/emergent/{kind}/{entity_id}"
        try:
            await self.store.delete_embeddings(emergent_ref)
        except Exception:
            # Best-effort cleanup; embeddings are recomputed on next index.
            return

    # ------------------------------------------------------------------ #
    # Demotion (reverse promotion) — §6
    # ------------------------------------------------------------------ #

    async def demote(
        self,
        world_id: str,
        kind: EntityKind | str,
        entity_id: str,
        *,
        copy_down_to: list[str] | None = None,
        source: str = "user",
    ) -> list[CampaignRef]:
        """Reverse promotion: remove an entity from the library.

        Spec 18 §Promotion (reverse): dependent campaigns get a
        dangling-ref warning and an option to copy-down to campaign-local
        emergent first.

        Order of operations:

        1. Look up dependents up front so the caller sees who will be
           affected even if the file delete fails.
        2. If ``copy_down_to`` is supplied, for each campaign id in the
           list, materialize the current library entity as an emergent
           file. The copy uses the live library row so what the campaign
           keeps matches what it just lost.
        3. Delete the library file + index row.
        4. Emit ``library_entity_demoted`` with the dependent list and
           the copy-down summary.

        Returns the dependents list so callers can render the warning.
        """
        normalized = _normalize_kind(kind)
        if normalized not in _World_ENTITY_KINDS:
            raise LibraryError(
                f"cannot demote kind {normalized!r}; only world-scoped kinds supported"
            )
        library_id = make_library_id(world_id, normalized, entity_id)
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise LibraryNotFoundError(
                f"cannot demote missing entity {kind}/{entity_id} in {world_id!r}"
            )

        dependents = await self.dependents(world_id, normalized, entity_id)
        copy_down_to = copy_down_to or []
        copied: list[str] = []
        for campaign_id in copy_down_to:
            await self.store.write_emergent(
                campaign_id=campaign_id,
                kind=normalized,
                entity_id=entity_id,
                frontmatter=row.get("frontmatter") or {},
                body=row.get("body") or "",
                source=f"{source}:demote-copy-down",
            )
            copied.append(campaign_id)

        await self.store.delete_library_file(library_id=library_id, source=source)

        await self._emit(
            events.LIBRARY_ENTITY_DEMOTED,
            {
                "world_id": world_id,
                "kind": normalized,
                "entity_id": entity_id,
                "library_id": library_id,
                "dependents": [d.id for d in dependents],
                "copied_down_to": copied,
            },
        )
        return dependents

    # ------------------------------------------------------------------ #
    # Save-back-to-library (override → library file) — §7
    # ------------------------------------------------------------------ #

    async def preview_save_override_to_library(
        self,
        campaign_id: str,
        library_id: str,
    ) -> dict[str, Any]:
        """Render the before/after of a save-back without committing.

        The frontend uses this to render an inline diff before the user
        confirms. Returns ``{"before": {...}, "after": {...}}`` where each
        side has ``frontmatter`` and ``body``. ``after`` is the merged
        cascade result (override + base) — what the library file will look
        like once the override is folded in.
        """
        ref = parse_library_id(library_id)
        if ref.world_id is None:
            raise LibraryError(f"library_id {library_id!r} is not world-scoped")
        before_row = await self.store.get_library_entity(library_id)
        if before_row is None:
            raise LibraryNotFoundError(f"library entity {library_id!r} does not exist")
        override = await self.store.get_override(campaign_id, library_id)
        if not override:
            raise LibraryError(f"no override on {library_id!r} for campaign {campaign_id!r}")
        merged_fm = dict(before_row.get("frontmatter") or {})
        merged_fm.update(override)
        return {
            "library_id": library_id,
            "before": {
                "frontmatter": before_row.get("frontmatter") or {},
                "body": before_row.get("body") or "",
                "version": int(before_row.get("version") or 0),
            },
            "after": {
                "frontmatter": merged_fm,
                "body": before_row.get("body") or "",
            },
        }

    async def save_override_to_library(
        self,
        campaign_id: str,
        library_id: str,
        *,
        source: str = "user",
    ) -> dict[str, Any]:
        """Fold a campaign override into the underlying library file.

        Spec 18 §Overrides: "A 'Save back to library' action propagates
        an override into the underlying library file (writes the file,
        increments version, clears the override)."

        Returns the preview shape (``before`` / ``after`` plus the new
        ``version``) so the caller can confirm what was written.
        """
        preview = await self.preview_save_override_to_library(campaign_id, library_id)
        result = await self.store.write_library_file(
            library_id=library_id,
            frontmatter=preview["after"]["frontmatter"],
            body=preview["after"]["body"],
            source=f"{source}:save-back-from-override",
            campaign_id=campaign_id,
        )
        await self.store.delete_override(
            campaign_id=campaign_id,
            library_id=library_id,
            source=f"{source}:save-back-cleanup",
        )
        await self._emit(
            events.LIBRARY_ENTITY_SAVE_BACK,
            {
                "campaign_id": campaign_id,
                "library_id": library_id,
                "from_version": preview["before"]["version"],
                "to_version": result.version,
            },
        )
        return {
            "library_id": library_id,
            "before": preview["before"],
            "after": {**preview["after"], "version": result.version},
        }

    # ------------------------------------------------------------------ #
    # Composition
    # ------------------------------------------------------------------ #

    async def get_composition(self, campaign_id: str) -> Composition:
        camp_row = await self.store.db.fetchone(
            "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
        )
        if camp_row is None:
            raise LibraryNotFoundError(f"campaign {campaign_id!r} not found")

        refs_raw = await self.store.list_world_refs(campaign_id)
        refs = [
            WorldRef(
                world_id=r["world_id"],
                priority=int(r["priority"]),
                include=list(r.get("include") or []),
                bound_at_version=int(r.get("bound_at_version") or 0),
                track_latest=bool(r.get("track_latest")),
            )
            for r in refs_raw
        ]
        config = _maybe_json(camp_row["config"]) or {}
        return Composition(
            worlds=refs,
            mechanics=camp_row["mechanics_module"],
            style_guide_id=camp_row["style_guide_id"],
            image_preset_id=camp_row["image_preset_id"],
            inline_style_guide=camp_row["inline_style_guide"],
            content_boundaries=camp_row["content_boundaries"],
            calendar_ids=list(config.get("calendar_ids") or []),
            holiday_set_ids=list(config.get("holiday_set_ids") or []),
            display_calendar_id=config.get("display_calendar_id"),
        )

    async def set_composition(self, campaign_id: str, composition: Composition) -> None:
        camp_row = await self.store.db.fetchone(
            "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
        )
        if camp_row is None:
            raise LibraryNotFoundError(f"campaign {campaign_id!r} not found")

        config = _maybe_json(camp_row["config"]) or {}
        # Calendar attachments piggyback on the existing config JSON column
        # to avoid a schema migration; built-in calendar/holiday-set ids
        # are stable, so this is a small, stable shape.
        if composition.calendar_ids or "calendar_ids" in config:
            config["calendar_ids"] = list(composition.calendar_ids)
        if composition.holiday_set_ids or "holiday_set_ids" in config:
            config["holiday_set_ids"] = list(composition.holiday_set_ids)
        if composition.display_calendar_id is not None or "display_calendar_id" in config:
            config["display_calendar_id"] = composition.display_calendar_id

        await self.store.upsert_campaign(
            campaign_id=campaign_id,
            name=camp_row["name"],
            description=camp_row["description"],
            mechanics_module=composition.mechanics,
            style_guide_id=composition.style_guide_id,
            image_preset_id=composition.image_preset_id,
            inline_style_guide=composition.inline_style_guide,
            content_boundaries=composition.content_boundaries,
            greeting_id=camp_row["greeting_id"],
            config=config if config else None,
        )

        existing = {r["world_id"] for r in await self.store.list_world_refs(campaign_id)}
        desired_ids: set[str] = set()
        for ref in composition.worlds:
            desired_ids.add(ref.world_id)
            await self.store.upsert_world_ref(
                campaign_id=campaign_id,
                world_id=ref.world_id,
                priority=ref.priority,
                include=list(ref.include or []),
                track_latest=ref.track_latest,
                bound_at_version=(ref.bound_at_version if ref.bound_at_version else None),
                snapshot_on_bind=self.config.version_pinning.snapshot_on_bind,
            )

        for old in existing - desired_ids:
            await self.store.db.execute(
                """
                DELETE FROM campaign_world_refs
                WHERE campaign_id = ? AND world_id = ?
                """,
                (campaign_id, old),
            )
            await self.store.db.execute(
                """
                DELETE FROM library_snapshots
                WHERE campaign_id = ? AND library_id IN (
                  SELECT id FROM library_index WHERE world_id = ?
                )
                """,
                (campaign_id, old),
            )

    async def world_diff(
        self,
        world_id: str,
        from_version: int,
        to_version: int | None = None,
    ) -> dict[str, Any]:
        """Synthesize a flat diff between two versions of a world.

        Compares the world's current ``library_index`` rows against
        ``from_version``. Entities whose ``version > from_version``
        appear as ``changed`` (with the current frontmatter+body as
        ``after``; ``before`` is None because we don't retain historical
        bodies). Entities whose ``version <= from_version`` are
        unchanged. ``added`` and ``removed`` are surfaced empty in v1
        and reserved for a future history table.

        ``to_version`` is accepted for symmetry with the spec but
        clamped to the world's current max version — we cannot
        reconstruct future or intermediate state without history.
        """
        # Ensure the world exists; raises if not.
        await self.get_world(world_id)
        max_row = await self.store.db.fetchone(
            "SELECT MAX(version) AS v FROM library_index WHERE world_id = ?",
            (world_id,),
        )
        current_max = int((max_row["v"] if max_row else 0) or 0)
        if to_version is None:
            to_version = current_max
        # Clamp; we cannot synthesize state past the latest indexed version.
        effective_to = min(to_version, current_max)

        rows = await self.store.db.fetchall(
            "SELECT id, kind, asset_id, name, frontmatter, body, version, world_id "
            "FROM library_index WHERE world_id = ? AND kind != 'world' "
            "ORDER BY kind, asset_id",
            (world_id,),
        )

        changed: list[dict[str, Any]] = []
        for raw in rows:
            row = _normalize_row(raw)
            version = int(row.get("version") or 0)
            if version <= from_version:
                continue
            path = f"{row.get('kind')}/{row.get('asset_id')}"
            after = {
                "name": row.get("name") or row.get("asset_id"),
                "frontmatter": row.get("frontmatter") or {},
                "body": row.get("body") or "",
                "version": version,
            }
            changed.append({"path": path, "before": None, "after": after})

        return {
            "world_id": world_id,
            "from_version": int(from_version),
            "to_version": int(effective_to),
            "added": [],
            "removed": [],
            "changed": changed,
        }

    async def preview_upgrade_world_ref(
        self,
        campaign_id: str,
        world_id: str,
    ) -> UpgradePreview:
        """Render the per-entity before/after of an upgrade without committing.

        Spec 18 §Version pinning: "Upgrade is a user action with a diff
        preview." The frontend uses this to render an inline diff per
        changed entity before calling :meth:`upgrade_world_ref`.

        For a pinned world ref we compare the campaign's snapshot rows
        (what it sees today) against the live ``library_index`` rows
        (what the upgrade would write). For a track-latest ref the
        snapshot table is empty by design, so the preview is empty —
        the campaign already sees live content.
        """
        camp_row = await self.store.db.fetchone(
            """
            SELECT bound_at_version, track_latest FROM campaign_world_refs
            WHERE campaign_id = ? AND world_id = ?
            """,
            (campaign_id, world_id),
        )
        if camp_row is None:
            raise LibraryNotFoundError(f"campaign {campaign_id!r} does not bind world {world_id!r}")
        from_version = int(camp_row["bound_at_version"] or 0)
        branch_id = f"{campaign_id}:main"

        live_rows = await self.store.list_library_in_world(world_id)
        live_by_id = {row["id"]: row for row in live_rows}

        snap_rows = await self.store.db.fetchall(
            """
            SELECT s.library_id AS library_id, s.version AS version,
                   s.frontmatter AS frontmatter, s.body AS body
            FROM library_snapshots s
            JOIN library_index i ON i.id = s.library_id
            WHERE s.campaign_id = ? AND s.branch_id = ? AND i.world_id = ?
            """,
            (campaign_id, branch_id, world_id),
        )
        snap_by_id = {row["library_id"]: row for row in snap_rows}

        max_row = await self.store.db.fetchone(
            "SELECT MAX(version) AS v FROM library_index WHERE world_id = ?",
            (world_id,),
        )
        to_version = int((max_row["v"] if max_row else 0) or 0)

        entries: list[UpgradeEntityChange] = []
        changed: list[str] = []
        added: list[str] = []
        removed: list[str] = []
        for lib_id in sorted(set(live_by_id) | set(snap_by_id)):
            live = live_by_id.get(lib_id)
            snap = snap_by_id.get(lib_id)
            before_version = int(snap["version"]) if snap else None
            after_version = int(live["version"]) if live else None
            if snap is None and live is not None:
                added.append(lib_id)
            elif live is None and snap is not None:
                removed.append(lib_id)
            elif snap is not None and live is not None and before_version != after_version:
                changed.append(lib_id)
            else:
                continue
            entries.append(
                UpgradeEntityChange(
                    library_id=lib_id,
                    before_version=before_version,
                    after_version=after_version,
                    before_frontmatter=_maybe_json(snap["frontmatter"]) if snap else None,
                    after_frontmatter=(live.get("frontmatter") if live else None),
                    before_body=(snap["body"] if snap and snap["body"] else None),
                    after_body=(live.get("body") if live else None),
                )
            )

        return UpgradePreview(
            campaign_id=campaign_id,
            world_id=world_id,
            from_version=from_version,
            to_version=to_version,
            changed_entities=changed,
            added_entities=added,
            removed_entities=removed,
            entries=entries,
        )

    async def upgrade_world_ref(self, campaign_id: str, world_id: str) -> UpgradeReport:
        before_max = await self.store.db.fetchone(
            "SELECT bound_at_version FROM campaign_world_refs "
            "WHERE campaign_id = ? AND world_id = ?",
            (campaign_id, world_id),
        )
        if before_max is None:
            raise LibraryNotFoundError(f"campaign {campaign_id!r} does not bind world {world_id!r}")
        from_version = int(before_max["bound_at_version"] or 0)

        report = await self.store.upgrade_world_ref(campaign_id=campaign_id, world_id=world_id)
        max_row = await self.store.db.fetchone(
            "SELECT MAX(version) AS v FROM library_index WHERE world_id = ?",
            (world_id,),
        )
        to_version = int((max_row["v"] if max_row else 0) or 0)
        return UpgradeReport(
            campaign_id=campaign_id,
            world_id=world_id,
            from_version=from_version,
            to_version=to_version,
            changed_entities=sorted(report.diff.keys()),
            diff=report.diff,
        )

    # ------------------------------------------------------------------ #
    # Resolution cascade
    # ------------------------------------------------------------------ #

    async def resolve(self, entity_id: str, campaign_id: str) -> ResolvedEntity:
        """Resolve an entity through the campaign cascade.

        ``entity_id`` is either a composite ``library_id``
        (``worlds/<world>/<kind>/<id>``) or a campaign-local
        ``emergent/<kind>/<id>`` reference. Walks emergent → override →
        snapshot (pinned) → live index → fail.
        """
        target_kind, world_id, asset_id, is_emergent_only = _parse_resolve_ref(entity_id)

        branch_id = f"{campaign_id}:main"

        if is_emergent_only:
            data = await self.store.resolve_entity(
                campaign_id=campaign_id,
                branch_id=branch_id,
                kind=target_kind,
                asset_id=asset_id,
                world_id=None,
            )
            if data is None:
                raise LibraryNotFoundError(
                    f"emergent {target_kind}/{asset_id} not found in campaign {campaign_id!r}"
                )
            return _build_resolved(target_kind, world_id, asset_id, data)

        # Worlds ref path: check emergent first (campaign-shadowed name),
        # then the store's override/snapshot/live cascade.
        emergent = await self.store.get_emergent(campaign_id, target_kind, asset_id)
        if emergent is not None:
            data = {
                "source": "campaign-emergent",
                "frontmatter": emergent.get("frontmatter") or {},
                "body": emergent.get("body") or "",
            }
            return _build_resolved(target_kind, world_id, asset_id, data)

        data = await self.store.resolve_entity(
            campaign_id=campaign_id,
            branch_id=branch_id,
            kind=target_kind,
            asset_id=asset_id,
            world_id=world_id,
        )
        if data is None:
            raise LibraryNotFoundError(f"cannot resolve {entity_id!r} for campaign {campaign_id!r}")
        return _build_resolved(target_kind, world_id, asset_id, data)

    # ------------------------------------------------------------------ #
    # Dependents
    # ------------------------------------------------------------------ #

    async def dependents(
        self, world_id: str, kind: EntityKind | str, entity_id: str
    ) -> list[CampaignRef]:
        """Return campaigns that reference ``world_id``.

        v1: a campaign is a dependent of any entity in a world it
        composes. Fine-grained per-entity dependency tracking (e.g. via
        override files) is a v2 refinement.
        """
        rows = await self.store.db.fetchall(
            """
            SELECT c.id AS id, c.name AS name
            FROM campaigns c
            JOIN campaign_world_refs r ON r.campaign_id = c.id
            WHERE r.world_id = ?
            ORDER BY c.name
            """,
            (world_id,),
        )
        return [CampaignRef(id=row["id"], name=row["name"]) for row in rows]

    # ------------------------------------------------------------------ #
    # Composition-aware listing (used by World/Context Builder)
    # ------------------------------------------------------------------ #

    async def list_for_composition(
        self,
        campaign_id: str,
        kind: EntityKind | str,
    ) -> list[LibraryEntity]:
        """Return entities of ``kind`` reachable through a campaign's composition.

        Walks world refs in priority order, respecting each ref's
        ``include`` filter. Higher-priority refs win when asset ids
        collide.
        """
        normalized = _normalize_kind(kind)
        refs = await self.store.list_world_refs(campaign_id)
        refs.sort(key=lambda r: int(r.get("priority") or 0))

        seen: dict[str, LibraryEntity] = {}
        for ref in refs:
            include_kinds = _include_to_kinds(ref.get("include"))
            if include_kinds is not None and normalized not in include_kinds:
                continue
            rows = await self.store.list_library_in_world(ref["world_id"], normalized)
            for row in rows:
                asset = row["asset_id"]
                if asset in seen:
                    continue
                seen[asset] = _entity_from_row(row)
        return list(seen.values())


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
    raw["frontmatter"] = _maybe_json(raw.get("frontmatter"))
    raw["tags"] = _maybe_json(raw.get("tags")) or []
    raw["keywords"] = _maybe_json(raw.get("keywords")) or []
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


def _maybe_json(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


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
    return ResolvedEntity(
        kind=EntityKind(kind),
        asset_id=asset_id,
        world_id=world_id,
        name=fm.get("name") or fm.get("title") or asset_id,
        frontmatter=fm,
        body=data.get("body") or "",
        source_chain=chain,
        overrides_applied=(["override"] if data.get("override") else []),
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


def _json_equal(left: Any, right: Any) -> bool:
    """Structural equality after canonical JSON serialization."""
    return json.dumps(left, sort_keys=True, default=str) == json.dumps(
        right, sort_keys=True, default=str
    )


def _slugify(value: str) -> str:
    """Crude ASCII slugifier: lowercase, non-alphanum -> hyphens, collapse + trim."""
    value = value.strip().lower()
    value = _re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "entity"


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
        if not _json_equal(value, baseline.get(key)):
            out[key] = value
    for key in baseline:
        if key not in current:
            out[key] = None
    return out
