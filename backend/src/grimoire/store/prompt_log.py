"""Per-turn prompt snapshots: what the model saw, frozen when it saw it (#157).

`GET /campaigns/{cid}/scenes/{sid}/context` composes the prompt *now*, which
answers "what would the model see if I sent a turn this second" -- not the
question anyone debugging a bad reply is asking. By then the chronicle has
another entry, character state has moved, and world-info activation has moved
with it.

Recomputation cannot stand in for capture, and that is what rules out the cheap
version of this feature: `context.macros.expand_macros` resolves
`{{random:a,b}}` and `{{roll:1d20}}` at render time, so two passes over an
*identical* store still produce different text. A prompt that was sent is only
knowable by having recorded it.

Layout, under `<campaign>/prompts/`:

    index.json      {"next": <int>, "entries": [<row>, ...]}   oldest first
    <id>.json       one frozen `context.context_breakdown` payload

The index carries the list view (`id`, `scene`, `ts`, `task`, `model` and the
three totals) and never the section text, so listing a scene's turns reads one
small file rather than every payload. Entries are keyed by a `scene` FIELD
rather than by a per-scene directory, which is what lets this store join the
fan-out in `scene_refs.repoint` instead of needing a directory move on every
rename the way `alternates`' filename-keyed sidecar does.

`next` only ever increases: a pruned id is never reissued, so a URL for an
evicted turn 404s instead of quietly resolving to a different one.

Retention is `prompt_log_depth` in config.md (default 50, 0 = off), counted per
CAMPAIGN rather than per scene. Per-scene reads better but is unbounded across
a library -- 100 scenes at 20 entries each is 2000 payloads of tens of KB, in a
store whose whole premise is that a human can read it. The cost of the campaign
window, stated rather than hidden: playing one scene long enough evicts
another's snapshots. This is a rolling debug window; #150 is where a durable
ledger belongs.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import atomic, config, locks
from .campaigns import paths as campaigns_paths
from .paths import safe_id

def depth() -> int:
    """How many snapshots to keep; 0 disables capture entirely.

    A hand-edited config.md holding nonsense falls back to the default rather
    than raising -- the same judgement `pack.budget_tokens` makes, and for the
    same reason: a malformed number must not take scene generation down with
    it.
    """
    default = config.DEFAULT_PROMPT_LOG_DEPTH
    try:
        return max(int(config.read_config().get("prompt_log_depth", default)), 0)
    except (TypeError, ValueError):
        return int(default)


def _root(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "prompts"


def _index_path(cid: str) -> Path:
    return _root(cid) / "index.json"


def _entry_path(cid: str, eid: str) -> Path:
    return _root(cid) / f"{eid}.json"


def _read_index(cid: str) -> dict:
    """The index, or an empty one. A corrupt or unreadable file reads as empty
    rather than raising: this is a debug view beside the campaign, and refusing
    to list turns -- or worse, refusing to record one -- is a poor trade for a
    file nothing else depends on. The next capture rebuilds it."""
    p = _index_path(cid)
    if not p.exists():
        return {"next": 1, "entries": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"next": 1, "entries": []}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return {"next": 1, "entries": []}
    # Ids must be BOTH path-safe and numeric. Numeric because `next` is derived
    # from them below, and `int("legacy")` raising there would escape `record`'s
    # OSError-only guard and 500 a chat turn -- turning a hand-edited debug file
    # into a failed generation, which is the one thing this module promises not
    # to do. A row that fails either test is dropped, like any other corruption.
    entries = [e for e in data["entries"]
               if isinstance(e, dict) and safe_id(e.get("id")) and str(e["id"]).isdigit()]
    try:
        nxt = int(data.get("next", 1))
    except (TypeError, ValueError):
        nxt = 1
    # Never below what the surviving rows already use, or a rebuilt index
    # reissues a live id and two turns share one payload file.
    return {"next": max(nxt, *(int(e["id"]) + 1 for e in entries)) if entries else max(nxt, 1),
            "entries": entries}


def _write_index(cid: str, index: dict) -> None:
    _root(cid).mkdir(parents=True, exist_ok=True)
    atomic.write_text(_index_path(cid), json.dumps(index, indent=2) + "\n")


def _unlink(cid: str, eid: str) -> None:
    """Best effort. A payload that will not go leaves litter; a payload whose
    unlink raised would leave the index already rewritten without it, which is
    the same litter plus a failed capture."""
    try:
        _entry_path(cid, eid).unlink(missing_ok=True)
    except OSError:
        pass


def record(cid: str, sid: str, task: str, breakdown: dict, model: str = "") -> str | None:
    """Freeze one turn's composition. Returns the new entry id, or None when
    nothing was recorded.

    `breakdown` is `context.compose_turn`'s second return value -- the SAME
    assemble/pack pass that produced the messages being sent, which is what
    keeps the record and the request from describing different prompts.

    Never raises. A debug view that can fail a generation is a worse bug than
    the one it exists to diagnose, so every storage failure -- a full disk, a
    read-only store, a contended cross-process lock -- costs the snapshot and
    nothing else.
    """
    keep = depth()
    if keep <= 0:
        return None
    row = {"scene": sid, "task": task, "model": model,
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "total_tokens": breakdown.get("total_tokens", 0),
           "dropped_tokens": breakdown.get("dropped_tokens", 0),
           "budget_tokens": breakdown.get("budget_tokens", 0)}
    try:
        with locks.campaign_lock(cid):
            index = _read_index(cid)
            eid = f"{index['next']:06d}"
            index["next"] += 1
            index["entries"].append({"id": eid, **row})
            # The payload lands BEFORE the index names it, so a failure between
            # the two leaves an unreferenced file rather than an index row whose
            # payload 404s. That orphan is bounded at one, not leaked forever:
            # the index write is what advances `next`, so the failing capture's
            # id is handed straight back to the following one, whose payload
            # write replaces the orphan in place.
            #
            # Deliberately WITHOUT `scene`: the index owns that field, because
            # the index is what `repoint_scenes` rewrites. A copy in the payload
            # would go stale on the first scene rename, and a reader trusting it
            # would then refuse the entry it had just listed.
            _root(cid).mkdir(parents=True, exist_ok=True)
            payload = {k: v for k, v in row.items() if k != "scene"}
            atomic.write_text(_entry_path(cid, eid),
                              json.dumps({"id": eid, **payload, **breakdown}, indent=2) + "\n")
            evicted = index["entries"][:max(0, len(index["entries"]) - keep)]
            index["entries"] = index["entries"][len(evicted):]
            _write_index(cid, index)
            for old in evicted:
                _unlink(cid, old["id"])
            return eid
    except (OSError, locks.StoreBusy):
        return None


def list_entries(cid: str, sid: str) -> list[dict]:
    """This scene's snapshots, newest first. Rows only -- no section text."""
    return [e for e in reversed(_read_index(cid)["entries"]) if e.get("scene") == sid]


def read_entry(cid: str, eid: str, scene: str | None = None) -> dict | None:
    """One frozen breakdown, or None when it never existed or has been evicted.

    `scene` scopes the read, and a caller serving a per-scene URL must pass it:
    ids are campaign-scoped, so without it any scene's path would serve any
    entry. The INDEX answers which scene an entry belongs to — the payload does
    not carry the field at all, precisely so it cannot disagree after a rename.
    """
    if not safe_id(eid):
        return None
    if scene is not None and not any(
            e["id"] == eid and e.get("scene") == scene for e in _read_index(cid)["entries"]):
        return None
    p = _entry_path(cid, eid)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids. Index only: the payloads carry the same field,
    but nothing reads it off them -- the list view is the index, and a payload
    is only ever fetched by the id the index handed out.

    Never raises, unlike its siblings in the `scene_refs.repoint` fan-out. They
    hold play records a rename must not silently desynchronise; this holds
    debug snapshots, and failing a rename over one would be the worse outcome.
    An unwritten repoint leaves entries pointing at a scene id that no longer
    exists, so they simply stop being listed -- the harmless direction.
    """
    try:
        with locks.campaign_lock(cid):
            index = _read_index(cid)
            hit = False
            for row in index["entries"]:
                if row.get("scene") in mapping:
                    row["scene"] = mapping[row["scene"]]
                    hit = True
            if hit:
                _write_index(cid, index)
    except (OSError, locks.StoreBusy):
        pass


def forget_scene(cid: str, sid: str) -> None:
    """Drop a deleted scene's snapshots. Scene ids are recycled (see
    `scenes.lifecycle.delete_scene`), so leaving them would show the next scene
    to take this id someone else's prompts.

    Never raises, for the reason `repoint_scenes` doesn't: `delete_scene` calls
    this between two unlinks it *does* let fail, and a snapshot that will not go
    is not worth refusing a scene deletion over. The stale rows are dropped by
    the next capture that evicts past them.
    """
    try:
        with locks.campaign_lock(cid):
            index = _read_index(cid)
            gone = [e for e in index["entries"] if e.get("scene") == sid]
            if not gone:
                return
            index["entries"] = [e for e in index["entries"] if e.get("scene") != sid]
            _write_index(cid, index)
            for row in gone:
                _unlink(cid, row["id"])
    except (OSError, locks.StoreBusy):
        pass
