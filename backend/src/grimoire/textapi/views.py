"""JSON views of what the text API reads (044): plain dicts of strings, numbers, booleans and lists.

Every ref is written in its own string form, so a client hands it back unchanged. Nothing here reads a
file or writes one; ``api.py`` gathers the values.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from grimoire.chats import NavEntry, ScopeEntry
from grimoire.chats.model import ChatRecord
from grimoire.core import DIRECTOR
from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.images import Hidden, Resolution, Shown, Unavailable
from grimoire.scene import SceneRefused, Take, TurnRecord
from grimoire.scenestore import StoredTurn

#: The source roles navigation filters by: a chat is found by its character or by its scenario.
FILTER_ROLES = {"character": ("character",), "scenario": ("scenario",)}


def _ref(ref: object) -> str | None:
    return None if ref is None else str(ref)


def source_key(entity_key: tuple[str, str, str]) -> str:
    """The unpinned ref string of a navigation mention's entity."""
    source, kind, entity = entity_key
    return str(SourceRef(source, kind, entity))


def chat_entry(entry: NavEntry, scope: ScopeEntry | None, record: ChatRecord | None,
               names: Mapping[str, str | None]) -> dict:
    """One chat as a list shows it. ``names`` maps an unpinned source ref string to its display name."""
    launch = None
    bound: list[dict] = []
    if record is not None:
        ref = record.launch.ref
        launch = {"kind": record.launch.kind, "ref": _ref(ref),
                  "name": None if ref is None else names.get(str(ref.unpinned()))}
        seen: set[tuple[str, str]] = set()
        for binding in record.bindings:
            if not binding.active:
                continue
            key = (binding.role, str(binding.ref.unpinned()))
            if key in seen:
                continue
            seen.add(key)
            bound.append({"role": binding.role, "ref": str(binding.ref.unpinned()), "name": names.get(key[1])})
    return {
        "chat": str(entry.ref),
        "label": entry.label,
        "last_activity": entry.last_activity,
        "scope": None if scope is None else {"campaign": scope.ref.campaign_id, "title": scope.title,
                                             "world": scope.world},
        "launch": launch,
        "sources": bound,
        "problem": entry.problem,
    }


def filter_sources(entries: Iterable[NavEntry], names: Mapping[str, str | None]) -> list[dict]:
    """Every character and scenario some chat binds now, for the filter picker, sorted by name then ref."""
    found: dict[tuple[str, str], dict] = {}
    for entry in entries:
        for role, key, _revision, active in entry.mentions:
            if not active or role not in ("character", "scenario"):
                continue
            ref = source_key(key)
            found.setdefault((role, ref), {"role": role, "ref": ref, "name": names.get(ref)})
    return sorted(found.values(), key=lambda item: ((item["name"] or "").casefold(), item["ref"], item["role"]))


def art_view(resolution: Resolution | None, problem: str | None = None) -> dict:
    """One actor's effective pictures: every slot, shown, hidden or unavailable, never substituted."""
    if resolution is None:
        return {"slots": [], "problem": problem}
    slots = []
    for slot in resolution.slots:
        if isinstance(slot, Shown):
            slots.append({"slot": slot.slot, "state": "shown", "role": slot.role, "image": str(slot.image),
                          "media_type": slot.blob.media_type, "size": slot.blob.size, "layer": slot.layer,
                          "crop": None if slot.crop is None else
                          {"x": slot.crop.x, "y": slot.crop.y, "w": slot.crop.w, "h": slot.crop.h},
                          "focus": None if slot.focus is None else {"x": slot.focus.x, "y": slot.focus.y}})
        elif isinstance(slot, Hidden):
            slots.append({"slot": slot.slot, "state": "hidden"})
        elif isinstance(slot, Unavailable):
            slots.append({"slot": slot.slot, "state": "unavailable", "reason": slot.reason, "note": slot.text})
    return {"slots": sorted(slots, key=lambda item: item["slot"]),
            "problems": sorted({problem.code for problem in resolution.problems}), "problem": problem}


def _speaker(actor: InstanceRef | None, names: Mapping[InstanceRef, str], fallback: str) -> str:
    if actor is None:
        return fallback
    return names.get(actor, actor.instance_id)


def heard(take: Take) -> tuple:
    """The lines of ``take`` the table's player heard: every shown line, except on a 041 stage, where a line
    counts only when a player character as of the take (``Take.players``, never control now) is in its
    audience, as 035's ``table_lines(as_heard=True)`` reads it. A staged take with no players gives none."""
    if take.on_stage is None:
        return take.shown()
    table = frozenset(take.players or ())
    return tuple(line for line in take.shown() if table.intersection(line.audience))


def _lines(take: Take) -> list[dict]:
    return [{"line": line.line_id, "speaker": line.speaker, "actor": _ref(line.actor), "text": line.text}
            for line in heard(take)]


def _take(take: Take) -> dict:
    return {"take": take.take, "state": take.state, "origin": take.origin, "lines": _lines(take),
            "outcome": take.outcome, "declined": list(take.declined),
            "calls": [{"actor": call.actor_id, "status": call.status, "reason": call.reason}
                      for call in take.calls if call.status != "accepted"]}


def turn_view(stored: StoredTurn, names: Mapping[InstanceRef, str]) -> dict:
    """One turn as the table's player reads it: the post, then the kept take's shown lines, or, while the
    turn is open, every take still on offer (proposed, running, halted)."""
    record: TurnRecord = stored.record
    post = None
    if record.extra_posts:
        raise SceneRefused("combined_view_unsupported", "combined turn rendering requires the local multi-PC adapter (115)")
    if record.post is not None:
        post = {"speaker": _speaker(record.post.actor, names, "Player"), "actor": str(record.post.actor),
                "kind": record.post.kind, "channel": record.post.channel, "text": record.post.text,
                "private": record.post.private, "withheld": record.post.withheld,
                "to": [names.get(actor, actor.instance_id) for actor in record.post.addressees]}
    kept = record.kept
    offered = [] if not record.open else [_take(take) for take in record.takes
                                          if take.state in ("proposed", "running")]
    return {
        "turn": record.turn_id, "number": record.number, "origin": record.origin, "device": record.device,
        "post": post, "kept": None if kept is None else _take(kept), "offered": offered,
        "open": record.open, "passed": record.passed_by is not None, "paused": record.paused,
        "set_aside": record.set_aside is not None,
    }


def set_aside_view(stored: StoredTurn) -> dict:
    """A turn a fork choice set aside: heard by no one (035), so only its id, number and device are shown."""
    record: TurnRecord = stored.record
    return {"turn": record.turn_id, "number": record.number, "device": record.device}


def fork_view(fork) -> dict | None:  # type: ignore[no-untyped-def]
    if fork is None:
        return None
    return {"number": fork.number, "after": fork.after, "state": fork.state,
            "branches": [{"head": branch.head, "device": branch.device, "turns": list(branch.turns),
                          "set_aside": branch.set_aside} for branch in fork.branches]}


def role_of(controller: str | None) -> str:
    if controller is None:
        return "unseated"
    return "npc" if controller == DIRECTOR else "player"


def report_view(report) -> dict:  # type: ignore[no-untyped-def]
    """028's ``TransferReport`` (or a refusal before any connection, as ``status: refused``)."""
    return {"peer": report.peer, "status": report.status, "reason": report.reason,
            "code": None if report.reason is None else report.reason.split(":", 1)[0], "passes": len(report.passes),
            "conflicts": [{"path": conflict.path, "code": conflict.code} for conflict in report.conflicts],
            "copied": len(report.copied), "deleted": [path for path, _device in report.deleted]}


__all__ = ["FILTER_ROLES", "art_view", "chat_entry", "filter_sources", "fork_view", "report_view", "role_of",
           "set_aside_view", "source_key", "turn_view"]
