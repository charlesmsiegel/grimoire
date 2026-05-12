"""JSON Schema validation helpers.

A thin wrapper around the `jsonschema` library that produces a consistent
error shape suitable for surfacing in the UI. All validators in the app
(mechanics sheet schemas, mechanics manifests, plugin manifests, plugin
`config_schema` forms) route through this module.
"""

from grimoire.validation.errors import ValidationError, ValidationResult
from grimoire.validation.manifests import (
    MECHANICS_MANIFEST_SCHEMA,
    PLUGIN_MANIFEST_SCHEMA,
    validate_mechanics_manifest,
    validate_plugin_manifest,
)
from grimoire.validation.validator import (
    check_schema,
    validate,
    validate_config,
    validate_sheet,
)

__all__ = [
    "MECHANICS_MANIFEST_SCHEMA",
    "PLUGIN_MANIFEST_SCHEMA",
    "ValidationError",
    "ValidationResult",
    "check_schema",
    "validate",
    "validate_config",
    "validate_mechanics_manifest",
    "validate_plugin_manifest",
    "validate_sheet",
]
