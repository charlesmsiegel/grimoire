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
from typing import Any

from jinja2 import TemplateError

from . import prompts, wire

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
                 profiles: dict[str, tuple[list[dict], dict | None]] | None = None,
                 campaign: str = ""):
        self._frozen_profiles = deepcopy(profiles)
        self._factory = factory
        self._primary_model = primary_model
        messages, breakdown = factory(primary_model)
        self._variants = {primary_model: deepcopy((messages, breakdown))}
        self._notified: set[tuple[str, str | None]] = set()
        self.breakdown = deepcopy(breakdown)
        self.on_variant: VariantCallback | None = None
        #: The campaign whose prompt this is. Image references (#377,
        #: `content_parts`) name no campaign, so this is what the facade
        #: resolves them against -- the campaign running the attempt, which for
        #: a snapshot restored in a fork is the fork.
        self.campaign = campaign
        # The response settings the prompt rendered; set by `assemble._prepare`.
        self.settings: dict | None = None
        #: Alternative endings chosen per dispatched ATTEMPT (`with_tails`), and
        #: the chooser that picks one from the attempt's connection. None for
        #: every ordinary prompt.
        self._tails: dict[str, list[dict]] | None = None
        self._choose: Callable[[Any], str] | None = None
        #: The tail the primary attempt is sent: with the primary model, the
        #: one combination the prompt record already holds.
        self._primary_tail: str | None = None
        super().__init__(deepcopy(messages))

    def for_model(self, model: str) -> list[dict]:
        """Return a fresh outgoing copy and record each fallback at most once."""
        messages, breakdown = self._variant(model)
        if model != self._primary_model:
            self._notify((model, None), model, breakdown)
        return deepcopy(messages)

    def _variant(self, model: str) -> tuple[list[dict], dict | None]:
        if model not in self._variants:
            self._variants[model] = deepcopy(self._factory(model))
        return self._variants[model]

    def _notify(self, key: tuple[str, str | None], model: str,
                breakdown: dict | None) -> None:
        """Tell `on_variant` about a prompt the record does not hold yet.

        Keyed on what the attempt was SENT -- the model, and for a tailed prompt
        the ending its connection chose -- rather than the model id alone: a
        fallback on the primary's model with another `prefill` setting is sent
        another tail, which the primary's record does not show (codex, #458).
        Marked before the call, so an observer reading the variant back through
        `for_connection` does not notify again."""
        if key in self._notified:
            return
        self._notified.add(key)
        if self.on_variant is not None:
            try:
                self.on_variant(model, deepcopy(breakdown))
            except Exception:  # noqa: BLE001 - optional observers must not abort provider dispatch
                # Recording is observability; its failure cannot turn a
                # usable fallback into another provider failure.
                _log.exception("Could not record model prompt variant")

    def with_tails(self, tails: dict[str, list[dict]], choose: Callable[[Any], str],
                   primary: dict | wire.Target) -> PreparedMessages:
        """A copy whose sent messages end in one of `tails`, chosen per attempt.

        "Keep writing" sends a partial reply either as a prefill (the reply is
        the last message) or followed by an instruction to continue it, and
        which one a route can take depends on that route's connection -- so a
        fallback of another sort has to get its own ending, not the primary's.
        `choose(attempt)` names the tail for an attempt -- a `wire.Target`, or
        the dict a call handed the facade through its shim; `primary` is the one
        the call starts on, and the list body (what the prompt log and a fake
        LLM see) is what that primary attempt sends.

        Built from the factory rather than `snapshot()`, which refuses a prompt
        that was never frozen. No breakdown: the old one does not measure the
        appended tail, so callers get None rather than a falsely precise total.
        """
        copy = PreparedMessages(self._primary_model, self._factory,
                                profiles=self._frozen_profiles, campaign=self.campaign)
        copy._tails = deepcopy(tails)
        copy._choose = choose
        copy._primary_tail = choose(primary)
        copy.breakdown = None
        copy.settings = deepcopy(self.settings)
        copy[:] = [*copy, *deepcopy(tails[copy._primary_tail])]
        return copy

    def for_connection(self, conn: dict | wire.Target, model: str) -> list[dict]:
        """`for_model`, plus the tail this attempt's connection chooses.

        `on_variant` fires once per (model, tail) the primary attempt was not
        sent -- so a same-model fallback whose connection takes the other
        ending is recorded too. An observer recording a tailed prompt reads
        `for_connection` with the fallback's connection
        (`character_turns._capture` does), so the prompt log holds the ending
        that fallback was really sent."""
        if self._tails is None or self._choose is None:
            return self.for_model(model)
        mode = self._choose(conn)
        messages, breakdown = self._variant(model)
        if (model, mode) != (self._primary_model, self._primary_tail):
            self._notify((model, mode), model, breakdown)
        return [*deepcopy(messages), *deepcopy(self._tails[mode])]

    def for_target(self, target: wire.Target) -> list[dict]:
        """`for_connection` for one typed attempt: the target is what the
        tail chooser is handed, and its model -- the one sent -- picks the
        variant."""
        return self.for_connection(target, target.model)

    def mode_for(self, conn: dict | wire.Target) -> str | None:
        """The tail `conn` would be sent, or None for an untailed prompt."""
        if self._tails is None or self._choose is None:
            return None
        return self._choose(conn)

    def any_variant(self, test: Callable[[list[dict]], bool]) -> bool:
        """Whether `test` holds for any variant this prompt could send, without
        building or recording one: every frozen profile when there are some,
        else the primary. A fallback's variant is packed for its own model, so
        it can keep what the primary's packing gave up."""
        if self._frozen_profiles is None:
            return test(list(self))
        return any(test(messages) for messages, _breakdown in self._frozen_profiles.values())

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
    def from_snapshot(cls, snapshot: dict, model: str, campaign: str = "") -> PreparedMessages:
        """Restore a writer without reconstructing the past from current files."""
        if snapshot.get("version") != 1 or "unprofiled" not in snapshot:
            raise ValueError("Unsupported frozen prompt snapshot")
        frozen = deepcopy({"": snapshot["unprofiled"], **snapshot.get("profiles", {})})
        def select(selected_model):
            return frozen.get(_ALIASES.get(selected_model, selected_model), frozen[""])
        return cls(model, select, profiles=frozen, campaign=campaign)

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
        return self.from_snapshot(snapshot, self._primary_model, campaign=self.campaign)
