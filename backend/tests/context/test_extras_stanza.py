"""Context Builder: narrative-extras spotlight stanza.

Mirrors the stub-driven style of ``test_builder.py`` -- we hand the builder
a small ``StubLibrary`` whose ``resolve(ref, campaign_id)`` returns a
frontmatter dict so the extras tier item renders without touching the
filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from grimoire.context import ContextBuilderConfig, ContextBuilderService

# Import stubs from the sibling test module to mirror its conventions.
from tests.context.test_builder import (  # type: ignore[import-not-found]
    StubCharacters,
    StubContinuity,
    StubLibrary,
    StubScenes,
    StubWorld,
    _Card,
    _Scene,
    _WorldMeta,
)


@dataclass
class _Resolved:
    name: str
    frontmatter: dict[str, Any]


class _LibraryWithExtras(StubLibrary):
    """Extends the existing stub library with a ``resolve()`` that returns
    frontmatter the extras stanza can read."""

    def __init__(
        self,
        *,
        extras_by_ref: dict[str, dict[str, Any]] | None = None,
        names_by_ref: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._extras = extras_by_ref or {}
        self._names = names_by_ref or {}

    async def resolve(self, entity_id: str, campaign_id: str) -> _Resolved:
        return _Resolved(
            name=self._names.get(entity_id, entity_id),
            frontmatter={"extras": self._extras.get(entity_id, {})},
        )


def _builder(**overrides: Any) -> ContextBuilderService:
    defaults: dict[str, Any] = {
        "library": _LibraryWithExtras(),
        "characters": StubCharacters(),
        "world": StubWorld(),
        "scenes": StubScenes(),
        "continuity": StubContinuity(),
    }
    defaults.update(overrides)
    return ContextBuilderService(**defaults)


winifred = "library:worlds/wod/characters/winifred"


async def test_extras_stanza_appears_in_spotlight_for_present_character():
    library = _LibraryWithExtras(
        extras_by_ref={
            winifred: {
                "smokes": "Sobranie Black Russians",
                "scars": ["above brow"],
            }
        },
        names_by_ref={winifred: "winifred Allard"},
        worlds={"wod": _WorldMeta(id="wod", name="WoD London")},
    )
    chars = StubCharacters(cards={winifred: _Card(full="# winifred\nFull card")})
    scenes = StubScenes(scene=_Scene(present_character_refs=[winifred]))

    builder = _builder(library=library, characters=chars, scenes=scenes)
    prompt = await builder.build("hello", "camp")
    blob = "\n".join(m.content for m in prompt.messages)
    assert "winifred Allard — extras:" in blob
    assert "smokes: Sobranie Black Russians" in blob
    assert "scars: above brow" in blob


async def test_extras_stanza_skipped_when_no_extras():
    library = _LibraryWithExtras(
        extras_by_ref={winifred: {}},
        names_by_ref={winifred: "winifred Allard"},
        worlds={"wod": _WorldMeta(id="wod", name="WoD London")},
    )
    chars = StubCharacters(cards={winifred: _Card(full="# winifred\nFull card")})
    scenes = StubScenes(scene=_Scene(present_character_refs=[winifred]))

    builder = _builder(library=library, characters=chars, scenes=scenes)
    prompt = await builder.build("hello", "camp")
    blob = "\n".join(m.content for m in prompt.messages)
    assert "extras:" not in blob


async def test_extras_demotes_to_breadcrumb_on_overflow():
    # Pack the extras with enough text to blow past the breadcrumb threshold.
    big_value = "x" * 2_000
    library = _LibraryWithExtras(
        extras_by_ref={winifred: {f"k_{i}": big_value for i in range(5)}},
        names_by_ref={winifred: "winifred Allard"},
        worlds={"wod": _WorldMeta(id="wod", name="WoD London")},
    )
    chars = StubCharacters(cards={winifred: _Card(full="# winifred\nFull card")})
    scenes = StubScenes(scene=_Scene(present_character_refs=[winifred]))

    config = ContextBuilderConfig(
        extras_demote_to_breadcrumb_threshold_tokens=50,
    )
    builder = _builder(library=library, characters=chars, scenes=scenes, config=config)
    prompt = await builder.build("hello", "camp")
    blob = "\n".join(m.content for m in prompt.messages)
    # Breadcrumb has keys but no values.
    assert "winifred Allard — extras: k_0" in blob
    # No value content from the breakdown should make it through.
    assert "k_0: x" not in blob


async def test_extras_handles_extravalue_disk_shape():
    # ExtraValue serialized as a dict with ``value`` + ``set_at`` -- the
    # renderer should project the ``value`` field.
    library = _LibraryWithExtras(
        extras_by_ref={
            winifred: {
                "favorite_drink": {
                    "value": "Glenfarclas 25",
                    "set_at": "2026-05-19T00:00:00+00:00",
                    "set_by": "user",
                    "scope": "library",
                }
            }
        },
        names_by_ref={winifred: "winifred Allard"},
        worlds={"wod": _WorldMeta(id="wod", name="WoD London")},
    )
    chars = StubCharacters(cards={winifred: _Card(full="# winifred\nFull card")})
    scenes = StubScenes(scene=_Scene(present_character_refs=[winifred]))

    builder = _builder(library=library, characters=chars, scenes=scenes)
    prompt = await builder.build("hello", "camp")
    blob = "\n".join(m.content for m in prompt.messages)
    assert "favorite_drink: Glenfarclas 25" in blob
