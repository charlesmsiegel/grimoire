"""Quick replies: the composer's one-tap buttons, as world and campaign sets.

A quick reply is ``{"id", "label", "kind", ...}`` and its kind decides what else
it carries and what tapping it does:

- ``send`` / ``direct`` (``text``, ``mode``) -- post ``text`` as the player, or
  send it as a director note; ``mode: "insert"`` puts it in the composer instead.
- ``roll`` (``notation``, ``roll_label?``) -- roll a saved dice string through
  the roll route; ``roll_label`` is the transcript label, not the button's.
- ``task`` (``task``) -- run the rolling summary or the scene-break check now,
  or open the next-scene chooser.
- ``opener`` -- open the opener generator on an empty scene.

``plugin`` is a reserved kind: refused until a plugin API exists, so a set
written later by a build that has one stays readable by this one.

**Sets and layering.** A world keeps ``<world>/quick_replies.json`` and a
campaign ``<campaign>/quick_replies.json``, each ``{"version": 1, "replies":
[...]}``. A campaign's effective set is its world's replies in order -- a
campaign reply with the same ``id`` replacing one in place, a campaign
``{"id", "hidden": true}`` entry hiding one -- followed by the campaign's own
remaining replies. That is the tracker-field layering pattern, one domain over.

**Reads salvage; writes refuse.** A missing or garbled file reads as an empty
set and one bad entry costs only itself, because a hand-edited or half-synced
file must not take the composer down with it. An entry of a kind this build
does not know (a future ``plugin``, a newer build's kind) is kept verbatim on
disk and carried across every write, but never shown -- an older build saving
its set must not erase what it cannot display. A write validates the whole set
and refuses any violation (``QuickReplyError``), so nothing invalid is stored.

**Concurrency.** Every write carries ``expect``, the digest of the set the
caller read, and is refused with ``SetChanged`` when the stored set moved since:
override and hide are client read-modify-writes and would otherwise lose an
update. The campaign writer holds ``campaign_lock(cid)`` across the digest check
and the write, so that guarantee is exact there. The world writer takes no lock
-- a world is not campaign-scoped, and ``atomic.write_text`` is what keeps the
file whole -- so its digest check narrows the window rather than closing it,
the same trade the tracker's world layer makes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from pathlib import Path

from . import atomic, dice, locks
from .campaigns import paths as campaigns_paths
from .campaigns import read as campaigns_read
from .worlds import paths as worlds_paths

log = logging.getLogger(__name__)

VERSION = 1
KINDS = ("send", "direct", "roll", "task", "opener")
RESERVED_KINDS = ("plugin",)
TASKS = ("rolling_summary", "scene_break", "next_scene")
MODES = ("send", "insert")
MAX_LABEL = 40
MAX_TEXT = 2000
MAX_ROLL_LABEL = 80
MAX_REPLIES = 50
FILENAME = "quick_replies.json"

_ID = re.compile(r"\A[0-9A-Za-z_-]{1,64}\Z")
_TEXT_KINDS = ("send", "direct")


class QuickReplyError(ValueError):
    """A quick reply or a set that breaks a rule; `code` is the API error kind."""

    def __init__(self, message: str, code: str = "invalid_quick_reply") -> None:
        super().__init__(message)
        self.code = code


def _check_id(raw: dict) -> str:
    given = raw.get("id")
    if given is None or given == "":
        return uuid.uuid4().hex
    if not isinstance(given, str) or not _ID.match(given):
        raise QuickReplyError(f"quick reply id {given!r} must be 1-64 letters, digits, _ or -")
    return given


def _is_hide(raw: dict) -> bool:
    return bool(raw.get("hidden"))


def _normalize_hide(raw: dict, *, campaign: bool) -> dict:
    if not campaign:
        raise QuickReplyError("only a campaign set can hide a world reply")
    if set(raw) != {"id", "hidden"} or raw.get("hidden") is not True:
        raise QuickReplyError('a hide entry is exactly {"id": ..., "hidden": true}')
    if not isinstance(raw["id"], str) or not _ID.match(raw["id"]):
        raise QuickReplyError("a hide entry needs the id of the reply it hides")
    return {"id": raw["id"], "hidden": True}


def _check_label(raw: dict) -> str:
    label = raw.get("label")
    if not isinstance(label, str) or not label.strip():
        raise QuickReplyError("a quick reply needs a label")
    label = label.strip()
    if len(label) > MAX_LABEL:
        raise QuickReplyError(f"a quick reply label is at most {MAX_LABEL} characters")
    return label


def _check_kind(raw: dict) -> str:
    kind = raw.get("kind")
    if kind in RESERVED_KINDS:
        raise QuickReplyError("plugin quick replies need a plugin API, and there is none yet",
                              code="plugin_api_unavailable")
    if kind not in KINDS:
        raise QuickReplyError(f"unknown quick reply kind {kind!r}")
    return str(kind)


def _text_fields(raw: dict) -> dict:
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        # An empty send is the "next NPC round" path, not a quick reply.
        raise QuickReplyError("a quick reply's text must say something")
    if len(text) > MAX_TEXT:
        raise QuickReplyError(f"a quick reply's text is at most {MAX_TEXT} characters")
    mode = raw.get("mode") or "send"
    if mode not in MODES:
        raise QuickReplyError(f"quick reply mode must be one of {', '.join(MODES)}")
    return {"text": text, "mode": mode}


def _roll_fields(raw: dict) -> dict:
    notation = raw.get("notation")
    if not isinstance(notation, str) or not notation.strip():
        raise QuickReplyError("a roll quick reply needs dice notation")
    notation = notation.strip()
    try:
        dice.parse(notation)
    except dice.DiceError as exc:
        raise QuickReplyError(str(exc)) from exc
    out = {"notation": notation}
    roll_label = raw.get("roll_label")
    if roll_label is not None and not isinstance(roll_label, str):
        raise QuickReplyError("a roll label must be text")
    # Collapsed the way the roll route collapses the label it is handed.
    collapsed = " ".join((roll_label or "").split())
    if len(collapsed) > MAX_ROLL_LABEL:
        raise QuickReplyError(f"a roll label is at most {MAX_ROLL_LABEL} characters")
    if collapsed:
        out["roll_label"] = collapsed
    return out


def _task_fields(raw: dict) -> dict:
    task = raw.get("task")
    if task not in TASKS:
        raise QuickReplyError(f"quick reply task must be one of {', '.join(TASKS)}")
    return {"task": str(task)}


def normalize(raw: object, *, campaign: bool) -> dict:
    """One entry, checked and reduced to its kind's fields (no `None` values).

    Mints a uuid4-hex id for an entry saved without one. A hide entry is legal
    only in a campaign set (`campaign=True`)."""
    if not isinstance(raw, dict):
        raise QuickReplyError("a quick reply must be an object")
    if _is_hide(raw):
        return _normalize_hide(raw, campaign=campaign)
    rid = _check_id(raw)
    label = _check_label(raw)
    kind = _check_kind(raw)
    out = {"id": rid, "label": label, "kind": kind}
    if kind in _TEXT_KINDS:
        out.update(_text_fields(raw))
    elif kind == "roll":
        out.update(_roll_fields(raw))
    elif kind == "task":
        out.update(_task_fields(raw))
    return out


def validate_set(raw: object, *, campaign: bool) -> list[dict]:
    """A whole set, each entry through `normalize`; ids unique, at most
    `MAX_REPLIES` entries."""
    if not isinstance(raw, list):
        raise QuickReplyError("quick replies must be a list")
    if len(raw) > MAX_REPLIES:
        raise QuickReplyError(f"a set holds at most {MAX_REPLIES} quick replies")
    out: list[dict] = []
    seen: set[str] = set()
    for entry in raw:
        clean = normalize(entry, campaign=campaign)
        if clean["id"] in seen:
            raise QuickReplyError(f"quick reply id {clean['id']!r} appears twice")
        seen.add(clean["id"])
        out.append(clean)
    return out


# --- sets on disk -------------------------------------------------------------

class SetChanged(Exception):  # noqa: N818 - named for the 409 kind it maps to (`set_changed`)
    """The stored set moved since the caller read it (its `expect` is stale)."""


def world_path(wid: str) -> Path:
    return worlds_paths.world_root(wid) / FILENAME


def campaign_path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / FILENAME


def digest(entries: list[dict]) -> str:
    """A short fingerprint of a stored set, in order -- entries this build does
    not show included, since a write carries them too."""
    blob = json.dumps(entries, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _unknown_kind(entry: dict) -> bool:
    """An entry a newer build (or a future plugin API) wrote: kept, not shown."""
    kind = entry.get("kind")
    return (not _is_hide(entry) and isinstance(kind, str) and bool(kind)
            and kind not in KINDS)


def is_shown(entry: dict) -> bool:
    """A known kind or a hide entry -- what this build reads, shows and edits."""
    return _is_hide(entry) or entry.get("kind") in KINDS


def _salvage(raw: object, *, campaign: bool, path: Path) -> list[dict]:
    if not isinstance(raw, dict) or raw.get("version") != VERSION:
        log.warning("quick replies: ignoring %s -- not a version-%d set", path, VERSION)
        return []
    replies = raw.get("replies")
    if not isinstance(replies, list):
        log.warning("quick replies: ignoring %s -- its replies are not a list", path)
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for entry in replies:
        if not isinstance(entry, dict):
            log.warning("quick replies: dropping a non-object entry in %s", path)
            continue
        rid = entry.get("id")
        if not isinstance(rid, str) or not rid or rid in seen:
            # No minting on read: an id made up here would change on every read.
            log.warning("quick replies: dropping an entry with a missing or repeated id in %s", path)
            continue
        if _unknown_kind(entry):
            out.append(entry)
            seen.add(rid)
            continue
        try:
            clean = normalize(entry, campaign=campaign)
        except QuickReplyError as exc:
            log.warning("quick replies: dropping %r in %s -- %s", rid, path, exc)
            continue
        out.append(clean)
        seen.add(rid)
    return out


def _load(path: Path, *, campaign: bool) -> list[dict]:
    """Every entry stored at `path` that survives salvage, unknown kinds
    included. Never raises: a missing or garbled file is an empty set."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        log.warning("quick replies: ignoring %s -- %s", path, exc)
        return []
    return _salvage(raw, campaign=campaign, path=path)


def _body(stored: list[dict]) -> dict:
    return {"version": VERSION, "replies": [e for e in stored if is_shown(e)],
            "digest": digest(stored)}


def _world_stored(wid: str) -> list[dict]:
    try:
        return _load(world_path(wid), campaign=False)
    except worlds_paths.WorldNotFound:
        return []


def world_set(wid: str) -> dict:
    """The world's set: `{"version", "replies", "digest"}`. An unknown world
    reads as empty -- a campaign whose world is gone still has a composer."""
    return _body(_world_stored(wid))


def campaign_set(cid: str) -> dict:
    """The campaign's own set, hide entries included. Raises
    `CampaignNotFound`."""
    if not campaigns_paths.campaign_exists(cid):
        raise campaigns_paths.CampaignNotFound(cid)
    return _body(_load(campaign_path(cid), campaign=True))


def _world_of(cid: str) -> str:
    return campaigns_read.read_campaign(cid)["meta"].get("world") or ""


def layer(world: list[dict], own: list[dict]) -> list[dict]:
    """The world's replies with the campaign's layered on top: a same-id reply
    replaces in place, a hide entry hides, the rest follow in order."""
    by_id = {e["id"]: e for e in own}
    out: list[dict] = []
    for reply in world:
        mine = by_id.get(reply["id"])
        if mine is None:
            out.append(reply)
        elif not _is_hide(mine):
            out.append(mine)
    world_ids = {r["id"] for r in world}
    out.extend(e for e in own if e["id"] not in world_ids and not _is_hide(e))
    return out


def effective(cid: str) -> list[dict]:
    """What the composer shows for a campaign. Raises `CampaignNotFound`."""
    wid = _world_of(cid)
    world = world_set(wid)["replies"] if wid else []
    return layer(world, campaign_set(cid)["replies"])


def _merge(clean: list[dict], stored: list[dict]) -> list[dict]:
    kept = [e for e in stored if not is_shown(e)]
    kept_ids = {e["id"] for e in kept}
    for entry in clean:
        if entry["id"] in kept_ids:
            raise QuickReplyError(f"quick reply id {entry['id']!r} is taken by a reply "
                                  "this version cannot show")
    merged = clean + kept
    if len(merged) > MAX_REPLIES:
        raise QuickReplyError(f"a set holds at most {MAX_REPLIES} quick replies")
    return merged


def _write(path: Path, entries: list[dict]) -> None:
    atomic.write_text(path, json.dumps({"version": VERSION, "replies": entries},
                                       indent=2, ensure_ascii=False) + "\n")


def write_world(wid: str, replies: object, expect: str) -> dict:
    """Replace the world's set. Atomic-only, no lock (module docstring).

    Raises `WorldNotFound`, `QuickReplyError`, `SetChanged`."""
    if not worlds_paths.world_exists(wid):
        raise worlds_paths.WorldNotFound(wid)
    clean = validate_set(replies, campaign=False)
    path = world_path(wid)
    stored = _load(path, campaign=False)
    if digest(stored) != expect:
        raise SetChanged(wid)
    _write(path, _merge(clean, stored))
    return world_set(wid)


def write_campaign(cid: str, replies: object, expect: str) -> dict:
    """Replace the campaign's own set; the digest check and the write are one
    hold of the campaign lock. A hide entry naming no world reply is dropped --
    it hides nothing and would count toward the cap.

    Raises `CampaignNotFound`, `QuickReplyError`, `SetChanged`."""
    clean = validate_set(replies, campaign=True)
    with locks.campaign_lock(cid):
        if not campaigns_paths.campaign_exists(cid):
            raise campaigns_paths.CampaignNotFound(cid)
        path = campaign_path(cid)
        stored = _load(path, campaign=True)
        if digest(stored) != expect:
            raise SetChanged(cid)
        wid = _world_of(cid)
        world_ids = {r["id"] for r in world_set(wid)["replies"]} if wid else set()
        clean = [e for e in clean if not _is_hide(e) or e["id"] in world_ids]
        _write(path, _merge(clean, stored))
    return campaign_set(cid)
