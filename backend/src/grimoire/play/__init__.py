"""The headless text scene runner (Sprint 035): plays a chat's turns over one open library.

An adapter: it joins 022's chat snapshot, 003's sovereignty ledger, 033's party, 032's reader and
transmission, 031's context packs, 027's briefs and runtime and the ``turns/`` family. See docs/SCENES.md.
"""

from .runner import DEFAULT_BUDGET, Scene, SceneRunner

__all__ = ["DEFAULT_BUDGET", "Scene", "SceneRunner"]
