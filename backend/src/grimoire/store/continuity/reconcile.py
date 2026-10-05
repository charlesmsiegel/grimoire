"""The reconciliation sweep (capstone spec §11): what the whole ledger may hold
twice, and what may be finished, found after End Scene or on a refresh.

**Scores rank; they never write (§3.2).** Discovery answers "which records are
worth asking about" and nothing more. A pair clearing every floor merges
nothing, a thread past its staleness threshold is not closed, and a deadline
behind the campaign's present resolves nothing: each becomes a *finding* in
the candidate cache, and only a reader's apply writes the ledgers. `discover`
itself writes nothing to the campaign -- the persists write the cache, under
the lock; the only thing a sweep warms is the global vector cache.

**What discovery finds** (Decisions 10 and 11):

- *Pairs.* Every ref to score is paired once against every other thread and
  commitment (closed and resolved included, §11.2), scored by Slice C's
  `similarity` rules so a sweep and absorb's identity check agree about what
  "close" means. A plausible pair is kept when it is in the top
  `RECONCILE_TOP_K` of either endpoint, per type, counting only an endpoint
  whose every pair this sweep scored -- so an incremental sweep keeps what a
  full one would for the changed ref, not every plausible pair it has, and
  defers the other direction to the next full sweep. Same-type pairs are
  ``possible_duplicate``; a thread and a commitment are ``possible_relation``,
  never a duplicate (§3.6).
- *Incremental vs full.* A full sweep scores every ref; an incremental one only
  the refs whose identity hash moved since the cache's basis (or that have no
  entry). The hash is salted with the embedding space, so a space change makes
  every ref changed until it is rescored under the new one. Scoring order is
  missing, then changed, then least recently scored, then ref: a sweep cut off
  at `RECONCILE_MAX_PAIRS` leaves the refs it did not finish for the next one,
  and successive sweeps rotate through the whole ledger rather than rescoring
  the same alphabetical prefix. A pool that could not be read leaves every ref
  unrescored, since none was paired against it.
- *Touched.* An incremental sweep re-checks the refs a scene names plus those
  whose identity text moved since the basis -- compared without the space
  salt (`basis.text_hashes`), so a space change moves nothing.
- *Lifecycle.* A stale thread is nominated for closure, an overdue commitment
  (a passed due, or a reached before/by/on event) for resolution. Both are
  persisted deterministically, with or without a model.

**Embeddings** (§9.4, Decision 12) are an enhancement with a bounded cost. One
`vectors.load` over every scored text is the sweep's only read of the vector
cache; it finds the uncached texts. An incremental sweep requires the changed
refs' uncached texts, and the rest of `RECONCILE_WARM_LIMIT` goes to a window
of the other uncached texts that `embed_space.warm_window` rotates on the
campaign id plus the run's stamp -- the stamp is what makes the rotation real,
since a seed of the campaign alone hands two runs the same offset and a text
stuck there would be retried forever. `similarity.semantic` embeds in
`embeddings.BATCH` chunks under one `embeddings.TIMEOUT` deadline, saving each
chunk as it lands and forgetting off-width vectors. A provider failure is mode
``failure`` and one counts-only error row; the lexical candidates stand. While
a space is configured, a ref left without a vector (a failure, or outside this
run's window) is scored lexically but not reported rescored: its basis entry
stays, so its prior pairs are kept and it is scored semantically once its
vector exists.

**Model-only nominations** (Decision 9). Two kinds of nomination carry no
deterministic signal worth showing on its own: a *temporal pair* (a commitment
whose due the calendar cannot read, beside a near event) and a *touched*
lifecycle re-check (a record the scene just moved, which cannot be stale).
Persisted without a proposal, either would flood the review after every
scene, and entirely so with no connection. So they live in `Sweep.model_only`,
go to the model, and land only in the second persist carrying its proposal;
one the model declines is cached as ``settled`` and shown to nobody. They are
filtered by verdict here, against the same `pending.Current` discovery read, so
a pair the reader dismissed or linked, or a re-check under a Keep open that
still holds, is never re-asked.

**Soft all the way down.** Pools, aging, the clock and pressure can each run
user calendar-plugin code or read a garbled ledger, so each is read through
`_soft`; a source that fell back, or whose ledger or chronicle could not be
read, is reported unchecked in `Sweep.lifecycle_checked`, so a persist never
retracts a finding on the word of a source it could not read. A malformed continuity.json reads as no
decisions at all, so while it lasts discovery finds nothing (Decision 2).

The constants below are candidate-generation parameters, not truth thresholds.
Each is justified structurally, and every one is to be tuned against real
prompts later.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterable
from typing import Any, Literal, TypeVar

from ... import embeddings
from .. import aging, calendars, chronicle, clock, embed_space, errors, fieldtext, paths, vectors
from . import candidates, canon, effective, pending, pressure, similarity

#: Uncached texts one sweep may embed: four `embeddings.BATCH` round trips under
#: one shared `embeddings.TIMEOUT`. Nobody waits on a sweep, but a live one
#: refuses a storage move, so it is bounded like a reader-facing window and the
#: rest rotate in over later runs. To be tuned against real prompts later.
RECONCILE_WARM_LIMIT = embeddings.BATCH * 4

#: Pairs one sweep scores. A pair is one token/trigram Jaccard plus, with
#: vectors, one pure-Python dot product (no numpy on Android); at embedding
#: widths in the low thousands this is a few seconds of one worker thread, which
#: keeps the storage-move refusal window short, and it covers every pair of
#: roughly two hundred same-type records in one sweep. To be tuned against real
#: prompts later.
RECONCILE_MAX_PAIRS = 20_000

#: Neighbours kept per record and type: the bound identity sets (§9.3), so a
#: record yields at most a handful of findings. To be tuned against real
#: prompts later.
RECONCILE_TOP_K = similarity.IDENTITY_TOP_K

#: Findings one adjudication call carries. Each renders two short records, a
#: few beats and a signal line, so this keeps the one prompt in the few-thousand
#: token range absorb's runs in. To be tuned against real prompts later.
RECONCILE_MAX_CANDIDATES = 24

#: Beats sent per record: the identity text's latest plus two earlier ones
#: (§9.1), and the same beats the review detail shows. To be tuned against real
#: prompts later.
RECONCILE_BEATS = pending.ROW_BEATS

#: Recent chronicle one-lines sent beside the beat scenes, enough to place the
#: candidates in the story's present. To be tuned against real prompts later.
RECONCILE_RECENT_SCENES = 6

#: Events one undatable commitment is paired with, nearest first: a temporal
#: pair is a guess about a date, and more than a few guesses per commitment is
#: noise. To be tuned against real prompts later.
RECONCILE_TEMPORAL_EVENTS = 3

#: Actor names sent per record, so a long thread with a large cast cannot
#: dominate the one prompt. To be tuned against real prompts later.
RECONCILE_ACTORS = 4

#: A proposal's reason, clipped -- the bound identity's resolver uses
#: (`identity.REASON_CHARS`), one short sentence.
RECONCILE_REASON_CHARS = 280

SWEEPS = ("full", "incremental")

_OVERDUE_SOURCES = frozenset({"deadline", "linked_deadline", "event"})
_DEADLINE_SOURCES = frozenset({"deadline", "linked_deadline"})
_LIFECYCLE_OF = {"thread": "possible_thread_closure",
                 "commitment": "possible_commitment_resolution"}
_NO_DEADLINE = {"state": "ok", "in_days": None, "friendly": ""}
#: The error row's module and task. A counts-only row: no title, beat or text.
_TASK = "continuity-reconcile"
_SEMANTIC_UNAVAILABLE = "semantic matching unavailable — basic matching used"
# The ledgers (`pending.Current.unreadable` prefixes) each source reads. A
# source whose ledger could not be read is never reported checked: its
# nominations are only what it could see, so they cannot retract anything.
_POOL_LEDGERS = frozenset({"thread", "commitment"})
_STALE_LEDGERS = frozenset({"thread"})
_DEADLINE_LEDGERS = frozenset({"commitment", "event"})

T = TypeVar("T")


def _soft(fn: Callable[..., T], fallback: T, *args: Any) -> T:
    """`fn(*args)`, or `fallback` when it raises anything at all."""
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 -- a source may run user calendar plugin code or read a garbled ledger; one source failing must cost only what it feeds, never the sweep
        return fallback


def generation(run_id: str) -> str:
    """A run's generation stamp: wall-clock nanoseconds, zero-padded so it sorts
    as a string, and the run id to break a tie (Decision 4)."""
    return f"{time.time_ns():020d}-{run_id}"


class Sweep:
    """What one discovery pass found, and what the persists need to know about
    how it found it."""

    def __init__(self, stamp: str, full: bool):
        self.stamp = stamp
        self.full = full
        #: id -> cache record (``proposal`` None): persisted deterministically.
        self.discovered: dict[str, dict] = {}
        #: id -> cache record: temporal pairs and touched re-checks (Decision 9).
        self.model_only: dict[str, dict] = {}
        #: Refs whose every pair was scored this sweep.
        self.rescored: frozenset[str] = frozenset()
        #: Ref -> current identity hash, for every ref in the pools.
        self.hashes: dict[str, str] = {}
        #: Ref -> hash of the identity text alone (no space salt), for every ref
        #: in the pools: what "moved since the last sweep" compares.
        self.text_hashes: dict[str, str] = {}
        #: Which lifecycle sources were read without falling back.
        self.lifecycle_checked = {"stale": False, "overdue": False, "temporal": False}
        #: Every temporal pair nominated, before the verdict filter.
        self.temporal_ids: frozenset[str] = frozenset()
        self.continuity: Literal["ok", "malformed"] = "ok"
        self.space = ""
        self.model = ""
        self.embedding: Literal["off", "configured", "failure"] = "off"
        self.embedding_error = ""
        self.pairs_scored = 0
        self.pairs_capped = False
        self.matching = "basic"


def _identity_hash(space: str, text: str) -> str:
    """Salted with the space, so a space change makes every ref changed;
    `surrogatepass`, so a lone surrogate a model wrote cannot raise."""
    return hashlib.sha256((space + "\0" + text).encode("utf-8", "surrogatepass")).hexdigest()


def _text_hash(text: str) -> str:
    """The identity text's hash with no space in it, so a space change moves
    no record (Decision 11's touched rule)."""
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _record(kind: str, refs: list[str], fingerprint: str, signals: dict) -> dict:
    return {"kind": kind, "refs": refs, "fingerprint": fingerprint, "signals": signals,
            "proposal": None, "created": paths.now_iso()}


def _nominate(out: dict[str, dict], current: pending.Current, kind: str,
              refs: list[str], signals: dict) -> None:
    """Add a finding to `out` under its candidate id, unless its fingerprint
    cannot be computed (a missing side, an unencodable title)."""
    fp = pending.fingerprint(current, kind, refs)
    if fp is not None:
        out[canon.candidate_id(kind, refs)] = _record(kind, refs, fp, signals)


# -------------------------------------------------------------------- pairs


def _order(subjects: list[similarity.Subject], hashes: dict[str, str], basis: dict,
           full: bool) -> list[str]:
    """The refs to score: every ref (full) or the changed ones (incremental),
    missing first, then changed, then least recently scored, then by ref."""
    stored, scored = basis["identity_hashes"], basis["scored"]

    def key(ref: str) -> tuple:
        rank = 0 if ref not in stored else 1 if stored[ref] != hashes[ref] else 2
        return (rank, scored.get(ref, ""), ref)

    refs = [s.ref for s in subjects]
    if not full:
        refs = [r for r in refs if stored.get(r) != hashes[r]]
    return sorted(refs, key=key)


def _signals(a: similarity.Subject, b: similarity.Subject,
             vecs: dict[str, list[float]]) -> dict | None:
    """The pair's similarity signals with the clause that admitted it, or None."""
    signals = similarity.lexical(a, b)
    if vecs:
        signals = similarity.with_cosine(signals, a, b, vecs)
    via = similarity.admitted_by(signals)
    return None if via is None else {**signals, "via": via}


def _score_pairs(to_score: list[str], subjects: list[similarity.Subject],
                 vecs: dict[str, list[float]],
                 budget: int) -> tuple[dict, frozenset, int, bool]:
    """Score each ref in `to_score` once against every other subject, each
    unordered pair once, stopping at `budget` pairs. Returns the plausible
    pairs (``(scored ref, other ref) -> signals``), the refs whose every pair
    was scored, the pair count and whether the budget cut the sweep short."""
    by_ref = {s.ref: s for s in subjects}
    done: set[frozenset[str]] = set()
    kept: dict[tuple[str, str], dict] = {}
    finished: set[str] = set()
    count = 0
    for ref in to_score:
        for other in subjects:
            pair = frozenset((ref, other.ref))
            if other.ref == ref or pair in done:
                continue
            if count >= budget:
                return kept, frozenset(finished), count, True
            done.add(pair)
            count += 1
            signals = _signals(by_ref[ref], other, vecs)
            if signals is not None:
                kept[(ref, other.ref)] = signals
        finished.add(ref)
    return kept, frozenset(finished), count, False


def _top_k(scored: dict[tuple[str, str], dict], by_ref: dict[str, similarity.Subject],
           finished: frozenset[str]) -> set[tuple[str, str]]:
    """The pairs in the top `RECONCILE_TOP_K` of either endpoint, per type --
    ranked only from an endpoint in `finished`. An unfinished endpoint's
    ranking holds only the pairs this sweep happened to score (on an
    incremental sweep, its one pair with the changed ref), so its "top k"
    would keep every plausible pair the changed ref has, which the next full
    sweep would retract again."""
    ranked: dict[tuple[str, str], list[tuple[tuple, tuple[str, str]]]] = {}
    for (a, b), signals in scored.items():
        for mine, other in ((a, b), (b, a)):
            if mine not in finished:
                continue
            ranked.setdefault((mine, by_ref[other].kind), []).append(
                (similarity.rank_key(signals, other), (a, b)))
    keep: set[tuple[str, str]] = set()
    for entries in ranked.values():
        entries.sort()
        keep.update(pair for _, pair in entries[:RECONCILE_TOP_K])
    return keep


def _pair_signals(signals: dict) -> dict:
    """The cache's signal shape (§6)."""
    return {"title_exact": signals["title_equal"], "slug_equal": signals["slug_equal"],
            "lexical": max(signals["tokens"], signals["chars"]),
            "cosine": signals["cosine"], "shared_actors": signals["actors"],
            "shared_scenes": signals["scenes"], "shared_anchors": signals["anchors"],
            "via": signals["via"]}


def _rescored(sweep: Sweep, finished: frozenset[str], by_ref: dict[str, similarity.Subject],
              vecs: dict[str, list[float]], pools_whole: bool) -> frozenset[str]:
    """The finished refs a persist may record as scored. None with a pool
    missing; with a space configured, only those that had a vector (Decision
    10), so a vectorless ref keeps its basis entry and is scored again once
    its vector exists."""
    if not pools_whole:
        return frozenset()
    if sweep.embedding == "off":
        return finished
    return frozenset(ref for ref in finished if by_ref[ref].text in vecs)


def _pairs(sweep: Sweep, current: pending.Current, subjects: list[similarity.Subject],
           order: list[str], vecs: dict[str, list[float]], pools_whole: bool) -> None:
    """Score and keep the pairs, with a cosine where both texts have a vector.
    With a pool missing (`pools_whole` False) no ref's every pair was scored --
    none was paired against the records that could not be read -- so nothing
    is reported rescored, and a persist keeps the cached pairs rather than
    retract them on a partial view."""
    scored, finished, count, capped = _score_pairs(order, subjects, vecs, RECONCILE_MAX_PAIRS)
    by_ref = {s.ref: s for s in subjects}
    sweep.rescored = _rescored(sweep, finished, by_ref, vecs, pools_whole)
    sweep.pairs_scored, sweep.pairs_capped = count, capped
    for a, b in sorted(_top_k(scored, by_ref, finished)):
        kind = "possible_duplicate" if by_ref[a].kind == by_ref[b].kind else "possible_relation"
        _nominate(sweep.discovered, current, kind, sorted((a, b)),
                  _pair_signals(scored[(a, b)]))


# --------------------------------------------------------------- embeddings


def _semantic(cid: str, sweep: Sweep, space: dict, subjects: list[similarity.Subject],
              order: list[str]) -> similarity.Semantic:
    """Vectors for the sweep (Decision 12): one probe of the cache, the changed
    refs' uncached texts required (incremental only), and a rotating window of
    the other uncached texts, all within `RECONCILE_WARM_LIMIT`."""
    text_of = {s.ref: s.text for s in subjects}
    texts = list(dict.fromkeys(text_of[ref] for ref in sorted(text_of)))
    loaded = vectors.load(space["space"], texts)
    uncached = [t for t in texts if t not in loaded]
    required: list[str] = []
    if not sweep.full:
        changed = (text_of[ref] for ref in order if text_of[ref] not in loaded)
        required = list(dict.fromkeys(changed))[:RECONCILE_WARM_LIMIT]
    need = set(required)
    warm = embed_space.warm_window([t for t in uncached if t not in need],
                                   f"{cid}\0{sweep.stamp}",
                                   RECONCILE_WARM_LIMIT - len(required))
    return similarity.semantic(required, warm, deadline=time.monotonic() + embeddings.TIMEOUT,
                               space=space, cached=texts, warm_limit=RECONCILE_WARM_LIMIT,
                               loaded=loaded)


def _embed(cid: str, sweep: Sweep, space: dict, subjects: list[similarity.Subject],
           order: list[str]) -> dict[str, list[float]]:
    """Run `_semantic`, record its mode on `sweep`, and answer its vectors. A
    failure (or anything unexpected) keeps basic matching and writes one
    counts-only error row."""
    failed = similarity.Semantic({}, "failure", error="unexpected")
    sem = _soft(_semantic, failed, cid, sweep, space, subjects, order)
    sweep.embedding, sweep.embedding_error = sem.mode, sem.error
    if sem.mode == "failure":
        errors.record(_TASK, sem.error or "network", _SEMANTIC_UNAVAILABLE,
                      campaign=cid, task=_TASK)
    return sem.vectors


# ---------------------------------------------------------------- lifecycle


def _chronicle_readable(cid: str) -> bool:
    """Whether chronicle.json parses: `aging.prepare` reads a garbled one as no
    scene dates, which would make a dated thread look undated, not stale."""
    return isinstance(_soft(chronicle.read_chronicle, None, cid), dict)


def _stale(cid: str, current: pending.Current, now: str) -> tuple[dict, bool]:
    """Closure nominations for live threads aging calls stale, and whether
    staleness could be read at all (a parseable present, a readable plot
    ledger and chronicle)."""
    actx = _soft(aging.prepare, None, cid, now)
    rows = _soft(effective.threads, None, cid)
    if actx is None or rows is None:
        return {}, False
    out: dict[str, dict] = {}
    for row in rows:
        block: dict = _soft(aging.age, {}, actx, row)
        if block.get("state") == aging.STALE:
            _nominate(out, current, "possible_thread_closure", [f"thread:{row['id']}"],
                      {"reason": "stale", "days_since": block["days_since"]})
    checked = (actx["now_fixed"] is not None and not current.unreadable & _STALE_LEDGERS
               and _chronicle_readable(cid))
    return out, checked


def _overdue(cid: str, current: pending.Current, now: str) -> tuple[dict, bool]:
    """Resolution nominations for every overdue deadline item -- a passed due or
    a reached before/by/on event -- and whether that was read whole: the
    calendar placed `now`, and the commitments and events were readable
    (`pressure.build` reads an unreadable one as having nothing due)."""
    built = _soft(pressure.build, None, cid, now, None, _OVERDUE_SOURCES)
    if built is None:
        return {}, False
    out: dict[str, dict] = {}
    for item in built["items"]:
        if item["state"] != "overdue" or not item["subject"]:
            continue
        signals = {"reason": "overdue", "in_days": item["in_days"], "via": item["kind"]}
        if item["kind"] == "linked_deadline":
            signals["event"] = item["ref"]
        _nominate(out, current, "possible_commitment_resolution", [item["subject"]], signals)
    return out, built["fixed"] is not None and not current.unreadable & _DEADLINE_LEDGERS


def _touched(current: pending.Current, refs: Iterable[str],
             nominated: dict[str, dict]) -> dict[str, dict]:
    """Model-only lifecycle re-checks for the live records a scene moved, on
    their live canonical refs, minus those already nominated."""
    out: dict[str, dict] = {}
    for raw in refs:
        ref = current.live.get(raw, raw)
        prefix = ref.partition(":")[0]
        kind, rec = _LIFECYCLE_OF.get(prefix), current.records.get(ref)
        if kind is None or rec is None or not effective.is_live(prefix, rec.get("status")):
            continue
        if canon.candidate_id(kind, [ref]) not in nominated:
            _nominate(out, current, kind, [ref], {"reason": "touched"})
    return out


def _touched_refs(sweep: Sweep, basis: dict, touched: Iterable[str]) -> list[str]:
    """Incremental only: the caller's refs, plus every ref whose identity text
    moved since the basis recorded it (a record a skipped run never
    re-checked). Compared through the unsalted `text_hashes`: a space change
    moves every `identity_hashes` entry, and a ref a capped sweep has not yet
    rescored under the new space keeps its old one for several sweeps. A ref
    with no entry is new, not moved."""
    if sweep.full:
        return []
    refs = [r for r in touched if isinstance(r, str)]
    stored = basis["text_hashes"]
    refs.extend(ref for ref, h in sweep.text_hashes.items() if ref in stored and stored[ref] != h)
    return refs


def _lifecycle(cid: str, current: pending.Current, touched: Iterable[str],
               now: str) -> tuple[dict, dict, dict]:
    """``(deterministic nominations, touched model-only, checked)``."""
    stale, stale_ok = _stale(cid, current, now)
    overdue, overdue_ok = _overdue(cid, current, now)
    found = {**stale, **overdue}
    return found, _touched(current, touched, found), {"stale": stale_ok, "overdue": overdue_ok}


def _near_events(items: list[dict]) -> list[dict]:
    """Unpassed events inside the suggestion horizon, nearest first."""
    near = [i for i in items if i["kind"] == "event" and i["state"] != "passed"
            and i["in_days"] is not None
            and 0 <= i["in_days"] <= calendars.UPCOMING_WINDOW_DAYS]
    near.sort(key=lambda i: (i["in_days"], i["ref"]))
    return near[:RECONCILE_TEMPORAL_EVENTS]


def _undatable(actx: dict, current: pending.Current, ref: str, rec: dict) -> bool:
    """A live commitment whose due is set, unreadable by the calendar, and not
    already tied to an event by a before/on/after/by link."""
    due = fieldtext.text(rec.get("due"))
    if not ref.startswith("commitment:") or not due:
        return False
    if not effective.is_live("commitment", rec.get("status")):
        return False
    block = _soft(aging.age, None, actx, {"due": due})
    if block is None or block["due_in"] is not None or block["days_over"] is not None:
        return False
    return not any(link["a"] == ref and link["relation"] in pending.TEMPORAL_RELATIONS
                   for link in current.links)


def _temporal(cid: str, current: pending.Current, now: str) -> tuple[dict, bool]:
    """Model-only temporal pairs, and whether the calendar, the commitments and
    the events could all be read. With any of them unreadable there are none:
    a broken calendar must not make every due look unparseable, and a pair
    nominated from half the records must not retract the rest."""
    actx = _soft(aging.prepare, None, cid, now)
    built = _soft(pressure.build, None, cid, now, None, {"event"})
    if actx is None or built is None or actx["now_fixed"] is None or built["fixed"] is None:
        return {}, False
    if current.unreadable & _DEADLINE_LEDGERS:
        return {}, False
    near = _near_events(built["items"])
    out: dict[str, dict] = {}
    for ref, rec in current.records.items():
        if not near or not _undatable(actx, current, ref, rec):
            continue
        for item in near:
            _nominate(out, current, "possible_relation", sorted((ref, item["ref"])),
                      {"reason": "temporal", "in_days": item["in_days"]})
    return out, True


def _live_only(current: pending.Current, found: dict[str, dict]) -> dict[str, dict]:
    """The nominations whose verdict is ``live`` (Decision 11)."""
    return {key: rec for key, rec in found.items()
            if _soft(pending.verdict, "unknown", current, rec) == "live"}


# ---------------------------------------------------------------- discovery


def _subjects(cid: str, current: pending.Current) -> tuple[list[similarity.Subject], bool]:
    """Both pools, and whether both were read whole: a pool that raised, or
    whose ledger is unreadable (`similarity.pool` reads that as empty)."""
    out: list[similarity.Subject] = []
    whole = not current.unreadable & _POOL_LEDGERS
    for kind in ("thread", "commitment"):
        pool = _soft(similarity.pool, None, cid, kind)
        whole = whole and pool is not None
        out.extend(pool or [])
    return out, whole


def discover(cid: str, *, stamp: str, full: bool, touched: Iterable[str] = (),
             embed: bool = True) -> Sweep:
    """One discovery pass over the campaign's ledgers. Read-only on the
    campaign (the only write is the global vector cache, through
    `similarity.semantic`), and never raises for an existing campaign.
    `touched` names the records a scene moved (incremental sweeps only).
    `embed` False skips the embedding step, leaving mode ``off``."""
    sweep = Sweep(stamp, full)
    space = _soft(similarity.available, None)
    if space is not None:
        sweep.space, sweep.model = space["space"], space["model"]
    sweep.matching = _soft(similarity.matching, "basic")
    current = _soft(pending.Current.load, None, cid)
    if current is None:
        return sweep
    if current.continuity_malformed:
        sweep.continuity = "malformed"
        return sweep
    basis = candidates.read(cid)["basis"]
    subjects, pools_whole = _subjects(cid, current)
    sweep.hashes = {s.ref: _identity_hash(sweep.space, s.text) for s in subjects}
    sweep.text_hashes = {s.ref: _text_hash(s.text) for s in subjects}
    order = _order(subjects, sweep.hashes, basis, sweep.full)
    vecs = _embed(cid, sweep, space, subjects, order) if embed and space is not None else {}
    _pairs(sweep, current, subjects, order, vecs, pools_whole)
    now = _soft(clock.now, "", cid)
    found, moved, checked = _lifecycle(cid, current, _touched_refs(sweep, basis, touched), now)
    temporal, temporal_ok = _temporal(cid, current, now)
    sweep.discovered.update(found)
    sweep.temporal_ids = frozenset(temporal)
    sweep.lifecycle_checked = {**checked, "temporal": temporal_ok}
    sweep.model_only = _live_only(current, {**temporal, **moved})
    return sweep


# ------------------------------------------------------------------ pressure


def _reading(item: dict | None) -> dict:
    if item is None:
        return dict(_NO_DEADLINE)
    return {"state": item["state"], "in_days": item["in_days"], "friendly": item["friendly"]}


def _pressure_by_ref(cid: str) -> dict[str, dict]:
    built = pressure.build(cid, sources=_DEADLINE_SOURCES)
    rank = pressure.PRESSURE_STATES.index
    best: dict[str, dict] = {}
    for item in built["items"]:
        subject = item["subject"]
        if subject and (subject not in best or rank(item["state"]) < rank(best[subject]["state"])):
            best[subject] = item
    owed: list[dict] = _soft(effective.commitments, [], cid)
    out = {f"commitment:{row['id']}": _reading(best.get(f"commitment:{row['id']}"))
           for row in owed}
    actx = _soft(aging.prepare, None, cid, built["now"])
    threads: list[dict] = _soft(effective.threads, [], cid)
    for row in threads:
        block: dict = _soft(aging.age, {}, actx, row) if actx is not None else {}
        state = "stale" if block.get("state") == aging.STALE else "ok"
        out[f"thread:{row['id']}"] = {"state": state, "in_days": None, "friendly": ""}
    return out


def pressure_by_ref(cid: str) -> dict[str, dict]:
    """``{ref: {state, in_days, friendly}}`` for every live canonical record: a
    commitment's most urgent deadline item (``ok`` with no day when it has
    none), a thread's aging -- ``stale`` or ``ok``, with no day. ``{}`` when
    the read fails; discovery and the candidates read both use it."""
    return _soft(_pressure_by_ref, {}, cid)
