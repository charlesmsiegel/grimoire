"""Validation tests for ``expression_vocabulary_extensions`` on mechanics manifests."""

from __future__ import annotations

from grimoire.validation.manifests import validate_mechanics_manifest


def _base() -> dict:
    return {"id": "wod", "name": "World of Darkness", "version": "1.0.0", "api_version": "1"}


def test_no_extensions_passes() -> None:
    result = validate_mechanics_manifest(_base())
    assert result.ok, result.errors


def test_extensions_accepted() -> None:
    manifest = _base() | {"expression_vocabulary_extensions": ["seductive", "awakened"]}
    result = validate_mechanics_manifest(manifest)
    assert result.ok, result.errors


def test_core_collision_rejected() -> None:
    manifest = _base() | {"expression_vocabulary_extensions": ["happy"]}
    result = validate_mechanics_manifest(manifest)
    assert not result.ok
    msgs = " ".join(e.message for e in result.errors)
    assert "happy" in msgs
    assert "core" in msgs


def test_invalid_label_pattern_rejected() -> None:
    manifest = _base() | {"expression_vocabulary_extensions": ["NotSnakeCase"]}
    result = validate_mechanics_manifest(manifest)
    assert not result.ok


def test_label_too_long_rejected() -> None:
    manifest = _base() | {"expression_vocabulary_extensions": ["a" * 33]}
    result = validate_mechanics_manifest(manifest)
    assert not result.ok
