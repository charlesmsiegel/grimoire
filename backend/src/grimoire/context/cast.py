"""CastResolver — resolve scene cast into tiered context items."""

from __future__ import annotations

import logging
from typing import Any

from grimoire.context.config import ContextBuilderConfig
from grimoire.context.tokens import TokenEstimator, estimate_tokens
from grimoire.context.types import TierItem, make_source_id
from grimoire.types.common import CampaignId
from grimoire.types.context import ContextSource
from grimoire.types.inclusion_reasons import InclusionReason
from grimoire.types.state import ContextTier

logger = logging.getLogger(__name__)


class CastResolver:
    def __init__(
        self,
        *,
        characters: Any,
        library: Any,
        transient_state: Any | None = None,
        config: ContextBuilderConfig,
        estimator: TokenEstimator,
    ) -> None:
        self._characters = characters
        self._library = library
        self._transient_state = transient_state
        self._config = config
        self._estimator = estimator

    def render_scene_header(self, scene: Any) -> str:
        if scene is None:
            return "No active scene."
        lines = [f"Scene: {getattr(scene, 'title', None) or getattr(scene, 'slug', '')}"]
        if getattr(scene, "location_ref", None):
            lines.append(f"Location: {scene.location_ref}")
        igt = getattr(scene, "in_game_start", None)
        if igt is not None:
            lines.append(f"In-game start: {igt}")
        if getattr(scene, "mood", None):
            lines.append(f"Mood: {scene.mood}")
        present = list(getattr(scene, "present_character_refs", []) or [])
        if present:
            lines.append("Present cast: " + ", ".join(present))
        return "\n".join(lines)

    async def active_pc_card(
        self, pc_ref: str | None, campaign_id: CampaignId
    ) -> tuple[str, ContextSource | None]:
        if not pc_ref:
            return "", None
        try:
            card = await self._characters.get_full_card(pc_ref, campaign_id)
        except Exception:
            return "", None
        if not card:
            return "", None
        source = ContextSource(
            kind="character",
            scope="campaign-local",
            owner_id=campaign_id,
            tier=ContextTier.LOCK_IN,
            summary=f"Active PC: {pc_ref}",
            source_id=make_source_id("pc_card", pc_ref),
            inclusion_reasons=[InclusionReason.PC_CARD],
        )
        return card, source

    async def active_pc_name(self, pc_ref: str | None, campaign_id: CampaignId) -> str:
        if not pc_ref:
            return ""
        try:
            resolved = await self._characters.resolve(pc_ref, campaign_id)
        except Exception:
            return ""
        character = getattr(resolved, "character", None)
        name = getattr(character, "name", "") if character is not None else ""
        return name or ""

    async def resolve(
        self,
        *,
        scene: Any,
        campaign_id: CampaignId,
        active_pc_ref: str | None,
        recent_posts: list[Any],
        commitments_targeting_pcs: set[str] | None = None,
    ) -> tuple[list[TierItem], list[TierItem], str]:
        spotlight_items: list[TierItem] = []
        background_items: list[TierItem] = []

        present_refs: list[str] = []
        if scene is not None:
            present_refs = list(getattr(scene, "present_character_refs", []) or [])

        reasons_by_ref: dict[str, set[InclusionReason]] = {}

        def _add_reason(ref: str, reason: InclusionReason) -> None:
            reasons_by_ref.setdefault(ref, set()).add(reason)

        for ref in present_refs:
            _add_reason(ref, InclusionReason.PRESENT_IN_SCENE)

        mentioned_refs = _mentions_in_posts(recent_posts)
        for ref in mentioned_refs:
            _add_reason(ref, InclusionReason.MENTIONED_IN_RECENT_POSTS)

        for ref in commitments_targeting_pcs or set():
            _add_reason(ref, InclusionReason.COMMITMENT_OPEN_TO_PC)

        tier_map = await self._recommend_tiers(
            scene=scene,
            campaign_id=campaign_id,
            recent_posts=recent_posts,
            commitments_targeting_pcs=commitments_targeting_pcs,
        )

        seen: set[str] = set()
        if active_pc_ref:
            seen.add(active_pc_ref)

        spotlight_refs: list[str] = []
        for ref in present_refs:
            if ref in seen:
                continue
            spotlight_refs.append(ref)
            seen.add(ref)
        for ref, tier in tier_map.items():
            if ref in seen or tier != ContextTier.SPOTLIGHT:
                continue
            spotlight_refs.append(ref)
            seen.add(ref)

        for ref in spotlight_refs:
            card = await self._try_full_card(ref, campaign_id)
            if not card:
                continue
            ref_reasons = sorted(reasons_by_ref.get(ref, set()), key=lambda r: r.value)
            spotlight_items.append(
                TierItem(
                    tier=ContextTier.SPOTLIGHT,
                    section="cast",
                    text=_with_cast_header(ref, card),
                    priority=10,
                    source=self._character_source(
                        ref, ContextTier.SPOTLIGHT, campaign_id, reasons=ref_reasons
                    ),
                )
            )
            if self._config.enable_voice_anchor:
                voice_text = await self._voice_anchor(ref, campaign_id)
                if voice_text:
                    spotlight_items.append(
                        TierItem(
                            tier=ContextTier.SPOTLIGHT,
                            section="voice_anchor",
                            text=f"# Voice anchor — {ref}\n{voice_text}",
                            priority=9,
                            source=ContextSource(
                                kind="character",
                                scope="library" if ref.startswith("library:") else "campaign-local",
                                owner_id=ref if ref.startswith("library:") else campaign_id,
                                tier=ContextTier.SPOTLIGHT,
                                summary=f"voice:{ref}",
                                source_id=make_source_id("voice", ref),
                                inclusion_reasons=list(ref_reasons),
                            ),
                        )
                    )
            stanza_item = await self._maybe_transient_stanza_item(
                ref=ref,
                campaign_id=campaign_id,
                active_pc_ref=active_pc_ref,
            )
            if stanza_item is not None:
                spotlight_items.append(stanza_item)

            if self._config.enable_extras_stanza:
                spot, bg = await self._extras_tier_items(ref, campaign_id)
                spotlight_items.extend(spot)
                background_items.extend(bg)

            dialogue = self._recent_dialogue_for(ref, recent_posts)
            if dialogue:
                spotlight_items.append(
                    TierItem(
                        tier=ContextTier.SPOTLIGHT,
                        section="recent_dialogue",
                        text=f"# Recent dialogue — {ref}\n{dialogue}",
                        priority=7,
                        source=ContextSource(
                            kind="post",
                            scope="campaign-local",
                            owner_id=campaign_id,
                            tier=ContextTier.SPOTLIGHT,
                            summary=f"dialogue:{ref}",
                            source_id=make_source_id("dialogue", ref),
                            inclusion_reasons=list(ref_reasons),
                        ),
                    )
                )

        background_refs: list[str] = []
        for ref, tier in tier_map.items():
            if ref in seen or tier != ContextTier.BACKGROUND:
                continue
            background_refs.append(ref)
            seen.add(ref)

        if not tier_map:
            mentioned = _mentions_in_posts(recent_posts) - seen
            for ref in mentioned:
                background_refs.append(ref)
                seen.add(ref)

        for ref in background_refs[: self._config.background_character_limit]:
            text = await self._try_compressed_card(ref, campaign_id)
            if not text:
                continue
            ref_reasons = sorted(reasons_by_ref.get(ref, set()), key=lambda r: r.value)
            background_items.append(
                TierItem(
                    tier=ContextTier.BACKGROUND,
                    section="cast",
                    text=_with_cast_header(ref, text),
                    priority=5,
                    source=self._character_source(
                        ref, ContextTier.BACKGROUND, campaign_id, reasons=ref_reasons
                    ),
                )
            )

        corrective_lines: list[str] = []
        for ref in [r for r in [active_pc_ref, *present_refs] if r]:
            try:
                snippet = await self._characters.drift_corrective_context(ref, campaign_id)
            except Exception:
                snippet = ""
            if snippet:
                corrective_lines.append(snippet)
        return spotlight_items, background_items, "\n\n".join(corrective_lines)

    async def _recommend_tiers(
        self,
        *,
        scene: Any,
        campaign_id: CampaignId,
        recent_posts: list[Any],
        commitments_targeting_pcs: set[str] | None,
    ) -> dict[str, ContextTier]:
        if scene is None:
            return {}
        recommend = getattr(self._characters, "recommend_tiers", None)
        if recommend is None:
            return {}
        try:
            out = await recommend(
                scene,
                campaign_id,
                recent_posts=list(recent_posts),
                commitments_targeting_pcs=commitments_targeting_pcs or set(),
            )
        except TypeError:
            try:
                out = await recommend(scene)
            except Exception:
                return {}
        except Exception as exc:
            logger.debug("recommend_tiers failed: %s", exc)
            return {}
        return dict(out or {})

    async def _voice_anchor(self, ref: str, campaign_id: CampaignId) -> str:
        getter = getattr(self._characters, "get_voice_only", None)
        if getter is None:
            return ""
        try:
            return (await getter(ref, campaign_id)) or ""
        except Exception as exc:
            logger.debug("get_voice_only(%s) failed: %s", ref, exc)
            return ""

    async def _maybe_transient_stanza_item(
        self,
        *,
        ref: str,
        campaign_id: CampaignId,
        active_pc_ref: str | None,
    ) -> TierItem | None:
        if self._transient_state is None:
            return None
        from grimoire.transient_state.stanza import render_transient_stanza
        from grimoire.types.transient import EntityKind, ObserverKind

        observer = ObserverKind.PC_OWNER if ref == active_pc_ref else ObserverKind.OTHER_PC
        try:
            bundle = await self._transient_state.get(
                campaign_id,
                EntityKind.CHARACTER,
                ref,
                for_observer=observer,
            )
        except Exception as exc:
            logger.debug("transient_state.get(%s) failed: %s", ref, exc)
            return None
        if not bundle:
            return None
        if not isinstance(bundle, dict):
            return None
        name = await self._character_display_name(ref, campaign_id)
        text = render_transient_stanza(name or ref, bundle)
        if not text:
            return None
        return TierItem(
            tier=ContextTier.SPOTLIGHT,
            section="transient",
            text=text,
            priority=8,
            source=ContextSource(
                kind="character",
                scope="campaign-local",
                owner_id=campaign_id,
                tier=ContextTier.SPOTLIGHT,
                summary=f"transient:{ref}",
            ),
        )

    async def _character_display_name(self, ref: str, campaign_id: CampaignId) -> str:
        getter = getattr(self._characters, "get_display_name", None)
        if getter is None:
            return ref
        try:
            return (await getter(ref, campaign_id)) or ref
        except Exception:
            return ref

    def _recent_dialogue_for(self, ref: str, posts: list[Any]) -> str:
        n = self._config.recent_dialogue_per_speaker
        if n <= 0 or not ref:
            return ""
        lines: list[str] = []
        for post in reversed(list(posts)):
            pc_ref = getattr(post, "author_pc_ref", None)
            npc_ref = getattr(post, "author_npc_ref", None)
            if pc_ref != ref and npc_ref != ref:
                continue
            body = (getattr(post, "body", "") or "").strip()
            if not body:
                continue
            lines.append(f"- {body}")
            if len(lines) >= n:
                break
        if not lines:
            return ""
        return "\n".join(reversed(lines))

    async def _extras_tier_items(
        self, ref: str, campaign_id: CampaignId
    ) -> tuple[list[TierItem], list[TierItem]]:
        try:
            resolved = await self._library.resolve(ref, campaign_id)
        except Exception as exc:
            logger.debug("library.resolve(%s) for extras failed: %s", ref, exc)
            return ([], [])
        fm = getattr(resolved, "frontmatter", None) or {}
        raw = fm.get("extras") or {}
        if not isinstance(raw, dict) or not raw:
            return ([], [])

        name = getattr(resolved, "name", None) or ref
        rendered = _format_extras_stanza(name, raw)
        if not rendered:
            return ([], [])

        token_estimate = await estimate_tokens(rendered, self._estimator)
        source = self._character_source(ref, ContextTier.SPOTLIGHT, campaign_id)
        if token_estimate <= self._config.extras_demote_to_breadcrumb_threshold_tokens:
            return (
                [
                    TierItem(
                        tier=ContextTier.SPOTLIGHT,
                        section="extras",
                        text=rendered,
                        priority=self._config.extras_spotlight_priority,
                        source=ContextSource(
                            kind="character",
                            scope=source.scope,
                            owner_id=source.owner_id,
                            tier=ContextTier.SPOTLIGHT,
                            summary=f"extras:{ref}",
                        ),
                    )
                ],
                [],
            )

        breadcrumb = _format_extras_breadcrumb(name, raw)
        return (
            [],
            [
                TierItem(
                    tier=ContextTier.BACKGROUND,
                    section="extras",
                    text=breadcrumb,
                    priority=3,
                    source=ContextSource(
                        kind="character",
                        scope=source.scope,
                        owner_id=source.owner_id,
                        tier=ContextTier.BACKGROUND,
                        summary=f"extras-breadcrumb:{ref}",
                    ),
                )
            ],
        )

    async def _try_full_card(self, ref: str, campaign_id: CampaignId) -> str:
        try:
            return await self._characters.get_full_card(ref, campaign_id)
        except Exception as exc:
            logger.debug("get_full_card(%s) failed: %s", ref, exc)
            return ""

    async def _try_compressed_card(self, ref: str, campaign_id: CampaignId) -> str:
        try:
            return await self._characters.get_compressed_card(ref, campaign_id)
        except Exception as exc:
            logger.debug("get_compressed_card(%s) failed: %s", ref, exc)
            return ""

    def _character_source(
        self,
        ref: str,
        tier: ContextTier,
        campaign_id: CampaignId,
        *,
        reasons: list[InclusionReason] | None = None,
    ) -> ContextSource:
        if ref.startswith("library:"):
            scope = "library"
            owner = ref
        else:
            scope = "campaign-local"
            owner = campaign_id
        return ContextSource(
            kind="character",
            scope=scope,
            owner_id=owner,
            tier=tier,
            summary=ref,
            source_id=make_source_id("character", ref),
            inclusion_reasons=list(reasons or []),
        )


# ------------------------------------------------------------------ #
# Module-level helpers
# ------------------------------------------------------------------ #


def _mentions_in_posts(posts: list[Any]) -> set[str]:
    mentioned: set[str] = set()
    for post in posts:
        body = getattr(post, "body", "") or ""
        for tok in body.split():
            if tok.startswith(("library:", "campaign:")):
                mentioned.add(tok.strip(".,;:!?"))
    return mentioned


def _with_cast_header(ref: str, card: str) -> str:
    if not ref.startswith("library:"):
        return card
    raw = ref[len("library:"):]
    parts = raw.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "worlds":
        world_id = parts[1]
        return f"[world:{world_id}]\n{card}"
    return card


def _format_extras_stanza(name: str, extras: dict) -> str:
    lines: list[str] = []
    for key, raw in extras.items():
        value = _project_extras_value(raw)
        rendered = _render_extras_value(value)
        if rendered is None or rendered == "":
            continue
        lines.append(f"  {key}: {rendered}")
    if not lines:
        return ""
    header = f"{name} — extras:"
    return "\n".join([header, *lines])


def _format_extras_breadcrumb(name: str, extras: dict) -> str:
    keys = [
        key
        for key, raw in extras.items()
        if _render_extras_value(_project_extras_value(raw)) not in (None, "")
    ]
    if not keys:
        return ""
    return f"{name} — extras: {', '.join(keys)}"


def _project_extras_value(raw: Any) -> Any:
    if isinstance(raw, dict) and "value" in raw and "set_at" in raw:
        return raw.get("value")
    return raw


def _render_extras_value(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float, str)):
        return str(value)
    if isinstance(value, list):
        rendered = [_render_extras_value(v) for v in value if _render_extras_value(v) is not None]
        return "; ".join(r for r in rendered if r) or None
    if isinstance(value, dict):
        rendered = [
            f"{k}={_render_extras_value(v)}"
            for k, v in value.items()
            if _render_extras_value(v) is not None
        ]
        return ", ".join(rendered) or None
    return str(value)
