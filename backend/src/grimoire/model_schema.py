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


def _wire_name(name: str) -> str:
    return name[:-1] if name.endswith("_") and keyword.iskeyword(name[:-1]) else name


def structured(model: type[BaseModel]) -> dict:
    make_schema = getattr(model, "model_json_schema", None) or model.schema
    raw = make_schema()
    definitions = raw.get("$defs", raw.get("definitions", {}))

    def normalize(node: dict, annotation=None, seen: frozenset[str] = frozenset()) -> dict:
        if unsupported := set(node) - _ALLOWED:
            raise ValueError(f"Unsupported structured-output schema constraints: {sorted(unsupported)}")
        if "$ref" in node:
            ref = node["$ref"]
            if ref in seen or not isinstance(ref, str) or not ref.startswith(("#/$defs/", "#/definitions/")):
                raise ValueError("Structured output needs a finite local model graph")
            return normalize(definitions[ref.rsplit("/", 1)[-1]], annotation, seen | {ref})
        args = get_args(annotation)
        nullable = type(None) in args
        concrete = next((arg for arg in args if arg is not type(None)), annotation) if nullable else annotation
        out = {key: value for key, value in node.items()
               if key not in {"title", "default", "$defs", "definitions", "required", "properties", "items", "anyOf"}}
        if "const" in out:
            out["enum"] = [out.pop("const")]
        if "properties" in node:
            hints = get_type_hints(concrete) if isinstance(concrete, type) and issubclass(concrete, BaseModel) else {}
            out["properties"] = {_wire_name(name): normalize(value, hints.get(name), seen)
                                 for name, value in node["properties"].items()}
            out["required"] = list(out["properties"])
            out["additionalProperties"] = False
        if "items" in node:
            item_args = get_args(concrete)
            out["items"] = normalize(node["items"], item_args[0] if item_args else None, seen)
        if "anyOf" in node:
            # Align union annotations to non-null branches rather than relying
            # on Pydantic1/2 to put optional null in the same position.
            choices = [arg for arg in args if arg is not type(None)]
            non_null = iter(choices)
            out["anyOf"] = [normalize(value, type(None) if value.get("type") == "null"
                                      else next(non_null, concrete), seen)
                            for value in node["anyOf"]]
        if nullable and not (out.get("type") == "null" or any(
                choice.get("type") == "null" for choice in out.get("anyOf", []))):
            out = {"anyOf": [out, {"type": "null"}]}
        return out

    result = normalize(raw, model)
    schemas.check(result)
    return result
