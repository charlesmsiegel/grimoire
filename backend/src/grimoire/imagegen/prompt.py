"""Prompt composition for ImageGen.

Spec 12 §Prompt composition: builds the prompt from an image preset, the
current location, present cast (each contributing an ``image.base_prompt``
template), scene-specific visual elements (typically pulled from the most
recent post), and mood/atmosphere.

The composer is dependency-injection friendly so tests can drop in fakes:
every collaborator (scene manager, library, characters, setting) is just
called through ``Protocol`` shapes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from grimoire.templates import render as render_template


@dataclass(frozen=True)
class ComposedPrompt:
    prompt: str
    negative_prompt: str
    params: dict[str, Any]
    parts: list[str]


class _SceneProvider(Protocol):
    async def get_scene(self, scene_id: str) -> Any: ...


class _LibraryProvider(Protocol):
    async def get_image_preset(self, id: str) -> Any: ...


class _SettingProvider(Protocol):
    async def resolve(self, entity_ref: str, campaign_id: str) -> Any: ...


class _CharactersProvider(Protocol):
    async def resolve(self, character_ref: str, campaign_id: str) -> Any: ...


def compose_prompt_parts(
    *,
    preset_preamble: str = "",
    location_description: str = "",
    character_prompts: list[str] | None = None,
    scene_elements: list[str] | None = None,
    mood: str = "",
) -> list[str]:
    """Spec 12 §Prompt composition ordering.

    Returns the ordered, non-empty fragments that go into the positive
    prompt. The final string form is produced by the
    ``imagegen_positive`` Jinja template (see :func:`compose_prompt`),
    which can re-shape the fragments if a modder ships their own variant.
    """
    parts: list[str] = []
    if preset_preamble:
        parts.append(preset_preamble.strip())
    if location_description:
        parts.append(location_description.strip())
    for char_prompt in character_prompts or []:
        if char_prompt and char_prompt.strip():
            parts.append(char_prompt.strip())
    for element in scene_elements or []:
        if element and element.strip():
            parts.append(element.strip())
    if mood:
        parts.append(mood.strip())
    return parts


def compose_prompt(
    *,
    preset_preamble: str = "",
    location_description: str = "",
    character_prompts: list[str] | None = None,
    scene_elements: list[str] | None = None,
    mood: str = "",
) -> str:
    """Render the positive image prompt via the ``imagegen_positive`` template."""
    return render_template(
        "imagegen_positive",
        preset_preamble=preset_preamble,
        location_description=location_description,
        character_prompts=character_prompts or [],
        scene_elements=scene_elements or [],
        mood=mood,
    )


def compose_negative_prompt(
    *,
    preset_negative: str = "",
    character_negatives: list[str] | None = None,
) -> str:
    return render_template(
        "imagegen_negative",
        preset_negative=preset_negative,
        character_negatives=character_negatives or [],
    )


_SENTENCE_SPLIT = re.compile(r"[.!?]\s+")


def extract_visual_elements(text: str, *, max_elements: int = 3) -> list[str]:
    """Heuristic extraction of visual sentences from a post body.

    Pulls the first ``max_elements`` sentences that contain at least one
    sensory verb/keyword. Cheap fallback when no LLM extraction is wired
    up — the Extractor module can later supply richer hints.
    """
    if not text:
        return []
    keywords = (
        "see",
        "saw",
        "look",
        "watch",
        "stand",
        "stood",
        "wear",
        "wore",
        "dress",
        "robe",
        "coat",
        "suit",
        "silk",
        "leather",
        "light",
        "shadow",
        "glow",
        "dark",
        "bright",
        "smile",
        "frown",
        "face",
        "rain",
        "sun",
        "moon",
        "blood",
        "fire",
    )
    sentences = _SENTENCE_SPLIT.split(text.strip())
    out: list[str] = []
    for sentence in sentences:
        clean = sentence.strip().rstrip(".!?")
        if not clean:
            continue
        low = clean.lower()
        if any(kw in low for kw in keywords):
            out.append(clean)
            if len(out) >= max_elements:
                break
    return out


class PromptComposer:
    """Per-campaign prompt composer.

    Each collaborator is optional — when ``None`` the corresponding part
    is simply skipped. This lets the Orchestrator use the composer before
    every module is wired up (e.g. early scene-only illustrations).
    """

    def __init__(
        self,
        *,
        scene_manager: _SceneProvider | None = None,
        library: _LibraryProvider | None = None,
        setting: _SettingProvider | None = None,
        characters: _CharactersProvider | None = None,
    ) -> None:
        self.scene_manager = scene_manager
        self.library = library
        self.setting = setting
        self.characters = characters

    async def compose(
        self,
        *,
        campaign_id: str,
        scene_id: str | None = None,
        post_body: str | None = None,
        image_preset_id: str | None = None,
        extra_elements: list[str] | None = None,
    ) -> ComposedPrompt:
        preset_preamble = ""
        preset_negative = ""
        preset_params: dict[str, Any] = {}
        if image_preset_id and self.library is not None:
            preset = await self.library.get_image_preset(image_preset_id)
            fm = getattr(preset, "frontmatter", None) or {}
            preset_preamble = str(fm.get("style_preamble") or fm.get("preamble") or "")
            preset_negative = str(fm.get("negative_prompt") or "")
            preset_params = dict(fm.get("default_params") or {})

        location_description = ""
        scene_present_refs: list[str] = []
        scene_mood = ""
        if scene_id and self.scene_manager is not None:
            scene = await self.scene_manager.get_scene(scene_id)
            scene_present_refs = list(getattr(scene, "present_character_refs", []) or [])
            scene_mood = str(getattr(scene, "mood", "") or "")
            location_ref = getattr(scene, "location_ref", None)
            if location_ref and self.setting is not None:
                try:
                    resolved = await self.setting.resolve(location_ref, campaign_id)
                except Exception:
                    resolved = None
                if resolved is not None:
                    fm = getattr(resolved, "frontmatter", None) or {}
                    location_description = str(
                        fm.get("visual_description")
                        or fm.get("description")
                        or getattr(resolved, "body", "")
                        or ""
                    )

        character_prompts: list[str] = []
        character_negatives: list[str] = []
        if scene_present_refs and self.characters is not None:
            for ref in scene_present_refs:
                try:
                    resolved = await self.characters.resolve(ref, campaign_id)
                except Exception:
                    continue
                char = getattr(resolved, "character", None) or resolved
                image = getattr(char, "image", None)
                if image is None:
                    continue
                base = getattr(image, "base_prompt", "") or ""
                negative = getattr(image, "negative_prompt", "") or ""
                if base:
                    character_prompts.append(base)
                if negative:
                    character_negatives.append(negative)

        scene_elements: list[str] = []
        if extra_elements:
            scene_elements.extend(extra_elements)
        if post_body:
            scene_elements.extend(extract_visual_elements(post_body))

        parts = compose_prompt_parts(
            preset_preamble=preset_preamble,
            location_description=location_description,
            character_prompts=character_prompts,
            scene_elements=scene_elements,
            mood=scene_mood,
        )
        prompt = compose_prompt(
            preset_preamble=preset_preamble,
            location_description=location_description,
            character_prompts=character_prompts,
            scene_elements=scene_elements,
            mood=scene_mood,
        )
        negative = compose_negative_prompt(
            preset_negative=preset_negative,
            character_negatives=character_negatives,
        )

        return ComposedPrompt(
            prompt=prompt,
            negative_prompt=negative,
            params=preset_params,
            parts=parts,
        )
