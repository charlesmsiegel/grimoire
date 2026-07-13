"""Bulk scene-id repointing across every store that persists scene ids.

A scene's id is its filename stem, so file renames (title renames, first-date
stamps, width re-pads, legacy migration) must be followed by every persisted
reference. Six stores hold scene ids: appearances (per-actor scenes lists),
audit (sheet baselines keyed by scene id), chronicle (record keys + id
fields), changes (per-record scene field), plot (beats[].scene +
last_scene), and rolls (per-entry scene field). Callers rename the files
themselves.
"""

from __future__ import annotations

from . import appearances, audit, changes, chronicle, plot, rolls


def repoint(cid: str, mapping: dict[str, str]) -> None:
    mapping = {old: new for old, new in mapping.items() if old != new}
    if not mapping:
        return
    for mod in (appearances, audit, changes, chronicle, plot, rolls):
        mod.repoint_scenes(cid, mapping)
