"""Model-derived structured output shared by prompt rendering and providers.

Inline finite model graphs into the provider's closed-object JSON subset. Optional
annotations restore Pydantic1's omitted null alternatives; unsupported constraints
fail explicitly instead of quietly weakening the requested output contract.
"""

from __future__ import annotations

import keyword
from typing import get_args, get_type_hints

from pydantic import BaseModel

from . import schemas

_ALLOWED = {"type", "properties", "required", "additionalProperties", "items", "enum",
            "const", "anyOf", "$ref", "$defs", "definitions", "title", "default", "description"}
#: The keys a normalized node rebuilds (or drops) rather than copies.
_REBUILT = {"title", "default", "$defs", "definitions", "required", "properties", "items", "anyOf"}


def _wire_name(name: str) -> str:
    return name[:-1] if name.endswith("_") and keyword.iskeyword(name[:-1]) else name


def _admitting_null(out: dict) -> dict:
    """`out` for a nullable annotation: as it is when it already admits null,
    else `out` or null."""
    if out.get("type") == "null" or any(
            choice.get("type") == "null" for choice in out.get("anyOf", [])):
        return out
    return {"anyOf": [out, {"type": "null"}]}


class _Normalizer:
    """One model's raw schema, walked into the provider's subset: local `$ref`s
    inlined from its definitions, every object closed with all properties
    required, and the annotation beside each node restoring the null
    alternative Pydantic1 omits."""

    def __init__(self, definitions: dict):
        self.definitions = definitions

    def node(self, node: dict, annotation=None, seen: frozenset[str] = frozenset()) -> dict:
        if unsupported := set(node) - _ALLOWED:
            raise ValueError(f"Unsupported structured-output schema constraints: {sorted(unsupported)}")
        if "$ref" in node:
            return self._ref(node["$ref"], annotation, seen)
        args = get_args(annotation)
        nullable = type(None) in args
        concrete = next((arg for arg in args if arg is not type(None)), annotation) if nullable else annotation
        out = {key: value for key, value in node.items() if key not in _REBUILT}
        if "const" in out:
            out["enum"] = [out.pop("const")]
        if "properties" in node:
            out["properties"] = self._properties(node["properties"], concrete, seen)
            out["required"] = list(out["properties"])
            out["additionalProperties"] = False
        if "items" in node:
            item_args = get_args(concrete)
            out["items"] = self.node(node["items"], item_args[0] if item_args else None, seen)
        if "anyOf" in node:
            out["anyOf"] = self._any_of(node["anyOf"], args, concrete, seen)
        return _admitting_null(out) if nullable else out

    def _ref(self, ref, annotation, seen: frozenset[str]) -> dict:
        """The definition `ref` names, inlined; a cycle or a non-local ref refused."""
        if ref in seen or not isinstance(ref, str) or not ref.startswith(("#/$defs/", "#/definitions/")):
            raise ValueError("Structured output needs a finite local model graph")
        return self.node(self.definitions[ref.rsplit("/", 1)[-1]], annotation, seen | {ref})

    def _properties(self, properties: dict, concrete, seen: frozenset[str]) -> dict:
        """Each property under its wire name, beside its field's annotation
        when `concrete` is a model."""
        hints = get_type_hints(concrete) if isinstance(concrete, type) and issubclass(concrete, BaseModel) else {}
        return {_wire_name(name): self.node(value, hints.get(name), seen)
                for name, value in properties.items()}

    def _any_of(self, choices: list, args: tuple, concrete, seen: frozenset[str]) -> list:
        # Align union annotations to non-null branches rather than relying
        # on Pydantic1/2 to put optional null in the same position.
        non_null = iter([arg for arg in args if arg is not type(None)])
        return [self.node(value, type(None) if value.get("type") == "null"
                          else next(non_null, concrete), seen)
                for value in choices]


def structured(model: type[BaseModel]) -> dict:
    make_schema = getattr(model, "model_json_schema", None) or model.schema
    raw = make_schema()
    definitions = raw.get("$defs", raw.get("definitions", {}))
    result = _Normalizer(definitions).node(raw, model)
    schemas.check(result)
    return result
