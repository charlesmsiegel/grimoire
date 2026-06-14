"""Privacy resolution for transient-state reads.

Spec §Privacy model — owned here, consumed by HUD / context / inline /
inspector. The schema sits on Character.privacy.internal_thoughts; a
campaign-level preset can override per-character defaults; the helper
returns the effective ``{hud, inline, context}`` triple for a given
observer.

Default = all-true (solo / co-author mode). Audience + POV mode strips
NPC internal_thought regardless of frontmatter.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from grimoire.types.transient import EntityKind, ObserverKind, TransientValue

INTERNAL_THOUGHT_FIELDS = frozenset({"internal_thought"})


@dataclass(frozen=True, slots=True, kw_only=True)
class PrivacyView:
    """Resolved per-character privacy triple.

    Fields are keyword-only (``kw_only=True``) so the three booleans are
    never constructed positionally — ``PrivacyView(True, False, True)`` is
    unreadable at the call site.
    """

    hud: bool = True
    inline: bool = True
    context: bool = True

    @classmethod
    def all_open(cls) -> PrivacyView:
        return cls(hud=True, inline=True, context=True)

    @classmethod
    def all_closed(cls) -> PrivacyView:
        return cls(hud=False, inline=False, context=False)


def _triple_from_mapping(m: dict[str, Any] | None) -> PrivacyView:
    if not m:
        return PrivacyView.all_open()
    return PrivacyView(
        hud=bool(m.get("surface_in_hud", True)),
        inline=bool(m.get("surface_inline", True)),
        context=bool(m.get("surface_in_context", True)),
    )


CharacterLoader = Callable[[str, str], Awaitable[Any]]


@dataclass
class PrivacyResolver:
    """Resolves the effective privacy triple per (character, observer).

    The resolver intentionally accepts loose object shapes so it can be
    wired to either the Characters service, a raw library lookup, or a
    test stub. Resolution order:

    1. AUTHOR: always all-open.
    2. PC owner viewing own character: always all-open.
    3. POV mode + AUDIENCE + non-PC: strip (all-closed). Plain AUDIENCE
       without ``pov_mode`` falls through to per-character frontmatter.
    4. Per-character frontmatter ``privacy.internal_thoughts``.
    5. Campaign preset (loaded from ``data/campaigns/<id>/privacy.yaml``).
    6. Default: all-open.
    """

    character_loader: CharacterLoader | None = None
    campaign_preset_loader: Callable[[str], Awaitable[dict[str, Any] | None]] | None = None
    pov_pc_ref: str | None = None
    pov_mode: bool = False

    async def resolve(
        self,
        campaign_id: str,
        character_id: str,
        observer: ObserverKind,
    ) -> PrivacyView:
        if observer == ObserverKind.AUTHOR:
            return PrivacyView.all_open()
        if observer == ObserverKind.PC_OWNER and character_id == self.pov_pc_ref:
            return PrivacyView.all_open()

        character = None
        if self.character_loader is not None:
            try:
                character = await self.character_loader(campaign_id, character_id)
            except Exception:
                character = None
        is_pc = bool(getattr(character, "role", "")) and (
            str(getattr(character, "role", "")).lower() == "pc"
        )

        if self.pov_mode and observer == ObserverKind.AUDIENCE and not is_pc:
            return PrivacyView.all_closed()

        per_char = self._extract_internal_thoughts(character)
        if per_char is not None:
            return _triple_from_mapping(per_char)

        preset = None
        if self.campaign_preset_loader is not None:
            try:
                preset = await self.campaign_preset_loader(campaign_id)
            except Exception:
                preset = None
        if preset is not None:
            return _triple_from_mapping((preset or {}).get("internal_thoughts"))

        return PrivacyView.all_open()

    @staticmethod
    def _extract_internal_thoughts(character: Any) -> dict[str, Any] | None:
        if character is None:
            return None
        privacy = getattr(character, "privacy", None)
        if privacy is None:
            return None
        thoughts = (
            getattr(privacy, "internal_thoughts", None)
            if not isinstance(privacy, dict)
            else privacy.get("internal_thoughts")
        )
        if thoughts is None:
            return None
        if hasattr(thoughts, "model_dump"):
            return thoughts.model_dump()
        if isinstance(thoughts, dict):
            return dict(thoughts)
        return {
            "surface_in_hud": getattr(thoughts, "surface_in_hud", True),
            "surface_inline": getattr(thoughts, "surface_inline", True),
            "surface_in_context": getattr(thoughts, "surface_in_context", True),
        }


def apply_privacy_filter(
    resolver: PrivacyResolver | None,
    entity_kind: EntityKind,
    entity_id: str,
    bundle: dict[str, TransientValue],
    *,
    campaign_id: str,
    observer: ObserverKind,
    surface: str = "context",
) -> dict[str, TransientValue]:
    """Strip private fields from a bundle in a synchronous code path.

    The resolver is consulted by the caller (which has an await context)
    and converted to a triple before this is invoked. As a convenience the
    no-resolver path applies the AUDIENCE-strip rule by entity kind so the
    privacy boundary holds even when no Characters service is wired.
    """
    if entity_kind != EntityKind.CHARACTER:
        return bundle
    has_internal = any(k in INTERNAL_THOUGHT_FIELDS for k in bundle)
    if not has_internal:
        return bundle
    if observer == ObserverKind.AUDIENCE:
        return {k: v for k, v in bundle.items() if k not in INTERNAL_THOUGHT_FIELDS}
    return bundle


async def resolve(
    character: Any,
    campaign_id: str,
    observer: ObserverKind,
    *,
    campaign_preset: dict[str, Any] | None = None,
    pov_pc_ref: str | None = None,
) -> PrivacyView:
    """Convenience: resolve the triple for a single character + observer.

    The character is passed directly (so callers that already have the
    object don't need to round-trip through the loader); campaign_preset
    is the parsed ``data/campaigns/<id>/privacy.yaml`` block when present.
    """

    async def _char(_campaign: str, _id: str) -> Any:
        return character

    async def _preset(_campaign: str) -> dict[str, Any] | None:
        return campaign_preset

    return await PrivacyResolver(
        character_loader=_char,
        campaign_preset_loader=_preset,
        pov_pc_ref=pov_pc_ref,
    ).resolve(campaign_id, getattr(character, "id", ""), observer)


def resolve_privacy(
    character: Any,
    *,
    observer: ObserverKind,
    is_self: bool = False,
    is_pc: bool = False,
    pov_mode: bool = False,
) -> PrivacyView:
    """Synchronous resolution against a Character object.

    Decision rules (spec §Privacy):
        1. AUTHOR: always all-open.
        2. PC_OWNER + is_self: always all-open.
        3. POV mode + not PC: all-closed (strip NPC internal thought).
        4. Otherwise: per-character frontmatter.
    """
    if observer == ObserverKind.AUTHOR:
        return PrivacyView.all_open()
    if observer == ObserverKind.PC_OWNER and is_self:
        return PrivacyView.all_open()
    if pov_mode and not is_pc:
        return PrivacyView.all_closed()
    privacy = getattr(character, "privacy", None)
    thoughts = getattr(privacy, "internal_thoughts", None) if privacy is not None else None
    if thoughts is None:
        return PrivacyView.all_open()
    return PrivacyView(
        hud=bool(getattr(thoughts, "surface_in_hud", True)),
        inline=bool(getattr(thoughts, "surface_inline", True)),
        context=bool(getattr(thoughts, "surface_in_context", True)),
    )
