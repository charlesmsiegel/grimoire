"""``ExtrasService``: cascade-resolved read/write of narrative extras.

Writes route to:
- ``library`` scope         → ``LibraryService.update_entity`` (frontmatter patch)
- ``campaign-local``        → ``StateStore.write_emergent`` (re-writes the emergent file)
- ``override``              → ``StateStore.write_override`` (frontmatter-patch overlay)

After every write the mirror is upserted. Reads pull from the cascade-
resolved frontmatter dict on ``ResolvedEntity``; the mirror is for query.
"""

from __future__ import annotations

import contextlib
import warnings
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

from grimoire.library.service import LibraryService
from grimoire.state_store.indexers import make_library_id
from grimoire.state_store.store import StateStore
from grimoire.types.common import EntityKind
from grimoire.types.extras import (
    HARD_CAP_CHARS_PER_STRING,
    HARD_CAP_PER_ENTITY,
    SOFT_CAP_CHARS_PER_STRING,
    SOFT_CAP_LIST_ITEMS,
    SOFT_CAP_PER_ENTITY,
    SOFT_CAP_TOTAL_BYTES,
    ExtraScope,
    ExtraValue,
    flatten_extras_value_for_search,
    validate_extras_key,
    validate_extras_value,
)

from .errors import (
    ExtrasHardCapError,
    ExtrasNotFoundError,
    ExtrasPromotionError,
    ExtrasSoftCapWarning,
)
from .mirror import ExtrasMirror


@dataclass(frozen=True)
class ExtrasSetResult:
    """Return payload from ``set``. ``warnings`` carries soft-cap notes."""

    extra: ExtraValue
    warnings: list[str]


@dataclass(frozen=True)
class ExtrasSearchHit:
    entity_kind: str
    entity_id: str
    key: str
    value_text: str


@dataclass(frozen=True)
class _ScopeOps:
    """Per-scope storage strategy (#523).

    Collapses the three-way ``scope`` branch that the read/write methods used
    to repeat. ``read`` returns a scope's raw ``extras`` dict; ``apply_patch``
    merges a ``{key: value}`` patch (``None`` value = override tombstone where
    the scope supports it); ``rewrite`` replaces the scope's ``extras`` section
    wholesale. Adding a storage scope is one table entry plus its three ops.
    """

    read: Callable[..., Awaitable[dict[str, Any]]]
    apply_patch: Callable[..., Awaitable[None]]
    rewrite: Callable[..., Awaitable[None]]


def _kind_str(kind: EntityKind | str) -> str:
    return kind.value if isinstance(kind, EntityKind) else str(kind)


def _entity_to_subkind(kind: EntityKind | str) -> str:
    """Map an EntityKind to the on-disk directory subkind used by the store."""
    return _kind_str(kind)


class ExtrasService:
    """Concrete implementation of the ``Extras`` protocol (spec §Service)."""

    def __init__(
        self,
        *,
        library: LibraryService,
        store: StateStore,
        mirror: ExtrasMirror | None = None,
    ) -> None:
        self.library = library
        self.store = store
        self.mirror = mirror or ExtrasMirror(store.db)

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    async def get(
        self,
        *,
        entity_kind: EntityKind,
        entity_id: str,
        campaign_id: str | None = None,
        world_id: str | None = None,
    ) -> dict[str, ExtraValue]:
        """Cascade-resolved extras for an entity.

        Library-only when ``campaign_id`` is ``None`` (reads the library
        frontmatter directly). With ``campaign_id``, walks the existing
        emergent → override → library cascade and returns the merged extras
        dict.
        """
        fm = await self._resolved_frontmatter(
            entity_kind=entity_kind,
            entity_id=entity_id,
            campaign_id=campaign_id,
            world_id=world_id,
        )
        return _decode_extras(fm.get("extras") or {})

    async def get_raw(
        self,
        *,
        entity_kind: EntityKind,
        entity_id: str,
        scope: ExtraScope,
        campaign_id: str | None = None,
        world_id: str | None = None,
    ) -> dict[str, ExtraValue]:
        """Single-scope read; no cascade. Used by the entity-detail UI to
        render source badges."""
        ops = self._scope_ops(scope)
        self._require_scope_args(scope, "reads", campaign_id=campaign_id, world_id=world_id)
        raw = await ops.read(
            self,
            kind=_kind_str(entity_kind),
            entity_id=entity_id,
            campaign_id=campaign_id,
            world_id=world_id,
            strict=True,
        )
        return _decode_extras(raw)

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #

    async def set(
        self,
        *,
        entity_kind: EntityKind,
        entity_id: str,
        key: str,
        value: Any,
        scope: ExtraScope,
        campaign_id: str | None,
        world_id: str | None = None,
        actor: str = "user",
        evidence: str | None = None,
    ) -> ExtrasSetResult:
        validate_extras_key(key)
        validate_extras_value(value)

        kind = _kind_str(entity_kind)

        # Cap enforcement reads the post-write extras dict.
        current = await self._raw_extras_for_scope(
            entity_kind=entity_kind,
            entity_id=entity_id,
            scope=scope,
            campaign_id=campaign_id,
            world_id=world_id,
        )
        projected = dict(current)
        extra = ExtraValue(
            value=value,
            set_at=datetime.now(UTC),
            set_by=actor,
            source_evidence=evidence,
            scope=scope,
        )
        projected[key] = extra.model_dump(mode="json")
        warnings_out = _enforce_caps(projected)

        ops = self._scope_ops(scope)
        self._require_scope_args(scope, "writes", campaign_id=campaign_id, world_id=world_id)
        await ops.apply_patch(
            self,
            kind=kind,
            entity_id=entity_id,
            campaign_id=campaign_id,
            world_id=world_id,
            patch={key: extra.model_dump(mode="json")},
            actor=actor,
        )

        await self.mirror.upsert(
            campaign_id=(campaign_id or ""),
            entity_kind=kind,
            entity_id=entity_id,
            scope=scope.value,
            key=key,
            value=value,
            set_at=extra.set_at,
            set_by=actor,
        )
        return ExtrasSetResult(extra=extra, warnings=warnings_out)

    async def delete(
        self,
        *,
        entity_kind: EntityKind,
        entity_id: str,
        key: str,
        scope: ExtraScope,
        campaign_id: str | None,
        world_id: str | None = None,
        actor: str = "user",
    ) -> None:
        validate_extras_key(key)
        kind = _kind_str(entity_kind)
        ops = self._scope_ops(scope)
        self._require_scope_args(scope, "delete", campaign_id=campaign_id, world_id=world_id)

        if scope == ExtraScope.OVERRIDE:
            # Override-null clears the cascade-resolved key (tombstone): the key
            # stays in the override as ``None`` rather than being removed.
            await ops.apply_patch(
                self,
                kind=kind,
                entity_id=entity_id,
                campaign_id=campaign_id,
                world_id=world_id,
                patch={key: None},
                actor=actor,
            )
        else:
            # Library / campaign-local: deep-merge can't remove a key, so read
            # the scope's extras, drop the key, and rewrite the section whole.
            existing = await self._raw_extras_for_scope(
                entity_kind=entity_kind,
                entity_id=entity_id,
                scope=scope,
                campaign_id=campaign_id,
                world_id=world_id,
            )
            if key not in existing:
                raise ExtrasNotFoundError(f"extras key not found in {scope.value}: {key!r}")
            existing.pop(key)
            await ops.rewrite(
                self,
                kind=kind,
                entity_id=entity_id,
                campaign_id=campaign_id,
                world_id=world_id,
                extras=existing,
                actor=actor,
            )

        await self.mirror.delete(
            campaign_id=(campaign_id or ""),
            entity_kind=kind,
            entity_id=entity_id,
            scope=scope.value,
            key=key,
        )

    async def rename(
        self,
        *,
        entity_kind: EntityKind,
        entity_id: str,
        old_key: str,
        new_key: str,
        scope: ExtraScope,
        campaign_id: str | None,
        world_id: str | None = None,
        actor: str = "user",
    ) -> ExtrasSetResult:
        validate_extras_key(new_key)
        existing = await self._raw_extras_for_scope(
            entity_kind=entity_kind,
            entity_id=entity_id,
            scope=scope,
            campaign_id=campaign_id,
            world_id=world_id,
        )
        if old_key not in existing:
            raise ExtrasNotFoundError(f"cannot rename missing key: {old_key!r}")
        if new_key in existing:
            raise ExtrasPromotionError(f"rename target collides with existing key: {new_key!r}")
        existing_value = existing[old_key]
        value = existing_value.get("value") if isinstance(existing_value, dict) else None
        # Hard-remove old_key from this scope's storage before writing
        # new_key. delete() on OVERRIDE writes an override-null tombstone
        # that would permanently mask the library value at old_key in this
        # campaign; rename is supposed to move the value, not hide what's
        # underneath -- so we strip the key from the scope file directly.
        await self._remove_key_in_scope(
            entity_kind=entity_kind,
            entity_id=entity_id,
            key=old_key,
            scope=scope,
            campaign_id=campaign_id,
            world_id=world_id,
            actor=actor,
        )
        return await self.set(
            entity_kind=entity_kind,
            entity_id=entity_id,
            key=new_key,
            value=value,
            scope=scope,
            campaign_id=campaign_id,
            world_id=world_id,
            actor=actor,
        )

    async def _remove_key_in_scope(
        self,
        *,
        entity_kind: EntityKind,
        entity_id: str,
        key: str,
        scope: ExtraScope,
        campaign_id: str | None,
        world_id: str | None,
        actor: str,
    ) -> None:
        """Remove ``key`` from a single scope's storage without writing an
        override-null tombstone. Used by rename(); the regular delete()
        path for OVERRIDE keeps the tombstone semantics intact for callers
        that want to mask the underlying library value."""
        kind = _kind_str(entity_kind)
        ops = self._scope_ops(scope)
        existing = await self._raw_extras_for_scope(
            entity_kind=entity_kind,
            entity_id=entity_id,
            scope=scope,
            campaign_id=campaign_id,
            world_id=world_id,
        )
        if key not in existing:
            raise ExtrasNotFoundError(f"key not found in {scope.value} scope: {key!r}")
        remaining = {k: v for k, v in existing.items() if k != key}
        self._require_scope_args(scope, "rename", campaign_id=campaign_id, world_id=world_id)
        # Wholesale section rewrite for every scope — unlike delete(), rename's
        # OVERRIDE path strips the key rather than leaving a tombstone behind.
        await ops.rewrite(
            self,
            kind=kind,
            entity_id=entity_id,
            campaign_id=campaign_id,
            world_id=world_id,
            extras=remaining,
            actor=actor,
        )
        await self.mirror.delete(
            campaign_id=(campaign_id or ""),
            entity_kind=kind,
            entity_id=entity_id,
            scope=scope.value,
            key=key,
        )

    async def _rewrite_override_extras_section(
        self,
        *,
        campaign_id: str,
        world_id: str,
        kind: str,
        entity_id: str,
        extras: dict[str, Any],
        actor: str,
    ) -> None:
        """Replace the ``extras`` section in an override file wholesale.

        Unlike ``_write_override_extras``, which preserves None entries as
        tombstones, this rewrites the section from scratch -- used by
        rename to hard-remove a key without leaving a mask behind.
        """
        library_id = make_library_id(world_id, kind, entity_id)
        existing = await self.store.get_override(campaign_id, library_id) or {}
        merged = dict(existing)
        if extras:
            merged["extras"] = dict(extras)
        else:
            merged.pop("extras", None)
        await self.store.write_override(
            campaign_id=campaign_id,
            library_id=library_id,
            patch=merged,
            source=actor,
        )

    # ------------------------------------------------------------------ #
    # Search
    # ------------------------------------------------------------------ #

    async def search(
        self,
        query: str,
        *,
        entity_kind: EntityKind | None = None,
        key: str | None = None,
        limit: int = 50,
    ) -> list[ExtrasSearchHit]:
        rows = await self.mirror.search(
            query,
            entity_kind=_kind_str(entity_kind) if entity_kind is not None else None,
            key=key,
            limit=limit,
        )
        return [
            ExtrasSearchHit(
                entity_kind=r["entity_kind"],
                entity_id=r["entity_id"],
                key=r["key"],
                value_text=r["value_text"] or "",
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # Promotion
    # ------------------------------------------------------------------ #

    async def promote_to_library(
        self,
        *,
        entity_kind: EntityKind,
        entity_id: str,
        key: str,
        campaign_id: str,
        world_id: str,
        actor: str = "promotion",
    ) -> ExtrasSetResult:
        resolved = await self.get(
            entity_kind=entity_kind,
            entity_id=entity_id,
            campaign_id=campaign_id,
            world_id=world_id,
        )
        extra = resolved.get(key)
        if extra is None:
            raise ExtrasPromotionError(f"nothing to promote: {key!r}")
        if extra.scope == ExtraScope.LIBRARY:
            raise ExtrasPromotionError(f"key already library-scope: {key!r}")
        result = await self.set(
            entity_kind=entity_kind,
            entity_id=entity_id,
            key=key,
            value=extra.value,
            scope=ExtraScope.LIBRARY,
            campaign_id=None,
            world_id=world_id,
            actor=actor,
            evidence=extra.source_evidence,
        )
        # Clear the override so the cascade reads from library going forward.
        # Suppression: campaign-local emergent has no override to clear.
        with contextlib.suppress(ExtrasNotFoundError):
            await self.delete(
                entity_kind=entity_kind,
                entity_id=entity_id,
                key=key,
                scope=ExtraScope.OVERRIDE,
                campaign_id=campaign_id,
                world_id=world_id,
                actor=actor,
            )
        return result

    # ------------------------------------------------------------------ #
    # Per-scope storage strategy (#523)
    # ------------------------------------------------------------------ #

    # Which of (campaign_id, world_id) each scope needs present.
    _SCOPE_REQUIRED_ARGS: ClassVar[dict[ExtraScope, tuple[str, ...]]] = {
        ExtraScope.LIBRARY: ("world_id",),
        ExtraScope.CAMPAIGN_LOCAL: ("campaign_id",),
        ExtraScope.OVERRIDE: ("campaign_id", "world_id"),
    }

    def _scope_ops(self, scope: ExtraScope) -> _ScopeOps:
        ops = self._SCOPE_OPS.get(scope)
        if ops is None:  # pragma: no cover -- ExtraScope membership is closed.
            raise ValueError(f"unknown scope {scope}")
        return ops

    def _missing_scope_args(
        self, scope: ExtraScope, campaign_id: str | None, world_id: str | None
    ) -> bool:
        vals = {"campaign_id": campaign_id, "world_id": world_id}
        return any(vals[name] is None for name in self._SCOPE_REQUIRED_ARGS[scope])

    def _require_scope_args(
        self,
        scope: ExtraScope,
        op: str,
        *,
        campaign_id: str | None,
        world_id: str | None,
    ) -> None:
        """Raise ``ExtrasNotFoundError`` if a required scope arg is missing."""
        vals = {"campaign_id": campaign_id, "world_id": world_id}
        missing = [name for name in self._SCOPE_REQUIRED_ARGS[scope] if vals[name] is None]
        if missing:
            raise ExtrasNotFoundError(f"{' and '.join(missing)} required for {scope.value} {op}")

    async def _read_library(
        self,
        *,
        kind: str,
        entity_id: str,
        campaign_id: str | None,
        world_id: str | None,
        strict: bool,
    ) -> dict[str, Any]:
        try:
            entity = await self.library.get_entity(world_id, kind, entity_id)
        except Exception:
            if strict:
                raise
            return {}
        return dict((entity.frontmatter or {}).get("extras") or {})

    async def _read_campaign_local(
        self,
        *,
        kind: str,
        entity_id: str,
        campaign_id: str | None,
        world_id: str | None,
        strict: bool,
    ) -> dict[str, Any]:
        emergent = await self.store.get_emergent(campaign_id, kind, entity_id)
        if emergent is None:
            return {}
        return dict((emergent.get("frontmatter") or {}).get("extras") or {})

    async def _read_override(
        self,
        *,
        kind: str,
        entity_id: str,
        campaign_id: str | None,
        world_id: str | None,
        strict: bool,
    ) -> dict[str, Any]:
        library_id = make_library_id(world_id, kind, entity_id)
        override = await self.store.get_override(campaign_id, library_id)
        return dict((override or {}).get("extras") or {})

    async def _apply_patch_library(
        self,
        *,
        kind: str,
        entity_id: str,
        campaign_id: str | None,
        world_id: str | None,
        patch: dict[str, Any],
        actor: str,
    ) -> None:
        await self.library.update_entity(
            world_id=world_id,
            kind=kind,
            entity_id=entity_id,
            frontmatter_patch={"extras": patch},
            source=actor,
        )

    async def _apply_patch_campaign_local(
        self,
        *,
        kind: str,
        entity_id: str,
        campaign_id: str | None,
        world_id: str | None,
        patch: dict[str, Any],
        actor: str,
    ) -> None:
        await self._write_emergent_extras(
            campaign_id=campaign_id, kind=kind, entity_id=entity_id, patch=patch, actor=actor
        )

    async def _apply_patch_override(
        self,
        *,
        kind: str,
        entity_id: str,
        campaign_id: str | None,
        world_id: str | None,
        patch: dict[str, Any],
        actor: str,
    ) -> None:
        await self._write_override_extras(
            campaign_id=campaign_id,
            world_id=world_id,
            kind=kind,
            entity_id=entity_id,
            patch=patch,
            actor=actor,
        )

    async def _rewrite_library(
        self,
        *,
        kind: str,
        entity_id: str,
        campaign_id: str | None,
        world_id: str | None,
        extras: dict[str, Any],
        actor: str,
    ) -> None:
        await self._rewrite_library_extras_section(
            world_id=world_id, kind=kind, entity_id=entity_id, extras=extras, actor=actor
        )

    async def _rewrite_campaign_local(
        self,
        *,
        kind: str,
        entity_id: str,
        campaign_id: str | None,
        world_id: str | None,
        extras: dict[str, Any],
        actor: str,
    ) -> None:
        await self._write_emergent_extras(
            campaign_id=campaign_id,
            kind=kind,
            entity_id=entity_id,
            patch=extras,
            actor=actor,
            replace=True,
        )

    async def _rewrite_override(
        self,
        *,
        kind: str,
        entity_id: str,
        campaign_id: str | None,
        world_id: str | None,
        extras: dict[str, Any],
        actor: str,
    ) -> None:
        await self._rewrite_override_extras_section(
            campaign_id=campaign_id,
            world_id=world_id,
            kind=kind,
            entity_id=entity_id,
            extras=extras,
            actor=actor,
        )

    _SCOPE_OPS: ClassVar[dict[ExtraScope, _ScopeOps]] = {
        ExtraScope.LIBRARY: _ScopeOps(_read_library, _apply_patch_library, _rewrite_library),
        ExtraScope.CAMPAIGN_LOCAL: _ScopeOps(
            _read_campaign_local, _apply_patch_campaign_local, _rewrite_campaign_local
        ),
        ExtraScope.OVERRIDE: _ScopeOps(_read_override, _apply_patch_override, _rewrite_override),
    }

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    async def _resolved_frontmatter(
        self,
        *,
        entity_kind: EntityKind,
        entity_id: str,
        campaign_id: str | None,
        world_id: str | None,
    ) -> dict[str, Any]:
        kind = _kind_str(entity_kind)
        if campaign_id is None:
            if world_id is None:
                raise ExtrasNotFoundError("world_id required when campaign_id is None")
            entity = await self.library.get_entity(world_id, kind, entity_id)
            return dict(entity.frontmatter or {})
        # Try emergent-first; otherwise resolve through the world cascade.
        if world_id is None:
            ref = f"emergent/{kind}/{entity_id}"
        else:
            ref = make_library_id(world_id, kind, entity_id)
        resolved = await self.library.resolve(ref, campaign_id)
        return dict(resolved.frontmatter or {})

    async def _raw_extras_for_scope(
        self,
        *,
        entity_kind: EntityKind,
        entity_id: str,
        scope: ExtraScope,
        campaign_id: str | None,
        world_id: str | None,
    ) -> dict[str, Any]:
        ops = self._scope_ops(scope)
        if self._missing_scope_args(scope, campaign_id, world_id):
            return {}
        return await ops.read(
            self,
            kind=_kind_str(entity_kind),
            entity_id=entity_id,
            campaign_id=campaign_id,
            world_id=world_id,
            strict=False,
        )

    async def _write_override_extras(
        self,
        *,
        campaign_id: str,
        world_id: str,
        kind: str,
        entity_id: str,
        patch: dict[str, Any],
        actor: str,
    ) -> None:
        library_id = make_library_id(world_id, kind, entity_id)
        existing = await self.store.get_override(campaign_id, library_id) or {}
        merged = dict(existing)
        current_extras = dict(merged.get("extras") or {})
        for key, value in patch.items():
            if value is None:
                # override-null: keep the key in the override so the cascade
                # clears the library value when merged.
                current_extras[key] = None
            else:
                current_extras[key] = value
        merged["extras"] = current_extras
        await self.store.write_override(
            campaign_id=campaign_id,
            library_id=library_id,
            patch=merged,
            source=actor,
        )

    async def _rewrite_library_extras_section(
        self,
        *,
        world_id: str,
        kind: str,
        entity_id: str,
        extras: dict[str, Any],
        actor: str,
    ) -> None:
        """Replace the ``extras`` section in a library file wholesale.

        Used by ``delete``/``rename`` because ``update_entity`` does a deep
        merge that cannot remove keys.
        """
        library_id = make_library_id(world_id, kind, entity_id)
        row = await self.store.get_library_entity(library_id)
        if row is None:
            raise ExtrasNotFoundError(f"library entity not found: {library_id}")
        new_fm = dict(row.get("frontmatter") or {})
        if extras:
            new_fm["extras"] = dict(extras)
        else:
            new_fm.pop("extras", None)
        body = row.get("body") or ""
        await self.store.write_library_file(
            library_id=library_id,
            frontmatter=new_fm,
            body=body,
            source=actor,
        )

    async def _write_emergent_extras(
        self,
        *,
        campaign_id: str,
        kind: str,
        entity_id: str,
        patch: dict[str, Any],
        actor: str,
        replace: bool = False,
    ) -> None:
        emergent = await self.store.get_emergent(campaign_id, kind, entity_id)
        if emergent is None:
            # Bootstrapping an emergent purely to host extras is unusual but
            # supported: caller is responsible for ensuring the entity name
            # / id are reasonable.
            frontmatter: dict[str, Any] = {"id": entity_id, "name": entity_id}
            body = ""
        else:
            frontmatter = dict(emergent.get("frontmatter") or {})
            body = emergent.get("body") or ""
        if replace:
            frontmatter["extras"] = dict(patch)
        else:
            extras = dict(frontmatter.get("extras") or {})
            for key, value in patch.items():
                if value is None:
                    extras.pop(key, None)
                else:
                    extras[key] = value
            frontmatter["extras"] = extras
        await self.store.write_emergent(
            campaign_id=campaign_id,
            kind=kind,
            entity_id=entity_id,
            frontmatter=frontmatter,
            body=body,
            source=actor,
        )


# ---------------------------------------------------------------------- #
# Cap enforcement / value coercion
# ---------------------------------------------------------------------- #


def _enforce_caps(projected: dict[str, Any]) -> list[str]:
    """Apply hard caps (raise) and soft caps (return warnings).

    ``projected`` is the post-write extras dict for a single scope. Hard
    string caps are enforced earlier by ``validate_extras_value``; here we
    enforce per-entity counts and aggregate size.
    """
    if len(projected) > HARD_CAP_PER_ENTITY:
        raise ExtrasHardCapError(
            f"extras count {len(projected)} exceeds hard cap {HARD_CAP_PER_ENTITY}"
        )
    out: list[str] = []
    if len(projected) > SOFT_CAP_PER_ENTITY:
        msg = f"extras count {len(projected)} exceeds soft cap {SOFT_CAP_PER_ENTITY}"
        out.append(msg)
        warnings.warn(msg, ExtrasSoftCapWarning, stacklevel=2)
    flattened = " ".join(
        flatten_extras_value_for_search(v.get("value") if isinstance(v, dict) else v)
        for v in projected.values()
    )
    byte_count = len(flattened.encode("utf-8"))
    if byte_count > SOFT_CAP_TOTAL_BYTES:
        msg = f"extras serialized bytes {byte_count} exceeds soft cap {SOFT_CAP_TOTAL_BYTES}"
        out.append(msg)
        warnings.warn(msg, ExtrasSoftCapWarning, stacklevel=2)
    for key, entry in projected.items():
        value = entry.get("value") if isinstance(entry, dict) else entry
        if isinstance(value, str) and len(value) > SOFT_CAP_CHARS_PER_STRING:
            msg = (
                f"extras key {key!r} string length {len(value)} "
                f"exceeds soft cap {SOFT_CAP_CHARS_PER_STRING}"
            )
            out.append(msg)
            warnings.warn(msg, ExtrasSoftCapWarning, stacklevel=2)
        if isinstance(value, list) and len(value) > SOFT_CAP_LIST_ITEMS:
            msg = (
                f"extras key {key!r} list length {len(value)} "
                f"exceeds soft cap {SOFT_CAP_LIST_ITEMS}"
            )
            out.append(msg)
            warnings.warn(msg, ExtrasSoftCapWarning, stacklevel=2)
    # Belt-and-suspenders: re-check hard string cap on values themselves.
    for key, entry in projected.items():
        value = entry.get("value") if isinstance(entry, dict) else entry
        if isinstance(value, str) and len(value) > HARD_CAP_CHARS_PER_STRING:
            raise ExtrasHardCapError(
                f"extras key {key!r} string length {len(value)} "
                f"exceeds hard cap {HARD_CAP_CHARS_PER_STRING}"
            )
    return out


def _decode_extras(raw: dict[str, Any]) -> dict[str, ExtraValue]:
    """Coerce a frontmatter ``extras`` dict into ``ExtraValue`` objects.

    Tolerates legacy plain-value entries (``extras: {scars: "..."}``) by
    wrapping them in a synthetic ExtraValue with ``set_by="legacy"``. Drops
    override-null entries (cleared keys).
    """
    out: dict[str, ExtraValue] = {}
    for key, entry in raw.items():
        if entry is None:
            continue
        if isinstance(entry, dict) and "value" in entry and "set_at" in entry:
            out[key] = ExtraValue.model_validate(entry)
        else:
            out[key] = ExtraValue(
                value=entry,
                set_at=datetime.now(UTC),
                set_by="legacy",
                scope=ExtraScope.LIBRARY,
            )
    return out


def coerce_extras_iterable(items: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    """Helper for batch ingestion of extras from a (key, value) iterable."""
    out: dict[str, Any] = {}
    for key, value in items:
        validate_extras_key(key)
        validate_extras_value(value)
        out[key] = value
    return out
