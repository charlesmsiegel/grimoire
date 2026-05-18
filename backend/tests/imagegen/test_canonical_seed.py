"""§11 Per-character canonical seed."""

from __future__ import annotations

from grimoire.imagegen import PromptComposer


class _CharImage:
    def __init__(self, base_prompt: str = "", canonical_seed=None) -> None:
        self.base_prompt = base_prompt
        self.negative_prompt = ""
        self.canonical_seed = canonical_seed


class _Resolved:
    def __init__(self, base_prompt: str = "", canonical_seed=None) -> None:
        self.character = type("Char", (), {"image": _CharImage(base_prompt, canonical_seed)})()


class _Characters:
    def __init__(self, refs: dict) -> None:
        self._refs = refs

    async def resolve(self, ref, campaign_id):
        return self._refs[ref]


class _Scene:
    def __init__(self, present: list[str]) -> None:
        self.present_character_refs = present
        self.mood = ""
        self.location_ref = None


class _SceneManager:
    def __init__(self, present: list[str]) -> None:
        self._scene = _Scene(present)

    async def get_scene(self, scene_id):
        return self._scene


async def test_canonical_seed_returned_for_single_character() -> None:
    composer = PromptComposer(
        scene_manager=_SceneManager(["vivienne"]),
        characters=_Characters({"vivienne": _Resolved("a redhead", 1234)}),
    )
    result = await composer.compose(campaign_id="c", scene_id="s")
    assert result.params.get("seed") == 1234


async def test_canonical_seed_xor_combiner_when_multiple() -> None:
    composer = PromptComposer(
        scene_manager=_SceneManager(["vivienne", "alice"]),
        characters=_Characters(
            {
                "vivienne": _Resolved("a redhead", 1234),
                "alice": _Resolved("a tall woman", 5),
            }
        ),
    )
    result = await composer.compose(campaign_id="c", scene_id="s")
    assert result.params.get("seed") == ((1234 ^ 5) & 0x7FFFFFFF)


async def test_no_canonical_seed_omits_seed_key() -> None:
    composer = PromptComposer(
        scene_manager=_SceneManager(["bob"]),
        characters=_Characters({"bob": _Resolved("a man", None)}),
    )
    result = await composer.compose(campaign_id="c", scene_id="s")
    assert "seed" not in result.params
