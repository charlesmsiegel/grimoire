"""Editable model profiles and a request's frozen message variants.

This module has no store dependency: scene composition supplies the factory,
and the LLM facade selects a variant only when a connection is dispatched.
The public list remains the primary prompt; dispatch receives independent
copies so provider adapters cannot mutate a later retry or its prompt record.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from copy import deepcopy

from jinja2 import TemplateError

from . import prompts

_log = logging.getLogger(__name__)
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_ALIASES = {"z-ai/glm-5.3": "glm-5.3"}


def freeze_profiles(**data) -> dict[str, str]:
    """Render direct-child templates once, keyed by their exact safe stems.

    A requested model never becomes a path. Discovery also excludes symlinks
    and subdirectories, so adding a profile is the only way to recognize a
    new ID. An invalid optional template costs its own guidance, not the turn.
    """
    directory = prompts.templates_dir() / "scene" / "model_guidance"
    profiles = {}
    for path in sorted(directory.glob("*.j2")):
        if (not _SAFE_ID.fullmatch(path.stem) or path.is_symlink()
                or not path.is_file() or path.resolve().parent != directory.resolve()):
            continue
        try:
            profiles[path.stem] = prompts.render(
                f"scene/model_guidance/{path.name}", **data).strip()
        except (OSError, UnicodeError, TemplateError):
            _log.warning("Could not render model guidance profile %s", path.stem, exc_info=True)
    return profiles


def guidance_for(model: str, profiles: dict[str, str]) -> str:
    """Exact ID lookup, with only the explicitly supported provider alias."""
    return profiles.get(_ALIASES.get(model, model), "")


VariantFactory = Callable[[str], tuple[list[dict], dict | None]]
VariantCallback = Callable[[str, dict | None], None]


class PreparedMessages(list):
    """List-compatible primary prompt with lazily selected, frozen variants.

    Factories must close over rendered inputs, never a live store. Distinct
    requested model IDs are cached separately even when they resolve to the same
    profile: the record describes which model was actually attempted.
    """

    def __init__(self, primary_model: str, factory: VariantFactory):
        self._factory = factory
        self._primary_model = primary_model
        messages, breakdown = factory(primary_model)
        self._variants = {primary_model: deepcopy((messages, breakdown))}
        self._notified: set[str] = set()
        self.breakdown = deepcopy(breakdown)
        self.on_variant: VariantCallback | None = None
        super().__init__(deepcopy(messages))

    def for_model(self, model: str) -> list[dict]:
        """Return a fresh outgoing copy and record each fallback at most once."""
        if model not in self._variants:
            self._variants[model] = deepcopy(self._factory(model))
        messages, breakdown = self._variants[model]
        if model != self._primary_model and model not in self._notified:
            self._notified.add(model)
            if self.on_variant is not None:
                try:
                    self.on_variant(model, deepcopy(breakdown))
                except Exception:  # noqa: BLE001 - optional observers must not abort provider dispatch
                    # Recording is observability; its failure cannot turn a
                    # usable fallback into another provider failure.
                    _log.exception("Could not record model prompt variant")
        return deepcopy(messages)
