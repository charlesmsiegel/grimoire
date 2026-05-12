"""Tests for the JSON Schema validation helpers."""

from __future__ import annotations

import pytest

from grimoire.validation import (
    ValidationError,
    ValidationResult,
    check_schema,
    validate,
    validate_config,
    validate_mechanics_manifest,
    validate_plugin_manifest,
    validate_sheet,
)

# ---------------------------------------------------------------------------
# Core wrapper
# ---------------------------------------------------------------------------


def test_validate_succeeds_for_valid_instance() -> None:
    schema = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}
    result = validate({"x": 5}, schema)
    assert result.ok is True
    assert bool(result) is True
    assert result.errors == ()


def test_validate_collects_multiple_errors() -> None:
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer", "minimum": 0},
        },
        "required": ["name", "age"],
    }
    result = validate({"age": -1}, schema)
    assert result.ok is False
    messages = [e.message for e in result.errors]
    # Missing `name` plus invalid `age` minimum yields at least two errors.
    assert len(result.errors) >= 2
    assert any("name" in m for m in messages)


def test_validate_error_paths_are_pointable() -> None:
    schema = {
        "type": "object",
        "properties": {
            "attributes": {
                "type": "object",
                "properties": {"strength": {"type": "integer"}},
                "required": ["strength"],
            },
        },
    }
    result = validate({"attributes": {"strength": "two"}}, schema)
    assert not result.ok
    err = result.errors[0]
    assert err.path == ("attributes", "strength")
    assert err.pointer == "/attributes/strength"


def test_pointer_escapes_special_chars() -> None:
    err = ValidationError(message="x", path=("a/b", "c~d"))
    assert err.pointer == "/a~1b/c~0d"


def test_check_schema_rejects_invalid_schema() -> None:
    bad = {"type": "not-a-real-type"}
    result = check_schema(bad)
    assert result.ok is False


def test_check_schema_rejects_non_object() -> None:
    result = check_schema("nope")  # type: ignore[arg-type]
    assert result.ok is False
    assert "object" in result.errors[0].message


def test_validate_reports_schema_error_when_schema_is_invalid() -> None:
    bad_schema = {"type": "not-a-real-type"}
    result = validate({"anything": 1}, bad_schema)
    assert result.ok is False


def test_validation_result_helpers() -> None:
    success = ValidationResult.success()
    assert success.ok is True
    assert success.to_dict() == {"ok": True, "errors": []}
    failure = ValidationResult.failure([ValidationError(message="boom", path=("x",))])
    assert failure.ok is False
    d = failure.to_dict()
    assert d["ok"] is False
    assert d["errors"][0]["pointer"] == "/x"


# ---------------------------------------------------------------------------
# Sheet validation
# ---------------------------------------------------------------------------


SHEET_SCHEMA: dict = {
    "type": "object",
    "title": "Tiny sheet",
    "properties": {
        "name": {"type": "string", "widget": "text"},
        "strength": {"type": "integer", "widget": "dot-rating", "minimum": 1, "maximum": 5},
    },
    "required": ["name", "strength"],
}


def test_validate_sheet_accepts_valid_sheet() -> None:
    result = validate_sheet({"name": "Aleksandr", "strength": 3}, SHEET_SCHEMA)
    assert result.ok


def test_validate_sheet_rejects_out_of_range() -> None:
    result = validate_sheet({"name": "Aleksandr", "strength": 9}, SHEET_SCHEMA)
    assert not result.ok
    assert result.errors[0].path == ("strength",)


def test_validate_sheet_ignores_widget_annotations() -> None:
    # `widget` is a UI hint, not a JSON Schema keyword; the validator should
    # not reject schemas that include it.
    schema = {
        "type": "object",
        "properties": {"x": {"type": "string", "widget": "made-up-widget"}},
    }
    assert validate_sheet({"x": "hello"}, schema).ok


# ---------------------------------------------------------------------------
# Plugin config schemas
# ---------------------------------------------------------------------------


CONFIG_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "api_key": {"type": "string"},
        "base_url": {"type": "string"},
        "max_retries": {"type": "integer", "minimum": 0},
    },
    "required": ["api_key"],
}


def test_validate_config_accepts_valid_config() -> None:
    result = validate_config({"api_key": "sk-xxx", "max_retries": 3}, CONFIG_SCHEMA)
    assert result.ok


def test_validate_config_flags_missing_required() -> None:
    result = validate_config({}, CONFIG_SCHEMA)
    assert not result.ok
    assert any("api_key" in e.message for e in result.errors)


# ---------------------------------------------------------------------------
# Plugin manifests
# ---------------------------------------------------------------------------


VALID_PLUGIN_MANIFEST: dict = {
    "id": "llm-anthropic",
    "name": "Anthropic LLM Provider",
    "version": "1.2.0",
    "api_version": "1",
    "implements": ["llm_provider"],
    "classes": {"llm_provider": "AnthropicLLMProvider"},
    "config_schema": {
        "type": "object",
        "properties": {"api_key": {"type": "string"}},
        "required": ["api_key"],
    },
}


def test_validate_plugin_manifest_accepts_valid_manifest() -> None:
    assert validate_plugin_manifest(VALID_PLUGIN_MANIFEST).ok


def test_validate_plugin_manifest_rejects_unknown_kind() -> None:
    manifest = {**VALID_PLUGIN_MANIFEST, "implements": ["telepathy_provider"]}
    result = validate_plugin_manifest(manifest)
    assert not result.ok


def test_validate_plugin_manifest_rejects_missing_class_for_implements() -> None:
    manifest = {
        **VALID_PLUGIN_MANIFEST,
        "implements": ["llm_provider", "embedding_provider"],
        "classes": {"llm_provider": "AnthropicLLMProvider"},
    }
    result = validate_plugin_manifest(manifest)
    assert not result.ok
    assert any("embedding_provider" in e.message for e in result.errors)


def test_validate_plugin_manifest_rejects_class_without_implements() -> None:
    manifest = {
        **VALID_PLUGIN_MANIFEST,
        "implements": ["llm_provider"],
        "classes": {
            "llm_provider": "AnthropicLLMProvider",
            "embedding_provider": "Stray",
        },
    }
    result = validate_plugin_manifest(manifest)
    assert not result.ok


def test_validate_plugin_manifest_rejects_unknown_api_version() -> None:
    manifest = {**VALID_PLUGIN_MANIFEST, "api_version": "99"}
    assert not validate_plugin_manifest(manifest).ok


def test_validate_plugin_manifest_rejects_bad_id() -> None:
    manifest = {**VALID_PLUGIN_MANIFEST, "id": "Has Spaces"}
    assert not validate_plugin_manifest(manifest).ok


def test_validate_plugin_manifest_rejects_invalid_config_schema() -> None:
    manifest = {
        **VALID_PLUGIN_MANIFEST,
        "config_schema": {"type": "not-a-real-type"},
    }
    result = validate_plugin_manifest(manifest)
    assert not result.ok
    assert any("config_schema" in e.message for e in result.errors)


def test_validate_plugin_manifest_rejects_non_object() -> None:
    assert not validate_plugin_manifest("nope").ok


# ---------------------------------------------------------------------------
# Mechanics manifests
# ---------------------------------------------------------------------------


VALID_MECHANICS_MANIFEST: dict = {
    "id": "wod-mechanics",
    "name": "World of Darkness Mechanics",
    "version": "1.2.0",
    "api_version": "1",
    "sheet_kinds": ["character", "location"],
    "content_kinds": ["discipline", "merit"],
    "capabilities": ["dice", "combat"],
    "ui": {"theme_css": "theme.css"},
}


def test_validate_mechanics_manifest_accepts_valid_manifest() -> None:
    assert validate_mechanics_manifest(VALID_MECHANICS_MANIFEST).ok


@pytest.mark.parametrize(
    "patch",
    [
        {"id": "Has Spaces"},
        {"version": "not-semver"},
        {"api_version": "99"},
        {"sheet_kinds": ["character", "character"]},  # not unique
    ],
)
def test_validate_mechanics_manifest_rejects_bad_fields(patch: dict) -> None:
    manifest = {**VALID_MECHANICS_MANIFEST, **patch}
    assert not validate_mechanics_manifest(manifest).ok


def test_validate_mechanics_manifest_allows_minimal_manifest() -> None:
    minimal = {
        "id": "freeform",
        "name": "Freeform",
        "version": "0.1.0",
        "api_version": "1",
    }
    assert validate_mechanics_manifest(minimal).ok
