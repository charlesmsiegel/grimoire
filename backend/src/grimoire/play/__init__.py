"""The headless text scene runner (Sprint 035): plays a chat's turns over one open library.

An adapter: it joins 022's chat snapshot, 003's sovereignty ledger, 033's party, 032's reader and
transmission, 031's context packs, 027's briefs and runtime, 041's stage (from the app's ``StageSource``) and the
``turns/`` family. See docs/SCENES.md.
"""

from .boundaries import Boundaries, new_boundary_id
from .runner import SceneRunner
from .scene import DEFAULT_BUDGET, Scene
from .stages import StageBook, StageSource

__all__ = ["DEFAULT_BUDGET", "Boundaries", "Scene", "SceneRunner", "StageBook", "StageSource", "new_boundary_id"]
