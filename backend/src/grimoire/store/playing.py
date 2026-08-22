"""Campaign play-state: the played-greeting set, availability bound to a
campaign, and starting a scene from a greeting."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from . import atomic, characters, entities, greetings, locks, overlay, pcs, scene_ideas
from .appearances import cast as appearances_cast
from .appearances import transitions as appearances_transitions
from .appearances import versions as appearances_versions
from .campaigns import paths as campaigns_paths
from .context import macros as context_macros
from .scenes import lifecycle as scenes_lifecycle
from .scenes import moment as scenes_moment
from .scenes import read as scenes_read
from .scenes import write as scenes_write


class PlayError(Exception):
    pass


_MARK_KEYS = ("played", "completed", "skipped")


def _marks_path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "played.json"


def read_marks(cid: str) -> dict[str, set[str]]:
    p = _marks_path(cid)
    if not p.exists():
        return {k: set() for k in _MARK_KEYS}
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, list):  # legacy format: a bare list of played ids
        data = {"played": data}
    return {k: set(data.get(k, [])) for k in _MARK_KEYS}


def _write_marks(cid: str, marks: dict[str, set[str]]) -> None:
    payload = {k: sorted(marks[k]) for k in _MARK_KEYS}
    atomic.write_text(_marks_path(cid), json.dumps(payload, indent=2) + "\n")


def read_played(cid: str) -> set[str]:
    return read_marks(cid)["played"]


def _mark_played(cid: str, gid: str) -> None:
    marks = read_marks(cid)
    marks["played"].add(gid)
    marks["completed"].discard(gid)  # actually playing supersedes an off-screen mark
    marks["skipped"].discard(gid)
    _write_marks(cid, marks)


def stamping_scene(cid: str, gid: str) -> str | None:
    """The scene recording that `gid` was played, or None if no scene does.

    Walks every scene's frontmatter head -- `read_scene_meta` exists for exactly
    this kind of bulk scan and never parses a transcript. Two head-parses per
    scene, since `list_scenes` has already done one: acceptable only because
    this runs on an explicit unmark, never in the picker's path."""
    for meta in scenes_read.list_scenes(cid):
        if scenes_read.read_scene_meta(cid, meta["id"]).get("greeting", "") == gid:
            return meta["id"]
    return None


def mark_greeting(cid: str, gid: str, status: str) -> None:
    """Set a greeting's off-screen mark: completed / skipped / none (clear).

    A played mark is normally immutable -- a scene records the play. But the
    scene can be gone: the new-scene chooser deletes a half-seeded scene on
    failure, and versions before the cleanup rule could strand the mark behind
    it. An orphaned mark is now clearable, because a played greeting is
    unavailable and an orphan would otherwise be unstartable forever.

    The scan-then-clear is one locked critical section, not two steps: without
    it, ``stamping_scene``'s sweep can find no scene stamping `gid`, and a
    concurrent `start_from_greeting` can stamp one into existence a moment
    later -- the mark then clears out from under a play that is genuinely
    mid-flight, on the strength of a scan that was already stale by the time
    it finished. `stamp_greeting` takes this same lock (`scenes._serialized`),
    so holding it here for the whole scan excludes that write, not just the
    final one that would have raced this call's own.
    """
    overlay.read_greeting(cid, gid)  # raises GreetingNotFound
    if status not in ("completed", "skipped", "none"):
        raise PlayError(f"unknown mark status: {status}")
    with locks.campaign_lock(cid):
        marks = read_marks(cid)
        if gid in marks["played"]:
            if status != "none" or stamping_scene(cid, gid) is not None:
                raise PlayError("greeting was played in a scene; its mark cannot be changed")
        marks["completed"].discard(gid)
        marks["skipped"].discard(gid)
        marks["played"].discard(gid)
        if status != "none":
            marks[status].add(gid)
        _write_marks(cid, marks)


def player_tags(cid: str) -> set[str]:
    out: set[str] = set()
    for a in appearances_cast.roster(cid):
        if a["role"] == "player" and a["kind"] == "pcs":
            try:
                out |= set(pcs.read_pc(overlay.pc_root(cid, a["id"]), a["id"])["meta"]["tags"])
            except pcs.PCNotFound:
                continue
    return out


def _resolve_locations(cid: str, rows: list[dict], known: set[str] | None = None) -> None:
    """Blank any `location` these rows name that this campaign no longer has.

    This is the read-side policy `suggest.validate_ideas` applies to a saved
    idea's refs, and for the same reason: a greeting is authored in a world and
    read through a campaign that may since have deleted the location it names, and
    every consumer downstream -- the confirm form's pre-filled picker, the
    ledger card's caption -- would otherwise be handed an id that resolves to
    nothing. `greetings.availability` cannot do this itself: it is pure and
    knows no root.

    The listing is taken only when some row actually names a location, and a
    caller holding the campaign's location ids already passes them as `known`
    rather than making this read them again. It costs one file read per location
    in the campaign, and `available_greetings` is on the scene picker's open
    path -- a world that does not use the field must not pay for it (#218).
    """
    if not any(r["location"] for r in rows):
        return
    if known is None:
        known = {e["id"] for e in overlay.list_entities(cid, "locations")}
    for r in rows:
        if r["location"] not in known:
            r["location"] = ""


def available_greetings(cid: str, after: str | None = None, *,
                        locations: bool = True) -> list[dict]:
    """Every greeting this campaign could start, with its gating verdict.

    `locations=False` leaves each row's `location` exactly as the greeting
    records it, unchecked. Resolving costs one file read per location in the
    campaign, so every caller that does not read `location` passes it: today
    `start_from_greeting` (which wants `{id: available}` alone),
    `suggest.greeting_candidates` (ranking), and `greeting_ideas` (which has a
    second batch of rows and takes one sweep over both instead of one each).
    Only the route serving the picker actually reads the field.
    """
    plotmap = overlay.read_plotmap(cid)
    marks = read_marks(cid)
    out = greetings.availability(overlay.list_greetings(cid), plotmap,
                                 marks["played"] | marks["completed"],
                                 player_tags(cid), skipped=marks["skipped"])
    mark_of = dict.fromkeys(marks["played"], "played")
    mark_of.update(dict.fromkeys(marks["completed"], "completed"))
    for g in out:
        g["mark"] = mark_of.get(g["id"])
    unlocked: set[str] = set()
    if after:
        gid = scenes_read.read_scene(cid, after)["meta"].get("greeting", "")
        if gid:
            unlocked = set(greetings.edges_of(plotmap, gid)["leads_to"])
    for g in out:
        g["unlocked"] = g["id"] in unlocked
    if locations:
        _resolve_locations(cid, out)
    out.sort(key=lambda g: not g["unlocked"])  # stable: unlocked first, rest keep order
    return out


def greeting_ideas(cid: str, *, known_locations: set[str] | None = None) -> list[dict]:
    """The greeting half of the scene ledger (#88), composed rather than stored.

    Lives here, next to the marks it reads, because status is *derived* from
    `played.json` and so cannot drift from what the greeting machinery itself
    believes:

    - played or completed -> `"used"`; the greeting opened a scene, or the
      reader recorded that it happened off-screen;
    - skipped -> `"dismissed"`; `greetings.availability` drops these from its
      output entirely (the plot routes around a greeting marked won't-do),
      which is why they are collected separately below;
    - otherwise startable -> `"active"`.

    A greeting that is neither marked nor startable -- gated behind a plot
    predecessor, excluded by something already played, missing a required
    player tag -- is omitted. It is not an idea anyone can act on, and its
    gating is the plot map's business rather than the ledger's.

    Cast and date are blank BY CONSTRUCTION, the same reason
    `sceneDraft.greetingDraft` leaves them so: the greeting body is the opening
    post and `start_from_greeting` seats its own cast under locked-version
    rules nothing here may re-implement. The location is NOT in that set any
    more (#218) -- a greeting now records the setting its scene opens at, so
    there is a real answer to copy rather than a rule against inventing one.
    `used_scene` is blank too rather than resolved -- `stamping_scene` is a
    per-greeting sweep of every scene's frontmatter, which is affordable on an
    explicit unmark and not in a list.

    This is not cheap: `available_greetings` parses the frontmatter of every
    greeting in the campaign, and the skipped ones need a second pass because
    `availability` drops them. The second pass is taken only when something
    actually is skipped, and the route that calls this lets a caller decline
    the whole composition (`GET /scene-ideas?greetings=false`) -- the picker
    does, since it renders greetings from its own ranked read.
    """
    marks = read_marks(cid)
    used = marks["played"] | marks["completed"]

    def entry(g: dict, status: str) -> dict:
        return {"id": f"{scene_ideas.GREETING_PREFIX}{g['id']}", "title": g["name"],
                "premise": "", "cast": [], "location": g["location"], "date": "",
                "pcless": bool(g.get("pcless")), "source": scene_ideas.GREETING,
                "status": status, "created": "", "used_scene": ""}

    # Both batches of rows are collected first and resolved in ONE sweep: the
    # check reads every location in the campaign, so doing it inside
    # `available_greetings` and again for the skipped rows would read them twice
    # to answer one request.
    startable = [g for g in available_greetings(cid, locations=False)
                 if g["id"] in used or g["available"]]
    skipped = ([g for g in overlay.list_greetings(cid) if g["id"] in marks["skipped"]]
               if marks["skipped"] else [])
    _resolve_locations(cid, startable + skipped, known=known_locations)
    return ([entry(g, scene_ideas.USED if g["id"] in used else scene_ideas.ACTIVE)
             for g in startable]
            + [entry(g, scene_ideas.DISMISSED) for g in skipped])


def _seed_location(cid: str, sid: str, eid: str, *, seed: bool) -> None:
    """Give a scene the setting its greeting names (#218) -- seeding, not
    overriding.

    Every one of the three reasons to do nothing lives here rather than at the
    call site: the caller opting out, the greeting naming nowhere, and the scene
    already being somewhere -- the last decided under the campaign lock, see
    below. Only a scene with no location yet is touched, and
    only for a caller that has made no location decision of its own -- see
    `StartFromGreeting.seed_location` for when the confirm pane opts out and
    when it deliberately does not. A location already on the scene
    is a choice someone
    made about *this* scene, and re-imposing the greeting's over it would make
    every location picker upstream a decoration. On an empty history
    `set_location` is silent, which is what keeps the opener from being preceded
    by a "the scene moves to X" line describing a move that never happened.

    A missing location is skipped rather than raised. An inherited greeting can
    name a location this campaign has since deleted, and the setting is one
    optional piece of metadata a reader can supply by hand in a click -- losing
    it must not cost them the opener, which is the part that cannot be
    reconstructed. Placed before `stamp_greeting` so it runs while the scene is
    still empty, and before the body is expanded and appended.
    """
    if not seed or not eid:
        return
    # The emptiness check and the write are ONE critical section, for the same
    # reason the scene-busy guards are taken inside the hold that covers their
    # mutation: check first and lock after, and a concurrent
    # `PUT /scenes/{sid}/location` fits in the window. `set_location` would then
    # find a non-empty history, take its `moved` branch, and prepend "the scene
    # moves to X" ahead of an opener that has not been written yet -- a
    # transition recording a move nobody made, in the one artifact this app
    # cannot regenerate. The lock is reentrant, so `set_location` taking it
    # again is free.
    with locks.campaign_lock(cid):
        if scenes_read.get_location_history(cid, sid):
            return
        with contextlib.suppress(entities.EntityNotFound):
            scenes_moment.set_location(cid, sid, eid)


def start_from_greeting(cid: str, sid: str, gid: str, *, seed_location: bool = True) -> str:
    g = overlay.read_greeting(cid, gid)["meta"]   # raises GreetingNotFound
    scene = scenes_read.read_scene(cid, sid)               # raises SceneNotFound
    if scene["messages"]:
        raise PlayError("scene already has messages")
    scene_pcless = scene["meta"].get("pcless") == "true"
    if scene_pcless and not g["pcless"]:
        raise PlayError("an offscreen scene must start from an offscreen greeting")
    if g["pcless"] and appearances_cast.players_in_scene(cid, sid):
        raise PlayError("an offscreen greeting cannot start a scene with players seated")
    # `locations=False`: this reads the availability verdict only, and resolving
    # each row's location would read every location in the campaign to throw the
    # answer away.
    if not {a["id"]: a["available"]
            for a in available_greetings(cid, locations=False)}.get(gid, False):
        raise PlayError(f"greeting {gid} is not available")
    # Cast everyone present at the opener. A locked version always wins; otherwise
    # the primary uses the greeting's version and co-present characters their default.
    for actor in dict.fromkeys(a for a in [g["character"], *g["present"]] if a):
        version = appearances_versions.locked_version(cid, "characters", actor)
        if version is None:
            version = g["version"] if actor == g["character"] else \
                characters.read_character(overlay.char_root(cid, actor), actor)["meta"]["default_version"]
            # A materialized actor's version set is authoritative. If the
            # campaign has purged the version this inherited greeting names,
            # don't let the first-appearance lock revive it from the world.
            if actor == g["character"] and appearances_versions.actor_hash(
                    overlay.char_root(cid, actor), "characters", actor, version) is None:
                raise PlayError(
                    f"greeting {gid} needs version '{version}' of {actor}, "
                    f"which is no longer in this campaign")
        appearances_transitions.appear(cid, sid, "characters", actor, version, "npc")
    if g["pcless"] and not scene_pcless:
        scenes_write.set_pcless(cid, sid)  # before substitution: {{user}} needs the pcless fallback
    _seed_location(cid, sid, g["location"], seed=seed_location)
    scenes_write.stamp_greeting(cid, sid, gid)
    text = context_macros.expand_macros(overlay.read_greeting(cid, gid)["body"],
                                        context_macros.scene_substitutions(cid, sid), cid, sid)
    # append_reply, not append_message: the greeting is authored rather than
    # generated, but it is the strongest length anchor the model has at the
    # start of a scene and it WILL be matched, so it records a turn like any
    # other model output.
    #
    # Split on the SAME marker grammar routes.streaming._persist_reply uses. Storing a
    # multi-block greeting as one segment records turn_sizes [1] while
    # _parse_messages re-splits it into N messages at read time; drift
    # segmentation would then measure only the trailing block of the very turn
    # that sets the scene's length anchor.
    scenes_write.append_reply(cid, sid, scenes_write.split_reply(
        text, frozenset(appearances_cast.player_names(cid, sid))))
    # Marked only once the body is actually on the scene. Marking earlier meant
    # a failed expansion or append consumed the greeting anyway, and -- since a
    # played greeting is now unavailable -- consumed it permanently.
    #
    # The availability guard above (`available_greetings`) ran unlocked, so a
    # concurrent start of this same `gid` -- into a different scene -- can
    # have passed it too and be racing to this same point; both would
    # otherwise mark `gid` played and both would report success (#318).
    # Recording the mark has to be re-verified and written in one locked
    # step, or the second racer's read of `marks` is already stale by the
    # time it writes. This is the only lock this function takes: the
    # narrower availability check above and the calendar-touching macro
    # expansion before it both stay outside, the latter deliberately --
    # `context_macros.expand_macros` resolves and runs a user-authored
    # calendar provider (see `scenes/lifecycle.py:_date_hint`), and nothing
    # bounds how long that can take.
    with locks.campaign_lock(cid):
        marks = read_marks(cid)
        if gid in marks["played"] or gid in marks["completed"]:
            # Distinct from the pre-flight guard's message above: that one is
            # a stale client (checked availability before someone else's
            # unrelated play landed); this one is a lost race, decided only
            # here, under the lock -- worth telling apart in a log.
            raise PlayError(f"greeting {gid} was just claimed by a concurrent start")
        _mark_played(cid, gid)
    # retitle last: any earlier failure leaves the caller's sid valid for cleanup
    return scenes_lifecycle.rename_scene(cid, sid, g["name"])
