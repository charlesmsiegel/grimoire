"""The scene ledger (#88): per-campaign curated scene ideas that outlive the
picker that produced them. Stored at <campaign>/scene_ideas.json.

    {"<idea_id>": {"title", "premise", "cast": ["characters:mara"],
                   "location": "<location id>" | "", "date": "<native>" | "",
                   "pcless": bool,
                   "source": "llm" | "user",
                   "status": "active" | "used" | "dismissed",
                   "created": "<iso stamp>", "used_scene": "<sid>" | ""}}

The gap this fills. A generated scene idea was ephemeral: `POST
/scene-suggestions` built a snapshot, asked the model for openings, handed them
to `SceneIdeaPicker` and kept nothing, so every open of the chooser regenerated
from scratch and every idea the reader liked but did not pick that minute was
gone. Greeting ideas already had the opposite property -- `played.json` gives
each one a durable lifecycle (played / completed / skipped) -- which is exactly
why greetings are *not* copied in here: `playing.greeting_ideas` composes them
on the fly from the marks that already exist, so the two can never drift.
Materializing them would duplicate `played.json` and a plot map whose gates and
unlocks move underneath a snapshot, and would need a migration for every
existing campaign. That is #88's Option A, and the reason it was recommended.

**Not named `ledger`,** which #88 proposed, because that word is taken: `GET
/campaigns/{cid}/ledger` is the continuity ledger (facts, commitments, plot,
relationships -- `routes.campaigns.get_ledger`), a different thing at a route
this would have collided with. `scene_ideas` says which ledger this is.

**This module stores; it does not validate references.** An idea names cast
tokens and a location id, and checking those against the campaign means reading
its entities and roster -- which is `suggest`'s job, and `suggest` reaches
`playing` and `scenes` in doing it. Importing it here would close a cycle
(`scenes.lifecycle` -> `scene_refs` -> here), so the two validation passes live
where the data is used instead:

- on write, `routes.scenes.post_scene_idea` runs `suggest.valid_refs`, so a
  token this campaign never had cannot enter the file;
- on read, `suggest.validate_ideas` drops what the campaign has since lost.

Two passes rather than one because an idea is durable and a campaign is not:
the character it casts can be deleted and the location it names can be renamed
between the day it was saved and the day it is picked. The stored record keeps
whatever it was given (a dangling id is data, not an error); the read is what
refuses to hand a picker an id the campaign no longer has.

`used_scene` is provenance, not a link to trust. Scene ids are recycled --
`scenes.lifecycle.delete_scene` frees a number for the next scene to take --
so an idea whose scene was deleted names an id that may later belong to a
different scene. Renames are followed (`repoint_scenes`, registered in
`scene_refs.repoint`); deletions are not, deliberately, since the alternative
is reviving a used idea whenever a scene is cleaned up.

Mutators serialize on `locks.campaign_lock(cid)`: the file is rewritten whole,
so two unlocked read-modify-writes lose one of them.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import atomic, locks, paths
from .campaigns import paths as campaigns_paths

ACTIVE = "active"
USED = "used"
DISMISSED = "dismissed"
STATUSES = (ACTIVE, USED, DISMISSED)

LLM = "llm"
USER = "user"
GREETING = "greeting"
SOURCES = (LLM, USER)

#: Composed greeting entries are addressed as `greeting:<gid>`. Stored ids come
#: from `slugify`, which strips colons, so the two id spaces cannot collide.
GREETING_PREFIX = "greeting:"

#: How much of a premise stands in for a missing title. Long enough to tell two
#: ideas apart in the picker, short enough to stay one line.
TITLE_FROM_PREMISE = 60


def _path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "scene_ideas.json"


def read(cid: str) -> dict:
    p = _path(cid)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _read_ledger(cid: str) -> dict:
    """`read`, refusing a document that is valid JSON of the wrong shape.

    Only the mutators use this, for `facts._read_ledger`'s reason: substituting
    `{}` for a file holding `[]` would publish an empty ledger over whatever it
    really held, and every `.get` below would raise something unrelated to what
    is actually wrong.
    """
    data = read(cid)
    if not isinstance(data, dict):
        raise ValueError("scene_ideas.json does not hold a scene ledger")
    return data


def _write(cid: str, data: dict) -> None:
    atomic.write_text(_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")


def get(cid: str, lid: str) -> dict | None:
    return read(cid).get(lid)


def _field(value, fallback: str = "") -> str:
    """A stored field as text, or `fallback` for anything that is not a string.

    scene_ideas.json is hand-editable and read by a bare `json.loads`, so every
    field is whatever the file says. Same rule, and the same reason, as
    `facts._field`: these are rendered straight into React, which refuses an
    object as a child and blanks the panel that holds it.
    """
    return value.strip() if isinstance(value, str) else fallback


def _tokens(value) -> list[str]:
    """The cast as a list of strings. A non-list, or an entry that is not a
    string, is dropped rather than coerced -- `str()` on a dict would produce a
    token that reads like data and matches nothing."""
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


def _title_from(title: str, premise: str) -> str:
    """A saved idea's title, falling back to the head of its premise.

    The picker's free-text box has no title field -- the reader types how the
    scene starts and nothing else -- so "save this" has to name the idea for
    them. Cut on a word boundary when there is one near the limit, so the
    fallback reads as a phrase rather than a truncation.
    """
    title = title.strip()
    if title:
        return title
    head = " ".join(premise.split())[:TITLE_FROM_PREMISE].strip()
    if len(head) == TITLE_FROM_PREMISE and " " in head:
        head = head[:head.rindex(" ")]
    return head or "Untitled idea"


def _standing_match(data: dict, title: str, premise: str, pcless: bool) -> str:
    """The id of a live idea that already says this, or "".

    An idea's identity, for dedupe purposes, is its title and premise in the
    mode it was saved for -- everything else (cast, location, date) is metadata
    the reader can change on the way into the scene.

    Only ACTIVE records match, and that is the whole difference from
    `facts.find`, which deliberately ignores status. A `used` idea is one that
    already became a scene, and saving the same premise again after playing it
    is a genuine second use; a `dismissed` one was explicitly pushed off the
    list, and reviving it silently through an unrelated Save would undo that
    decision without saying so.

    Case-insensitively, like `facts.restates` and `materializer.
    _new_commitment_id` compare: two renderings of one sentence differing only
    in capitalisation are one sentence.
    """
    want = (title.casefold(), premise.casefold(), bool(pcless))
    return next((k for k, rec in data.items()
                 if isinstance(rec, dict)
                 and _field(rec.get("status"), ACTIVE).lower() == ACTIVE
                 and (_field(rec.get("title")).casefold(),
                      _field(rec.get("premise")).casefold(),
                      bool(rec.get("pcless"))) == want), "")


def add(cid: str, title: str, premise: str, cast: list[str] | None = None,
        location: str = "", date: str = "", pcless: bool = False,
        source: str = USER) -> str:
    """Save an idea and return its id.

    `source` says where it came from -- a generated card the reader kept
    (`"llm"`) or their own typed one (`"user"`). `"greeting"` is not accepted:
    greeting entries are composed from `playing`'s marks, never stored (see the
    module docstring).

    **Idempotent while an identical idea is still standing**: re-saving the
    same title and premise for the same mode returns the id already holding it
    rather than opening a second entry. Saving twice is an ordinary thing to
    do, not an error -- an impatient double-click on Save, a retry after a
    dropped response, the same suggestion coming back from a Regenerate -- and
    without this each one filed a duplicate under `<slug>-2` that the reader
    then had to dismiss twice. `facts.record` is idempotent per scene for the
    same reason.

    References are stored as given; the caller validates them (again, see the
    module docstring). `pcless` is stored rather than checked here for the same
    reason -- it is what tells the *reader* which player tokens are legal, an
    offscreen idea that casts the PC not being an offscreen idea. It is part of
    the dedupe key rather than metadata around it: the two modes cast
    different people, so the same sentence saved for each is two ideas.
    """
    if source not in SOURCES:
        raise ValueError(f"unknown idea source: {source}")
    premise = premise.strip()
    title = _title_from(title, premise)
    with locks.campaign_lock(cid):
        data = _read_ledger(cid)
        standing = _standing_match(data, title, premise, pcless)
        if standing:
            return standing
        lid = paths.uniquify(paths.slugify(title), lambda c: c in data)
        data[lid] = {"title": title, "premise": premise, "cast": _tokens(cast),
                     "location": _field(location), "date": _field(date),
                     "pcless": bool(pcless), "source": source, "status": ACTIVE,
                     "created": paths.now_iso(), "used_scene": ""}
        _write(cid, data)
        return lid


def set_status(cid: str, lid: str, status: str, scene: str = "") -> bool:
    """Move a stored idea between active / used / dismissed. False when no such
    idea exists.

    `scene` stamps `used_scene` and is only meaningful for `"used"`. Every
    other status CLEARS it: an idea restored to the list is one nobody has
    played, and leaving the old stamp behind would have the ledger claim a
    scene it no longer describes.

    Greeting ids are not handled here -- they are not in this file. The route
    delegates those to `playing.mark_greeting`.
    """
    if status not in STATUSES:
        raise ValueError(f"unknown idea status: {status}")
    with locks.campaign_lock(cid):
        data = _read_ledger(cid)
        rec = data.get(lid)
        if not isinstance(rec, dict):
            return False
        rec["status"] = status
        rec["used_scene"] = scene.strip() if status == USED else ""
        _write(cid, data)
        return True


def mark_used(cid: str, lid: str, scene: str) -> bool:
    """The transition a pick makes: this idea became that scene."""
    return set_status(cid, lid, USED, scene)


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids in `used_scene`. Part of the
    `scene_refs.repoint` fan-out.

    An unreadable file and malformed records are stepped over rather than
    trusted, for the reason `facts.repoint_scenes` gives: this runs AFTER the
    scene file has been renamed, so raising here 500s the rename and leaves
    every store later in the sweep pointing at an id that no longer exists.
    """
    with locks.campaign_lock(cid):
        try:
            data = read(cid)
        except Exception:  # noqa: BLE001 — unparseable scene_ideas.json: skip this store
            return         # (the shape check below is the valid-JSON half of the same rule)
        if not isinstance(data, dict):
            return
        hit = False
        for rec in data.values():
            if not isinstance(rec, dict):
                continue
            # `isinstance(..., str)` before `in mapping`: a list-valued id is
            # unhashable and a membership test on it raises rather than missing.
            sid = rec.get("used_scene")
            if isinstance(sid, str) and sid in mapping:
                rec["used_scene"] = mapping[sid]
                hit = True
        if hit:
            _write(cid, data)


def _project(lid: str, rec: dict) -> dict:
    source = _field(rec.get("source"), USER)
    status = _field(rec.get("status"), ACTIVE).lower()
    return {"id": lid, "title": _field(rec.get("title"), lid),
            "premise": _field(rec.get("premise")),
            "cast": _tokens(rec.get("cast")), "location": _field(rec.get("location")),
            "date": _field(rec.get("date")), "pcless": bool(rec.get("pcless")),
            "source": source if source in SOURCES else USER,
            "status": status if status in STATUSES else ACTIVE,
            "created": _field(rec.get("created")),
            "used_scene": _field(rec.get("used_scene"))}


def records(cid: str) -> list[dict]:
    """Every stored idea, newest first, each field coerced to the type its
    reader expects. References are whatever the file holds -- validating them
    is the caller's, see the module docstring.

    Newest first because this list is read to be *picked from*: the idea saved
    a minute ago is the one being looked for, and the ids are slugs rather than
    a counter, so nothing else in the record orders them. Ties break on the id,
    which only matters for records written inside one clock tick or by hand.
    """
    items = [_project(lid, rec) for lid, rec in read(cid).items() if isinstance(rec, dict)]
    items.sort(key=lambda i: (i["created"], i["id"]), reverse=True)
    return items
