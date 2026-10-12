"""Read-only group membership helpers (spec 07).

The pure leaf is also used by overlay, which owns copy-on-write and must not
import this higher-level reader. Inverse/projection readers belong here;
they derive facts from group frontmatter rather than author a second roster.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from pydantic import BaseModel

from . import (
    entities,
    entity_schema,
    fieldtext,
    group_inventory,
    group_refs,
    overlay,
    statcache,
)

#: The kinds a group's `leader` and `members` may name; a test holds it equal to
#: `entity_schema.FIELDS["groups"]`'s members `kinds` (spec 07 §4.3).
ACTOR_KINDS = group_refs.ACTOR_KINDS
parse_members = group_refs.parse_members
parse_leader = group_refs.parse_leader
edited_line = group_refs.edited_line


class Roster(BaseModel):
    ref: str
    name: str
    secrecy: str
    group_type: str
    leader: str | None
    members: tuple[str, ...]
    headquarters: str | None


class Affiliation(BaseModel):
    group: str
    roles: tuple[str, ...]


class Index(BaseModel):
    groups: dict[str, Roster]
    by_actor: dict[str, tuple[Affiliation, ...]]
    digest: str


class GroupRecord(BaseModel):
    id: str
    meta: dict[str, str]
    body: str


class GroupSnapshot(BaseModel):
    """Group entries and inverse from one source version, for activation."""
    index: Index
    records: tuple[GroupRecord, ...]


class SceneGroup(BaseModel):
    group: str
    name: str
    secrecy: str
    present: tuple[str, ...]
    leader_present: bool


SCENE_GROUP_LIMIT = 8


def _headquarters(row: Mapping[str, object]) -> str | None:
    for ref in entity_schema.parse_refs(row.get("headquarters")):
        kind, _, eid = ref.partition(":")
        if kind == "locations" and entity_schema.referenceable(eid):
            return ref
    return None


def index_from_rows(rows: Iterable[Mapping[str, object]]) -> Index:
    """Invert authoritative group summaries, without actor existence or I/O.

    Leadership and explicit membership are independent facts. The digest
    describes every projected roster field in a stable order; prose, triggers
    and state do not become membership authority merely by sharing a file.
    """
    groups: dict[str, Roster] = {}
    for row in rows:
        eid = row.get("id")
        if not isinstance(eid, str) or not eid:
            continue
        ref = f"groups:{eid}"
        groups[ref] = Roster(
            ref=ref, name=fieldtext.text(row.get("name"), eid) or eid,
            secrecy=entities.normalize_secrecy(fieldtext.text(row.get("secrecy"))),
            group_type=fieldtext.text(row.get("group_type")),
            leader=parse_leader(row), members=parse_members(row),
            headquarters=_headquarters(row))
    groups = dict(sorted(groups.items()))
    by_actor: dict[str, list[Affiliation]] = {}
    for roster in groups.values():
        roles: dict[str, list[str]] = {}
        if roster.leader is not None:
            roles[roster.leader] = ["leader"]
        for actor in roster.members:
            roles.setdefault(actor, []).append("member")
        for actor, actor_roles in roles.items():
            by_actor.setdefault(actor, []).append(Affiliation(group=roster.ref, roles=tuple(actor_roles)))
    canonical = [(r.ref, r.name, r.secrecy, r.group_type, r.leader, r.members, r.headquarters)
                 for r in groups.values()]
    digest = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return Index(groups=groups, by_actor={actor: tuple(entries) for actor, entries in sorted(by_actor.items())},
                 digest=digest)


def groups_for(index: Index, actor: str) -> tuple[Affiliation, ...]:
    """Current affiliations, with leadership and membership roles distinct."""
    return index.by_actor.get(actor, ())


def members_of(index: Index, group: str) -> tuple[str, ...]:
    """The explicit `members` only: a leader nobody listed is not in it (§4.4).

    So a group audience read through this silently leaves out that leader.
    Epistemic retrieval (11) expands a `groups:<id>` audience from
    `campaign_index` and then this or `affiliated` -- 11 decides which and says
    so -- never by `parse_members` off `overlay.read_entity`, which skips the
    detached rule (§7.6) and is not a supported path (§12.4).
    """
    roster = index.groups.get(group)
    return () if roster is None else roster.members


def affiliated(index: Index, group: str) -> tuple[str, ...]:
    """Leader first, then explicit members in their stored order, once each.

    This is the recommended expansion of a group: retrieval's (09) `group`
    relation -- scenes where two or more of a seeded group stood -- reads
    this, not `members_of`, so a leader nobody listed still counts (§12.3).
    Epistemic retrieval (11) reaches it through `campaign_index`, never
    through `parse_members` on an entity read (§12.4). It is current state: a
    past scene must not be described with today's affiliation.
    """
    roster = index.groups.get(group)
    if roster is None:
        return ()
    return tuple(dict.fromkeys((roster.leader, *roster.members))) if roster.leader else roster.members


def co_affiliates(index: Index, actor: str) -> dict[str, tuple[str, ...]]:
    """Actors sharing a current affiliation with `actor`, each with the groups.

    Members and leaders are unioned through `affiliated`, and `actor` itself is
    never listed. These are recall signals, not knowledge evidence (§12.3):
    co-membership says two people are connected, never that either knows what
    the other saw, and it never yields `ACTOR_KNOWN` on its own (§12.4).
    """
    others: dict[str, set[str]] = {}
    for affiliation in groups_for(index, actor):
        for other in affiliated(index, affiliation.group):
            if other != actor:
                others.setdefault(other, set()).add(affiliation.group)
    return {other: tuple(sorted(groups)) for other, groups in sorted(others.items())}


def prompt_visible(roster: Roster, *, secret_framed: bool = False) -> bool:
    """Model text excludes GM-only and requires explicit framing for secret."""
    if roster.secrecy == entities.GM_ONLY:
        return False
    return roster.secrecy != entities.SECRET or secret_framed


def scene_groups(index: Index, cast: Iterable[str], *,
                 visible: Callable[[Roster], bool] | None = None,
                 limit: int | None = None) -> tuple[SceneGroup, ...]:
    """Current scene affiliations, bounded by inverse candidates (spec07 §12).

    Metadata defaults are uncapped and include marked secret/GM-only groups.
    Any model text or embedding consumer must explicitly pass prompt_visible;
    secret framing is an explicit narrator choice, never actor knowledge.
    An unlisted leader affiliates independently. Current co-presence establishes
    neither history nor knowledge; audience expansion belongs to its consumer.

    The rules its consumers inherit (§12.3, §12.4): 09's `group` relation reads
    `affiliated`, not `members_of`; 11 expands an audience through
    `campaign_index` plus `affiliated` or `members_of`, never `parse_members`
    off `overlay.read_entity`; membership is now, not then; and the gap that
    secret lore owned only by a group is known by no actor (`actor.knows`
    treats a group owner as an object owner) is 11's to close, not this
    module's -- closing it changes NPC prompts.
    """
    if limit is not None and limit <= 0:
        return ()
    actors = {ref for token in cast
              if (ref := group_refs.actor_ref(token.replace("/", ":", 1))) is not None}
    candidates: dict[str, set[str]] = {}
    for actor in actors:
        for affiliation in groups_for(index, actor):
            candidates.setdefault(affiliation.group, set()).add(actor)
    rows = []
    for group, present_actors in candidates.items():
        roster = index.groups.get(group)
        if roster is None or (visible is not None and not visible(roster)):
            continue
        # The inverse already proves these affiliations. Scanning a candidate
        # roster here would charge a tiny cast for every off-scene member.
        present = tuple(sorted(present_actors))
        if present:
            rows.append(SceneGroup(group=group, name=roster.name, secrecy=roster.secrecy,
                                   present=present, leader_present=roster.leader in actors))
    rows.sort(key=lambda row: (-len(row.present), row.name, row.group))
    return tuple(rows if limit is None else rows[:limit])


class _MemoValue(BaseModel):
    index: Index
    records: tuple[GroupRecord, ...]
    roots: tuple[Path, ...]
    ledger_digest: str
    groups_digest: str
    inherited_refs: tuple[str, ...]
    detachment_signature: tuple[str, ...]


# One scope entry holds the inverse and its proofs. A library sweep must not
# evict other store derivations from statcache's shared pool (spec 07).
_memo_pool: dict[tuple, tuple[tuple[statcache.Stamp, ...], _MemoValue]] = {}


def _native_signature(capture: group_inventory.GroupCapture, refs: tuple[str, ...]) -> tuple[str, ...]:
    if len(capture.roots) == 1:
        return ()
    return tuple(sorted(overlay.actor_detachments(capture.roots[0], capture.roots[1], refs, set(capture.detached))))


def _matches(value: _MemoValue, capture: group_inventory.GroupCapture) -> bool:
    return (tuple(map(os.fspath, value.roots)) == tuple(map(os.fspath, capture.roots)) and value.ledger_digest == capture.ledger_digest
        and value.groups_digest == capture.groups_digest
        and value.detachment_signature == _native_signature(capture, value.inherited_refs))


def _derive(capture: group_inventory.GroupCapture) -> _MemoValue:
    refs: tuple[str, ...]
    signature: tuple[str, ...]
    if len(capture.roots) == 1:
        rows, refs, signature = group_inventory.rows(capture.sources, include_body=True), (), ()
    else:
        rows, refs, signature = overlay.group_rows(capture, include_body=True)
    records = tuple(GroupRecord(id=row["id"], body=row["body"],
        meta={key: value for key, value in row.items() if key not in ("id", "layer", "body")}) for row in rows)
    return _MemoValue(index=index_from_rows(rows), records=records, roots=capture.roots,
        ledger_digest=capture.ledger_digest, groups_digest=capture.groups_digest,
        inherited_refs=refs, detachment_signature=signature)


def _read_value(capture_inputs: Callable[[], group_inventory.GroupCapture]) -> _MemoValue:
    # Availability and exact bytes are checked even on warm, stat-preserving
    # hits. The memo saves parsing/inversion, not the proof of current content.
    preflight = capture_inputs()
    scope = "world" if len(preflight.roots) == 1 else "campaign"
    key = ("membership", scope, *(os.fspath(root) for root in preflight.roots))
    entry = _memo_pool.get(key)
    if preflight.stamps is None or (entry is not None and not _matches(entry[1], preflight)):
        _memo_pool.pop(key, None)
    computed: _MemoValue | None = None

    def compute() -> tuple[_MemoValue, tuple[statcache.Stamp, ...] | None]:
        nonlocal computed
        capture = capture_inputs()
        computed = _derive(capture)
        stamps = capture.stamps if _matches(computed, preflight) else None
        return computed, stamps

    value = statcache.memo_stamped(key, compute, pool=_memo_pool, max_entries=64)
    # Another reader can publish between our inspection and memo_stamped's
    # own lookup. Check the ACTUAL returned wrapper, not just the prior entry.
    # Our own mismatching computation is already the one fresh uncached answer;
    # only a different reader's contrary wrapper needs a fresh fallback.
    if not _matches(value, preflight):
        _memo_pool.pop(key, None)
        if value is not computed:
            value = _derive(capture_inputs())
    return value


def _read_index(capture_inputs: Callable[[], group_inventory.GroupCapture]) -> Index:
    return copy.deepcopy(_read_value(capture_inputs).index)


def campaign_snapshot(cid: str, *, capture: group_inventory.GroupCapture | None = None) -> GroupSnapshot:
    # Visibility, controls, prose and effective actors share the memo's original
    # bytes/proofs. A later entity read cannot make old GM-only refs public.
    value = _read_value(lambda: capture if capture is not None else overlay.group_capture(cid))
    return copy.deepcopy(GroupSnapshot(index=value.index, records=value.records))


def world_index(wroot: Path, *, store_root: Path | None = None) -> Index:
    return _read_index(lambda: entities.group_capture(wroot, store_root=store_root))


def campaign_index(cid: str) -> Index:
    """The campaign's effective inverse, with §7.6's detached rule applied.

    This is the supported door for a consumer outside this module (09, 11):
    reading `parse_members` off `overlay.read_entity` would skip the detached
    rule (§12.4).
    """
    return _read_index(lambda: overlay.group_capture(cid))
