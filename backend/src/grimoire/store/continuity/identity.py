"""Absorb identity: which proposed records might already exist (capstone spec §10).

Before absorb treats a plot thread or commitment as NEW, this module asks which
of the extraction's rows would open a record, and which stored same-type
records each of those is plausibly the same business as. It reads; it never
writes, and it decides nothing on its own -- the resolver (and after it, the
reviewer) does that, and `similarity` only chooses what they see.

**Proposed-new is the materializer's answer, not a second opinion.** A row is
proposed-new iff the id `absorb.materializer.assign_ids` gives it names no
stored record of its type -- the same call `materialize` makes, over the same
ledgers, so the two cannot disagree about which slug collisions are honoured,
which `slug-N` a CJK title lands on, or which ids earlier rows in the batch
reserved. A row the assignment drops (blank beat, no usable id or title, a
second move of one record) is ignored. An id-less row whose slug collision the
§10.5 predicate honours -- an open record with the same title -- is the model
naming a record it was shown, and materialize stages it onto that record
whatever a resolver would say; it is a target here, like an explicit id, and is
not asked about (deviation 2).

**Neighbours** come from `similarity.pool`: effective records, closed and
resolved included (a closed thread is shown so the resolver knows it was
settled), keyed by canonical ref, so a merged-away alias source is never a
candidate. At most `similarity.IDENTITY_TOP_K` per row, never padded.

**Embeddings** are an enhancement. When a space is configured, the proposed
texts are always embedded, and the pool's uncached texts that pass the weak
lexical floors against some proposal are warmed (up to the warm limit); every
other pool text is read from the cache only. So a lexically unrelated
paraphrase is found only once its neighbour's vector is already cached -- by an
earlier absorb's warm, or by Slice D's reconcile sweep, which warms the whole
ledger. An embedding failure is a mode, never an exception: the lexical
and structural candidates still stand.
"""

from __future__ import annotations

from typing import Literal

from .. import fieldtext
from ..absorb import materializer as absorb_materializer
from . import canon, effective, involvement, similarity

#: Parsed section -> the record kind its rows open.
SECTIONS: dict[str, similarity.Kind] = {"plot_movements": "thread",
                                        "commitment_movements": "commitment"}

#: The private row key an accepted retarget carries its original row under, for
#: materialize to build the as-new alternative from.
AS_NEW_KEY = "_identity_as_new"

#: What the alias reader can raise on a hand-edited continuity.json whose shape
#: it did not expect; any of them reads as "no aliases", as everywhere else.
_UNREADABLE = (OSError, ValueError, TypeError, KeyError, AttributeError)


def has_proposals(parsed: dict) -> bool:
    """Any row in either section. Cheap: it is what lets an absorb with
    neither section skip the worker thread."""
    return any(parsed.get(section) for section in SECTIONS)


class Examined:
    """One proposed-new row that has at least one plausible neighbour."""

    def __init__(self, section: str, index: int, key: str, kind: similarity.Kind,
                 row: dict, assigned: str,
                 candidates: list[tuple[similarity.Subject, dict]],
                 distinguished_from: list[str]):
        self.section = section
        self.index = index            # position in its section
        self.key = key                # "r1", "r2", ... in examination order
        self.kind = kind
        self.row = row                # the parsed row as given
        self.assigned = assigned      # the id materialize would open it under
        self.title = _text(row.get("title")) or _text(row.get("id"))
        self.candidates = candidates
        self.distinguished_from = distinguished_from
        self.decision = "unchecked"
        self.status = "hint_only"
        self.reason = ""
        self.target: str | None = None   # an accepted canonical id


class Examination:
    """What `examine` found: the examined rows and how it looked for them."""

    def __init__(self, rows: list[Examined], proposed: int,
                 matching: Literal["basic", "semantic"],
                 embedding: Literal["off", "configured", "failure"],
                 embedding_error: str, embedded: int,
                 targets: set[tuple[str, str]], live: dict[str, str]):
        self.rows = rows
        self.proposed = proposed
        self.matching = matching
        self.embedding = embedding
        self.embedding_error = embedding_error
        self.embedded = embedded
        self.targets = targets   # (kind, canonical id) non-proposed rows stage onto
        self.live = live         # the live_canon map examine loaded


def _text(value) -> str:
    return fieldtext.text(value).strip()


def _live(cid: str, ledgers: effective.Ledgers) -> dict[str, str]:
    try:
        return effective.live_canon(cid, ledgers)
    except _UNREADABLE:
        return {}


def _canonical(live: dict[str, str], kind: str, rid: str) -> str:
    """`rid` mapped through the live alias map, as a bare id."""
    ref = live.get(f"{kind}:{rid}")
    return ref.partition(":")[2] if ref else rid


def _bare(kind: str, value) -> str:
    """A row-supplied id, stripped, with a leading ``<kind>:`` removed."""
    return _text(value).removeprefix(f"{kind}:")


def _distinguished(row: dict, kind: str, stored: dict, live: dict[str, str]) -> list[str]:
    """The row's `distinguished_from`, kept only where it names a stored record
    of the row's type, canonicalized and deduped."""
    named = row.get("distinguished_from")
    out: list[str] = []
    for value in named if isinstance(named, list) else ():
        rid = _bare(kind, value)
        if rid and isinstance(stored.get(rid), dict):
            canonical = _canonical(live, kind, rid)
            if canonical not in out:
                out.append(canonical)
    return out


_Pools = dict[similarity.Kind, list[similarity.Subject]]


class _Proposal:
    """A proposed-new row on its way to being examined."""

    __slots__ = ("assigned", "index", "kind", "row", "section", "subject")

    def __init__(self, section: str, index: int, kind: similarity.Kind, row: dict,
                 assigned: str, subject: similarity.Subject):
        self.section = section
        self.index = index
        self.kind = kind
        self.row = row
        self.assigned = assigned
        self.subject = subject


def _subject(kind: similarity.Kind, n: int, row: dict, sid: str,
             actors: set[str]) -> similarity.Subject:
    record = {"title": _text(row.get("title")),
              "beats": [{"text": _text(row.get("beat")), "scene": sid}],
              "kind": _text(row.get("kind")), "due": _text(row.get("due"))}
    # The ref never leaves `examine`: keys are given to examined rows only.
    return similarity.subject(kind, f"proposed:{n}", record, actors=actors, scenes={sid})


def _actors(cid: str, sid: str, facts: dict) -> set[str]:
    cast = facts.get("cast")
    present = {canon.actor_ref(c) for c in (cast if isinstance(cast, list) else ())
               if isinstance(c, str) and c}
    return present | involvement.scene_actors(cid).get(sid, set())


def _classify(cid: str, sid: str, parsed: dict, facts: dict, ledgers: effective.Ledgers,
              live: dict[str, str]) -> tuple[list[_Proposal], set[tuple[str, str]]]:
    """Split the assigned rows into proposals and the targets the rest stage onto."""
    threads = ledgers.threads if ledgers.threads is not None else {}
    assigned = absorb_materializer.assign_ids(threads, ledgers.commitments, parsed, live)
    proposals: list[_Proposal] = []
    targets: set[tuple[str, str]] = set()
    actors: set[str] | None = None
    for section, kind in SECTIONS.items():
        for i, row in enumerate(parsed.get(section) or []):
            slot = assigned.get((section, i))
            if slot is None:
                continue
            if slot.existing:
                targets.add((kind, _canonical(live, kind, slot.id)))
                continue
            if actors is None:
                actors = _actors(cid, sid, facts)
            proposals.append(_Proposal(section, i, kind, row, slot.id,
                                       _subject(kind, len(proposals) + 1, row, sid, actors)))
    return proposals, targets


def _warm(proposals: list[_Proposal], pools: _Pools) -> list[str]:
    """Pool texts lexically near some proposal (`similarity.weak`), best first."""
    best: dict[str, tuple] = {}
    for p in proposals:
        for other in pools[p.kind]:
            signals = similarity.lexical(p.subject, other)
            if not similarity.weak(signals):
                continue
            key = similarity.rank_key(signals, other.ref)
            if other.text not in best or key < best[other.text]:
                best[other.text] = key
    return sorted(best, key=lambda text: best[text])


def _semantic(proposals: list[_Proposal], pools: _Pools,
              embed_deadline: float | None) -> similarity.Semantic:
    space = similarity.available()
    if space is None:
        return similarity.Semantic({}, "off")
    if not proposals:
        return similarity.Semantic({}, "configured")
    cached = [s.text for pool in pools.values() for s in pool]
    return similarity.semantic([p.subject.text for p in proposals], _warm(proposals, pools),
                               deadline=embed_deadline, space=space, cached=cached)


def examine(cid: str, sid: str, parsed: dict, facts: dict, *,
            embed_deadline: float | None) -> Examination:
    """Find the proposed-new rows in `parsed` and their plausible neighbours.

    Runs in a worker thread: it reads the ledgers, the alias map, the scene
    cast and (when configured) the vector cache, and may call the embeddings
    provider until `embed_deadline`. Writes nothing but that cache."""
    ledgers = effective.Ledgers.load(cid)
    live = _live(cid, ledgers)
    proposals, targets = _classify(cid, sid, parsed, facts, ledgers, live)
    kinds = sorted({p.kind for p in proposals})
    pools = {kind: similarity.pool(cid, kind) for kind in kinds}
    sem = _semantic(proposals, pools, embed_deadline)
    vecs = sem.vectors if sem.mode != "off" else None
    stored = {"thread": ledgers.threads or {}, "commitment": ledgers.commitments or {}}
    rows: list[Examined] = []
    for p in proposals:
        candidates = similarity.nearest(p.subject, pools[p.kind], vecs)
        if not candidates:
            continue
        rows.append(Examined(p.section, p.index, f"r{len(rows) + 1}", p.kind, p.row,
                             p.assigned, candidates,
                             _distinguished(p.row, p.kind, stored[p.kind], live)))
    matching: Literal["basic", "semantic"] = (
        "semantic" if similarity.matching() == "semantic" else "basic")
    return Examination(rows, len(proposals), matching, sem.mode, sem.error, sem.embedded,
                       targets, live)
