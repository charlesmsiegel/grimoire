"""One actor's campaign-local record, assembled for the play view's dossier.

The play view's context column swaps between the cast grid and *one* actor.
This is what the second state shows: everything the campaign has decided about
her, gathered from the five files the absorb pass already writes.

    state.md      standing / knows / suspects   (playstate.py)
    dossier.md    the paragraph                 (dossiers.py)
    tagline.md    the one-line identity         (taglines.py)
    relationships.json  feels-toward meters     (relationships.py)
    facts.json    standing facts                (facts.py)

Nothing here is new information and nothing here costs a token. Every field is
a record on disk that had no reader outside a staged review row — the review
row disappears the moment it is approved, so until now the *only* time these
values were visible was the few seconds you spent judging them.

Two joins are worth naming, because both are approximations and both are
deliberate:

- **Last seen** comes from the appearance record's scene list, not from the
  chronicle. The chronicle knows more (it has the location), but it only knows
  it for a scene that has been *absorbed*, and the scene you are playing has
  not been. Naming the newest scene she is cast in answers "where did I last
  see her" correctly during play, which is when the question gets asked.

- **Standing facts** are matched by display name against the fact's text.
  facts.json records no actors — a fact is a sentence about the world — so
  there is no exact join to make. This one errs toward showing, the same
  direction `briefing.py` errs: a fact that mentions her by name is almost
  always about her, and a fact this misses costs a line in a panel rather than
  hiding an obligation. It is a reading aid, never an input to the model.

Read-only, and tolerant the way `briefing.build` is tolerant: every section
degrades to empty on its own, so one hand-edited file empties a block rather
than the column. It takes `campaign_lock` for the reason the ledger and the
briefing take it — a save writes several of these files in sequence, and a
read that lands mid-sequence shows a standing state the dossier beside it has
already moved past.
"""

from __future__ import annotations

from . import campaigns, dossiers, facts, locks, playstate, relationships, taglines
from .appearances import cast as appearances_cast
from .appearances import paths as appearances_paths
from .scenes import read as scenes_read

#: Meters run 0–5 in `relationships.set_feeling`; the column draws five pips.
FEELING_AXES = ("trust", "affection", "tension")


def _text(value, fallback: str = "") -> str:
    """A projected field as text. Same guard, for the same reason, as
    `briefing._text`: these files are hand-editable, React refuses an object as
    a child, and one odd value must not blank the panel it appears in."""
    return value.strip() if isinstance(value, str) else fallback


def _pips(feeling: dict) -> dict:
    """One feeling as three 0–5 integers plus its note.

    Clamped rather than trusted: the meter draws `n` filled pips out of five,
    and a hand-edited 9 would draw four phantom ones. A non-integer reads as 0,
    which renders as an empty meter -- visibly nothing, rather than a crash.
    """
    out: dict = {}
    for axis in FEELING_AXES:
        raw = feeling.get(axis)
        out[axis] = min(5, max(0, raw)) if isinstance(raw, int) and not isinstance(raw, bool) else 0
    out["note"] = _text(feeling.get("note"))
    return out


def _standing_facts(cid: str, name: str) -> list[dict]:
    """Active standing facts that name this actor. See the module docstring for
    why this is a substring match and not a join.

    The recording scene is resolved to `{id, title, date}` — the same shape
    `routes.campaigns.get_ledger` projects, so both views share one type and one
    renderer. A scene id is a filename (`004--the-priory-door`), which is not
    something to show a reader.
    """
    if not name:
        return []
    needle = name.lower()
    hits = [f for f in facts.active(cid) if needle in f["text"].lower()]
    if not hits:
        return []
    # Read once, and only if there is something to label.
    titles = {s["id"]: s["title"] for s in scenes_read.list_scenes(cid)}
    return [{**f, "scene": {"id": f["scene"], "title": titles.get(f["scene"], f["scene"]),
                            "date": f["date"]}}
            for f in hits]


def build(cid: str, sid: str, kind: str, actor_id: str) -> dict:
    """The dossier for one actor of one scene.

    `sid` is not used to narrow anything -- this record is campaign-scoped, not
    scene-scoped -- but it is required, and the route checks the actor really is
    in that scene before calling. That check is what keeps this from becoming a
    way to read any character's campaign state by guessing an id.
    """
    def _tolerant(read, empty):
        try:
            return read()
        except Exception:  # noqa: BLE001 — a garbled file empties its section, not the view
            return empty

    croot = campaigns.campaign_root(cid)
    aroot = appearances_paths.locked_actor_root(cid)
    ref = f"{kind}:{actor_id}"

    with locks.campaign_lock(cid):
        # NOT tolerant, and first: this is the membership check, and it is this
        # endpoint's access control. Swallowing it would answer an empty record
        # for any id at all rather than 404 -- which reads as "she has nothing
        # recorded" for a character who is simply not in this scene, and hands
        # out campaign state to a guessed id besides.
        present = appearances_cast.scene_cast(cid, sid)
        if not any(a["kind"] == kind and a["id"] == actor_id for a in present):
            raise appearances_paths.AppearError(
                f"{kind}/{actor_id} is not in scene {sid}")
        # The card read itself IS tolerant: a cast member whose card will not
        # parse still has state, a dossier and facts, and those are what the
        # column is for. She falls back to her id for a name.
        detail = _tolerant(lambda: appearances_cast.cast_detail(cid, sid, kind, actor_id), {})
        name = _text(detail.get("name")) or actor_id
        record = _tolerant(lambda: appearances_paths.record(cid).get(f"{kind}/{actor_id}", {}), {})
        scene_ids = [s for s in record.get("scenes", []) if isinstance(s, str)]
        # Labelled, not left as filenames. A scene id is `004--the-priory-door`,
        # and "Cast · in scene 004--the-priory-door" is the store's business
        # leaking into a sentence about a person. Same projection the standing
        # facts get, and the same one `get_ledger` uses.
        titles = _tolerant(lambda: {x["id"]: x["title"] for x in scenes_read.list_scenes(cid)}, {})
        scenes = [{"id": x, "title": titles.get(x, x)} for x in scene_ids]

        # Characters keep their play state and dossier beside the campaign's own
        # copy of the card; a PC has neither -- she is the one actor whose state
        # the player holds rather than the absorb pass.
        state = _tolerant(lambda: playstate.read_state(aroot, actor_id), None) or {}
        dossier = _tolerant(lambda: dossiers.read(croot, actor_id), "")
        # Only meaningful for someone who has never been played: once there is a
        # dossier, the tagline is the guess the dossier replaced.
        tagline = _tolerant(lambda: taglines.read(aroot, actor_id), "")

        feelings = _tolerant(lambda: relationships.read(cid)["feelings"], {})
        toward = []
        for other in present:
            other_ref = f"{other['kind']}:{other['id']}"
            if other_ref == ref:
                continue
            f = feelings.get(relationships.feeling_key(ref, other_ref))
            if not isinstance(f, dict):
                continue
            toward.append({
                "ref": other_ref,
                "kind": other["kind"],
                "id": other["id"],
                "name": _text(other.get("name")) or _text(other.get("id")),
                **_pips(f),
            })

        standing_facts = _tolerant(lambda: _standing_facts(cid, name), [])

    return {
        "kind": kind,
        "id": actor_id,
        "name": name,
        "version": _text(detail.get("version")),
        "role": _text(record.get("role")) or ("player" if kind == "pcs" else "npc"),
        "scenes": scenes,
        "last_seen": scenes[-1]["title"] if scenes else "",
        "standing": _text(state.get("current_state")),
        "knows": _text(state.get("knows")),
        "suspects": _text(state.get("suspects")),
        "dossier": dossier,
        "tagline": tagline,
        "feels_toward": toward,
        "standing_facts": standing_facts,
    }
