"""Expression vocabulary types: core enum + extension resolution.

The Grimoire Expression Vocabulary (GEV) is a closed set of 14 core labels
that ship with the engine. Mechanics modules may declare additional labels
via ``manifest.yaml.expression_vocabulary_extensions``; those are stored
namespaced as ``<module_id>.<label>`` to avoid collisions.
"""

from __future__ import annotations

import logging
import re
from enum import StrEnum

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CoreExpression(StrEnum):
    """The 14 core expression labels shipped with Grimoire.

    Extending this enum is a versioned codebase change. Module-contributed
    extensions go through the namespaced path (``<module_id>.<label>``).
    """

    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    SURPRISED = "surprised"
    FEARFUL = "fearful"
    DISGUSTED = "disgusted"
    SMUG = "smug"
    THOUGHTFUL = "thoughtful"
    EMBARRASSED = "embarrassed"
    DETERMINED = "determined"
    HURT = "hurt"
    TIRED = "tired"
    SUSPICIOUS = "suspicious"


CORE_EXPRESSION_VALUES: frozenset[str] = frozenset(e.value for e in CoreExpression)

# snake_case, 1..32 chars, must start with a lowercase letter so a leading
# digit or punctuation can't produce odd filenames.
EXTENSION_LABEL_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class ExpressionChange(BaseModel):
    """A typed candidate emitted by the expression-classifier strategy."""

    character_id: str
    scene_id: str = ""
    post_id: str = ""
    emotion: str  # core enum value or "<module>.<label>"
    confidence: float = 0.0
    evidence: str = ""


class ExpressionRecord(BaseModel):
    """A single row of ``expression_state`` returned to callers."""

    campaign_id: str
    scene_id: str
    character_id: str
    turn_id: str
    post_id: str
    emotion: str
    provenance: str
    confidence: float = 1.0
    set_at: str = ""


class VocabularyError(ValueError):
    """Raised when an emotion label isn't part of the resolved vocabulary."""


def is_valid_extension_label(label: str) -> bool:
    """Return True for labels that may be declared as module extensions."""
    return bool(EXTENSION_LABEL_RE.match(label))


def namespace_label(module_id: str, label: str) -> str:
    """Compose a fully-qualified emotion label for a module extension."""
    return f"{module_id}.{label}"


def resolve_label(
    label: str,
    *,
    modules: list[str] | None = None,
    module_extensions: dict[str, list[str]] | None = None,
) -> str:
    """Resolve a possibly-bare label into its canonical form.

    Lookup order:
    1. Core ``CoreExpression`` values pass through unchanged.
    2. A fully-qualified ``<module>.<label>`` whose ``<module>`` appears in
       ``modules`` (or in ``module_extensions``) passes through unchanged.
    3. A bare label is resolved against ``module_extensions`` (mapping of
       module id → list of declared extension labels). The first matching
       module wins; ambiguous matches log a warning.

    ``modules`` is accepted as a lightweight alternative to
    ``module_extensions`` for callers that only need namespacing (no
    membership check). When neither is supplied, only core labels resolve.
    """
    if label in CORE_EXPRESSION_VALUES:
        return label

    if "." in label:
        module_part, _, _ = label.partition(".")
        if modules and module_part in modules:
            return label
        if module_extensions and module_part in module_extensions:
            return label
        return label  # leave fully-qualified labels intact even on mismatch

    if module_extensions:
        matches = [m for m, labels in module_extensions.items() if label in labels]
        if len(matches) == 1:
            return namespace_label(matches[0], label)
        if len(matches) > 1:
            logger.warning(
                "ambiguous expression label %r matched %d modules; using first (%s)",
                label,
                len(matches),
                matches[0],
            )
            return namespace_label(matches[0], label)
        return label

    if modules:
        return namespace_label(modules[0], label) if len(modules) == 1 else label
    return label


def is_known_label(
    label: str,
    *,
    module_extensions: dict[str, list[str]] | None = None,
) -> bool:
    """Return True if ``label`` (as stored) is part of the active vocabulary."""
    if label in CORE_EXPRESSION_VALUES:
        return True
    if "." not in label:
        return False
    module_part, _, suffix = label.partition(".")
    if not module_extensions:
        return False
    return suffix in module_extensions.get(module_part, [])


__all__ = [
    "CORE_EXPRESSION_VALUES",
    "EXTENSION_LABEL_RE",
    "CoreExpression",
    "ExpressionChange",
    "ExpressionRecord",
    "VocabularyError",
    "is_known_label",
    "is_valid_extension_label",
    "namespace_label",
    "resolve_label",
]


class ExpressionVocabulary(BaseModel):
    """The resolved vocabulary for a campaign/module-set.

    Used by service-level validation: a label is acceptable if it's either
    a ``CoreExpression`` value or a namespaced module extension that
    appears in the active module set.
    """

    module_extensions: dict[str, list[str]] = Field(default_factory=dict)

    def is_valid(self, label: str) -> bool:
        return is_known_label(label, module_extensions=self.module_extensions)

    def resolve(self, label: str) -> str:
        return resolve_label(label, module_extensions=self.module_extensions)
