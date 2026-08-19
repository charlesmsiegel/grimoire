"""Idempotency ledger and commit journal for the chronicle commit (#235, #271).

`PUT /chronicle` is not idempotent and cannot cheaply be made so: timeline
events append, plot movements append a beat, weather spans append a record, and
`new_character`/`new_location`/`new_lore` create one. Six appends, each correct
on its own and each a duplicate when the same save runs twice.

Rather than make all six individually replay-safe, the commit carries an
idempotency key: `POST /absorb` mints one, the review sends it back, and a save
whose token is already spent returns the result the first one produced instead
of applying anything. That is what lets the review panel offer a retry after a
save whose response was lost -- the case where the write landed and the client
cannot tell.

Three things live here, one per hole in that story:

- **The token entry** -- who asked, for which scene, for what content, and
  whether it finished. A spent token replays its stored result.
- **The commit journal** (#271) -- what an *unfinished* attempt already did. The
  commit is four writes and cannot be staged behind one rename, so instead each
  non-idempotent step is journalled the moment it is attempted. A retry then
  finishes the commit rather than refusing it, and never repeats a step whose
  outcome the journal already knows.
- **The per-scene commit epoch** (#271) -- how many commits this scene has
  begun, carried in the token itself. The key orders a review against
  *itself*; a second review of the same scene carries a different key and is
  invisible to it. The epoch is what says "this review was prepared before
  something else started saving this scene". It advances when a commit CLAIMS
  its token, not when it finishes, so a commit that dies partway still retires
  its rivals.

Nothing here is written at mint time: `POST /absorb` is a proposal and leaves
the campaign byte-identical (#235), so the epoch travels in the token rather
than in an entry the mint would have to create.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta

from . import atomic
from .campaigns import paths as campaigns_paths
from .paths import now_iso

#: How long a completed entry stays retryable. Deliberately time, not count: a
#: review sits open on someone's screen for as long as they leave it there, and
#: evicting by count would drop their token the moment the campaign saw enough
#: other saves -- their retry would then replay every append. An UNFINISHED
#: reservation never expires at all; it is the entry whose loss lets a partly
#: landed commit run again.
RETAIN_DAYS = 30


def _path(cid: str):
    return campaigns_paths.campaign_root(cid) / "commits.json"


#: Stamped into every file this module writes. The nested shape is told apart
#: from the pre-#271 flat token map by this marker and nothing else: a token is
#: a caller-chosen string, so a legacy ledger may well contain one keyed
#: literally `"tokens"` or `"scenes"`, and reading its entry as the new schema
#: would hide every sibling token in that file -- which is exactly what lets a
#: retry replay its appends. `v` cannot collide: a legacy value under any key is
#: an entry dict, never the integer 2.
SCHEMA = 2


def _write(cid: str, tokens: dict, scenes: dict) -> None:
    """The only writer, so the schema marker cannot be forgotten by one of the
    three call sites -- and forgetting it is what makes the next read fall back
    to the flat-ledger branch and lose every token in the file."""
    atomic.write_text(_path(cid), json.dumps(
        {"v": SCHEMA, "tokens": tokens, "scenes": scenes}, indent=2) + "\n")


def _read(cid: str) -> dict:
    """``{"tokens": {token: entry}, "scenes": {sid: epoch}}``.

    Tolerates the pre-#271 shape, where the file *was* the token map: an upgrade
    must not read as a garbled ledger and forget every open review's token,
    because forgetting one is what lets its retry replay the appends.
    """
    p = _path(cid)
    empty: dict = {"tokens": {}, "scenes": {}}
    if not p.exists():
        return empty
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty           # a garbled ledger forgets, it never blocks a save
    if not isinstance(data, dict):
        return empty
    if data.get("v") != SCHEMA:
        return {"tokens": {t: e for t, e in data.items() if isinstance(e, dict)},
                "scenes": {}}
    tokens, scenes = data.get("tokens"), data.get("scenes")
    return {"tokens": tokens if isinstance(tokens, dict) else {},
            "scenes": scenes if isinstance(scenes, dict) else {}}


def fingerprint(body: dict) -> str:
    """A stable digest of the save body.

    A token identifies the *attempt*; this identifies what the attempt was for.
    A review stays editable after a failed save, so a retry can carry the same
    token and different content -- returning the first result then reports
    success while silently discarding the edits made in between.

    The journal leans on it too: a resumed commit indexes its edits by position,
    which is only meaningful because a body that changed is refused outright.
    """
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()


#: The exact shape ``mint`` produces. Matched whole, because a caller-minted key
#: is allowed to be anything: `123` or `1-custom` would otherwise read as an
#: epoch its owner never meant and lose its first save to a spurious 409.
#:
#: `[0-9]`, not `\d`: `\d` also matches Unicode decimal digits, so a caller key
#: like `٠-<32 hex>` would be read as a minted token and parsed as epoch 0 --
#: `int()` accepts those digits too, so it would not even fail loudly. `mint`
#: emits ASCII and nothing else.
#:
#: Bounded, because the group feeds `int()`: past ~4300 digits CPython refuses
#: the conversion outright, so an unbounded match would turn a caller key of
#: 5000 digits plus a hex tail into a 500 on its first save. A scene's commit
#: count will not reach ten digits.
_MINTED_TOKEN = re.compile(r"\A([0-9]{1,9})-[0-9a-f]{32}\Z")


def mint(epoch: int) -> str:
    """A token for a review prepared at `epoch`: ``<epoch>-<uuid4 hex>``.

    Takes the epoch rather than reading it, because *when* it is read is the
    whole guarantee. The stamp has to date the SNAPSHOT the review was built
    from, and absorb spends a minute or more on LLM calls between reading that
    snapshot and returning it -- a mint that read the epoch at the end would
    hand a proposal built from pre-save state an epoch taken after that save
    landed, and its own supersession check would then wave it through. Passing
    the value in makes the caller hold it from the start.
    """
    return f"{epoch}-{uuid.uuid4().hex}"


def token_epoch(token: str) -> int | None:
    """The scene epoch a token was minted at, or None when it carries none.

    None for anything this module did not mint (an older build, a direct API
    caller): unstampable, so it keeps the weaker pre-#271 guarantee rather than
    being refused for a stamp it was never asked to carry.
    """
    m = _MINTED_TOKEN.match(token)
    return int(m.group(1)) if m else None


def scene_epoch(cid: str, sid: str) -> int:
    """How many commits this scene has begun. Advanced by ``reserve``.

    Deliberately at reserve and not at record: a commit that claims its token
    and then dies never records, so an epoch advanced at completion would leave
    a rival review -- minted at the same epoch -- free to save on top of the
    half-applied one, which is the ordering this exists to impose. Advancing at
    the claim also means a wedged commit does not block its scene forever: the
    next re-absorb mints at the new epoch and saves normally.
    """
    epoch = _read(cid).get("scenes", {}).get(sid)
    return epoch if isinstance(epoch, int) else 0


def retire_scene(cid: str, sid: str) -> None:
    """Advance a scene's epoch so nothing prepared for it can save again.

    Called when the scene is deleted. Scene ids are recycled -- the numbering
    reuses the highest deleted number, so remaking a scene under the same title
    can hand it the same id -- and every check here identifies a scene by that
    id alone. Without this, a review or an unfinished reservation left over from
    the deleted scene matches the replacement and writes the old summary,
    timeline and edits into it.

    Advancing rather than deleting: the entries themselves stay, because an
    unfinished reservation is the one record that must never be dropped, and a
    dropped token reads as unseen -- which is a *fresh* commit into the
    replacement scene, the very thing this prevents.
    """
    _put(cid, "", {}, bump_sid=sid)


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids: the epoch keys and each entry's ``sid``.

    A scene's id is its filename stem, so a title rename or width re-pad moves
    it. Without this the epoch map reads the new id as 0 and refuses an open
    review as superseded, while the token entries keep the old id and refuse a
    retry as a scene mismatch. See scene_refs.repoint.
    """
    data = _read(cid)
    tokens, scenes = data["tokens"], data["scenes"]
    hit = False
    for entry in tokens.values():
        if not isinstance(entry, dict):
            continue
        if entry.get("sid") in mapping:
            entry["sid"] = mapping[entry["sid"]]
            hit = True
        # The stored result is the chronicle record, which carries the scene id
        # a second time. A replay after a rename would otherwise be accepted and
        # answer with a record naming a scene that no longer exists.
        result = entry.get("result")
        if isinstance(result, dict) and result.get("id") in mapping:
            result["id"] = mapping[result["id"]]
            hit = True
    out: dict = {}
    for sid, epoch in scenes.items():
        target = mapping.get(sid, sid)
        hit = hit or target != sid
        # max(): a rename INTO an id that already carries an epoch would
        # otherwise lower it, and an epoch must never go backwards.
        #
        # Including the epoch `retire_scene` left behind for a scene that was
        # DELETED, which costs the arriving scene an open review and is still
        # the right way round. Delete a scene, rename another onto the id it
        # freed, and the dead scene's leftover entries -- which name a scene by
        # id and nothing else -- now match the live one; its tombstone epoch is
        # the only thing still refusing them. Taking the arriving scene's lower
        # epoch would resume a dead scene's commit into an unrelated one. The
        # review open across the rename is refused as `commit_superseded` and
        # re-absorbed, which is recoverable; the write this would otherwise let
        # through is not. Ids that cannot be recycled at all are the real fix,
        # and belong to the scene-identity follow-up rather than here.
        out[target] = max(epoch, out[target]) if target in out else epoch
    if not hit:
        return
    _write(cid, tokens, out)


def lookup(cid: str, token: str) -> dict | None:
    """This token's ledger entry, or None when it is unseen.

    An entry is ``{"done": bool, "result": dict | None, "fingerprint": str,
    "sid": str, "progress": dict, "journalled": bool, "claimed": int | None,
    "at": iso}`` -- ``claimed`` being the scene epoch this reservation's own
    claim produced, which is what a resume compares against to see whether a
    newer save has overtaken it. The ledger
    is campaign-scoped, so ``sid`` is what keeps one scene's spent token from
    answering for another's save. ``done`` is False between ``reserve`` and
    ``record`` -- the commit began and its outcome is unknown, which is the
    state ``progress`` exists to describe.

    ``journalled`` says whether ``progress`` is that commit's real account or
    merely an empty stand-in. A pre-#271 entry has no journal at all, and its
    commit could have appended the timeline and applied any number of edits
    before it died -- so an empty journal must NOT be read as "nothing
    happened", which would resume it as fresh work and duplicate every one of
    them. Callers refuse those instead.

    An empty token is always unseen: a client that sends none opts out of the
    guard, and must not collide with every other tokenless save.
    """
    if not token:
        return None
    entry = _read(cid)["tokens"].get(token)
    if not isinstance(entry, dict) or "done" not in entry:
        return None
    progress = entry.get("progress")
    return {**entry, "progress": progress if isinstance(progress, dict) else {},
            "journalled": isinstance(progress, dict)}


def _prune(tokens: dict) -> dict:
    """Drop completed entries past RETAIN_DAYS. Reservations are kept."""
    cutoff = (datetime.now(UTC)
              - timedelta(days=RETAIN_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {t: e for t, e in tokens.items()
            if not (isinstance(e, dict) and e.get("done")
                    and str(e.get("at", "")) < cutoff)}


def _put(cid: str, token: str, entry: dict, bump_sid: str = "") -> None:
    """Write one entry (and optionally advance a scene's epoch) in one file write."""
    if not token and not bump_sid:
        return                 # nothing to say: a tokenless call writes nothing
    data = _read(cid)
    tokens = _prune(data["tokens"])
    if token:
        tokens[token] = {**entry, "at": now_iso()}
    scenes = dict(data["scenes"])
    if bump_sid:
        epoch = scenes.get(bump_sid)
        scenes[bump_sid] = (epoch if isinstance(epoch, int) else 0) + 1
    _write(cid, tokens, scenes)


def reserve(cid: str, token: str, fp: str = "", sid: str = "",
            progress: dict | None = None) -> None:
    """Claim the token before the first non-idempotent write, carrying its journal.

    Recording only *after* the effects leaves a window: a crash in between (or a
    failing ledger write) returns no response while the token still reads as
    unseen, so the retry re-runs every append. Reserving first makes that window
    durable -- and `progress` is what turns the retry that lands in it from a
    refusal into a resumption.

    The claim is also what advances the scene's epoch, and only the FIRST claim
    of a token does (a resumption is the same commit, not a second one). A
    tokenless save has no entry to reserve but is still a commit, so it advances
    the epoch too.

    The epoch that claim produced is kept on the entry as ``claimed``, which is
    how a resume tells "nothing has happened to this scene since I started" from
    "a newer save has overtaken me". On the entry rather than derived from the
    token, because a caller-minted key carries no epoch and is a supported thing
    to send -- deriving it would fence only the tokens we mint ourselves.
    """
    prior = lookup(cid, token)
    if not token:
        if sid:
            _put(cid, "", {}, bump_sid=sid)
        return
    entry = {"done": False, "result": None, "fingerprint": fp, "sid": sid,
             "progress": progress or {}}
    if prior is None:
        entry["claimed"] = scene_epoch(cid, sid) + 1 if sid else None
        _put(cid, token, entry, bump_sid=sid)
    else:
        entry["claimed"] = prior.get("claimed")
        _put(cid, token, entry)


def checkpoint(cid: str, token: str, progress: dict) -> None:
    """Persist the journal of a commit in flight.

    Called to make each step durably *attempted* before it is attempted. A crash
    between the journal write and the write it describes leaves that one step
    unresolved, which a resume reports rather than guesses at -- the alternative
    is re-running an append that may already have landed.
    """
    if not token:
        return                 # no ledger entry to journal into: nothing to do
    # Read once rather than lookup-then-_put: the journal carries the write-back
    # delta of every applied edit, and this runs once per edit.
    data = _read(cid)
    entry = data["tokens"].get(token)
    if not isinstance(entry, dict) or "done" not in entry or entry.get("done"):
        return                 # a settled outcome is not reopened by a late journal
    data["tokens"][token] = {**entry, "progress": progress, "at": now_iso()}
    _write(cid, data["tokens"], data["scenes"])


def record(cid: str, token: str, result: dict, fp: str = "", sid: str = "") -> None:
    """Complete the reservation with what this token's save returned.

    Drops the journal: the result supersedes it, and it is the bulky half of the
    entry -- it carries the write-back delta of every applied edit. The scene's
    epoch was already advanced by ``reserve``; retiring the earlier reviews at
    the claim rather than here is what keeps a commit that never finishes from
    letting one of them save on top of it.
    """
    _put(cid, token, {"done": True, "result": result, "fingerprint": fp, "sid": sid,
                      "progress": {}})
