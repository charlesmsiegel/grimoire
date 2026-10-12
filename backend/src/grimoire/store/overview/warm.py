"""Warming overview artifacts at write time (spec 04 section 5, 04-C2a).

The server needs no retirement step: every projection is reached through a key
computed from the current bytes (03-C2), so a write makes the old artifact
unreachable the moment it lands. What a write CAN do is make the next read a
hit, by storing the artifact for the bytes it just wrote (03-C5). 04 wires that
at the one site where it pays on every turn -- `routes/streaming.
_fire_follow_up`, after the turn's outcome is decided -- and provides
`warm_paths` for 05 to register as its `overview` `WarmHook`.

Both are best effort and never raise. Both read the files back, so what they
store is keyed on the bytes actually on disk: a newline-translating write, or
a second writer landing in between, cannot make a warm vouch for bytes it did
not read. Neither takes a lock or a run's exclusion key, and neither records a
`sources` row (spec 03 section 5, rule 4: a file just written is inside the
racy window). A kind that is not registered (`compiled.KINDS`) has nothing
persisted to warm.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .. import compiled, ledger_texts, logs
from ..appearances import cast
from ..campaigns import paths as campaigns_paths
from ..paths import safe_id
from ..scenes import paths as scenes_paths
from ..worlds import paths as worlds_paths
from . import kinds, persist
from . import read as overview_read

#: The continuity files by their file name, as `warm_paths` meets them.
_CONTINUITY_FILES = {
    "plot.json": "plot", "commitments.json": "commitments", "events.json": "events",
    "continuity.json": "continuity", "continuity_candidates.json": "candidates",
}


def _usable(kind: compiled.Kind) -> bool:
    return persist.registered(kind) and compiled.available()


def _store(kind: compiled.Kind, inputs: list[compiled.WrittenInput], value: object,
           params: dict) -> None:
    with compiled.batch() as request:
        request.store_written(kind, inputs, value, params=params)


def _warm_scene(cid: str, sid: str) -> bool:
    kind = compiled.OVERVIEW_SCENE_TURNS
    if not _usable(kind):
        return False
    p = scenes_paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        return False
    # The names the shell will ask with. A cast change before that read
    # misses on `players` and computes: correct, only not warm.
    if p.stat().st_size < overview_read.SCENE_TURNS_PERSIST_MIN_BYTES:
        return False   # a short scene is never persisted: nothing to warm
    players = tuple(sorted(cast.player_names(cid, sid)))
    data = p.read_bytes()
    if len(data) < overview_read.SCENE_TURNS_PERSIST_MIN_BYTES:
        return False
    _store(kind, [compiled.WrittenInput("scene", p, data)],
           kinds.scene_turns(data, players), {"players": list(players)})
    return True


def warm_scene(cid: str, sid: str) -> None:
    """Store `scene_turns` for the scene file as it now is.

    Best effort, and never raises: a failed warm is a later miss, and nothing
    else. Reads the file back, records no `sources` row, holds no lock."""
    try:
        _warm_scene(cid, sid)
    except Exception as e:  # noqa: BLE001 -- a warm is never worth the turn
        logs.record("debug", "overview", "post-turn warm skipped", kind="overview_warm",
                    error=type(e).__name__)


def _warm_meta(kind: compiled.Kind, mp: Path, parse) -> bool:
    if not _usable(kind):
        return False
    data = mp.read_bytes()
    _store(kind, [compiled.WrittenInput("meta", mp, data)], parse(ledger_texts.decode(data)), {})
    return True


def _warm_continuity(cid: str) -> bool:
    """Through `derive`, which reads the five files back exactly as a read
    would and stores on its miss. Not `store_written`: an absent ledger is
    keyed as the explicit absent sentinel (03-C1 as amended), and a written
    input is bytes, so it could not key a campaign missing any of the five
    the way the next read will."""
    kind = compiled.OVERVIEW_CONTINUITY
    if not _usable(kind):
        return False
    paths = kinds.continuity_paths(cid)
    with compiled.batch() as request:
        request.derive(kind, [compiled.Input(name, path) for name, path in paths.items()], params={},
                       compute=lambda view, _params: kinds.continuity(
                           cid, {item.role: item.data for item in view.files}))
    return True


def _instance(path: str) -> tuple[str, tuple[str, ...]] | None:
    """`(kind, instance)` for a store-relative path an overview kind reads, or
    None. The mapping is spec 04 section 5.3's table."""
    parts = path.strip("/").split("/")
    if len(parts) == 3 and parts[0] == "worlds" and parts[2] == "world.md" and safe_id(parts[1]):
        return "world_row", (parts[1],)
    if len(parts) == 3 and parts[0] == "campaigns" and safe_id(parts[1]):
        if parts[2] == "campaign.md":
            return "campaign_row", (parts[1],)
        if parts[2] in _CONTINUITY_FILES:
            return "continuity_summary", (parts[1],)
    if (len(parts) == 4 and parts[0] == "campaigns" and parts[2] == "scenes" and safe_id(parts[1])
            and parts[3].endswith(".md") and safe_id(parts[3][:-3])):
        return "scene_turns", (parts[1], parts[3][:-3])
    return None


def _warm(kind: str, instance: tuple[str, ...]) -> bool:
    if kind == "world_row":
        return _warm_meta(compiled.OVERVIEW_WORLD_ROW, worlds_paths.world_meta_path(instance[0]),
                          kinds.world_row)
    if kind == "campaign_row":
        return _warm_meta(compiled.OVERVIEW_CAMPAIGN_ROW,
                          campaigns_paths.campaign_meta_path(instance[0]), kinds.campaign_row)
    if kind == "continuity_summary":
        return _warm_continuity(instance[0])
    return _warm_scene(*instance)


def warm_paths(paths: Iterable[str]) -> dict[str, str]:
    """Rebuild and store the overview artifacts derived from `paths` (store-
    relative, `/`-separated). Returns one outcome per path: "warmed", "none"
    (no persisted overview kind reads it -- entity, character, greeting, image
    and PC paths among them, since a world's counts are live listings) or
    "failed". Instances are coalesced: five continuity paths for one campaign
    warm one `continuity_summary`. Never raises; holds no lock."""
    by_instance: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    out: dict[str, str] = {}
    for path in paths:
        try:
            found = _instance(path)
        except Exception:  # noqa: BLE001 -- a path this cannot parse reads nothing
            found = None
        if found is None:
            out[path] = "none"
        else:
            by_instance.setdefault(found, []).append(path)
    for (kind, instance), members in by_instance.items():
        try:
            outcome = "warmed" if _warm(kind, instance) else "none"
        except Exception:  # noqa: BLE001 -- a failed warm is a later miss, and nothing else
            outcome = "failed"
        for path in members:
            out[path] = outcome
    return out
