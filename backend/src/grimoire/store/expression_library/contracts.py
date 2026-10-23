"""Validated semantic addresses and sparse outfit metadata.

Stable slugs address assets; Unicode labels are only display text. Manifests
describe outfits and hide rules, while image placements alone own image IDs.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .. import paths


class ExpressionInvalidError(ValueError):
    """A mutation or pack does not satisfy the expression-library contract."""


class ExpressionBudgetError(ExpressionInvalidError):
    """An upload exceeds the bounded resource budget."""


class UnsupportedSpriteError(ExpressionInvalidError):
    """A decodable image that this static-sprite release cannot use."""

    def __init__(self, message: str, pixels: int):
        super().__init__(message)
        self.pixels = pixels


class ExpressionUnavailableError(ValueError):
    """Unknown or incomplete collection: never overwrite it as an empty one."""


class ExpressionConflictError(ValueError):
    """The selected collection changed since the caller read it."""


def slug(value: object) -> str:
    if (not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value)
            or value in {"con", "prn", "aux", "nul", "clock$"}
            or re.fullmatch(r"(?:com|lpt)[1-9]", value)):
        raise ExpressionInvalidError("Expected a lowercase outfit/expression slug")
    return value


@dataclass(frozen=True)
class ExpressionActorTarget:
    scope: str
    owner_id: str
    kind: str
    actor_id: str

    def __post_init__(self):
        if self.scope not in ("world", "campaign") or self.kind not in ("characters", "pcs"):
            raise ExpressionInvalidError("Unknown expression scope or actor kind")
        if not all(isinstance(v, str) and paths.safe_id(v) for v in (self.owner_id, self.actor_id)):
            raise ExpressionInvalidError("Invalid actor address")


@dataclass(frozen=True)
class ExpressionTarget(ExpressionActorTarget):
    version_id: str

    def __post_init__(self):
        super().__post_init__()
        if not isinstance(self.version_id, str) or not paths.safe_id(self.version_id):
            raise ExpressionInvalidError("Invalid version address")


PRESENTATION = {"scale": 1.0, "offset_x": 0.0, "offset_y": 0.0, "anchor": "bottom-center"}


def metadata(patch: dict) -> dict:
    if not isinstance(patch, dict) or set(patch) - {"label", "clothing_aliases", "fallback_expression", "presentation"}:
        raise ExpressionInvalidError("Unknown outfit metadata")
    out = dict(patch)
    if "label" in out and (not isinstance(out["label"], str) or not out["label"].strip() or len(out["label"]) > 200):
        raise ExpressionInvalidError("Outfit label must be nonempty and at most 200 characters")
    if "clothing_aliases" in out:
        aliases = out["clothing_aliases"]
        if not isinstance(aliases, list) or len(aliases) > 64 or any(not isinstance(a, str) or len(a) > 200 for a in aliases):
            raise ExpressionInvalidError("Invalid clothing aliases")
    if "fallback_expression" in out:
        slug(out["fallback_expression"])
    if "presentation" in out:
        presentation(out["presentation"])
    return out


def presentation(p: dict) -> None:
    if not isinstance(p, dict) or set(p) - PRESENTATION.keys():
        raise ExpressionInvalidError("Unknown presentation adjustment")
    for key, value in p.items():
        if key == "anchor":
            if value != "bottom-center":
                raise ExpressionInvalidError("Unsupported anchor")
        elif (type(value) not in (int, float) or not math.isfinite(value)
              or not ((0.1 <= value <= 4) if key == "scale" else (-1 <= value <= 1))):
            raise ExpressionInvalidError("Presentation adjustment out of range")


def merge_metadata(outfit: str, base: dict, patch: dict) -> dict:
    return {"label": outfit, "clothing_aliases": [], "fallback_expression": "neutral",
            **base, **patch,
            "presentation": {**PRESENTATION, **base.get("presentation", {}), **patch.get("presentation", {})}}
