"""Bulk scene-id repointing across every store that persists scene ids.

A scene's id is its filename stem, so file renames (title renames, first-date
stamps, width re-pads, legacy migration) must be followed by every persisted
reference. Sixteen stores hold scene ids: appearances (per-actor scenes lists),
audit (sheet baselines keyed by scene id), chronicle (record keys + id
fields), changes (per-record scene field), plot and commitments (both
beats[].scene + last_scene), facts (each fact's recording scene and, once it
is retired, the scene that ended it), journal (the append-only change history's
per-entry scene field), provenance (each citation's scene field, the post it was
quoted from), rolls (per-entry scene field), prompt_log
(the frozen per-turn prompt index's scene field), commits (the per-scene commit
epoch's keys + each token entry's sid), turnstate (the per-turn state ledger,
keyed by scene id then post index), scene_ideas (the scene ledger's
`used_scene`, the scene a saved idea became), pins (each scene-scoped pin or
exclude, which carries its scene id in the record *and* in its key), replay (the
retcon-replay session's scene, whose backlog is the only copy of the posts that
scene's cut removed), and alternates (a
`<sid>.alts.json` sidecar, which moves rather than being rewritten — it is the
one store keyed by *filename* instead of by a field, and so is not reachable
through the fan-out the others share). Callers rename the `.md` files
themselves.

A sixteenth, `usage`, joins the fan-out without rewriting anything: the cost
ledger is append-only and its writes take no lock, so a rewrite would race
them. It appends a row saying the rename happened and its readers follow the
trail (`store.usage.KIND_RENAME`).
"""

from __future__ import annotations

from . import (alternates, changes, chronicle, commitments, commits, facts, journal,
               pins, plot, prompt_log, provenance, replay, rolls, scene_ideas,
               turnstate, usage)
from .appearances import paths as appearances_paths
from .audit import baselines as audit_baselines


def repoint(cid: str, mapping: dict[str, str]) -> None:
    mapping = {old: new for old, new in mapping.items() if old != new}
    if not mapping:
        return
    for mod in (alternates, appearances_paths, audit_baselines, changes, chronicle,
                commitments, commits, facts, journal, pins, plot, prompt_log,
                provenance, replay, rolls, scene_ideas, turnstate, usage):
        mod.repoint_scenes(cid, mapping)
