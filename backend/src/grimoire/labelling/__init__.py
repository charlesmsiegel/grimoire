"""Labelling a player character's action for 029 (035 milestone 2): the facts offered, the prompt, the reply.

Pure: no model call, file or clock. The call is ``grimoire.runtime.LabelRuntime``'s, on an untrusted handle;
the runner (``grimoire.play``) decides what is written. See docs/SCENES.md.
"""

from .facts import MAX_FACTS, MAX_STAKE_CHARS, Fact, facts, label_messages
from .parse import LABEL_REFUSALS, LabelRefused, parse_labels

__all__ = ["LABEL_REFUSALS", "MAX_FACTS", "MAX_STAKE_CHARS", "Fact", "LabelRefused", "facts", "label_messages",
           "parse_labels"]
