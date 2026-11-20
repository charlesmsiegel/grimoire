"""Sprint 115's adapter ops for a campaign's story continuity: divergent offline scenes (049) and reviewing a fork
or retcon (054). Called as ``story.<op>``.

Only Sprint 115 edits this module. Each op is ``handler(ctx, args) -> dict`` over an existing domain service; see
``grimoire.app.adapter`` and docs/STORY.md. Nothing here keeps a record of its own: every answer is read again from
the library each time, and the one write is 054's own.

Continuity review is the director's (``ContinuityLibrary`` checks ``requested_by='director'`` on every read and
holds the exact original scene envelopes of a retained fork), so every op refuses ``director_only`` unless
``audience`` is ``director``.

**Divergent scenes (049).** ``divergent`` lists what the last syncs left of one campaign: each file a sync kept in
a keep-both conflict here (019/028's own record of it, as Devices and sync reads it: this device's version and
every other one kept beside it, so both candidates stay until a person resolves), with what 049's semantic
reconciliation says of it: whether keeping one side through the ordinary resolver is refused for it
(``semantic.resolution_risk``, the check a serving peer makes), what taking the other device's version here would
leave unmet (``semantic.validate_transition``, the serving-side admission check, as reason codes), and whether the
last sync with each peer since the app started held it for semantic review. It also lists the retained forks the
campaign holds (``ContinuityLibrary.inspect_fork``), each scene's variants and whether this device holds the
restored scene and the reconciled ledger the archive predicts. Restoring a scene as a retained fork
(``continuitystore.resolve_fork``) needs both devices' libraries open on one machine with their own authorities;
this app serves one library and reaches the other device only through sync, so it is named as not available and
never offered.

**Retcons and forks (054).** ``scenes`` reads 054's native snapshot (pins, recorded dependencies and problems) with
a short label per record, and a revision of the whole snapshot (``etag``). ``preview`` answers 054's impact report
for an edit, cut, replacement, replay or fork of the records named, with the pinned correction references 053
would cite and, given an authored alternative, 054's bounded rehearsal (speculative, no model). Nothing is written.
``declare_plan`` and ``declare_commit`` record one explicit dependency the native records cannot establish (a
line of dialogue that rests on an event, say) through ``ContinuityLibrary.declare_dependency``: reviewed against
the snapshot revision the page was shown, and committed as reviewed, once, however often it is retried.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping

from grimoire.app.adapter import Adapter, AdapterContext, Handler
from grimoire.chats.codec import ChatCodecError
from grimoire.continuity import Dependency, Pin, preview_impact
from grimoire.continuity.declarations import DEPENDENCY_SCHEMA, decode_dependency
from grimoire.continuitystore import FORK_SCHEMA, ContinuityLibrary
from grimoire.continuitywire.archive import archived_turn
from grimoire.core import DIRECTOR
from grimoire.core.authority import AuthorityError
from grimoire.core.identity import AcceptedRef, ChatRef, ContinuityRef, validate_segment
from grimoire.library import LibraryPathError
from grimoire.peersync.pairing import pairing_ended
from grimoire.scene import SceneRefused
from grimoire.scene.model import parse_turn_id
from grimoire.scenestore.library import StoredTurn
from grimoire.store import StaleWrite, StoreError, etag
from grimoire.store.layout import address_parts
from grimoire.sync import (
    NoDeviceIdentity,
    SyncError,
    SyncInterrupted,
    device_of,
    semantic,
)
from grimoire.sync.turns import open_conflicts
from grimoire.textapi import ApiRefused
from grimoire.textapi.views import turn_view

from .campaign import hub_of
from .media import media_of

ACTIONS = ("edit", "cut", "replace", "replay", "fork")
#: What a director may declare: the dependence 054's native records cannot see (docs/CONTINUITY.md). The other
#: reasons are recorded natively (a scene an event rests on, a receipt's possession) or are only potential.
DECLARABLE = ("dialogue", "causal_action", "asset")
#: At most this many records may be the roots of one preview from this page (054's own bound is 2048).
MAX_ROOTS = 64
#: 054's rehearsal takes 1 to 8 authored alternatives of at most 16,384 characters; the page sends one.
MAX_ALTERNATIVE = 16384
#: Reason codes of 049's semantic review that say its ancestry is uncertain, not recorded: a restoration refuses
#: them whatever device is chosen (``continuitystore.forks``).
UNCERTAIN = frozenset({"legacy_scene_history", "malformed_continuity_sources", "malformed_dependency",
                       "malformed_semantic_record", "unreadable_semantic_sources", "potential_direct_relation_impact",
                       "accepted_scene_missing", "missing_dependency_endpoint"})
#: What the domain does not do, said on the page and never offered as a control.
UNAVAILABLE = (
    {"what": "Restore one device's scene as a retained fork",
     "why": "049's restoration needs both devices' libraries open on one machine, each with its own authority; "
            "this app serves one library and reaches the other device only through sync"},
    {"what": "Merge both versions of a scene", "why": "049 keeps both versions; it never merges their text"},
    {"what": "Repair what depends on a changed scene", "why": "054 flags what a change touches; it repairs nothing"},
    {"what": "Write a continuation", "why": "no model is called here; a rehearsal is your own words"},
    {"what": "Promote an alternative into the story", "why": "054's alternatives and retained forks stay historical"},
)

_FAMILY = {"turns": "scene", "accepted": "accepted_event", "relations": "relationship", "wrapups": "summary",
           "holdings": "holdings", "evidence": "evidence", "events": "evidence", "images": "picture",
           "placements": "placement", "scope-art": "picture", "bookmarks": "bookmark", "chats": "chat",
           "instances": "instance", "transcripts": "transcript", "continuity": "continuity"}


# -- shared ----------------------------------------------------------------------------------------------------
def _director(args: Mapping[str, object]) -> None:
    audience = args.get("audience", "table")
    if audience not in ("table", "director"):
        raise ApiRefused("invalid_field", "audience must be table or director")
    if audience != "director":
        raise ApiRefused("director_only", "a story's continuity review is the director's")


def _segment(args: Mapping[str, object], key: str) -> str:
    value = args.get(key)
    if type(value) is not str:
        raise ApiRefused("invalid_field", f"{key} must be an id")
    try:
        validate_segment(value, key)
    except ValueError:
        raise ApiRefused("invalid_field", f"{key} is not a valid id") from None
    return value


def _campaign(ctx: AdapterContext, args: Mapping[str, object]) -> str:
    _director(args)
    campaign = _segment(args, "campaign")
    if ctx.store.campaign_presence(campaign) != "present":
        raise ApiRefused("no_campaign", f"this library holds no campaign {campaign}")
    return campaign


def _guarded(handler: Handler) -> Handler:
    """Every domain refusal keeps a machine-readable reason; none escapes as a server error."""
    def guarded(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
        try:
            return handler(ctx, args)
        except ApiRefused:
            raise
        except StaleWrite:
            raise ApiRefused("changed", "the campaign changed since it was read: review it again") from None
        except AuthorityError as error:
            raise ApiRefused("not_authorized", str(error) or "continuity review is the library owner's") from None
        except (NoDeviceIdentity, SyncInterrupted, SyncError, StoreError, LibraryPathError, OSError) as error:
            raise ApiRefused("unreadable", f"the campaign's records cannot be read: {type(error).__name__}") from None
        except (ValueError, TypeError, KeyError) as error:
            raise ApiRefused("continuity_refused", str(error) or type(error).__name__) from None
    guarded.__name__ = guarded.__qualname__ = handler.__name__
    return guarded


def _library(ctx: AdapterContext) -> ContinuityLibrary:
    return ContinuityLibrary(ctx.store, ctx.handle)


def _path(ref) -> str:  # type: ignore[no-untyped-def]
    return "/".join(address_parts(ref))


def _kind(path: str) -> str:
    parts = path.split("/")
    return _FAMILY.get(parts[2], parts[2]) if len(parts) > 2 else "other"


def _turn_of(path: str) -> tuple[str, int] | None:
    """(chat id, number) of a turn's path, or None."""
    parts = path.split("/")
    if len(parts) != 4 or parts[2] != "turns" or not parts[3].endswith(".json"):
        return None
    parsed = parse_turn_id(parts[3][:-5])
    return None if parsed is None else (parsed[0], parsed[1])


def _said(view: dict) -> str:
    """What a turn's table view says: its posts, then its kept take's lines."""
    said = [post["text"] for post in view.get("posts", ()) if post.get("text")]
    kept = view.get("kept") or {}
    said += [line.get("text") for line in kept.get("lines", ()) if isinstance(line, dict) and line.get("text")]
    return " / ".join(said)


class _Chats:
    """Each chat of the campaign opened at most once per answer, for its cast's names and its turns' table view."""

    def __init__(self, ctx: AdapterContext, campaign: str) -> None:
        self.ctx, self.campaign, self.open = ctx, campaign, {}  # type: ignore[var-annotated]

    def scene(self, chat_id: str):  # type: ignore[no-untyped-def]
        if chat_id not in self.open:
            try:
                self.open[chat_id] = self.ctx.runner.open(ChatRef(self.campaign, chat_id))
            except (SceneRefused, StoreError, ChatCodecError, LibraryPathError, OSError, ValueError):
                self.open[chat_id] = None
        return self.open[chat_id]

    def names(self, chat_id: str):  # type: ignore[no-untyped-def]
        scene = self.scene(chat_id)
        return {} if scene is None else scene.names

    def said(self, chat_id: str, turn_id: str) -> str | None:
        scene = self.scene(chat_id)
        if scene is None:
            return None
        for stored in scene.turns.turns:
            if stored.record.turn_id == turn_id:
                return _said(turn_view(stored, scene.names))
        return None


# -- records (054's snapshot) ----------------------------------------------------------------------------------
def _digest(snap) -> str:  # type: ignore[no-untyped-def]
    """The revision of the whole snapshot a review was made against: every pin, edge and problem."""
    value = {"pins": sorted((pin.path, pin.revision) for pin in snap.pins.values()),
             "edges": sorted((e.source.path, e.source.revision, e.dependent.path, e.dependent.revision, e.reason,
                              e.certainty) for e in snap.dependencies),
             "problems": list(snap.problems)}
    return "sha256:" + hashlib.sha256(json.dumps(value, separators=(",", ":")).encode("utf-8")).hexdigest()


def _record(ctx: AdapterContext, campaign: str, pin: Pin, chats: _Chats) -> dict:
    path = pin.path
    kind = _kind(path)
    name = path.rsplit("/", 1)[-1][:-5]
    entry: dict = {"path": path, "revision": pin.revision, "kind": kind, "id": name, "label": None}
    turn = _turn_of(path)
    if turn is not None:
        chat_id, number = turn
        entry.update(chat=str(ChatRef(campaign, chat_id)), number=number, label=chats.said(chat_id, name))
    elif kind == "accepted_event":
        try:
            event, _etag = hub_of(ctx).effects.read(AcceptedRef(campaign, name), requested_by=DIRECTOR)
            entry["label"] = event.summary
            entry["chat"] = str(event.chat)
        except (StoreError, LibraryPathError, OSError, ValueError, AuthorityError, KeyError):
            pass
    elif kind == "holdings":
        entry["label"] = "what " + name + " holds"
    return entry


def _records_view(ctx: AdapterContext, campaign: str, snap) -> dict:  # type: ignore[no-untyped-def]
    chats = _Chats(ctx, campaign)
    records = [_record(ctx, campaign, snap.pins[path], chats) for path in sorted(snap.pins)]
    edges = [{"source": edge.source.path, "dependent": edge.dependent.path, "reason": edge.reason,
              "certainty": edge.certainty} for edge in snap.dependencies]
    return {"campaign": campaign, "etag": _digest(snap), "coverage": snap.coverage, "records": records,
            "dependencies": edges, "problems": list(snap.problems), "declarable": list(DECLARABLE),
            "actions": list(ACTIONS), "unavailable": [dict(item) for item in UNAVAILABLE]}


def _scenes(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    campaign = _campaign(ctx, args)
    return _records_view(ctx, campaign, _library(ctx).snapshot(campaign))


def _pin(value: object, key: str) -> Pin:
    if not isinstance(value, Mapping) or set(value) != {"path", "revision"}:
        raise ApiRefused("invalid_field", f"{key} names a record as shown: its path and revision")
    try:
        return Pin(value["path"], value["revision"])
    except (ValueError, TypeError) as error:
        raise ApiRefused("invalid_field", f"{key}: {error}") from None


def _current(snap, pin: Pin, campaign: str) -> Pin:  # type: ignore[no-untyped-def]
    """``pin`` as the snapshot holds it now: missing is refused, and a revision since moved on is ``changed``."""
    if pin.campaign_id != campaign:
        raise ApiRefused("invalid_field", f"{pin.path} is not in campaign {campaign}")
    found = snap.pins.get(pin.path)
    if found is None:
        raise ApiRefused("missing_record", f"{pin.path} is no longer in this campaign")
    if found != pin:
        raise ApiRefused("changed", f"{pin.path} changed since it was shown: review it again")
    return found


def _impact(item, labels: Mapping[str, dict]) -> dict:  # type: ignore[no-untyped-def]
    return {"target": labels.get(item.target.path, {"path": item.target.path, "kind": _kind(item.target.path)}),
            "reason": item.reason, "certainty": item.certainty, "chain": [pin.path for pin in item.chain]}


def _preview(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """054's impact report for ``action`` on ``roots`` (records as shown), with what 053 would cite and, given an
    authored ``alternative``, the bounded rehearsal. Nothing is written and no model is called."""
    campaign = _campaign(ctx, args)
    action = args.get("action")
    if action not in ACTIONS:
        raise ApiRefused("invalid_field", "action must be edit, cut, replace, replay or fork")
    raw = args.get("roots")
    if type(raw) is not list or not 1 <= len(raw) <= MAX_ROOTS:
        raise ApiRefused("invalid_field", f"name from 1 to {MAX_ROOTS} records to change")
    alternative = args.get("alternative")
    if alternative is not None and (type(alternative) is not str or not alternative.strip()
                                    or len(alternative) > MAX_ALTERNATIVE):
        raise ApiRefused("invalid_field", f"an alternative is your own words, at most {MAX_ALTERNATIVE} characters")
    library = _library(ctx)
    snap = library.snapshot(campaign)
    roots = [_pin(item, "roots") for item in raw]
    if len({pin.path for pin in roots}) != len(roots):
        raise ApiRefused("invalid_field", "each record is named once")
    roots = [_current(snap, pin, campaign) for pin in roots]
    view = _records_view(ctx, campaign, snap)
    labels = {item["path"]: item for item in view["records"]}
    if alternative is None:
        report, rehearsal = library.preview(campaign, roots, action), None
    else:
        rehearsed = library.rehearse(campaign, roots, action, (alternative,))
        report = rehearsed.report
        rehearsal = {"status": rehearsed.rehearsal.status, "model_cost": rehearsed.rehearsal.model_cost,
                     "alternatives": list(rehearsed.rehearsal.alternatives)}
    corrections, refused = None, None
    if report.complete:
        corrections = [{"source": item.source.path, "dependent": item.dependent.path, "reason": item.reason,
                        "certainty": item.certainty, "chain": [pin.path for pin in item.chain]}
                       for item in library.corrections(campaign, roots, action)]
    else:
        refused = "an incomplete report exports no correction: " + "; ".join(report.problems)
    return {"campaign": campaign, "etag": view["etag"], "action": action, "roots": [pin.path for pin in report.roots],
            "complete": report.complete, "problems": list(report.problems),
            "impacts": [_impact(item, labels) for item in report.impacts], "corrections": corrections,
            "corrections_refused": refused, "rehearsal": rehearsal, "written": False}


# -- declaring a dependency (054) ------------------------------------------------------------------------------
def _declaration(ctx: AdapterContext, args: Mapping[str, object]):  # type: ignore[no-untyped-def]
    """The declaration the request names, checked against the snapshot revision it was reviewed at:
    ``(campaign, library, snapshot, edge, ref, already_applied)``. Nothing is written."""
    campaign = _campaign(ctx, args)
    operation_id = _segment(args, "operation_id")
    reason = args.get("reason")
    if reason not in DECLARABLE:
        raise ApiRefused("invalid_field", "reason must be " + ", ".join(DECLARABLE))
    expected = args.get("expected")
    if type(expected) is not str:
        raise ApiRefused("review_required", "read the campaign's records before declaring what depends on what")
    source, dependent = _pin(args.get("source"), "source"), _pin(args.get("dependent"), "dependent")
    if source.path == dependent.path:
        raise ApiRefused("invalid_field", "a record does not depend on itself")
    library = _library(ctx)
    ref = ContinuityRef(campaign, operation_id)
    edge = Dependency(source, dependent, reason, "known")
    found = ctx.store.campaign_continuity(campaign)
    stored = found.records.get(ref)
    if stored is None and any(problem.path == _path(ref) for problem in found.problems):
        raise ApiRefused("unreadable", f"{_path(ref)} cannot be read")
    if stored is not None:  # the same operation id: the same declaration again, or another one refused
        same = False
        if stored.record.schema == DEPENDENCY_SCHEMA:
            try:
                same = decode_dependency(stored.record.schema, stored.record.payload) == edge
            except (ValueError, TypeError, KeyError):
                same = False
        if not same:
            raise ApiRefused("operation_conflict", "this operation id already records something else")
        return campaign, library, library.snapshot(campaign), edge, ref, True
    snap = library.snapshot(campaign)
    if _digest(snap) != expected:
        raise ApiRefused("changed", "the campaign's records changed since they were shown: review them again")
    if snap.problems:
        raise ApiRefused("incomplete_snapshot", "a declaration needs every record readable: " + "; ".join(snap.problems))
    _current(snap, source, campaign)
    _current(snap, dependent, campaign)
    if edge in snap.dependencies:
        raise ApiRefused("already_declared", "that dependence is already recorded")
    trial = preview_impact(snap.pins.values(), snap.dependencies + (edge,), (source,))
    if not trial.complete:
        raise ApiRefused("invalid_dependency", "; ".join(trial.problems))
    return campaign, library, snap, edge, ref, False


def _declared(ctx: AdapterContext, campaign: str, snap, edge, ref, already: bool) -> dict:  # type: ignore[no-untyped-def]
    """What the declaration does: the file it writes and what a change of its source would then reach."""
    before = {item.target.path for item in preview_impact(snap.pins.values(), snap.dependencies, (edge.source,)).impacts}
    deps = snap.dependencies if already else snap.dependencies + (edge,)
    after = preview_impact(snap.pins.values(), deps, (edge.source,))
    labels = {item["path"]: item for item in _records_view(ctx, campaign, snap)["records"]}
    newly = [_impact(item, labels) for item in after.impacts if item.target.path not in before
             or (item.target.path == edge.dependent.path and item.certainty == "known")]
    return {"operation": ref.record_id, "already_applied": already, "source": labels.get(edge.source.path),
            "dependent": labels.get(edge.dependent.path), "reason": edge.reason, "files": [_path(ref)],
            "reaches": newly}


def _declare_plan(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    campaign, _, snap, edge, ref, already = _declaration(ctx, args)
    return {"plan": _declared(ctx, campaign, snap, edge, ref, already), "scenes": _records_view(ctx, campaign, snap)}


def _declare_commit(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    campaign, library, snap, edge, ref, already = _declaration(ctx, args)
    done = _declared(ctx, campaign, snap, edge, ref, already)
    if not already:
        try:
            library.declare_dependency(ref.record_id, edge.source, edge.dependent, edge.reason)
        except ValueError as error:  # 054 rechecks under the store lock: an endpoint moved, or a cycle
            message = str(error)
            if "stale" in message or "missing" in message:
                raise ApiRefused("changed", message) from None
            raise ApiRefused("invalid_dependency", message) from None
    return {"done": done, "scenes": _records_view(ctx, campaign, library.snapshot(campaign))}


# -- divergent offline scenes (049) ----------------------------------------------------------------------------
def _reasons(ctx: AdapterContext, campaign: str, path: str, data: bytes | None) -> dict | None:
    """What 049's serving-side admission check says of taking ``data`` at ``path`` on this device."""
    if data is None:
        return None
    try:
        found = [review for review in semantic.validate_transition(ctx.store, {path: data})
                 if review.campaign == campaign]
    except (StoreError, LibraryPathError, OSError, SyncError):
        return {"reasons": ["unreadable_semantic_sources"], "uncertain": True, "scenes": [], "endpoints": []}
    reasons = sorted({reason for review in found for reason in review.reasons})
    return {"reasons": reasons, "uncertain": bool(UNCERTAIN & set(reasons)),
            "scenes": sorted({p for review in found for p in review.scenes}),
            "endpoints": sorted({p for review in found for p in review.endpoints})}


def _candidate(found: dict) -> dict:
    """One kept version as Devices and sync shows it, cut to what this page says: the table's view of a turn (never
    intentions, briefs or unshown lines), and only the kind of any other record."""
    preview = found.get("preview") or {}
    turn = preview.get("turn")
    return {"etag": found.get("etag"), "deleted": bool(found.get("deleted")), "peers": list(found.get("peers", [])),
            "said": _said(turn) or None if isinstance(turn, dict) else None,
            # a turn a fork choice set aside was heard by no one (035): its id and number only, never its text
            "set_aside": isinstance(turn, dict) and "posts" not in turn,
            "unreadable": "unreadable" in preview}


def _variant_text(path: str, text: str, chats: _Chats) -> str | None:
    try:
        _, turn = archived_turn(path, text)
    except (ValueError, TypeError, KeyError):
        return None
    return _said(turn_view(StoredTurn(turn, etag(text.encode("utf-8"))), chats.names(turn.chat.chat_id)))


def _retained(ctx: AdapterContext, campaign: str, device: str | None, chats: _Chats) -> tuple[list, list]:
    """The campaign's retained forks, as 054 validates them, and the problems of those that do not read."""
    library = _library(ctx)
    found = ctx.store.campaign_continuity(campaign)
    problems = [{"path": problem.path, "code": problem.code} for problem in found.problems]
    listing = None
    forks = []
    for ref, stored in sorted(found.records.items(), key=lambda item: item[0].record_id):
        if stored.record.schema != FORK_SCHEMA:
            continue
        try:
            archive = library.inspect_fork(campaign, ref.record_id)
        except (ValueError, TypeError, KeyError) as error:
            problems.append({"path": _path(ref), "code": str(error) or type(error).__name__})
            continue
        if listing is None:
            listing = semantic.listing(ctx.store, campaign)
        here = {p: tag for p, tag in listing.items() if p != _path(ref)}
        scenes = []
        for scene in archive["scenes"]:
            current = listing.get(scene["path"])
            mine = scene["variants"].get(device) if device else None
            state = ("restored" if current == scene["source_etag"] else
                     "original" if mine is not None and current == mine["before"] else "changed")
            turn = _turn_of(scene["path"])
            scenes.append({"path": scene["path"], "chat": None if turn is None else str(ChatRef(campaign, turn[0])),
                           "number": None if turn is None else turn[1], "here": state,
                           "variants": [{"device": name, "selected": name == archive["source_device"],
                                         "said": _variant_text(scene["path"], variant["text"], chats)}
                                        for name, variant in sorted(scene["variants"].items())]})
        forks.append({"operation": archive["operation_id"], "source_device": archive["source_device"],
                      "devices": list(archive["devices"]), "scenes": scenes,
                      "source_revisions": len(archive["source_revisions"]),
                      "reconciled_here": here == archive["reconciled"]})
    return forks, problems


def _divergent(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    campaign = _campaign(ctx, args)
    try:
        device = device_of(ctx.store)
    except NoDeviceIdentity:
        device = None
    media = media_of(ctx.api)
    listed = media.call("sync.conflicts", {})
    conflicts = [item for item in listed["conflicts"] if item["campaign"] == campaign]
    folder = f"campaigns/{campaign}"
    try:
        kept, _complete = open_conflicts(ctx.store, folder, ended=lambda peer: pairing_ended(ctx.store, peer),
                                         require_kept=True)
    except (StoreError, OSError, ValueError, SyncInterrupted):
        kept = {}
    kept_bytes = {etag(data): data for versions in kept.values() for data in versions}
    reports = dict(getattr(ctx.api, "reports", {}) or {})
    held_by = {peer: sorted(item["path"] for item in report.get("conflicts", ())
                            if item.get("code") == "semantic_review" and item["path"].startswith(folder + "/"))
               for peer, report in reports.items()}
    chats = _Chats(ctx, campaign)
    # what 054's snapshot cannot establish about scene ancestry here (an accepted event with no record of the
    # scenes it rests on, an unreadable turn or declaration): a restoration refuses it whatever device is chosen
    ancestry = [problem for problem in _library(ctx).snapshot(campaign).problems
                if any(f"/{family}/" in problem for family in ("accepted", "turns", "continuity"))]
    scenes = []
    for item in conflicts:
        path = item["path"]
        peers = sorted({side["peer"] for side in item["peers"]} | {p for t in item["theirs"] for p in t["peers"]})
        refused = {peer: semantic.resolution_risk(ctx.store, path, peer) for peer in peers}
        turn = _turn_of(path)
        entry = {"path": path, "kind": _kind(path), "mine": _candidate(item["mine"]),
                 "theirs": [_candidate(t) for t in item["theirs"]], "peers": peers,
                 "their_deletion": item["their_deletion"],
                 "keep_one": "refused" if any(refused.values()) or not peers else "needs_you",
                 "detected": sorted(peer for peer, paths in held_by.items() if path in paths),
                 "taking_theirs": [_reasons(ctx, campaign, path, kept_bytes.get(t["etag"])) for t in item["theirs"]],
                 "uncertain": bool(ancestry),
                 "chat": None if turn is None else str(ChatRef(campaign, turn[0])),
                 "number": None if turn is None else turn[1], "continues": None}
        entry["uncertain"] = entry["uncertain"] or any(review is None or review["uncertain"]
                                                       for review in entry["taking_theirs"])
        scenes.append(entry)
    # a later scene of the same chat that differs too is a divergent continuation: it may rest on the earlier one
    for entry in scenes:
        if entry["number"] is None:
            continue
        earlier = [other for other in scenes if other["chat"] == entry["chat"] and other["number"] is not None
                   and other["number"] < entry["number"]]
        if earlier:
            entry["continues"] = min(earlier, key=lambda other: other["number"])["path"]
    shown = {entry["path"] for entry in scenes}
    held = sorted({path for paths in held_by.values() for path in paths} - shown)
    forks, problems = _retained(ctx, campaign, device, chats)
    waiting = media.call("sync.forks", {})
    choices = [item["chat"] for item in waiting["forks"] if item["chat"].startswith(f"campaign:{campaign}#")]
    return {"campaign": campaign, "device": device,
            "peers": [{"peer": peer, "status": report.get("status"), "held": held_by.get(peer, [])}
                      for peer, report in sorted(reports.items())],
            "scenes": scenes, "ancestry": ancestry, "held": [{"path": path, "kind": _kind(path)} for path in held],
            "unknown": campaign in listed["unknown"], "retained": forks, "problems": problems,
            "fork_choices": choices, "unavailable": [dict(item) for item in UNAVAILABLE[:2]]}


_OPS: dict[str, Callable[[AdapterContext, Mapping[str, object]], dict]] = {
    "divergent": _divergent, "scenes": _scenes, "preview": _preview,
    "declare_plan": _declare_plan, "declare_commit": _declare_commit,
}

ADAPTER = Adapter("story", owner="115", ops={name: _guarded(op) for name, op in _OPS.items()})

__all__ = ["ACTIONS", "ADAPTER", "DECLARABLE", "UNAVAILABLE", "UNCERTAIN"]
