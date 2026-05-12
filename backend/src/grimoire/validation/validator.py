"""Core JSON Schema validator wrapper.

The app uses Draft 2020-12 throughout. `jsonschema` errors are converted
into `ValidationError` instances with stable paths so the same renderer
can present problems whether they came from a sheet, a manifest, or a
plugin config form.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JSONSchemaValidationError

from grimoire.validation.errors import ValidationError, ValidationResult

# Widget names referenced by mechanics sheet schemas. The Frontend renders
# unknown widgets with a generic fallback and a warning; the validator does
# not reject unknown values, since the widget vocabulary is versioned and
# may grow ahead of the backend.
KNOWN_SHEET_WIDGETS: frozenset[str] = frozenset(
    {
        "text",
        "textarea",
        "number",
        "select",
        "multi-select",
        "boolean",
        "dot-rating",
        "dice-pool",
        "health-track",
        "power-list",
        "grid-rating",
        "slot-list",
        "keyword-list",
        "nested-section",
    }
)


@lru_cache(maxsize=256)
def _validator_for(schema_key: tuple) -> Draft202012Validator:
    # Cache validator instances when the schema is hashable (i.e., passed
    # through `_freeze`). Schema compilation is the expensive part.
    schema = _thaw(schema_key)
    return Draft202012Validator(schema)


def _freeze(obj: Any) -> Any:
    if isinstance(obj, dict):
        return ("__dict__", tuple(sorted((k, _freeze(v)) for k, v in obj.items())))
    if isinstance(obj, list):
        return ("__list__", tuple(_freeze(v) for v in obj))
    return obj


def _thaw(obj: Any) -> Any:
    if isinstance(obj, tuple) and len(obj) == 2 and obj[0] == "__dict__":
        return {k: _thaw(v) for k, v in obj[1]}
    if isinstance(obj, tuple) and len(obj) == 2 and obj[0] == "__list__":
        return [_thaw(v) for v in obj[1]]
    return obj


def _make_validator(schema: dict) -> Draft202012Validator:
    try:
        return _validator_for(_freeze(schema))
    except TypeError:
        # Schema contained a non-hashable value we don't normalize; build
        # a fresh validator without caching.
        return Draft202012Validator(schema)


def _convert_error(err: JSONSchemaValidationError) -> ValidationError:
    return ValidationError(
        message=err.message,
        path=tuple(err.absolute_path),
        schema_path=tuple(err.absolute_schema_path),
        validator=str(err.validator) if err.validator else "",
    )


def check_schema(schema: Any) -> ValidationResult:
    """Verify that `schema` is itself a valid JSON Schema (Draft 2020-12)."""
    if not isinstance(schema, dict):
        return ValidationResult.failure(
            [ValidationError(message="schema must be a JSON object", validator="type")]
        )
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as err:
        return ValidationResult.failure(
            [
                ValidationError(
                    message=err.message,
                    path=tuple(err.absolute_path),
                    schema_path=tuple(err.absolute_schema_path),
                    validator=str(err.validator) if err.validator else "schema",
                )
            ]
        )
    return ValidationResult.success()


def validate(instance: Any, schema: dict) -> ValidationResult:
    """Validate `instance` against `schema`, collecting every error.

    Returns a `ValidationResult` rather than raising so callers can render
    multiple problems at once. If the schema itself is malformed, the
    result contains a single schema error.
    """
    schema_check = check_schema(schema)
    if not schema_check.ok:
        return schema_check
    validator = _make_validator(schema)
    raw_errors = sorted(validator.iter_errors(instance), key=_error_sort_key)
    errors = [_convert_error(e) for e in raw_errors]
    if errors:
        return ValidationResult.failure(errors)
    return ValidationResult.success()


def _error_sort_key(err: JSONSchemaValidationError) -> tuple:
    return (tuple(str(p) for p in err.absolute_path), err.message)


def validate_sheet(sheet: Any, sheet_schema: dict) -> ValidationResult:
    """Validate a mechanics sheet against a schema produced by a mechanics module.

    Behaves like `validate` — `widget` annotations in the schema are
    ignored by the validator (they are UI hints, not constraints).
    """
    return validate(sheet, sheet_schema)


def validate_config(config: Any, config_schema: dict) -> ValidationResult:
    """Validate a plugin config dict against the plugin's `config_schema`.

    The `secret: true` annotation in a property is informational (it tells
    the UI to render a password field). It is ignored here.
    """
    return validate(config, config_schema)


__all__ = [
    "KNOWN_SHEET_WIDGETS",
    "check_schema",
    "validate",
    "validate_config",
    "validate_sheet",
]
