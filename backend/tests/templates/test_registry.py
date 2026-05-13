"""Tests for the Jinja2 prompt-template registry.

Spec: every LLM/image prompt is rendered from a ``templates/<name>/<variant>.j2``
file. The registry must support per-template variant overrides, fall back
to ``default`` when a missing variant is requested, and let extra search
paths (modder directories) shadow the bundled templates.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.templates import DEFAULT_VARIANT, TemplateRegistry


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_renders_default_variant(tmp_path: Path) -> None:
    _write(tmp_path / "greet" / "default.j2", "hello {{ name }}")
    registry = TemplateRegistry(search_paths=[tmp_path])

    assert registry.render("greet", name="world") == "hello world"


def test_explicit_variant_overrides_default(tmp_path: Path) -> None:
    _write(tmp_path / "greet" / "default.j2", "hello {{ name }}")
    _write(tmp_path / "greet" / "terse.j2", "hi {{ name }}")
    registry = TemplateRegistry(search_paths=[tmp_path])

    assert registry.render("greet", variant="terse", name="alex") == "hi alex"


def test_set_variant_persists_for_subsequent_renders(tmp_path: Path) -> None:
    _write(tmp_path / "greet" / "default.j2", "hello {{ name }}")
    _write(tmp_path / "greet" / "loud.j2", "HELLO {{ name|upper }}")
    registry = TemplateRegistry(search_paths=[tmp_path])

    registry.set_variant("greet", "loud")
    assert registry.render("greet", name="alex") == "HELLO ALEX"

    registry.set_variant("greet", None)
    assert registry.render("greet", name="alex") == "hello alex"


def test_missing_variant_falls_back_to_default(tmp_path: Path) -> None:
    _write(tmp_path / "greet" / "default.j2", "hello {{ name }}")
    registry = TemplateRegistry(search_paths=[tmp_path])

    # Variant doesn't exist — registry transparently uses default.j2.
    assert registry.render("greet", variant="missing", name="x") == "hello x"


def test_missing_default_raises(tmp_path: Path) -> None:
    """If neither the requested variant nor ``default.j2`` exists, fail loudly."""
    _write(tmp_path / "greet" / "loud.j2", "HELLO")
    registry = TemplateRegistry(search_paths=[tmp_path])

    from jinja2 import TemplateNotFound

    with pytest.raises(TemplateNotFound):
        registry.render("greet")


def test_list_variants_returns_all(tmp_path: Path) -> None:
    _write(tmp_path / "greet" / "default.j2", "x")
    _write(tmp_path / "greet" / "loud.j2", "y")
    _write(tmp_path / "greet" / "README.md", "ignored")  # not .j2 — must be skipped
    registry = TemplateRegistry(search_paths=[tmp_path])

    assert registry.list_variants("greet") == ["default", "loud"]


def test_register_search_path_overrides_bundled(tmp_path: Path) -> None:
    """User dirs registered with prepend=True shadow earlier paths."""
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    _write(bundled / "greet" / "default.j2", "bundled hello")
    _write(user / "greet" / "default.j2", "user hello")

    registry = TemplateRegistry(search_paths=[bundled])
    assert registry.render("greet") == "bundled hello"

    registry.register_search_path(user)  # prepend=True by default
    assert registry.render("greet") == "user hello"


def test_strict_undefined_raises_on_missing_var(tmp_path: Path) -> None:
    """Catch template-context mistakes early instead of silently emitting empty."""
    _write(tmp_path / "greet" / "default.j2", "hello {{ name }}")
    registry = TemplateRegistry(search_paths=[tmp_path])

    from jinja2 import UndefinedError

    with pytest.raises(UndefinedError):
        registry.render("greet")  # no `name` supplied


def test_default_variant_constant_is_used() -> None:
    assert DEFAULT_VARIANT == "default"


def test_bundled_templates_are_discoverable() -> None:
    """The bundled templates ship under the package and load by default."""
    registry = TemplateRegistry()  # no overrides

    # A representative subset of templates we ship in this refactor.
    assert "default" in registry.list_variants("continuity_judge_system")
    assert "default" in registry.list_variants("continuity_judge_user")
    assert "default" in registry.list_variants("extractor_system")
    assert "default" in registry.list_variants("extractor_user")
    assert "default" in registry.list_variants("imagegen_positive")
    assert "default" in registry.list_variants("imagegen_negative")
    assert "default" in registry.list_variants("context_system_block")
    assert "default" in registry.list_variants("context_lock_in_block")
    assert "default" in registry.list_variants("context_tier_block")
    assert "default" in registry.list_variants("context_recent_older_block")
    assert "default" in registry.list_variants("context_location")


def test_imagegen_positive_joins_and_strips() -> None:
    registry = TemplateRegistry()
    rendered = registry.render(
        "imagegen_positive",
        preset_preamble="  oil painting  ",
        location_description="moonlit ruin",
        character_prompts=["redhead, scar", ""],
        scene_elements=["", "rain"],
        mood="ominous",
    )
    assert rendered == "oil painting, moonlit ruin, redhead, scar, rain, ominous"


def test_imagegen_negative_skips_empty() -> None:
    registry = TemplateRegistry()
    assert registry.render(
        "imagegen_negative",
        preset_negative="lowres",
        character_negatives=["", "blurry", "  "],
    ) == "lowres, blurry"
