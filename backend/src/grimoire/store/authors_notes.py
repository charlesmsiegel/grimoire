"""Author's notes (play controls V): short standing instructions the context
builder inserts into the history at a chosen depth. Stored at
<campaign>/authors_notes.json.

    {"campaign":   {"text": str, "depth": int, "every": int},
     "scenes":     {"<scene identity>": note},
     "characters": {"characters:<id>": note}}

`text` is at most `MAX_TEXT` characters and may span lines -- which is why this
is JSON and not frontmatter, which is single-line by construction. `depth` is
posts from the end (`0` is after the last post); `every` is the cadence, a note
applying on every Nth turn (`applies`).

**Scene notes are keyed by identity, never by sid.** A sid moves on a rename
and on the first date stamp, and is recycled after a delete; the identity token
does neither. So a rename needs nothing from here, `scenes.lifecycle.
delete_scene` calls `drop` (a replacement scene gets a fresh identity and could
never read the note anyway -- dropping it is housekeeping), and branching calls
`copy_scene` so a sibling keeps its source's steer. A fork copies this file
with the rest of the campaign directory, and a retrospective fork's cut goes
through `delete_scene`, so the scenes it takes off lose their notes.

**Character notes are per campaign**, keyed by the full actor ref the response
ledger uses: the same character can need different steering in two campaigns.

**Reads are lenient and lock-free.** `read` sits on the path that composes a
turn -- in a worker, the opener's included, where a lock wait would hold the turn
being built -- so a missing file, unparseable JSON, a document of the
wrong shape or a malformed entry all read as no note, never as a failed turn.

**The mutators read leniently too**, so saving over a garbled file replaces it.
That is a decision rather than an accident: these are short notes the player
can retype, and refusing to write would leave the panel with no way to repair
the file. Every mutator rewrites the whole file under `locks.campaign_lock(cid)`
through `atomic`, so two concurrent saves cannot lose one of them.

Nothing here imports `store.scenes`: the identity is the caller's to resolve.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import atomic, locks
from .campaigns import paths as campaigns_paths

FILENAME = "authors_notes.json"
MAX_TEXT = 2000
DEPTH_MAX = 50
EVERY_MAX = 50
DEFAULT_DEPTH = 4
DEFAULT_EVERY = 1

#: The levels, in the order notes at one insertion point are placed.
LEVELS = ("campaign", "scene", "character")


def _path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / FILENAME


def _int(value: object, low: int, high: int, default: int) -> int:
    """A stored int clamped into range; `default` when missing or not an int
    (a bool is not one). `0` is a real depth, so it is kept, not defaulted."""
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return min(max(value, low), high)


def normalize(raw: object) -> dict | None:
    """A note as the store keeps it, or None when `raw` is not one: a dict whose
    `text` is a string with something in it. Text past `MAX_TEXT` is cut; depth
    and cadence are clamped, and default when missing or malformed."""
    if not isinstance(raw, dict):
        return None
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    return {"text": text[:MAX_TEXT],
            "depth": _int(raw.get("depth"), 0, DEPTH_MAX, DEFAULT_DEPTH),
            "every": _int(raw.get("every"), 1, EVERY_MAX, DEFAULT_EVERY)}


def _notes(raw: object) -> dict[str, dict]:
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in raw.items():
        note = normalize(value)
        if isinstance(key, str) and note is not None:
            out[key] = note
    return out


def read(cid: str) -> dict:
    """Every note this campaign holds, always in the full shape:
    `{"campaign": note | None, "scenes": {identity: note}, "characters": {ref: note}}`.
    Never raises for a missing, unreadable or malformed file -- see the module
    docstring."""
    try:
        data = json.loads(_path(cid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        data = {}
    return {"campaign": normalize(data.get("campaign")),
            "scenes": _notes(data.get("scenes")),
            "characters": _notes(data.get("characters"))}


def _write(cid: str, data: dict) -> None:
    atomic.write_text(_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")


def set_campaign(cid: str, note: dict | None) -> None:
    """Set the campaign's note; None (or one with no text) clears it."""
    with locks.campaign_lock(cid):
        data = read(cid)
        data["campaign"] = normalize(note)
        _write(cid, data)


def _set_keyed(cid: str, section: str, key: str, note: dict | None) -> None:
    with locks.campaign_lock(cid):
        data = read(cid)
        clean = normalize(note)
        if clean is None:
            data[section].pop(key, None)
        else:
            data[section][key] = clean
        _write(cid, data)


def set_scene(cid: str, identity: str, note: dict | None) -> None:
    """Set the note for the scene with this identity; None clears it."""
    _set_keyed(cid, "scenes", identity, note)


def set_character(cid: str, ref: str, note: dict | None) -> None:
    """Set the note for the actor `ref` (`characters:<id>`); None clears it."""
    _set_keyed(cid, "characters", ref, note)


def drop(cid: str, identity: str) -> None:
    """Forget a deleted scene's note. A scene with none is a no-op that writes
    nothing."""
    with locks.campaign_lock(cid):
        data = read(cid)
        if data["scenes"].pop(identity, None) is not None:
            _write(cid, data)


def copy_scene(cid: str, src_identity: str, dst_identity: str) -> None:
    """Give a branch its source's scene note (play controls III, branching
    spec resolution 16). A source with no note is a no-op."""
    with locks.campaign_lock(cid):
        data = read(cid)
        note = data["scenes"].get(src_identity)
        if note is None:
            return
        data["scenes"][dst_identity] = dict(note)
        _write(cid, data)


def applies(note: dict, turn: int) -> bool:
    """Whether `note` is due on turn `turn`. Turn 0 -- a scene with no player
    post or director note yet, and every opener -- is an explicit exception
    rather than `0 % every == 0`: only an every-turn note applies there."""
    every = note["every"]
    return every == 1 if turn == 0 else turn % every == 0
