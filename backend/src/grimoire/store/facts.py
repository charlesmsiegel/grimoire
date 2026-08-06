"""Per-campaign fact ledger: dated standing truths, each addressable, each able
to be retired when it stops being true. Stored at <campaign>/facts.json.

The gap this fills (#114). Nothing else in the store keeps a fact that can stop
being true. `chronicle.json` is keyed by SCENE, so re-absorbing one scene
replaces its record cleanly but no fact inside it is addressable and no scene
can say anything about a fact another scene recorded. `timeline.md` is an
unbounded text append with no retirement path at all. `playstate`'s `state.md`
is deliberately a snapshot -- rewritten whole on every absorb, so it holds what
is true now and no history of what was true before or why it changed. A ledger
is the missing third shape: every fact keeps its own id, its own date and its
own lifecycle, so scene 9 can say "the fact scene 3 recorded is no longer
true", and say what replaced it.

    {"<fact_id>": {"text", "date", "scene",
                   "status": "active" | "retired",
                   "superseded_by": "<fact_id>" | "",
                   "retired_scene": "<sid>" | ""}}

`date` is free text in the campaign's own reckoning -- "the third night", "two
winters ago" -- the same convention `timeline_events[].date` and
`commitments.due` already use, and for the same reason: the model can read the
fiction's own dating and cannot know a real calendar. `scene` is the scene that
recorded the fact, `retired_scene` the one that ended it, and those two dates
are what make the ledger answer "as of when".

**Facts are not edited.** A fact's text never changes once recorded; a fact
that stops being true is retired, and a fact that replaced it points back at it
through `superseded_by`. That immutability is the whole difference from a
snapshot -- an edit-in-place ledger is just `state.md` with extra keys, and
loses exactly the history this exists to keep. It also means the only fields
that can ever move on a stored record are `status`, `superseded_by` and
`retired_scene`, which is what `absorb.conflicts.fact_line` fingerprints.

Retirement comes in two shapes because the fiction has two:

- **superseded** -- something replaced it ("the ambassador trusts the party"
  gives way to "the ambassador believes the party sold him out"). `record`
  writes the new fact and retires the old one in the same write, pointing each
  at the other.
- **retired outright** -- it simply stopped applying, with nothing to say in
  its place (a deadline passed, a secret became common knowledge). `retire`.
  Folding this into supersession would force a replacement sentence to be
  invented for every fact that ends, which puts fiction nobody wrote into the
  ledger and then into every later prompt.

Mutators serialize on `locks.campaign_lock(cid)`: facts.json is rewritten
whole, so two unlocked read-modify-writes lose one of them -- and `record`
touches two records at once, so an unlocked one can also leave a fact
superseded by an id that never landed.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import prompts
from . import atomic, locks
from .campaigns import paths as campaigns_paths

ACTIVE = "active"
RETIRED = "retired"


def _path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "facts.json"


def read(cid: str) -> dict:
    p = _path(cid)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _read_ledger(cid: str) -> dict:
    """`read`, refusing a document that is valid JSON of the wrong shape.

    Only the mutators use this. `read` is a bare `json.loads`, so a facts.json
    holding `[]` comes back as a list and every `.get` below raises something
    unrelated to what is wrong -- and the alternative, quietly substituting
    `{}`, would publish an empty ledger over the file and destroy whatever it
    really held. Raising is what the callers are built for: `apply_edits`
    reports a failed edit and the reviewer keeps their row.
    """
    data = read(cid)
    if not isinstance(data, dict):
        raise ValueError("facts.json does not hold a fact ledger")
    return data


def _write(cid: str, data: dict) -> None:
    atomic.write_text(_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")


def get(cid: str, fid: str) -> dict | None:
    return read(cid).get(fid)


def _field(value, fallback: str = "") -> str:
    """A stored field as text, or `fallback` for anything that is not a string.

    facts.json is hand-editable and read by a bare `json.loads`, so every field
    is whatever the file says. Same rule -- and the same reason -- as
    `commitments._field`: a list-valued `text` reaching the ledger route renders
    as an object React refuses as a child and blanks the panel, and one handed
    to `.strip()` inside `materialize` turns a paid-for absorb into a 500.
    """
    return value.strip() if isinstance(value, str) else fallback


def is_active(rec) -> bool:
    """Whether this record is still standing.

    Anything that is not literally retired counts as active, mirroring
    `plot.open_threads`' "not closed" and `commitments`' "not resolved": a
    record written by an older version, or by hand, stays on the ledger instead
    of silently dropping out of it. Case-folded for the reason
    `commitments.open_commitments` folds -- a hand-edited `"Retired"` must not
    read as a live fact the model is asked to reason from.
    """
    return isinstance(rec, dict) and _field(rec.get("status"), ACTIVE).lower() != RETIRED


def _next_id(data: dict) -> str:
    """The first free ``f<N>``.

    Short because the id is shown to the model in the absorb snapshot and cited
    back by it, and distinct in SHAPE from every other id in that prompt (plot
    threads, commitments, characters and locations are all slugs) so a fact
    reference cannot be confused for one of those.

    Never reuses an id, not even one whose fact was retired: retired records
    stay in the file, so `in data` covers them, and `superseded_by` pointers
    from elsewhere in the ledger would otherwise silently re-aim at a different
    fact.
    """
    n = len(data) + 1
    while f"f{n}" in data:
        n += 1
    return f"f{n}"


def record(cid: str, text: str, date: str, scene: str, supersedes: str = "") -> str:
    """Record a standing fact and return its id, retiring the one it replaces.

    Both halves of a supersession land in ONE write. Split across two, a crash
    between them leaves either a fact whose predecessor is still standing
    (a contradiction on the ledger) or a fact retired by an id that never
    landed.

    Idempotent per scene: re-recording the same text for the same scene returns
    the id already holding it rather than opening a second fact. Absorbing a
    scene twice is an ordinary, supported thing to do (`POST .../absorb?force`),
    and it re-proposes every fact the first pass found -- the exact shape of the
    `timeline.md` re-append this ledger exists to improve on. Compared
    case-insensitively, like `materializer._new_commitment_id` compares titles:
    the two passes are two model replies, and identical-but-for-capitalisation
    is a re-extraction rather than a second fact.

    A `supersedes` naming a fact that is missing or already retired is dropped
    rather than reported: it is the same situation `absorb.conflicts` refuses
    the row for at save time, so anything reaching here has either been judged
    or been answered for, and the fact itself must still land.
    """
    text = text.strip()
    if not text:
        raise ValueError("a fact needs text")
    with locks.campaign_lock(cid):
        data = _read_ledger(cid)
        want = text.casefold()
        fid = next((k for k, rec in data.items()
                    if is_active(rec) and _field(rec.get("scene")) == scene
                    and _field(rec.get("text")).casefold() == want), "")
        if not fid:
            fid = _next_id(data)
            data[fid] = {"text": text, "date": date.strip(), "scene": scene,
                         "status": ACTIVE, "superseded_by": "", "retired_scene": ""}
        # `!= fid` guards the degenerate self-reference a forged save body can
        # send, and the one a dedupe hit can produce: retiring a fact as
        # superseded by itself would take it off the ledger in the same write
        # that put it there.
        prior = data.get(supersedes) if supersedes and supersedes != fid else None
        if is_active(prior):
            prior["status"] = RETIRED
            prior["superseded_by"] = fid
            prior["retired_scene"] = scene
        _write(cid, data)
        return fid


def retire(cid: str, fid: str, scene: str) -> bool:
    """Retire a fact that stopped being true with nothing replacing it. False
    when no active fact holds that id -- already retired, or never there.

    `superseded_by` is written blank rather than left alone: "nothing replaced
    it" is what this operation means, and an active record carrying a
    supersession pointer is incoherent in the first place.
    """
    with locks.campaign_lock(cid):
        data = _read_ledger(cid)
        rec = data.get(fid)
        if not is_active(rec):
            return False
        rec["status"] = RETIRED
        rec["superseded_by"] = ""
        rec["retired_scene"] = scene
        _write(cid, data)
        return True


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids in `scene` and `retired_scene`.

    An unreadable file and malformed records are both stepped over rather than
    trusted, for the reason `commitments.repoint_scenes` gives: this runs from
    `scene_refs.repoint` AFTER the scene file has been renamed, so raising here
    500s the rename and leaves every store later in the sweep pointing at an id
    that no longer exists.
    """
    with locks.campaign_lock(cid):
        try:
            data = read(cid)
        except Exception:  # noqa: BLE001 — unparseable facts.json: skip this store
            return         # (the shape check below is the valid-JSON half of the same rule)
        if not isinstance(data, dict):
            return
        hit = False
        for rec in data.values():
            if not isinstance(rec, dict):
                continue
            for field in ("scene", "retired_scene"):
                # `isinstance(..., str)` before `in mapping`: a list-valued id is
                # unhashable and a membership test on it raises rather than missing.
                sid = rec.get(field)
                if isinstance(sid, str) and sid in mapping:
                    rec[field] = mapping[sid]
                    hit = True
        if hit:
            _write(cid, data)


def active(cid: str) -> list[dict]:
    """Every standing fact, oldest scene first.

    Sorted by the recording scene, like `plot.open_threads` and
    `commitments.open_commitments` sort by `last_scene` -- a scene id is its
    zero-padded filename stem, so that ordering is chronological. Ties break on
    the id by LENGTH first, which is what puts `f9` before `f10` instead of
    after it; the ids are a counter, and reading a ledger out of counting order
    would misdate the very thing it is for.
    """
    items = [(fid, r) for fid, r in read(cid).items() if is_active(r)]
    items.sort(key=lambda fr: (_field(fr[1].get("scene")), len(fr[0]), fr[0]))
    return [{"id": fid, "text": _field(r.get("text")), "date": _field(r.get("date")),
             "scene": _field(r.get("scene"))} for fid, r in items]


def render_active(cid: str, limit: int | None = None) -> list[str]:
    """Formatted lines for the standing facts, leading with the id so the absorb
    prompt's reply can cite one to supersede. The line format lives in
    templates/snippets/fact_line.j2. Tolerant of a garbled facts.json (returns
    []) -- same policy as `plot.render_open` and `commitments.render_open`, for
    the same reason: a broken file must cost the model one context block, not
    the whole turn.

    `limit` keeps the most recent N and drops the rest. Unlike a plot thread,
    which closes, or a commitment, which resolves, a standing fact leaves this
    list only when a later scene explicitly contradicts it -- so most never do,
    and the prompt-side caller needs a ceiling its siblings do not (see
    `absorb.snapshots.FACT_SNAPSHOT_LIMIT`). The OLDEST go, because they are the
    ones play has moved furthest past; the cost is that a fact past the cap can
    no longer be superseded, which is the trade the constant's comment explains.
    A `limit` of None or <= 0 keeps everything.
    """
    try:
        rows = active(cid)
    except Exception:  # noqa: BLE001 — garbled facts.json: omit, don't crash callers
        return []
    if limit is not None and limit > 0:
        rows = rows[-limit:]
    return [prompts.render("snippets/fact_line.j2", f=f) for f in rows]
