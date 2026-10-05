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

import re
import uuid

from . import dice

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
