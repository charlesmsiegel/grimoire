"""continuity.json: the reviewed half of the continuity capstone (spec §5).

Stored at ``<campaign>/continuity.json``::

    {"version": 1,
     "aliases":      {"<source ref>": {"to", "created", "source", "note"}},
     "links":        {"<link id>": {"a", "b", "relation", "created", "scene", "note"}},
     "suppressions": {"<fingerprint>": {"kind", "refs", "decision", "created"}}}

It holds only what a reader decided -- never a candidate ranking, an embedding,
or a copy of a thread's text -- so losing it loses decisions, and nothing here
can be regenerated from the ledgers it talks about.

**Reader and writer disagree on purpose**, the line `events.read` /
`events._mutable` draws. `read` never raises over the file: its callers are
prompt sections, a graph and a panel, and a hand-edited file that no longer
parses must cost them an enhancement, not a turn. It is also tolerant PER
SECTION -- an ``aliases`` that is a list reads as no aliases while the links
beside it still apply -- because the three sections are independent decisions
and one bad edit should not void the other two. The mutators read strictly and
refuse (`ContinuityError`) to publish over a file, or a section, they could not
read: answering a corrupt file with ``{}`` and writing that back would erase
the reader's decisions to record one more.

Records inside a section are returned exactly as stored. A record may be any
JSON value -- the file is hand-editable -- and the readers that interpret one
(`canon`, `effective`) check its shape where they use it.

Unknown top-level keys and a stored ``version`` are written back unchanged, so
a file written by a later version survives a round trip through this one.

Every mutator takes `locks.campaign_lock`: the file is rewritten whole, so two
unserialized read-modify-writes lose one of them.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import atomic, locks
from ..campaigns import paths as campaigns_paths

SECTIONS = ("aliases", "links", "suppressions")
VERSION = 1


class ContinuityError(Exception):
    """A continuity.json (or one section of it) the writer refuses to publish over,
    or a record a restore refuses to put back."""


def _path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "continuity.json"


def _load(cid: str):
    """The parsed file, None when absent; raises the parse failure."""
    p = _path(cid)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _version(raw: dict) -> int:
    v = raw.get("version")
    return v if isinstance(v, int) and not isinstance(v, bool) else VERSION


def read(cid: str) -> dict:
    """The stored decisions, with every malformed part reading as empty."""
    try:
        raw = _load(cid)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        raw = None
    if not isinstance(raw, dict):
        raw = {}
    out = {k: v for k, v in raw.items() if k not in SECTIONS}
    out["version"] = _version(raw)
    for name in SECTIONS:
        section = raw.get(name)
        out[name] = section if isinstance(section, dict) else {}
    return out


def malformed(cid: str) -> list[str]:
    """What the reader had to discard: ``["file"]`` for an unparseable file or a
    non-object top level, else the sections present but not objects."""
    try:
        raw = _load(cid)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return ["file"]
    if raw is None:
        return []
    if not isinstance(raw, dict):
        return ["file"]
    return [name for name in SECTIONS if name in raw and not isinstance(raw[name], dict)]


def _mutable(cid: str, section: str) -> dict:
    """`read` for a writer: the whole stored document, refusing what it cannot read."""
    try:
        raw = _load(cid)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        raise ContinuityError("continuity.json cannot be read") from e
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ContinuityError("continuity.json does not hold a continuity record")
    current = raw.get(section, {})
    if not isinstance(current, dict):
        raise ContinuityError(f"continuity.json's {section} section cannot be read")
    raw[section] = current
    raw["version"] = _version(raw)
    return raw


def _write(cid: str, data: dict) -> None:
    atomic.write_text(_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")


# The two helpers take no lock of their own: each public mutator below takes it,
# so the lock sits on the entry point the domain guard surveys (a helper that
# locked would read to it as an atomic unit and hide the caller's write).
def _put(cid: str, section: str, key: str, record: object) -> None:
    data = _mutable(cid, section)
    data[section][key] = record
    _write(cid, data)


def _drop(cid: str, section: str, key: str):
    data = _mutable(cid, section)
    if key not in data[section]:
        return None
    gone = data[section].pop(key)
    _write(cid, data)
    return gone


def get_alias(cid: str, ref: str):
    return read(cid)["aliases"].get(ref)


def get_link(cid: str, lid: str):
    return read(cid)["links"].get(lid)


def put_alias(cid: str, ref: str, record: dict) -> None:
    with locks.campaign_lock(cid):
        _put(cid, "aliases", ref, record)


def drop_alias(cid: str, ref: str):
    with locks.campaign_lock(cid):
        return _drop(cid, "aliases", ref)


def put_link(cid: str, lid: str, record: dict) -> None:
    with locks.campaign_lock(cid):
        _put(cid, "links", lid, record)


def drop_link(cid: str, lid: str):
    with locks.campaign_lock(cid):
        return _drop(cid, "links", lid)


def put_suppression(cid: str, fp: str, record: dict) -> None:
    with locks.campaign_lock(cid):
        _put(cid, "suppressions", fp, record)


def drop_suppression(cid: str, fp: str):
    with locks.campaign_lock(cid):
        return _drop(cid, "suppressions", fp)


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids in each reviewed link's ``scene``.

    Tolerant the way `commitments.repoint_scenes` is and for its reason: this
    runs from `scene_refs.repoint` AFTER the scene file has been renamed, so
    raising would 500 the rename and leave every store after this one in the
    sweep pointing at an id that no longer exists. A file, section or record it
    cannot read keeps its stale id -- the outcome of never having been
    repointed, and strictly better than aborting the sweep. It writes only when
    something matched, so an absent file stays absent.
    """
    with locks.campaign_lock(cid):
        try:
            data = _mutable(cid, "links")
        except ContinuityError:
            return
        hit = False
        for record in data["links"].values():
            scene = record.get("scene") if isinstance(record, dict) else None
            if isinstance(scene, str) and scene in mapping:
                record["scene"] = mapping[scene]
                hit = True
        if hit:
            _write(cid, data)


# ---------------------------------------------------------------- restores
#
# What `undo` puts back. The journal's compare-and-swap covers only the one
# record being restored, so it cannot see that putting an alias back would close
# a cycle with one made since, or that a link put back duplicates an equivalent
# one made since -- these validate for exactly that, and refuse with
# `ContinuityError`, which `undo.write_value` turns into a 409.
#
# This module may not import `canon` or `effective` (it is the leaf they build
# on), so the relation table and the alias walk are private copies here; a test
# holds the table to `effective.RELATIONS`. Record existence is not visible from
# here, which makes "equivalent" the alias-graph approximation of an equivalent
# effective link -- `effective` checks existence again at read.

_ALIASABLE = ("thread", "commitment")
_RELATIONS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "continues": (frozenset({"thread"}), frozenset({"thread"})),
    "subthread_of": (frozenset({"thread"}), frozenset({"thread"})),
    "pays_off": (frozenset({"thread"}), frozenset({"commitment"})),
    **{rel: (frozenset({"thread", "commitment"}), frozenset({"event"}))
       for rel in ("before", "on", "after", "by")},
    "related_to": (frozenset({"thread", "commitment", "event"}),
                   frozenset({"thread", "commitment", "event"})),
}
_SYMMETRIC = frozenset({"related_to"})


def _prefix(ref) -> str | None:
    if not isinstance(ref, str):
        return None
    prefix, sep, rest = ref.partition(":")
    return prefix if sep and prefix and rest else None


def _target(record) -> str | None:
    to = record.get("to") if isinstance(record, dict) else None
    return to if isinstance(to, str) and to else None


def reaches(aliases: dict, start: str, target: str) -> bool:
    """Does following the stored mappings from `start` arrive at `target`?"""
    seen, cur = set(), start
    while cur not in seen:
        if cur == target:
            return True
        seen.add(cur)
        nxt = _target(aliases.get(cur))
        if nxt is None:
            return False
        cur = nxt
    return False


def _alias_walk(aliases: dict, ref: str) -> str:
    """Where `ref`'s same-type alias chain ends; itself on a cycle."""
    seen, cur = {ref}, ref
    while True:
        nxt = _target(aliases.get(cur))
        if nxt is None or _prefix(nxt) != _prefix(cur):
            return cur
        if nxt in seen:
            return ref
        seen.add(nxt)
        cur = nxt


def _link_key(aliases: dict, record) -> tuple[str, str, str] | None:
    if not isinstance(record, dict):
        return None
    a, b, relation = record.get("a"), record.get("b"), record.get("relation")
    if not (isinstance(a, str) and isinstance(b, str) and isinstance(relation, str)):
        return None
    ca, cb = _alias_walk(aliases, a), _alias_walk(aliases, b)
    if relation in _SYMMETRIC:
        ca, cb = sorted((ca, cb))
    return relation, ca, cb


def restore_alias(cid: str, ref: str, value) -> None:
    """Put one alias back (``None`` removes it), refusing a record that is not a
    same-type thread/commitment alias or that would close a cycle."""
    with locks.campaign_lock(cid):
        data = _mutable(cid, "aliases")
        if value is None:
            if data["aliases"].pop(ref, None) is not None:
                _write(cid, data)
            return
        to = _target(value)
        if to is None:
            raise ContinuityError("an alias needs a target")
        if _prefix(ref) != _prefix(to) or _prefix(ref) not in _ALIASABLE:
            raise ContinuityError("an alias joins two threads or two commitments")
        if ref == to:
            raise ContinuityError("a record cannot be merged into itself")
        if reaches(data["aliases"], to, ref):
            raise ContinuityError(
                "putting this merge back would make a loop with a merge made since")
        data["aliases"][ref] = value
        _write(cid, data)


def restore_link(cid: str, lid: str, value) -> None:
    """Put one link back (``None`` removes it), refusing a record outside the
    relation vocabulary or one equivalent to another link stored since."""
    with locks.campaign_lock(cid):
        data = _mutable(cid, "links")
        if value is None:
            if data["links"].pop(lid, None) is not None:
                _write(cid, data)
            return
        stored = data.get("aliases")
        aliases: dict = stored if isinstance(stored, dict) else {}
        key = _link_key(aliases, value)
        if key is None:
            raise ContinuityError("a link needs two refs and a relation")
        rule = _RELATIONS.get(key[0])
        if rule is None or _prefix(value["a"]) not in rule[0] or _prefix(value["b"]) not in rule[1]:
            raise ContinuityError("that relation cannot join those two records")
        for other, record in data["links"].items():
            if other != lid and _link_key(aliases, record) == key:
                raise ContinuityError(
                    "an equivalent link has been made since; putting this one back "
                    "would duplicate it")
        data["links"][lid] = value
        _write(cid, data)


# -------------------------------------------------------------- snapshots
#
# What `undo` compares and puts back. A record's VALUE cannot be the snapshot:
# a hand-edited record may be JSON null, which reads exactly like "no record",
# so deleting one would look like a no-op to the journal and undoing a
# replacement would remove the key instead of putting the null back. The
# snapshot says whether the key was there at all.


def _snapshot(section: str, cid: str, key: str) -> dict:
    stored = read(cid)[section]
    return {"present": key in stored, "value": stored.get(key)}


def alias_snapshot(cid: str, ref: str) -> dict:
    return _snapshot("aliases", cid, ref)


def link_snapshot(cid: str, lid: str) -> dict:
    return _snapshot("links", cid, lid)


def _restore_snapshot(cid: str, section: str, key: str, snap, validated) -> None:
    if not isinstance(snap, dict):
        raise ContinuityError("this change's record of what was there cannot be read")
    if not snap.get("present"):
        validated(cid, key, None)
    elif isinstance(snap.get("value"), dict):
        validated(cid, key, snap["value"])
    else:
        # A malformed record put back exactly as it was: validating it would
        # refuse to restore what the file held, which is the whole job.
        with locks.campaign_lock(cid):
            _put(cid, section, key, snap.get("value"))


def restore_alias_snapshot(cid: str, ref: str, snap) -> None:
    with locks.campaign_lock(cid):
        _restore_snapshot(cid, "aliases", ref, snap, restore_alias)


def restore_link_snapshot(cid: str, lid: str, snap) -> None:
    with locks.campaign_lock(cid):
        _restore_snapshot(cid, "links", lid, snap, restore_link)
