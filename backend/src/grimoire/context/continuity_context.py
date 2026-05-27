"""ContinuityContextResolver — commitments, facts, relationship deltas."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from grimoire.context.config import ContextBuilderConfig
from grimoire.context.types import TierItem, make_source_id
from grimoire.continuity.registry import resolve_continuity
from grimoire.types.common import CampaignId
from grimoire.types.context import ContextSource
from grimoire.types.inclusion_reasons import InclusionReason
from grimoire.types.state import ContextTier


class ContinuityContextResolver:
    def __init__(
        self,
        *,
        continuity: Any,
        characters: Any,
        time_engine: Any | None = None,
        config: ContextBuilderConfig,
    ) -> None:
        self._continuity = continuity
        self._characters = characters
        self._time_engine = time_engine
        self._config = config

    async def open_commitments(self, campaign_id: CampaignId) -> list[Any]:
        continuity = resolve_continuity(self._continuity, campaign_id)
        if continuity is None:
            return []
        try:
            return list(await continuity.open_commitments(limit=20))
        except Exception:
            return []

    def continuity_config(self) -> Any | None:
        target = self._continuity
        if target is None:
            return None
        return getattr(target, "config", None) or getattr(target, "_config", None)

    async def current_in_game_time(
        self,
        campaign_id: CampaignId,
        scene: Any,
    ) -> Any | None:
        when = None
        if self._time_engine is not None:
            try:
                when = await self._time_engine.current(campaign_id)
            except Exception:
                when = None
        if when is None and scene is not None:
            when = getattr(scene, "in_game_start", None)
        return when

    async def overdue_commitments(
        self,
        campaign_id: CampaignId,
        as_of: Any | None,
    ) -> list[Any]:
        config = self.continuity_config()
        if not getattr(config, "surface_overdue_in_context", True):
            return []
        if as_of is None:
            return []
        continuity = resolve_continuity(self._continuity, campaign_id)
        if continuity is None:
            return []
        fetch = getattr(continuity, "overdue_commitments", None)
        if fetch is None:
            return []
        try:
            return list(await fetch(as_of))
        except Exception:
            return []

    async def stale_commitments(self, campaign_id: CampaignId) -> list[Any]:
        config = self.continuity_config()
        if not getattr(config, "surface_stale_in_context", False):
            return []
        threshold = getattr(config, "commitment_stale_threshold", None)
        if threshold is None:
            return []
        continuity = resolve_continuity(self._continuity, campaign_id)
        if continuity is None:
            return []
        fetch = getattr(continuity, "stale_commitments", None)
        if fetch is None:
            return []
        try:
            return list(await fetch(threshold))
        except Exception:
            return []

    async def pc_refs(self, campaign_id: CampaignId) -> set[str]:
        lister = getattr(self._characters, "list_pcs", None)
        if lister is None:
            return set()
        try:
            entries = await lister(campaign_id)
        except Exception:
            return set()
        out: set[str] = set()
        for entry in entries or []:
            ref = getattr(entry, "character_ref", None) or getattr(entry, "ref", None)
            if ref:
                out.add(ref)
        return out

    def commitments_targeting_pcs(self, commitments: list[Any], pc_refs: set[str]) -> set[str]:
        if not pc_refs:
            return set()
        out: set[str] = set()
        for c in commitments:
            from_id = getattr(c, "from_id", None)
            to_id = getattr(c, "to_id", None)
            if from_id and from_id not in pc_refs and to_id in pc_refs:
                out.add(from_id)
        return out

    def render_commitments_block(
        self,
        campaign_id: CampaignId,
        commitments: list[Any],
        overdue: list[Any] | None = None,
        stale: list[Any] | None = None,
    ) -> tuple[str, ContextSource | None]:
        config = self.continuity_config()
        surface_overdue = getattr(config, "surface_overdue_in_context", True)
        surface_stale = getattr(config, "surface_stale_in_context", False)
        overdue = overdue or []
        stale = stale or []

        def _is_status_overdue(c: Any) -> bool:
            return getattr(getattr(c, "status", None), "value", "") == "overdue"

        if surface_overdue:
            display: list[Any] = list(commitments)
            seen_ids = {getattr(c, "id", "") for c in display}
            for c in overdue:
                cid = getattr(c, "id", "")
                if cid and cid in seen_ids:
                    continue
                seen_ids.add(cid)
                display.append(c)
            overdue_ids = {getattr(c, "id", "") for c in display if _is_status_overdue(c)}
            overdue_ids.update(getattr(c, "id", "") for c in overdue)
        else:
            display = [c for c in commitments if not _is_status_overdue(c)]
            overdue_ids = set()

        stale_ids: set[str] = set()
        if surface_stale and stale:
            display_ids = {getattr(c, "id", "") for c in display}
            for c in stale:
                cid = getattr(c, "id", "")
                if cid and cid in display_ids:
                    continue
                display.append(c)
                stale_ids.add(cid)

        if not display:
            return "", None
        lines: list[str] = []
        for c in display[:10]:
            text = getattr(c, "text", "") or ""
            due = getattr(c, "due_by", None)
            due_part = f" (due {due.day_count})" if due is not None else ""
            cid = getattr(c, "id", "")
            marker = ""
            if cid in stale_ids:
                marker = " [STALE]"
            elif cid in overdue_ids:
                marker = " [OVERDUE]"
            lines.append(f"- {text}{due_part}{marker}")
        block = "Active commitments:\n" + "\n".join(lines)
        source = ContextSource(
            kind="commitment",
            scope="campaign-local",
            owner_id=campaign_id,
            tier=ContextTier.LOCK_IN,
            summary=f"{len(display)} open",
            source_id=make_source_id("commitments", campaign_id),
            inclusion_reasons=[InclusionReason.COMMITMENT_OPEN_TO_PC],
        )
        return block, source

    async def continuity_background(
        self,
        campaign_id: CampaignId,
        active_pc_ref: str | None = None,
        recent_posts: list[Any] | None = None,
    ) -> list[TierItem]:
        continuity = resolve_continuity(self._continuity, campaign_id)
        if continuity is None:
            return []
        try:
            limit = self._config.recent_facts_limit
            if active_pc_ref and hasattr(continuity, "facts_known_by"):
                facts = await continuity.facts_known_by(active_pc_ref, limit=limit)
            else:
                facts = await continuity.facts_about(limit=limit)
        except Exception:
            return []
        if recent_posts and hasattr(continuity, "facts_for_terms"):
            seen_ids = {getattr(f, "id", "") for f in facts}
            try:
                terms = _proper_noun_terms(recent_posts)
                if terms:
                    extra = await continuity.facts_for_terms(terms, limit=5)
                    for f in extra:
                        fid = getattr(f, "id", "")
                        if fid and fid in seen_ids:
                            continue
                        seen_ids.add(fid)
                        facts.append(f)
            except Exception:
                pass
        if not facts:
            return []
        lines: list[str] = []
        char_cap = self._config.recent_facts_char_cap
        used = 0
        for fact in facts:
            text = (getattr(fact, "text", "") or "").strip()
            if not text:
                continue
            line = f"- {text}"
            cost = len(line) + 1
            if used + cost > char_cap:
                break
            lines.append(line)
            used += cost
        if not lines:
            return []
        block = "Recent facts:\n" + "\n".join(lines)
        return [
            TierItem(
                tier=ContextTier.BACKGROUND,
                section="facts",
                text=block,
                priority=2,
                source=ContextSource(
                    kind="fact",
                    scope="campaign-local",
                    owner_id=campaign_id,
                    tier=ContextTier.BACKGROUND,
                    summary=f"{len(lines)} facts",
                    source_id=make_source_id("facts", campaign_id),
                    inclusion_reasons=[InclusionReason.KEYWORD_TRIGGERED],
                ),
            )
        ]

    async def relationship_deltas(
        self,
        *,
        active_pc_ref: str | None,
        scene: Any,
        campaign_id: CampaignId,
    ) -> list[TierItem]:
        if not active_pc_ref or scene is None:
            return []
        getter = getattr(self._characters, "get_relationship_history", None)
        if getter is None:
            return []
        present = list(getattr(scene, "present_character_refs", []) or [])
        lines: list[str] = []
        for other in present:
            if other == active_pc_ref:
                continue
            try:
                history = await getter(active_pc_ref, other, campaign_id)
            except Exception:
                history = []
            if not history:
                continue
            event = history[-1]
            summary = (
                event.get("summary") if isinstance(event, dict) else getattr(event, "summary", "")
            )
            delta = event.get("delta") if isinstance(event, dict) else getattr(event, "delta", {})
            if not summary and not delta:
                continue
            delta_str = _format_delta(delta or {})
            text_parts = []
            if delta_str:
                text_parts.append(delta_str)
            if summary:
                text_parts.append(str(summary))
            line = f"- {active_pc_ref} ↔ {other}: " + " — ".join(text_parts)
            lines.append(line)
        if not lines:
            return []
        block = "Relationship deltas since last scene:\n" + "\n".join(lines)
        return [
            TierItem(
                tier=ContextTier.BACKGROUND,
                section="relationship_deltas",
                text=block,
                priority=4,
                source=ContextSource(
                    kind="relationship",
                    scope="campaign-local",
                    owner_id=campaign_id,
                    tier=ContextTier.BACKGROUND,
                    summary=f"{len(lines)} deltas",
                    source_id=make_source_id("relationship_deltas", campaign_id),
                    inclusion_reasons=[InclusionReason.RELATIONSHIP_TO_PRESENT],
                ),
            )
        ]


# ------------------------------------------------------------------ #
# Module-level helpers
# ------------------------------------------------------------------ #

_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-zA-Z]{2,}\b")


def _proper_noun_terms(recent_posts: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for post in recent_posts:
        text = getattr(post, "content", "") or getattr(post, "text", "")
        if not isinstance(text, str):
            continue
        for match in _PROPER_NOUN_RE.findall(text):
            if match not in seen:
                seen.add(match)
                out.append(match)
    return out


def _format_delta(delta: dict) -> str:
    if not delta:
        return ""
    parts: list[str] = []
    for key in ("affection", "trust", "dominance", "intimacy"):
        if key not in delta:
            continue
        try:
            val = int(delta[key])
        except (TypeError, ValueError):
            continue
        sign = "+" if val >= 0 else ""
        parts.append(f"{key} {sign}{val}")
    return ", ".join(parts)
