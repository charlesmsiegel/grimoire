"""Ref grammar, alias-graph resolution, and the ids continuity stores (spec §4, §5).

A ref is ``<prefix>:<id>``, split on the FIRST colon: ids may themselves hold
colons (a birthday occurrence, ``birthday:characters:mara:740000``) and slashes
(a plot id the model wrote). Actor tokens elsewhere in the store use the
``characters/<id>`` form; `actor_ref` is the one place that converts.

`resolve` answers what the stored alias mappings SAY. It does not ask whether
the records exist, because some of its callers -- cycle checks, strict mutator
validation -- are questions about the graph itself. Every question about the
campaign's current state goes through `effective.live_canon` instead, which
follows a hop only to a record that is really there; using this for that would
let a dangling alias redirect a write to a record that does not exist.

The three ids are the contract later slices store and compare, so their inputs
are fixed here and nowhere else:

- ``link_id`` is derived ONCE, from the endpoints as given at creation, and is
  never recomputed -- an id derived from canonicalized endpoints would change
  whenever an alias was made or removed.
- ``candidate_id`` names a finding by its kind and refs, so a refresh that finds
  the same thing again keeps its address.
- the fingerprints say what a finding was ABOUT. A pair's excludes beat text, so
  a "these are distinct" dismissal is not undone by the next scene's beat; a
  lifecycle finding's takes the beat count and the latest beat's hash, which
  every advance moves, and never ``last_scene``, which a scene rename moves
  without changing what the record means.
"""

from __future__ import annotations

import hashlib
import json

from . import doc

KIND_OF_PREFIX: dict[str, str] = {
    "scene": "scene",
    "characters": "character",
    "pcs": "pc",
    "locations": "location",
    "groups": "group",
    "thread": "thread",
    "commitment": "commitment",
    "event": "event",
    "fact": "fact",
    "idea": "idea",
    "holiday": "holiday",
    "birthday": "birthday",
}
PREFIX_OF_KIND: dict[str, str] = {kind: prefix for prefix, kind in KIND_OF_PREFIX.items()}


def split_ref(ref) -> tuple[str, str]:
    """``(prefix, id)``; ValueError for anything that is not a well-formed ref."""
    if not isinstance(ref, str):
        raise ValueError(f"not a ref: {ref!r}")
    prefix, sep, rest = ref.partition(":")
    if not sep or not prefix or not rest:
        raise ValueError(f"not a ref: {ref!r}")
    return prefix, rest


def ref_kind(ref) -> str:
    prefix, _ = split_ref(ref)
    if prefix not in KIND_OF_PREFIX:
        raise ValueError(f"unknown ref prefix: {prefix!r}")
    return KIND_OF_PREFIX[prefix]


def actor_ref(token: str) -> str:
    """``characters/mara`` -> ``characters:mara``; a colon form is returned as is."""
    if ":" in token:
        return token
    kind, sep, rest = token.partition("/")
    return f"{kind}:{rest}" if sep else token


def resolve(aliases: dict, ref: str, *, strict: bool = False) -> str:
    """Follow the stored ``to`` mappings from `ref` as far as they go.

    The walk stops at a ref with no record, a record that is not an object, or a
    ``to`` that is not a non-empty string -- hand-edited files exist, and a
    shape this cannot read ends the chain rather than raising out of every
    continuity read. A cycle (which writers refuse, but a hand edit can make)
    resolves the input to itself, or raises in `strict` form.
    """
    seen = {ref}
    cur = ref
    while True:
        record = aliases.get(cur) if isinstance(aliases, dict) else None
        to = record.get("to") if isinstance(record, dict) else None
        if not isinstance(to, str) or not to:
            return cur
        if to in seen:
            if strict:
                raise doc.ContinuityError(f"alias cycle through {ref!r}")
            return ref
        seen.add(to)
        cur = to


def _aliases(cid: str, strict: bool) -> dict:
    if strict:
        bad = set(doc.malformed(cid)) & {"file", "aliases"}
        if bad:
            raise doc.ContinuityError("continuity.json's aliases cannot be read")
        return doc.read(cid)["aliases"]
    try:
        return doc.read(cid)["aliases"]
    except Exception:  # noqa: BLE001 -- a lenient read degrades to "no aliases"
        return {}


def canonical_ref(cid: str, ref: str, *, strict: bool = False) -> str:
    return resolve(_aliases(cid, strict), ref, strict=strict)


def canonical_refs(cid: str, refs, *, strict: bool = False) -> dict[str, str]:
    aliases = _aliases(cid, strict)
    return {ref: resolve(aliases, ref, strict=strict) for ref in refs}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def link_id(relation: str, a: str, b: str) -> str:
    if relation == "related_to":
        a, b = sorted((a, b))
    return "l" + _digest(f"{relation}\0{a}\0{b}")[:20]


def candidate_id(kind: str, refs) -> str:
    return f"{kind}-" + _digest("\0".join(sorted(refs)))[:16]


def _fingerprint(payload: dict) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "fp1_" + _digest(text)


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def pair_fingerprint(kind: str, sides: list[dict]) -> str:
    """What a pair finding is about: each side's identity, never its beats."""
    rows = sorted(({key: _text(side.get(key)) for key in ("ref", "title", "status", "kind", "due")}
                   for side in sides), key=lambda row: row["ref"])
    return _fingerprint({"kind": kind, "refs": [row["ref"] for row in rows], "sides": rows})


def lifecycle_fingerprint(kind: str, ref: str, status: str, beat_count: int,
                          latest_beat: str, due: str, temporal_link_ids) -> str:
    """What a closure/resolution finding is about: the record as it stands now."""
    return _fingerprint({
        "kind": kind, "ref": ref, "status": status, "beat_count": beat_count,
        "latest_beat": _digest(latest_beat), "due": due,
        "temporal_links": sorted(temporal_link_ids),
    })
