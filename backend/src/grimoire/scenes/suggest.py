"""Scene suggestion engine: assembles context and generates ideas via LLM."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from grimoire.scenes.ledger import SceneLedger
from grimoire.types.llm import CompletionRequest, Message, MessageRole

logger = logging.getLogger(__name__)

_MAX_LEDGER_PICKS = 3
_MIN_GENERATED = 2
_TOTAL_SUGGESTIONS = 5

_SUGGEST_SYSTEM = (
    "You are a narrative assistant for a tabletop RPG campaign. Given the "
    "campaign context below, generate scene suggestions for what could happen "
    "next.\n\n"
    "Each suggestion is a single sentence describing a scene hook. Include a "
    "proposed_location (where it takes place) and proposed_cast (list of "
    "character names likely involved). Return a JSON array of objects with "
    "keys: summary, proposed_location, proposed_cast.\n\n"
    "Generate diverse suggestions: some advancing the main plot, some "
    "exploring character relationships, some introducing new complications."
)


@dataclass
class SuggestionContext:
    campaign_id: str
    recent_summaries: list[str]
    open_threads: list[str]
    active_pcs: list[str]
    last_location: str | None
    in_game_time: str | None
    unused_greeting_names: list[str]
    num_to_generate: int = _MIN_GENERATED


class SceneSuggestionEngine:
    def __init__(self, *, ledger: SceneLedger, gateway: object) -> None:
        self._ledger = ledger
        self._gateway = gateway

    async def suggest(self, ctx: SuggestionContext) -> dict:
        active = await self._ledger.list_active(ctx.campaign_id)
        ledger_picks = active[:_MAX_LEDGER_PICKS]

        num_to_generate = max(_MIN_GENERATED, _TOTAL_SUGGESTIONS - len(ledger_picks))
        ctx = SuggestionContext(
            campaign_id=ctx.campaign_id,
            recent_summaries=ctx.recent_summaries,
            open_threads=ctx.open_threads,
            active_pcs=ctx.active_pcs,
            last_location=ctx.last_location,
            in_game_time=ctx.in_game_time,
            unused_greeting_names=ctx.unused_greeting_names,
            num_to_generate=num_to_generate,
        )

        generated: list[dict] = []
        raw_response: str | None = None
        try:
            prompt = self._build_prompt(ctx)
            logger.info("scene_suggest prompt:\n%s", prompt)
            request = CompletionRequest(
                model="default",
                messages=[
                    Message(role=MessageRole.USER, content=prompt),
                ],
                system=_SUGGEST_SYSTEM,
                max_tokens=1024,
                temperature=1.0,
            )
            response = await self._gateway.complete(
                "scene_suggest", request, campaign_id=ctx.campaign_id
            )
            raw_response = response.text
            logger.info("scene_suggest response:\n%s", raw_response[:2000])
            cleaned = re.sub(r"^```(?:json)?\s*", "", raw_response.strip())
            cleaned = re.sub(r"\s*```$", "", cleaned)
            parsed = json.loads(cleaned)
            if isinstance(parsed, list):
                generated = parsed
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "Failed to parse suggestion response:\n%s",
                raw_response[:2000] if raw_response else "(no response)",
            )
        except Exception:
            logger.warning("LLM suggestion generation failed", exc_info=True)

        return {
            "ledger_picks": [
                {
                    "ledger_id": item["id"],
                    "summary": item["summary"],
                    "greeting_id": item.get("greeting_id"),
                    "source": item["source"],
                }
                for item in ledger_picks
            ],
            "generated": [
                {
                    "summary": g.get("summary", ""),
                    "proposed_location": g.get("proposed_location"),
                    "proposed_cast": g.get("proposed_cast", []),
                }
                for g in generated
                if isinstance(g, dict)
            ],
        }

    def _build_prompt(self, ctx: SuggestionContext) -> str:
        parts = [f"Generate {ctx.num_to_generate} scene suggestions.\n"]
        if ctx.recent_summaries:
            parts.append("Recent scenes:")
            for s in ctx.recent_summaries:
                parts.append(f"- {s}")
        if ctx.open_threads:
            parts.append("\nOpen threads:")
            for t in ctx.open_threads:
                parts.append(f"- {t}")
        if ctx.active_pcs:
            parts.append(f"\nActive PCs: {', '.join(ctx.active_pcs)}")
        if ctx.last_location:
            parts.append(f"Last location: {ctx.last_location}")
        if ctx.in_game_time:
            parts.append(f"In-game time: {ctx.in_game_time}")
        if ctx.unused_greeting_names:
            parts.append("\nUnused greeting hooks (available for inspiration):")
            for name in ctx.unused_greeting_names:
                parts.append(f"- {name}")
        return "\n".join(parts)
