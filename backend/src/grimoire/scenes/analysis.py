"""Bundled scene analysis: summary + extraction in one LLM pass.

Produces a summary, key beats, facts, commitments, entity candidates,
and threads from the full scene text in a single (or windowed) LLM call.
Results route through the existing extraction/review infrastructure.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, Protocol

from pydantic import BaseModel, Field

from grimoire.scenes.types import Post, Scene, Thread
from grimoire.types.common import CampaignId
from grimoire.types.extraction import ExtractionResult
from grimoire.types.llm import CompletionRequest, Message, MessageRole
from grimoire.util import extract_json_object

PayloadParser = Callable[..., Any]
SchemaFactory = Callable[[], dict]

logger = logging.getLogger(__name__)

_DEFAULT_TASK = "scene_analysis"
_DEFAULT_MAX_TOKENS = 4096
_FALLBACK_CONTEXT_WINDOW = 100_000


class SceneAnalysisResult(BaseModel):
    summary: str = ""
    key_beats: list[str] = Field(default_factory=list)
    threads_introduced: list[Thread] = Field(default_factory=list)
    threads_paid_off: list[Thread] = Field(default_factory=list)
    extraction: ExtractionResult = Field(default_factory=ExtractionResult)


class _Gateway(Protocol):
    async def complete(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: str | None = None,
        *,
        turn_id: str | None = None,
    ) -> object: ...


class _AdaptiveGateway(Protocol):
    async def complete(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: str | None = None,
        *,
        turn_id: str | None = None,
    ) -> object: ...

    def resolve_route(self, task: str, campaign_id: str | None = None) -> object: ...

    async def get_model_info(self, provider_id: str, model: str) -> object | None: ...


def analysis_schema(extraction_schema_fn: SchemaFactory) -> dict:
    """JSON schema for the bundled analysis LLM output.

    Extends the extraction schema with summary, key_beats, and threads.
    """
    extraction = extraction_schema_fn()
    extraction["properties"]["summary"] = {"type": "string"}
    extraction["properties"]["key_beats"] = {
        "type": "array",
        "items": {"type": "string"},
    }
    thread = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "text": {"type": "string"},
            "status": {"type": "string", "enum": ["introduced", "paid_off"]},
            "at_post": {"type": ["integer", "null"]},
        },
        "required": ["text", "status"],
    }
    extraction["properties"]["threads"] = {
        "type": "array",
        "items": thread,
    }
    return extraction


def _post_window(posts: list[Post], n: int | None = None) -> str:
    target = posts if n is None else posts[-n:]
    parts: list[str] = []
    for p in target:
        parts.append(f"[Post {p.order_in_scene} — {p.author_label}]\n{p.body.strip()}")
    return "\n\n".join(parts)


def _build_system_prompt(schema: dict) -> str:
    schema_json = json.dumps(schema, separators=(",", ":"))
    return (
        "You are a scene analyzer for a tabletop RPG companion. "
        "Read the full scene and return a single JSON object containing:\n"
        "1. A concise narrative summary (3-5 sentences)\n"
        "2. Up to 5 key beats that drove the scene\n"
        "3. Narrative threads introduced or paid off\n"
        "4. Extracted facts, commitments, entity candidates, and state changes\n\n"
        "Confidences are numbers in [0, 1]. All new entities are campaign-local. "
        "Output JSON only — no commentary, no markdown fences.\n"
        f"Schema: {schema_json}"
    )


def _build_user_prompt(
    scene: Scene,
    posts: list[Post],
    running_summary: str | None = None,
) -> str:
    running_block = (running_summary or "(none)").strip()
    posts_text = _post_window(posts)
    return (
        f"Scene title: {scene.title or scene.slug}\n"
        f"Running summary so far: {running_block}\n\n"
        f"Full scene posts:\n{posts_text}\n\n"
        "Return the JSON analysis."
    )


def _parse_threads(raw_threads: list) -> tuple[list[Thread], list[Thread]]:
    introduced: list[Thread] = []
    paid_off: list[Thread] = []
    for item in raw_threads:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        status = str(item.get("status", "introduced"))
        at_post = item.get("at_post")
        thread = Thread(
            text=text,
            introduced_at_post=at_post if status == "introduced" else None,
            paid_off_at_post=at_post if status == "paid_off" else None,
        )
        if status == "paid_off":
            paid_off.append(thread)
        else:
            introduced.append(thread)
    return introduced, paid_off


def _parse_analysis_response(
    payload: dict,
    *,
    campaign_id: CampaignId,
    payload_parser: PayloadParser,
    source: str = "scene_analysis",
    max_key_beats: int = 5,
    max_new_entities: int = 10,
) -> SceneAnalysisResult:
    """Convert a raw JSON analysis payload into a typed result."""
    summary = str(payload.get("summary") or "").strip()
    beats_raw = payload.get("key_beats") or []
    beats = [str(b).strip() for b in beats_raw if isinstance(b, (str, int))]
    beats = [b for b in beats if b][:max_key_beats]

    raw_threads = payload.get("threads") or []
    introduced, paid_off = _parse_threads(raw_threads)

    llm_output = payload_parser(
        payload,
        campaign_id=campaign_id,
        source=source,
        max_new_entities=max_new_entities,
    )

    extraction = ExtractionResult(
        deltas=llm_output.deltas,
        candidates=llm_output.candidates,
        flags=llm_output.flags,
        transient_updates=llm_output.transient_updates,
        cast_changes=llm_output.cast_changes,
        confidence_overall=llm_output.confidence_avg,
        extraction_strategies_run=["scene_analysis"],
    )

    return SceneAnalysisResult(
        summary=summary,
        key_beats=beats,
        threads_introduced=introduced,
        threads_paid_off=paid_off,
        extraction=extraction,
    )


def make_scene_analyzer(
    gateway: _Gateway,
    *,
    extraction_schema_fn: SchemaFactory,
    payload_parser: PayloadParser,
    task: str = _DEFAULT_TASK,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    model: str = "default",
    max_key_beats: int = 5,
    max_new_entities: int = 10,
):
    """Build the single-pass scene analyzer callable.

    Returns an async callable matching the SceneAnalyzer type alias:
    ``(Scene, list[Post], CampaignId) -> SceneAnalysisResult``.
    """
    schema = analysis_schema(extraction_schema_fn)

    async def _analyze(
        scene: Scene,
        posts: list[Post],
        campaign_id: CampaignId,
    ) -> SceneAnalysisResult:
        if not posts:
            return SceneAnalysisResult(summary=scene.running_summary or "")

        system = _build_system_prompt(schema)
        user = _build_user_prompt(scene, posts, scene.running_summary)

        request = CompletionRequest(
            model=model,
            messages=[Message(role=MessageRole.USER, content=user)],
            system=system,
            max_tokens=max_tokens,
            temperature=0.3,
        )
        try:
            response = await gateway.complete(task, request, campaign_id=campaign_id)
        except Exception as exc:
            logger.warning("scene analysis LLM call failed: %s", exc)
            return SceneAnalysisResult()

        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            return SceneAnalysisResult()

        parsed = extract_json_object(text)
        if parsed is None:
            return SceneAnalysisResult()

        return _parse_analysis_response(
            parsed,
            campaign_id=campaign_id,
            payload_parser=payload_parser,
            max_key_beats=max_key_beats,
            max_new_entities=max_new_entities,
        )

    return _analyze


def make_adaptive_scene_analyzer(
    gateway: _AdaptiveGateway,
    *,
    extraction_schema_fn: SchemaFactory,
    payload_parser: PayloadParser,
    task: str = _DEFAULT_TASK,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    model: str = "default",
    max_key_beats: int = 5,
    max_new_entities: int = 10,
):
    """Build a scene analyzer that adapts between single-pass and windowed mode.

    For scenes fitting in half the context window, runs a single analysis pass.
    For longer scenes, processes windows with rolling summaries and extraction,
    then consolidates into a final result.
    """
    schema = analysis_schema(extraction_schema_fn)

    async def _get_context_window(campaign_id: CampaignId | None = None) -> int:
        try:
            route = gateway.resolve_route(task, campaign_id)
            info = await gateway.get_model_info(route.provider_id, route.model)
            if info is not None:
                cw = getattr(info, "context_window", 0) or 0
                if cw > 0:
                    return cw
        except Exception:
            pass
        return _FALLBACK_CONTEXT_WINDOW

    async def _single_pass(
        scene: Scene,
        posts: list[Post],
        campaign_id: CampaignId,
    ) -> SceneAnalysisResult:
        system = _build_system_prompt(schema)
        user = _build_user_prompt(scene, posts, scene.running_summary)

        request = CompletionRequest(
            model=model,
            messages=[Message(role=MessageRole.USER, content=user)],
            system=system,
            max_tokens=max_tokens,
            temperature=0.3,
        )
        try:
            response = await gateway.complete(task, request, campaign_id=campaign_id)
        except Exception as exc:
            logger.warning("scene analysis LLM call failed: %s", exc)
            return SceneAnalysisResult()

        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            return SceneAnalysisResult()

        parsed = extract_json_object(text)
        if parsed is None:
            return SceneAnalysisResult()

        return _parse_analysis_response(
            parsed,
            campaign_id=campaign_id,
            payload_parser=payload_parser,
            max_key_beats=max_key_beats,
            max_new_entities=max_new_entities,
        )

    async def _rolling_window(
        previous_summary: str | None,
        window_posts: list[Post],
        scene: Scene,
        campaign_id: CampaignId,
    ) -> tuple[str, ExtractionResult]:
        """Analyze one window: produce rolling summary + extraction."""
        system = (
            "You are a scene analyzer for a tabletop RPG companion. "
            "Analyze this segment of a scene and return JSON with:\n"
            '1. "summary": updated rolling summary (3-5 sentences)\n'
            "2. All extraction fields (facts, commitments, new entities, etc.)\n\n"
            "Confidences are numbers in [0, 1]. Output JSON only.\n"
            f"Schema: {json.dumps(schema, separators=(',', ':'))}"
        )
        previous_block = (previous_summary or "(no prior summary)").strip()
        posts_text = _post_window(window_posts)
        user = (
            f"Scene title: {scene.title or scene.slug}\n"
            f"Summary so far: {previous_block}\n\n"
            f"Scene segment:\n{posts_text}\n\n"
            "Return the JSON analysis for this segment."
        )
        request = CompletionRequest(
            model=model,
            messages=[Message(role=MessageRole.USER, content=user)],
            system=system,
            max_tokens=max_tokens,
            temperature=0.3,
        )
        try:
            response = await gateway.complete(task, request, campaign_id=campaign_id)
        except Exception as exc:
            logger.warning("windowed analysis LLM call failed: %s", exc)
            return previous_summary or "", ExtractionResult()

        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            return previous_summary or "", ExtractionResult()

        parsed = extract_json_object(text)
        if parsed is None:
            return previous_summary or "", ExtractionResult()

        window_summary = str(parsed.get("summary") or previous_summary or "").strip()
        llm_output = payload_parser(
            parsed,
            campaign_id=campaign_id,
            source="scene_analysis:window",
            max_new_entities=max_new_entities,
        )
        window_extraction = ExtractionResult(
            deltas=llm_output.deltas,
            candidates=llm_output.candidates,
            flags=llm_output.flags,
            transient_updates=llm_output.transient_updates,
            confidence_overall=llm_output.confidence_avg,
            extraction_strategies_run=["scene_analysis:window"],
        )
        return window_summary, window_extraction

    async def _final_consolidation(
        scene: Scene,
        posts: list[Post],
        accumulated_summary: str,
        campaign_id: CampaignId,
    ) -> tuple[str, list[str], list[Thread], list[Thread]]:
        """Final pass: produce summary, key_beats, and threads from accumulated context."""
        system = (
            "You are a scene close-out summarizer for a tabletop RPG companion. "
            "Given the accumulated summary and full scene context, return JSON with:\n"
            '1. "summary": final summary (3-5 sentences)\n'
            f'2. "key_beats": up to {max_key_beats} key beats\n'
            '3. "threads": narrative threads '
            '[{{"text": "...", "status": "introduced|paid_off"}}]\n\n'
            "Output JSON only."
        )
        user = (
            f"Scene title: {scene.title or scene.slug}\n"
            f"Accumulated summary: {accumulated_summary}\n"
            f"Post count: {len(posts)}\n\n"
            "Return the final analysis JSON."
        )
        request = CompletionRequest(
            model=model,
            messages=[Message(role=MessageRole.USER, content=user)],
            system=system,
            max_tokens=max_tokens,
            temperature=0.3,
        )
        try:
            response = await gateway.complete(task, request, campaign_id=campaign_id)
        except Exception as exc:
            logger.warning("final consolidation LLM call failed: %s", exc)
            return accumulated_summary, [], [], []

        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            return accumulated_summary, [], [], []

        parsed = extract_json_object(text)
        if parsed is None:
            return accumulated_summary, [], [], []

        summary = str(parsed.get("summary") or accumulated_summary).strip()
        beats_raw = parsed.get("key_beats") or []
        beats = [str(b).strip() for b in beats_raw if isinstance(b, (str, int))]
        beats = [b for b in beats if b][:max_key_beats]
        raw_threads = parsed.get("threads") or []
        introduced, paid_off = _parse_threads(raw_threads)
        return summary, beats, introduced, paid_off

    def _merge_extractions(results: list[ExtractionResult]) -> ExtractionResult:
        """Merge multiple windowed extraction results."""
        all_deltas = []
        all_candidates = []
        all_flags = []
        all_transient = []
        all_strategies: set[str] = set()
        for r in results:
            all_deltas.extend(r.deltas)
            all_candidates.extend(r.candidates)
            all_flags.extend(r.flags)
            all_transient.extend(r.transient_updates)
            all_strategies.update(r.extraction_strategies_run)
        confidence = sum(d.confidence for d in all_deltas) / len(all_deltas) if all_deltas else 0.0
        return ExtractionResult(
            deltas=all_deltas,
            candidates=all_candidates[:max_new_entities],
            flags=all_flags,
            transient_updates=all_transient,
            confidence_overall=confidence,
            extraction_strategies_run=sorted(all_strategies),
        )

    async def _adaptive(
        scene: Scene,
        posts: list[Post],
        campaign_id: CampaignId,
    ) -> SceneAnalysisResult:
        if not posts:
            return SceneAnalysisResult(summary=scene.running_summary or "")

        context_window = await _get_context_window(campaign_id)
        total_chars = sum(len(p.body) for p in posts)
        total_tokens_est = total_chars // 4
        budget = context_window // 2

        if total_tokens_est <= budget:
            return await _single_pass(scene, posts, campaign_id)

        window_chars = budget * 4
        windows: list[list[Post]] = []
        current_window: list[Post] = []
        current_chars = 0
        for p in posts:
            if current_chars + len(p.body) > window_chars and current_window:
                windows.append(current_window)
                current_window = []
                current_chars = 0
            current_window.append(p)
            current_chars += len(p.body)
        if current_window:
            windows.append(current_window)

        rolling_summary = scene.running_summary
        window_extractions: list[ExtractionResult] = []

        for window in windows:
            rolling_summary, extraction = await _rolling_window(
                rolling_summary, window, scene, campaign_id
            )
            window_extractions.append(extraction)

        summary, beats, introduced, paid_off = await _final_consolidation(
            scene, posts, rolling_summary or "", campaign_id
        )

        merged_extraction = _merge_extractions(window_extractions)

        return SceneAnalysisResult(
            summary=summary,
            key_beats=beats,
            threads_introduced=introduced,
            threads_paid_off=paid_off,
            extraction=merged_extraction,
        )

    return _adaptive


__all__ = [
    "SceneAnalysisResult",
    "analysis_schema",
    "make_adaptive_scene_analyzer",
    "make_scene_analyzer",
]
