"""continuity_candidates.json: the derived candidate cache (spec §6).

Stored at ``<campaign>/continuity_candidates.json``::

    {"version": 1, "generated", "generation",
     "basis":   {"embedding_space", "embedding_model", "identity_hashes", "scored",
                 "text_hashes"},
     "records": {"<candidate id>": {"kind", "refs", "fingerprint", "signals",
                                    "proposal", "created"}}}

`scored` (``{ref: generation}``) and `text_hashes` (``{ref: hash of the
identity text with no space salt}``) are additive beyond §6's basis: the first
orders a capped sweep, the second is what "moved since the last sweep" compares,
since every `identity_hashes` entry moves when the embedding space does.

What a reconcile sweep FOUND, never what a reader decided -- those live in
continuity.json (`doc`). So every byte here is rebuildable, and that decides
both halves of the IO the opposite way from `doc`:

- `read` is tolerant all the way down. An unparseable file reads as empty; a
  record of the wrong shape (a pair naming one ref twice among them) is
  skipped while its neighbours are kept; a nested
  field of the wrong type is normalized (``signals`` to ``{}``, a ``signals``
  value that is not standard JSON -- inf, NaN -- dropped, a ``proposal`` that
  does not fit to ``None``) so every consumer downstream can index a record
  without guarding it, and render it as JSON.
- `write` overwrites a malformed file instead of refusing it. Refusing protects
  decisions; there are none here, and the next sweep rebuilds what was lost.

Not journalled: applying a finding writes the durable stores, and those writes
are (spec §6, §12.8).

A leaf, as §7.0 requires: ref prefixes are checked by the private `_split`
rather than `canon.split_ref`, so this module imports nothing above the store's
IO primitives. Every public mutator takes `locks.campaign_lock`: the file is
rewritten whole, so two unserialized read-modify-writes lose one of them.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import atomic, locks
from ..campaigns import paths as campaigns_paths

VERSION = 1
KINDS = ("possible_duplicate", "possible_relation",
         "possible_thread_closure", "possible_commitment_resolution")
PAIR_KINDS = KINDS[:2]
LIFECYCLE_KINDS = KINDS[2:]

# The ref prefixes each kind may name. A pair is matched as an unordered set of
# prefixes: a duplicate is two records of one type, a relation is a thread and
# a commitment, or a commitment and the event it is dated against (a temporal
# pair, §11.3). Records of different types are never duplicates (§3.6).
_PAIR_PREFIXES = {
    "possible_duplicate": ({"thread"}, {"commitment"}),
    "possible_relation": ({"thread", "commitment"}, {"commitment", "event"}),
}
_LIFECYCLE_PREFIX = {
    "possible_thread_closure": "thread",
    "possible_commitment_resolution": "commitment",
}
_PROPOSAL_TEXT = ("decision", "from", "to", "relation", "status", "reason")

# What reading the file can raise: ValueError covers JSONDecodeError and
# UnicodeDecodeError, RecursionError a pathological nesting (as in `doc`).
_UNREADABLE = (ValueError, RecursionError, OSError)


def _path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "continuity_candidates.json"


def empty() -> dict:
    return {"version": VERSION, "generated": "", "generation": "",
            "basis": {"embedding_space": "", "embedding_model": "",
                      "identity_hashes": {}, "scored": {}, "text_hashes": {}},
            "records": {}}


def _load(cid: str):
    """The parsed file, None when absent; raises the parse failure."""
    p = _path(cid)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _split(ref) -> tuple[str, str] | None:
    """``(prefix, id)``, or None for anything that is not a well-formed ref --
    the rule `canon.split_ref` applies, kept here so this module stays a leaf."""
    if not isinstance(ref, str):
        return None
    prefix, sep, rest = ref.partition(":")
    if not sep or not prefix or not rest:
        return None
    return prefix, rest


def _refs_fit(kind: str, refs) -> bool:
    if not isinstance(refs, list):
        return False
    split = [_split(r) for r in refs]
    if any(s is None for s in split):
        return False
    prefixes = [s[0] for s in split if s is not None]
    if kind in _LIFECYCLE_PREFIX:
        return prefixes == [_LIFECYCLE_PREFIX[kind]]
    # A pair is two records: one ref twice is no finding, and kept it would
    # reach an alias plan with no record to merge away.
    if len(prefixes) != 2 or refs[0] == refs[1]:
        return False
    if kind == "possible_duplicate":
        return prefixes[0] == prefixes[1] and {prefixes[0]} in _PAIR_PREFIXES[kind]
    return set(prefixes) in _PAIR_PREFIXES[kind]


def _str_dict(value) -> dict:
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}


def _json_safe(value) -> bool:
    """True when ``value`` renders as standard JSON. `json.loads` reads the
    literal ``1e999`` as inf and accepts NaN / Infinity, and a JSON response
    renders with ``allow_nan=False`` -- one such value would 500 every route
    that carries the record."""
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, RecursionError):
        return False
    return True


def _signals(value) -> dict:
    """The JSON-safe entries of ``signals``; a value that is not is dropped
    alone, so its neighbours still reach the reader."""
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if _json_safe(v)}


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _proposal(value) -> dict | None:
    """A proposal every consumer can index, or None when any part does not fit."""
    if not isinstance(value, dict):
        return None
    out: dict = {}
    for key in _PROPOSAL_TEXT:
        v = value.get(key, "")
        if not isinstance(v, str):
            return None
        out[key] = v
    scenes = value.get("evidence_scenes", [])
    if not isinstance(scenes, list) or not all(isinstance(s, str) for s in scenes):
        return None
    out["evidence_scenes"] = list(scenes)
    return out


def _record(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    kind = value.get("kind")
    if kind not in KINDS or not _refs_fit(kind, value.get("refs")):
        return None
    if not isinstance(value.get("fingerprint"), str):
        return None
    return {
        "kind": kind,
        "refs": list(value["refs"]),
        "fingerprint": value["fingerprint"],
        "signals": _signals(value.get("signals")),
        "proposal": _proposal(value.get("proposal")),
        "created": _text(value.get("created")),
    }


def _basis(value) -> dict:
    raw = value if isinstance(value, dict) else {}
    return {
        "embedding_space": _text(raw.get("embedding_space")),
        "embedding_model": _text(raw.get("embedding_model")),
        "identity_hashes": _str_dict(raw.get("identity_hashes")),
        "scored": _str_dict(raw.get("scored")),
        "text_hashes": _str_dict(raw.get("text_hashes")),
    }


def read(cid: str) -> dict:
    """The cache with `empty`'s keys; whatever does not fit reads as absent."""
    try:
        raw = _load(cid)
    except _UNREADABLE:
        raw = None
    if not isinstance(raw, dict):
        return empty()
    out = empty()
    out["generated"] = _text(raw.get("generated"))
    out["generation"] = _text(raw.get("generation"))
    out["basis"] = _basis(raw.get("basis"))
    stored = raw.get("records")
    if isinstance(stored, dict):
        for key, value in stored.items():
            record = _record(value)
            if isinstance(key, str) and record is not None:
                out["records"][key] = record
    return out


def malformed(cid: str) -> bool:
    """True when the file exists and is unparseable or not an object."""
    try:
        raw = _load(cid)
    except _UNREADABLE:
        return True
    return raw is not None and not isinstance(raw, dict)


def records(cid: str) -> list[dict]:
    """Every readable finding as ``{"id", **record}``, sorted by id (spec §7.0)."""
    stored = read(cid)["records"]
    return [{"id": key, **stored[key]} for key in sorted(stored)]


# Takes no lock of its own: each public mutator below takes it, so the lock sits
# on the entry point the domain guard surveys (a helper that locked would read to
# it as an atomic unit and hide the caller's write).
def _write(cid: str, data: dict) -> None:
    atomic.write_text(_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")


def write(cid: str, data: dict) -> None:
    """Replace the cache whole -- over a malformed file too, since it is rebuildable."""
    with locks.campaign_lock(cid):
        _write(cid, data)


def drop(cid: str, candidate_id: str) -> dict | None:
    """Remove one finding and return it; no write when it is absent or the file
    cannot be read (a malformed cache is the next sweep's to rebuild)."""
    with locks.campaign_lock(cid):
        if malformed(cid):
            return None
        data = read(cid)
        gone = data["records"].pop(candidate_id, None)
        if gone is None:
            return None
        _write(cid, data)
        return gone
