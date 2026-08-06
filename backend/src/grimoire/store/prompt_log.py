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

What a snapshot is, and is not (both raised in review, both deliberate):

- It records the messages **grimoire composed**, not the bytes a provider
  received. An `openai_compatible` connection with `post_process: "strict"`
  runs `openai_compatible._strict_messages` over the list on the way out --
  folding system blocks into user turns, merging adjacent roles, sometimes
  inserting a `(continue)` turn -- and the Claude path flattens the whole
  conversation into one string (`pack.MESSAGE_OVERHEAD` says the same of its
  own estimate). Capturing post-transform would mean capturing a list with no
  sections left in it, which is the entire vocabulary of the panel this feeds.
  The composition is the layer that can be explained, so it is the layer that
  is recorded.
- `model` is the scene's stamped frontmatter, which is what the live
  `GET .../context` route reports too. It can differ from the model the active
  connection actually used if the connection changed after the scene was
  created. Recording the connection's model *here alone* would make the frozen
  panel and the live panel disagree about the same scene, which is worse than
  the shared inaccuracy; fixing it belongs wherever that field is fixed for
  both.

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
    except (TypeError, ValueError, OSError):
        # OSError as well as the conversion errors, so this function cannot be
        # the thing that fails a turn -- config.md locked by a sync client reads
        # as "use the default", like a malformed number. It does NOT make an
        # unreadable config.md survivable overall: `context._assemble` calls
        # `read_config()` with no guard at all a moment later, so the turn fails
        # there instead. This only keeps the promise THIS module makes.
        return int(default)


#: Longest id `_read_index` will admit. Ids are `f"{n:06d}"` off a counter that
#: advances once per captured turn, so 18 digits is unreachable by orders of
#: magnitude -- it exists only to keep a corrupt file's id out of `int()`.
_MAX_ID_DIGITS = 18


def capturing() -> bool:
    """Whether anything would be recorded at all.

    Asked BEFORE composing, not after: building a breakdown is a full tokenizer
    pass (`context._breakdown`), and on the default unbounded budget the packer
    does no counting of its own — so a route that composed one anyway and let
    `record` discard it would put that cost on every turn of a feature the user
    has switched off.
    """
    return depth() > 0


def _root(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "prompts"


def _index_path(cid: str) -> Path:
    return _root(cid) / "index.json"


def _entry_path(cid: str, eid: str) -> Path:
    return _root(cid) / f"{eid}.json"


def _read_index(cid: str, strict: bool = False) -> dict:
    """The index, or an empty one. A corrupt or unreadable file reads as empty
    rather than raising: this is a debug view beside the campaign, and refusing
    to list turns -- or worse, refusing to record one -- is a poor trade for a
    file nothing else depends on. The next capture rebuilds it.

    `strict` re-raises an OSError instead, for the one caller that cannot
    tolerate a false empty: `forget_scene`. There, "no rows for this scene" is
    what lets `delete_scene` free a recycled id, so a transiently locked index
    read as empty would leave the rows on disk and hand them to the next scene
    to take that id -- the exact hazard the write-side raise closes, reached
    through the read instead.

    Only OSError, deliberately. A structurally corrupt index is not transient:
    raising on it would block every scene deletion in the campaign forever,
    while its rows are invisible to `list_entries` anyway (which reads through
    this same empty result), so a recycled id inherits nothing a user can see.
    A locked file, by contrast, becomes readable again -- and that is precisely
    when the stale rows would surface.
    """
    p = _index_path(cid)
    if not p.exists():
        return {"next": 1, "entries": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError:
        if strict:
            raise
        return {"next": 1, "entries": []}
    except ValueError:
        # ValueError, not JSONDecodeError: `read_text` raises UnicodeDecodeError
        # on invalid UTF-8 (a sync conflict, a hand edit), which is a ValueError
        # and not an OSError -- so the narrower catch let it escape `record` and
        # fail every generating turn over an unreadable debug file. Both are the
        # same thing to this module: a file it cannot use.
        return {"next": 1, "entries": []}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return {"next": 1, "entries": []}
    # Ids must be path-safe, numeric, AND short. Numeric because `next` is
    # derived from them below, and `int("legacy")` raising there would escape
    # `record`'s OSError-only guard and 500 a chat turn -- turning a hand-edited
    # debug file into a failed generation, which is the one thing this module
    # promises not to do.
    #
    # Short for the same reason and not for tidiness: `safe_id` caps nothing,
    # `"1" * 5000` is `isdigit()`, and CPython refuses `int()` on a string over
    # `sys.int_info.str_digits_check_threshold` (4300 by default) -- with a
    # ValueError, through the same hole. `_MAX_ID_DIGITS` is far past any id
    # this module could ever have written, so anything longer is corruption by
    # definition. A row failing any of the three is dropped, like any other.
    entries = [e for e in data["entries"] if _well_formed_row(e)]
    try:
        nxt = int(data.get("next", 1))
    except (TypeError, ValueError):
        nxt = 1
    # Never below what the surviving rows already use, or a rebuilt index
    # reissues a live id and two turns share one payload file.
    return {"next": max(nxt, *(int(e["id"]) + 1 for e in entries)) if entries else max(nxt, 1),
            "entries": entries}


#: An index row's UI-facing fields and their types. `SceneInspector` renders
#: `TASK_LABELS[t.task] ?? t.task` and `whenLabel(t.ts)` straight into the DOM,
#: and React throws outright on an object child -- so a hand-edited row carrying
#: `"task": {}` would take the whole inspector down the moment Turn history is
#: opened. Same judgement as `_well_formed` makes for a payload: being strict
#: here is what lets the frontend stay trusting.
_ROW_FIELD_TYPES = {"scene": str, "task": str, "ts": str, "model": str,
                    "total_tokens": int, "dropped_tokens": int, "budget_tokens": int}


def _well_formed_row(e: object) -> bool:
    """Whether an index row can be listed without risking the panel.

    The id carries three extra requirements of its own: path-safe (it names a
    file), numeric and short (`next` is derived from it, and `int()` refuses a
    string over 4300 digits -- see the module's other guards).
    """
    if not isinstance(e, dict):
        return False
    eid = e.get("id")
    if not (safe_id(eid) and str(eid).isdigit() and len(str(eid)) <= _MAX_ID_DIGITS):
        return False
    return all(isinstance(e.get(k), t) for k, t in _ROW_FIELD_TYPES.items())


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

    Never raises, and never WAITS. A debug view that can fail a generation is a
    worse bug than the one it exists to diagnose, so every storage failure -- a
    full disk, a read-only store -- costs the snapshot and nothing else.

    Contention is the same rule taken seriously. This runs synchronously on the
    generating path, before the route returns its streaming response, so a
    blocking acquisition would stall the turn for the whole `LOCK_TIMEOUT` (30s)
    whenever another process or a long absorb holds the campaign lock -- and
    then swallow `StoreBusy` and discard the snapshot anyway. Half a minute of
    dead air before the model is even called is not "costs the snapshot and
    nothing else". So the lock is taken NON-BLOCKING and a contended campaign
    simply goes unrecorded, which is what the retention window makes survivable.
    Reentrant acquisition still succeeds: the underlying RLock grants a
    non-blocking request to a thread that already owns it.
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
        with locks.campaign_lock_nowait(cid) as got:
            if not got:
                return None               # contended: skip, never stall the turn
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
    except OSError:
        return None


def list_entries(cid: str, sid: str) -> list[dict]:
    """This scene's snapshots, newest first. Rows only -- no section text."""
    return [e for e in reversed(_read_index(cid)["entries"]) if e.get("scene") == sid]


#: Every field the inspector dereferences without checking, and the type it
#: assumes. `ContextBreakdown` calls `ctx.total_tokens.toLocaleString()` and
#: `ctx.sections.map(...)` the moment a snapshot is selected, so a payload that
#: is valid JSON but the wrong shape -- `{}` after a hand edit or a sync
#: conflict -- would take the whole inspector down rather than reading as a
#: debug entry that is simply unavailable. Being strict HERE is what lets the
#: frontend stay trusting.
_ROW_TYPES = {"label": str, "text": str, "tier": str,
              "dropped": bool, "trimmed": int, "tokens": int}
_TOTAL_KEYS = ("total_tokens", "dropped_tokens", "budget_tokens")


def _well_formed(data: object) -> bool:
    """Whether a payload can be served to the inspector without crashing it.

    Only the fields the panel dereferences are required; unknown extras pass
    through untouched, so a payload written by a later version stays readable.
    """
    if not isinstance(data, dict):
        return False
    if not all(isinstance(data.get(k), int) for k in _TOTAL_KEYS):
        return False
    rows = data.get("sections")
    if not isinstance(rows, list):
        return False
    return all(isinstance(r, dict)
               and all(isinstance(r.get(k), t) for k, t in _ROW_TYPES.items())
               for r in rows)


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
    except (ValueError, OSError):   # ValueError covers UnicodeDecodeError -- see _read_index
        return None
    return data if _well_formed(data) else None


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids. Index only: the payloads carry the same field,
    but nothing reads it off them -- the list view is the index, and a payload
    is only ever fetched by the id the index handed out.

    Never raises, unlike `forget_scene` -- and unlike its siblings in the
    `scene_refs.repoint` fan-out. The reason is the call order in
    `scenes.lifecycle`: every transcript is renamed on disk BEFORE `repoint`
    runs (the same reason `alternates.clear_destinations` is hoisted above the
    renames with a comment saying so). Raising from here would abort a rename
    with the `.md` files already moved and the other stores already repointed,
    trading stale debug rows for a half-renamed campaign -- the transcript being
    the one artifact that cannot be regenerated.

    So the failure path DROPS the rows instead of leaving them keyed to an id
    that has moved on. Stale rows are not merely unlisted: scene ids are
    recycled by number and title, so a rename that failed to repoint, followed
    by a delete and a same-titled recreation, hands the new scene the old id and
    with it the old prompts. Losing snapshots is this module's accepted cost;
    showing another scene's is not. If the drop cannot be written either -- most
    likely, since it is the same file that just refused -- the rename still
    stands and the rows are no worse than before.
    """
    try:
        with locks.campaign_lock(cid):
            # Strict, for the same reason `forget_scene` is: a lenient read turns
            # a locked index into an empty one, and this function would then
            # return having neither repointed the rows nor dropped them -- exiting
            # above the `_write_index` fallback that is supposed to catch exactly
            # this. The raise lands in the `except` below, which still cannot
            # propagate (see above), but it reaches `_drop_scenes` on the way.
            index = _read_index(cid, strict=True)
            if not any(row.get("scene") in mapping for row in index["entries"]):
                return
            for row in index["entries"]:
                if row.get("scene") in mapping:
                    row["scene"] = mapping[row["scene"]]
            try:
                _write_index(cid, index)
            except OSError:
                _drop_scenes(cid, set(mapping))
    except (OSError, locks.StoreBusy):
        # Either the strict read above or the drop below it. One last attempt to
        # invalidate the old ids, because leaving them is the outcome that hands
        # a recreated scene someone else's prompts; if this fails too the file is
        # simply unavailable and the rename still stands.
        try:
            _drop_scenes(cid, set(mapping))
        except (OSError, locks.StoreBusy):
            pass


def _drop_scenes(cid: str, scenes: set[str]) -> None:
    """Best-effort removal of every row for `scenes`. The fallback when a
    repoint cannot be written or read -- see `repoint_scenes`.

    Takes the lock itself because it is reached two ways: from inside
    `repoint_scenes`' own hold, where it is free (reentrant), and from that
    function's outer `except`, where the hold has already been unwound and this
    would otherwise read-modify-write the index unserialized."""
    with locks.campaign_lock(cid):
        index = _read_index(cid)
        gone = [e for e in index["entries"] if e.get("scene") in scenes]
        if not gone:
            return
        index["entries"] = [e for e in index["entries"] if e.get("scene") not in scenes]
        _write_index(cid, index)
        for row in gone:
            _unlink(cid, row["id"])


def forget_scene(cid: str, sid: str) -> None:
    """Drop a deleted scene's snapshots.

    **Raises**, unlike everything else here, and the exception is the point.
    Scene ids are recycled -- `delete_scene` says so itself -- so rows left
    behind are adopted by the next scene to take this id, which then lists
    someone else's prompts under its own Turn history until retention happens to
    evict them. That is the same hazard `delete_scene` already refuses to accept
    for the reroll-alternates sidecar ("that orphan would be adopted by the next
    scene to take this id"), and it gets the same answer: fail before the
    transcript is unlinked, leaving the scene intact and the id unfreed, rather
    than free an id whose old prompts are still on file.

    Only the index write is load-bearing. The payloads are unlinked
    best-effort afterwards: once the index no longer names them nothing can
    reach them, so a payload that will not go is invisible litter rather than
    another scene's prompt.
    """
    with locks.campaign_lock(cid):
        # Strict: an index that cannot be READ would otherwise come back empty,
        # `gone` would be empty, and the delete would proceed to free the id
        # with the rows still on disk -- the write-side raise below stepped over
        # entirely. See `_read_index`.
        index = _read_index(cid, strict=True)
        gone = [e for e in index["entries"] if e.get("scene") == sid]
        if not gone:
            return
        index["entries"] = [e for e in index["entries"] if e.get("scene") != sid]
        _write_index(cid, index)
        for row in gone:
            _unlink(cid, row["id"])
