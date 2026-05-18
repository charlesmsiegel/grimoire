"""Schemas and validators for plugin + mechanics manifests.

The shapes are taken from `specs/15-plugins.md` (plugin manifest) and
`specs/06-mechanics.md` (mechanics manifest). Plugin loaders apply
additional cross-field checks (e.g., `id` matches directory name, every
declared `implements` has a matching `classes` entry); those checks live
with the loader, but everything expressible in JSON Schema lives here.
"""

from __future__ import annotations

from typing import Any

from grimoire.validation.errors import ValidationError, ValidationResult
from grimoire.validation.validator import check_schema, validate

# Plugin kinds the app recognizes. Mirrors the per-kind registries in the
# Plugins module (spec 15 §Interface).
PLUGIN_KINDS: tuple[str, ...] = (
    "llm_provider",
    "embedding_provider",
    "imagegen_backend",
    "export_adapter",
)

# Currently supported plugin manifest API versions. New API revisions add
# entries here; the loader rejects manifests targeting unknown versions.
PLUGIN_API_VERSIONS: tuple[str, ...] = ("1",)
MECHANICS_API_VERSIONS: tuple[str, ...] = ("1",)

_SEMVER_PATTERN = r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$"
_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"


PLUGIN_MANIFEST_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Plugin manifest",
    "type": "object",
    "required": ["id", "name", "version", "api_version", "implements", "classes"],
    "properties": {
        "id": {"type": "string", "pattern": _ID_PATTERN},
        "name": {"type": "string", "minLength": 1},
        "version": {"type": "string", "pattern": _SEMVER_PATTERN},
        "api_version": {"type": "string", "enum": list(PLUGIN_API_VERSIONS)},
        "author": {"type": "string"},
        "homepage": {"type": "string"},
        "description": {"type": "string"},
        "implements": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "string", "enum": list(PLUGIN_KINDS)},
        },
        "classes": {
            "type": "object",
            "minProperties": 1,
            "propertyNames": {"enum": list(PLUGIN_KINDS)},
            "additionalProperties": {"type": "string", "minLength": 1},
        },
        "config_schema": {"type": "object"},
        # Typed per-kind capability blocks. The schema is permissive
        # (``additionalProperties: true``) so legacy free-form metadata
        # still validates; the loader projects only the recognised keys
        # onto :class:`PluginCapabilities`.
        "capabilities": {
            "type": "object",
            "properties": {
                "llm_provider": {
                    "type": "object",
                    "properties": {
                        "streaming": {"type": "boolean"},
                        "tools": {"type": "boolean"},
                        "vision": {"type": "boolean"},
                        "embeddings": {"type": "boolean"},
                        "max_context": {"type": "integer", "minimum": 0},
                    },
                    "additionalProperties": True,
                },
                "embedding_provider": {
                    "type": "object",
                    "properties": {
                        "dimensions": {"type": "integer", "minimum": 0},
                        "max_batch_size": {"type": ["integer", "null"]},
                        "model_id": {"type": ["string", "null"]},
                    },
                    "additionalProperties": True,
                },
                "imagegen_backend": {
                    "type": "object",
                    "properties": {
                        "text_to_image": {"type": "boolean"},
                        "image_to_image": {"type": "boolean"},
                        "inpainting": {"type": "boolean"},
                        "controlnet": {"type": "boolean"},
                        "lora": {"type": "boolean"},
                        "max_resolution": {
                            "type": "array",
                            "items": {"type": "integer", "minimum": 0},
                            "minItems": 2,
                            "maxItems": 2,
                        },
                    },
                    "additionalProperties": True,
                },
                "export_adapter": {
                    "type": "object",
                    "properties": {
                        "extensions": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                        },
                        "mime_type": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
            },
            "additionalProperties": True,
        },
        "requirements": {
            "type": "array",
            "items": {"type": "string"},
        },
        "shares_secrets_with": {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "string", "pattern": _ID_PATTERN},
        },
        "notes": {"type": "string"},
    },
    "additionalProperties": True,
}


MECHANICS_MANIFEST_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Mechanics manifest",
    "type": "object",
    "required": ["id", "name", "version", "api_version"],
    "properties": {
        "id": {"type": "string", "pattern": _ID_PATTERN},
        "name": {"type": "string", "minLength": 1},
        "version": {"type": "string", "pattern": _SEMVER_PATTERN},
        "api_version": {"type": "string", "enum": list(MECHANICS_API_VERSIONS)},
        "author": {"type": "string"},
        "homepage": {"type": "string"},
        "description": {"type": "string"},
        "sheet_kinds": {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1},
        },
        "content_kinds": {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1},
        },
        "capabilities": {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1},
        },
        "ui": {
            "type": "object",
            "properties": {
                "theme_css": {"type": "string"},
                "custom_components": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
            },
            "additionalProperties": True,
        },
    },
    "additionalProperties": True,
}


def _cross_check_plugin(manifest: dict) -> list[ValidationError]:
    """Checks that JSON Schema can't express on its own."""
    errors: list[ValidationError] = []
    implements = manifest.get("implements")
    classes = manifest.get("classes")
    if isinstance(implements, list) and isinstance(classes, dict):
        for kind in implements:
            if kind not in classes:
                errors.append(
                    ValidationError(
                        message=f"`classes` is missing an entry for implemented kind '{kind}'",
                        path=("classes",),
                        validator="required",
                    )
                )
        for kind in classes:
            if isinstance(kind, str) and kind not in implements:
                errors.append(
                    ValidationError(
                        message=(
                            f"`classes` declares '{kind}' but it is not listed in `implements`"
                        ),
                        path=("classes", kind),
                        validator="consistency",
                    )
                )
    capabilities = manifest.get("capabilities")
    if isinstance(capabilities, dict) and isinstance(implements, list):
        # Per-kind capability blocks must match a kind the plugin
        # implements; an `llm_provider` capability block on an export
        # adapter is almost certainly a typo.
        for kind in capabilities:
            if kind in PLUGIN_KINDS and kind not in implements:
                errors.append(
                    ValidationError(
                        message=(
                            f"`capabilities.{kind}` is declared but `{kind}` "
                            "is not listed in `implements`"
                        ),
                        path=("capabilities", kind),
                        validator="consistency",
                    )
                )
    return errors


def validate_plugin_manifest(manifest: Any) -> ValidationResult:
    """Validate a parsed plugin `manifest.yaml`.

    Returns all errors at once. The plugin loader is expected to apply an
    additional check that `manifest["id"]` matches the directory name —
    that check needs filesystem context and lives with the loader.
    """
    result = validate(manifest, PLUGIN_MANIFEST_SCHEMA)
    extras: list[ValidationError] = []
    if isinstance(manifest, dict):
        config_schema = manifest.get("config_schema")
        if config_schema is not None:
            schema_result = check_schema(config_schema)
            if not schema_result.ok:
                for err in schema_result.errors:
                    extras.append(
                        ValidationError(
                            message=f"`config_schema` is not valid JSON Schema: {err.message}",
                            path=("config_schema", *err.path),
                            schema_path=err.schema_path,
                            validator=err.validator or "schema",
                        )
                    )
        extras.extend(_cross_check_plugin(manifest))
    if not result.ok or extras:
        return ValidationResult.failure([*result.errors, *extras])
    return ValidationResult.success()


def validate_mechanics_manifest(manifest: Any) -> ValidationResult:
    """Validate a parsed mechanics `manifest.yaml`."""
    return validate(manifest, MECHANICS_MANIFEST_SCHEMA)


__all__ = [
    "MECHANICS_API_VERSIONS",
    "MECHANICS_MANIFEST_SCHEMA",
    "PLUGIN_API_VERSIONS",
    "PLUGIN_KINDS",
    "PLUGIN_MANIFEST_SCHEMA",
    "validate_mechanics_manifest",
    "validate_plugin_manifest",
]
