"""Start Chat (Sprint 052, first slice): one action from a character, scenario, character + lorebook
bundle or one-line premise to an open, world-less 035 chat. See docs/QUICKSTART.md.

``model`` is pure; ``launcher`` composes 107's ``ChatLibrary`` and 035's ``SceneRunner`` and adds no record
family. Trusted application code; never handed to a worker.
"""

from .launcher import QuickStart
from .model import SELECTION_KINDS, LaunchOptions, Opening, QuickStartRefused, Repair, Selection, Started

__all__ = ["LaunchOptions", "Opening", "QuickStart", "QuickStartRefused", "Repair", "SELECTION_KINDS",
           "Selection", "Started"]
