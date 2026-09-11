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

    def __init__(self, primary_model: str, factory: VariantFactory, *,
                 profiles: dict[str, tuple[list[dict], dict | None]] | None = None):
        self._frozen_profiles = deepcopy(profiles)
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

    def snapshot(self) -> dict:
        """Portable historical prompts; no templates or live state are consulted.

        Scene composers supply the entire finite profile set at construction.
        Generic factories cannot promise that and must not masquerade as durable
        context: a snapshot is only supported for explicitly frozen factories.
        """
        if self._frozen_profiles is None:
            raise ValueError("Prepared messages have no durable frozen profiles")
        return deepcopy({"version": 1, "primary_model": self._primary_model,
                         "unprofiled": self._frozen_profiles[""],
                         "profiles": {k: v for k, v in self._frozen_profiles.items() if k}})

    @classmethod
    def from_snapshot(cls, snapshot: dict, model: str) -> PreparedMessages:
        """Restore a writer without reconstructing the past from current files."""
        if snapshot.get("version") != 1 or "unprofiled" not in snapshot:
            raise ValueError("Unsupported frozen prompt snapshot")
        frozen = deepcopy({"": snapshot["unprofiled"], **snapshot.get("profiles", {})})
        def select(selected_model):
            return frozen.get(_ALIASES.get(selected_model, selected_model), frozen[""])
        return cls(model, select, profiles=frozen)

    def with_appended(self, message: dict) -> PreparedMessages:
        """Append explicit reroll steering to every frozen historical variant.

        Historical packing is retained: dropped evidence cannot be recovered.
        The old breakdown no longer measures the sent prompt, so callers get
        None rather than a falsely precise total. Provider usage remains metered.
        """
        snapshot = self.snapshot()
        def append(variant):
            return [*deepcopy(variant[0]), deepcopy(message)], None
        snapshot["unprofiled"] = append(snapshot["unprofiled"])
        snapshot["profiles"] = {k: append(v) for k, v in snapshot["profiles"].items()}
        return self.from_snapshot(snapshot, self._primary_model)
