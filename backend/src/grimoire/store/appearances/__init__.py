"""Per-campaign actor appearance state: which actors (characters or PCs) appeared, the
locked version, role, sync base hash, and the scenes they're in. Source of truth for
actors in a campaign (the generic sync.md covers only locations/lore).

Stored as <campaign>/appearances.json, keyed "<kind>/<id>":
  {"characters/seraphine": {"version":"corrupted","base":"<h>","scenes":["s1"],"role":"npc"},
   "pcs/elara":            {"version":"default","base":"<h>","scenes":["s1"],"role":"player"}}
"""

from __future__ import annotations

# Submodules before names, and `cast`/`transitions`/`detect` after
# `paths`/`versions`: `cast.py` and `transitions.py` both reach back into
# `paths` (and `transitions.py` into `versions` too), so those two must already
# be bound here first; `detect.py` reads `cast` the same way.
from . import cast, detect, paths, transitions, versions  # noqa: F401
from .cast import (  # noqa: F401
    _actor_name,
    cast_detail,
    is_appeared,
    player_names,
    players_in_scene,
    roster,
    roster_names,
    scene_cast,
)
from .detect import cast_changes  # noqa: F401
from .paths import (  # noqa: F401
    ACTOR_KINDS,
    AppearError,
    _path,
    _ref,
    _split,
    _write,
    locked_actor_root,
    record,
    repoint_scenes,
)
from .transitions import appear, leave, suggestions  # noqa: F401
from .versions import (  # noqa: F401
    _copy_actor,
    _drop_manifest_ref,
    _lock,
    _meta_name,
    _purge_other_versions,
    _set_default,
    _version_ext,
    actor_hash,
    actor_source,
    import_version,
    locked_version,
    pick_version,
    set_base,
)
