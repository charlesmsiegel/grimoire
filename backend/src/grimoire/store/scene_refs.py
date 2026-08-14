"""Bulk scene-id repointing across every store that persists scene ids.

A scene's id is its filename stem, so file renames (title renames, first-date
stamps, width re-pads, legacy migration) must be followed by every persisted
reference. Thirteen stores hold scene ids: appearances (per-actor scenes lists),
audit (sheet baselines keyed by scene id), chronicle (record keys + id
fields), changes (per-record scene field), plot and commitments (both
beats[].scene + last_scene), facts (each fact's recording scene and, once it
is retired, the scene that ended it), journal (the append-only change history's
per-entry scene field), rolls (per-entry scene field), prompt_log
(the frozen per-turn prompt index's scene field), commits (the per-scene commit
epoch's keys + each token entry's sid), turnstate (the per-turn state ledger,
keyed by scene id then post index), scene_ideas (the scene ledger's
`used_scene`, the scene a saved idea became), and alternates (a
`<sid>.alts.json` sidecar, which moves rather than being rewritten — it is the
one store keyed by *filename* instead of by a field, and so is not reachable
through the fan-out the others share). Callers rename the `.md` files
themselves.
"""

from __future__ import annotations

from . import (alternates, changes, chronicle, commitments, commits, facts, journal,
               plot, prompt_log, rolls, scene_ideas, turnstate)
from .appearances import paths as appearances_paths
from .audit import baselines as audit_baselines


def repoint(cid: str, mapping: dict[str, str]) -> None:
    mapping = {old: new for old, new in mapping.items() if old != new}
    if not mapping:
        return
    for mod in (alternates, appearances_paths, audit_baselines, changes, chronicle,
                commitments, commits, facts, journal, plot, prompt_log, rolls,
                scene_ideas, turnstate):
        mod.repoint_scenes(cid, mapping)
