"""Prompt composition tests."""

from __future__ import annotations

from types import SimpleNamespace

from grimoire.imagegen.prompt import (
    PromptComposer,
    compose_negative_prompt,
    compose_prompt_parts,
    extract_visual_elements,
)


def test_compose_prompt_parts_drops_blanks_and_preserves_order() -> None:
    parts = compose_prompt_parts(
        preset_preamble="oil painting, dramatic lighting",
        location_description="a misty Soho alleyway",
        character_prompts=["bowler hat, walking stick", "", "auburn hair"],
        scene_elements=["rain on cobblestones"],
        mood="ominous",
    )
    assert parts == [
        "oil painting, dramatic lighting",
        "a misty Soho alleyway",
        "bowler hat, walking stick",
        "auburn hair",
        "rain on cobblestones",
        "ominous",
    ]


def test_compose_negative_prompt_joins_with_commas() -> None:
    assert compose_negative_prompt(
        preset_negative="blurry, low quality",
        character_negatives=["modern clothing", ""],
    ) == "blurry, low quality, modern clothing"


def test_extract_visual_elements_picks_sensory_sentences() -> None:
    body = (
        "Alistair stood at the window, watching the rain. He sighed. "
        "Her dress was crimson silk."
    )
    out = extract_visual_elements(body)
    assert any("rain" in s for s in out)
    assert any("crimson" in s for s in out) or any("dress" in s for s in out)


async def test_prompt_composer_uses_all_providers() -> None:
    preset = SimpleNamespace(
        frontmatter={
            "style_preamble": "watercolor",
            "negative_prompt": "blurry",
            "default_params": {"width": 768, "height": 768, "steps": 24},
        }
    )

    class FakeLibrary:
        async def get_image_preset(self, id: str):
            assert id == "noir-preset"
            return preset

    class FakeScene:
        def __init__(self) -> None:
            self.location_ref = "loc-1"
            self.mood = "tense"
            self.present_character_refs = ["char-1"]

    class FakeSceneManager:
        async def get_scene(self, scene_id: str):
            assert scene_id == "scene-1"
            return FakeScene()

    class FakeSetting:
        async def resolve(self, ref: str, campaign_id: str):
            return SimpleNamespace(frontmatter={"visual_description": "a Soho alley"})

    class FakeCharacters:
        async def resolve(self, ref: str, campaign_id: str):
            return SimpleNamespace(
                character=SimpleNamespace(
                    image=SimpleNamespace(base_prompt="bowler hat", negative_prompt="cartoon")
                )
            )

    composer = PromptComposer(
        scene_manager=FakeSceneManager(),
        library=FakeLibrary(),
        setting=FakeSetting(),
        characters=FakeCharacters(),
    )
    out = await composer.compose(
        campaign_id="camp-1",
        scene_id="scene-1",
        post_body="The rain fell on the cobblestones.",
        image_preset_id="noir-preset",
    )
    assert "watercolor" in out.prompt
    assert "Soho alley" in out.prompt
    assert "bowler hat" in out.prompt
    assert "tense" in out.prompt
    assert "blurry" in out.negative_prompt
    assert "cartoon" in out.negative_prompt
    assert out.params["width"] == 768


async def test_prompt_composer_handles_missing_providers() -> None:
    composer = PromptComposer()
    out = await composer.compose(campaign_id="camp-1")
    assert out.prompt == ""
    assert out.negative_prompt == ""
    assert out.params == {}
    assert out.parts == []
