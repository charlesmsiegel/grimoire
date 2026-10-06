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
chunk as it lands and forgetting off-width vectors. The required texts and the
window go in separate calls, and a failed required call is retried once over a
rotating proper subset of what it did not save: a changed ref left without a
vector stays changed and so stays required, so a text the provider refuses
would otherwise fail the window and every changed ref beside it on every sweep,
which is the stuck head the rotation exists to remove. A provider failure is
mode ``failure`` and one counts-only error row; the lexical candidates stand.
While a space is configured, a ref left without a vector (a failure, or outside
this run's window) is scored lexically but not reported rescored: its basis
entry stays, so its prior pairs are kept and it is scored semantically once its
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

**Adjudication** (§11.2-§11.4, Decision 13) is one model call per sweep, never
one per finding. `select` chooses what it asks -- overdue resolutions, then
duplicates strongest first, then the rest, capped at `RECONCILE_MAX_CANDIDATES`
-- from the findings with no proposal, re-checking each model-only nomination
so nothing the reader dismissed or linked is sent. `build_payload` sends the
records those findings name and nothing else of the ledger: each record's line,
its last beats with their scenes, its pressure, links and people, and the
chronicle lines around them -- never a transcript. The scenes it shows are the
only evidence `parse_output` accepts. The parser trusts no field of the reply:
a word outside the candidate's vocabulary, a direction the link rules refuse,
or a closure without a reason and a known evidence scene is ``uncertain``, and
a reply with no decodable object is None -- a failed run, not "no proposals".

**Two persists** (§11.1 steps 2 and 4, Decisions 7 and 8) share one campaign
lock hold. Persist 1 writes what the sweep found plus every cached record it
did not ask about again; persist 2 sets the model's proposals. Each re-judges
every record against continuity.json as it stands under that hold, so a
dismissal, merge or link that landed after discovery is never undone, and each
is fenced on the run's generation, so a newer run's cache is never overwritten.
One that would leave the cache as it was writes nothing and moves no token.

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
import re
import time
from collections.abc import Callable, Iterable
from typing import Any, Literal, TypeVar

from ... import embeddings, prompts
from .. import (
    aging,
    calendars,
    chronicle,
    clock,
    embed_space,
    errors,
    fieldtext,
    locks,
    paths,
    relationships,
    revision,
    scene_ids,
    vectors,
)
from ..absorb import parse as absorb_parse
from ..campaigns import paths as campaigns_paths
from ..scenes import read as scenes_read
from . import candidates, canon, effective, involvement, pending, pressure, similarity

#: Uncached texts one sweep may embed: four `embeddings.BATCH` round trips under
#: one shared `embeddings.TIMEOUT`. Nobody waits on a sweep, but a live one
#: refuses a storage move, so it is bounded like a reader-facing window and the
#: rest rotate in over later runs. To be tuned against real prompts later.
RECONCILE_WARM_LIMIT = embeddings.BATCH * 4

#: Pairs one sweep scores. A pair costs two comparisons of sets built once per
#: record (`similarity.Subject.features`: the token and trigram sets), plus at
#: most one pure-Python dot product when both texts have vectors (no numpy on
#: Android). Normalization is per record, not per pair, so the pair cost is the
#: comparisons and the dot product; at embedding widths in the low thousands the
#: dot product dominates, and the bound keeps the work to one worker thread for
#: a short while -- which keeps the storage-move refusal window short -- while
#: covering every pair of roughly two hundred same-type records in one sweep. To
#: be tuned against real prompts later.
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

#: Bytes of stored text one record shows -- its id, kind, status, title, due,
#: beats, link titles and actor names together. The counts above bound how
#: many of each are sent, not how long they are, and a ledger route takes a
#: title or beat of any length; this is the bound the identity resolver puts on
#: the same records (`similarity.CONTINUITY_IDENTITY_BYTES`), so a pasted note
#: can neither push the one call past the model's context nor, by failing it
#: on every pass, keep the other candidates in it from ever being decided. To
#: be tuned against real prompts later.
RECONCILE_RECORD_BYTES = similarity.CONTINUITY_IDENTITY_BYTES

#: Bytes of one chronicle one-line, which the absorb pass writes as a single
#: sentence but a reader may rewrite at any length (the Ledger's chronicle
#: edit, the review save). A long sentence fits even in a three-byte script. To be tuned against real prompts
#: later.
RECONCILE_SCENE_LINE_BYTES = 500

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
        #: Unordered ref pairs scored with every signal they can have (a
        #: cosine, when a space is configured) and admitted by no clause.
        self.refuted: frozenset[frozenset[str]] = frozenset()
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
             vecs: dict[str, list[float]]) -> dict:
    """The pair's similarity signals, with ``via`` the clause that admitted
    it or None."""
    signals = similarity.lexical(a, b)
    if vecs:
        signals = similarity.with_cosine(signals, a, b, vecs)
    return {**signals, "via": similarity.admitted_by(signals)}


def _score_pairs(to_score: list[str], subjects: list[similarity.Subject],
                 vecs: dict[str, list[float]], budget: int,
                 semantic: bool) -> tuple[dict, frozenset, frozenset, int, bool]:
    """Score each ref in `to_score` once against every other subject, each
    unordered pair once, stopping at `budget` pairs. Returns the plausible
    pairs (``(scored ref, other ref) -> signals``), the refuted pairs (no
    clause admits them, and -- with a space configured, `semantic` -- the
    cosine was there to ask), the refs whose every pair was scored, the pair
    count and whether the budget cut the sweep short."""
    by_ref = {s.ref: s for s in subjects}
    done: set[frozenset[str]] = set()
    kept: dict[tuple[str, str], dict] = {}
    refuted: set[frozenset[str]] = set()
    finished: set[str] = set()
    count = 0
    for ref in to_score:
        for other in subjects:
            pair = frozenset((ref, other.ref))
            if other.ref == ref or pair in done:
                continue
            if count >= budget:
                return kept, frozenset(refuted), frozenset(finished), count, True
            done.add(pair)
            count += 1
            signals = _signals(by_ref[ref], other, vecs)
            if signals["via"] is not None:
                kept[(ref, other.ref)] = signals
            elif not semantic or signals["cosine"] is not None:
                refuted.add(pair)
        finished.add(ref)
    return kept, frozenset(refuted), frozenset(finished), count, False


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
    scored, refuted, finished, count, capped = _score_pairs(
        order, subjects, vecs, RECONCILE_MAX_PAIRS, sweep.embedding != "off")
    by_ref = {s.ref: s for s in subjects}
    sweep.rescored = _rescored(sweep, finished, by_ref, vecs, pools_whole)
    sweep.refuted = refuted
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
    seed = f"{cid}\0{sweep.stamp}"
    room = max(RECONCILE_WARM_LIMIT - len(required), 0)
    # Sliced as well: `warm_window` hands back a lone text whatever its limit,
    # which past a full `required` would embed one text over the bound.
    warm = embed_space.warm_window([t for t in uncached if t not in need], seed, room)[:room]
    return similarity.semantic(required, warm, deadline=time.monotonic() + embeddings.TIMEOUT,
                               space=space, cached=texts, warm_limit=RECONCILE_WARM_LIMIT,
                               loaded=loaded, rotate=seed)


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


# ----------------------------------------------------------------- persists
#
# §11.1 steps 2 and 4 (Decisions 4, 7 and 8). Both run the same hold
# (`_commit`); they differ only in what they build from the cache as it
# stands under that hold.

#: The shape `generation` writes: twenty digits, a dash, the run id.
_GENERATION = re.compile(r"\A\d{20}-")
#: The verdicts a persist keeps (Decision 2): a finding a ledger it cannot
#: read says nothing about is never thrown away.
_KEPT = frozenset({"live", "settled", "unknown"})
_RETRACTABLE = ("stale", "overdue")

Build = Callable[[dict, pending.Current], tuple[dict, dict]]


def _superseded(stamp: str, stored: str) -> bool:
    """Decision 4: a run that started after this one already wrote the cache.
    A stored generation from this machine's future fences nothing -- another
    device whose clock runs ahead (the store may sit in a synced folder) would
    otherwise freeze every local sweep until the clock caught up -- and a
    missing or unparseable one is older than everything."""
    if not _GENERATION.match(stored):
        return False
    return stamp < stored <= f"{time.time_ns():020d}~"


def _rank(scored: dict[str, str]) -> dict[str, int]:
    """`basis.scored` as the order it gives `_order`: each ref's dense rank."""
    ranks = {stamp: n for n, stamp in enumerate(sorted(set(scored.values())))}
    return {ref: ranks[stamp] for ref, stamp in scored.items()}


def _same_basis(a: dict, b: dict) -> bool:
    """Equal, reading `scored` by the order it gives rather than by its stamps.
    `_order` is its only reader, and an uncapped full sweep restamps every ref
    without changing which was least recently scored -- so two identical
    sweeps write once, while a capped one, which moves the refs it finished to
    the back of the order, still writes."""
    rest = [k for k in a if k != "scored"]
    return ([a[k] for k in rest] == [b.get(k) for k in rest]
            and _rank(a["scored"]) == _rank(b["scored"]))


def _keep(current: pending.Current, records: dict[str, dict]) -> tuple[dict[str, dict], int]:
    """The records a persist keeps, and how many of them are ``live``. A
    record no normalization foresaw reads ``gone``, as `pending.findings`
    reads it."""
    kept: dict[str, dict] = {}
    live = 0
    for key, record in records.items():
        verdict = _soft(pending.verdict, "gone", current, record)
        if verdict in _KEPT:
            kept[key] = record
            live += verdict == "live"
    return kept, live


def _result(**changes: Any) -> dict:
    return {"written": False, "superseded": False, "gone": False, "cancelled": False,
            "continuity": "ok", "candidates": 0, **changes}


def _commit(cid: str, sweep: Sweep, build: Build, stillborn: Callable[[], bool]) -> dict:
    """Decision 7's hold, in order: a run that was stopped or forgotten writes
    nothing, nor does a run whose campaign is gone (no directory is
    recreated); a newer generation fences this one; continuity.json malformed
    leaves the cache as it is, since every decision would read as absent;
    then build, filter by verdict, and write and stamp only when the records
    or the basis moved. An End Scene that found nothing new must not move the
    write token, which a time-skip preview re-prices over."""
    with locks.campaign_lock(cid):
        if stillborn():
            return _result(cancelled=True)
        if not campaigns_paths.campaign_exists(cid):
            return _result(gone=True)
        stored = candidates.read(cid)
        if _superseded(sweep.stamp, stored["generation"]):
            return _result(superseded=True)
        current = pending.Current.load(cid)
        if current.continuity_malformed:
            return _result(continuity="malformed")
        built, basis = build(stored, current)
        records, live = _keep(current, built)
        if (records == stored["records"] and _same_basis(basis, stored["basis"])
                and not candidates.malformed(cid)):
            return _result(candidates=live)
        candidates.write(cid, {"version": candidates.VERSION, "generated": paths.now_iso(),
                               "generation": sweep.stamp, "basis": basis, "records": records})
        revision.bump(cid)
        return _result(written=True, candidates=live)


def _retracted(key: str, record: dict, sweep: Sweep) -> bool:
    """Decision 8: a cached record this sweep asked about again and did not
    find. A pair is asked again when it was scored with every signal it can
    have and no clause admits it, or when both of its refs were rescored --
    the top k is ranked only from a rescored endpoint, so with one endpoint
    rescored (an End Scene's changed ref beside an unchanged one; a ref a
    vector outage left unscored) a plausible pair the other endpoint's top k
    kept was never ranked again, and retracting it would drop its proposal
    until the next Refresh re-found it (Decision 10, §26). A stale or overdue
    finding is asked again when its source was read whole; a temporal pair
    when the calendar, commitments and events were. A touched re-check is
    never asked again by a sweep, so it is never retracted by one."""
    if key in sweep.discovered:
        return False
    if pending.is_temporal(record):
        return sweep.lifecycle_checked["temporal"] and key not in sweep.temporal_ids
    if record["kind"] in candidates.PAIR_KINDS:
        return (frozenset(record["refs"]) in sweep.refuted
                or all(ref in sweep.rescored for ref in record["refs"]))
    reason = record["signals"].get("reason")
    return reason in _RETRACTABLE and sweep.lifecycle_checked[reason]


def _carried(record: dict, cached: dict | None) -> dict:
    """A rediscovered record keeps its first `created`, and its proposal, when
    the cached copy meant the same records."""
    if cached is None or cached["fingerprint"] != record["fingerprint"]:
        return record
    return {**record, "proposal": cached["proposal"], "created": cached["created"]
            or record["created"]}


def _unvoided(record: dict, scenes: set[str] | None) -> dict:
    """A proposal citing a scene that no longer exists (a rename, a first
    datetime stamp, a repad) becomes None, so `select` asks it again (§5.6).
    A declined model-only nomination keeps its answer: it is never applied,
    and nulling it would surface it. An unreadable scene list voids nothing."""
    if scenes is None or pending.settled(record) or pending.evidence_ok(record["proposal"],
                                                                        scenes):
        return record
    return {**record, "proposal": None}


def _found_basis(sweep: Sweep, current: pending.Current, old: dict) -> dict:
    """The rescored refs' hashes and stamp; every other ref that still exists
    keeps its old entries (absent when new), so a ref the cap or a missing
    vector left unscored is still changed next time."""
    def exists(ref: str) -> bool:
        return ref in current.records or ref.partition(":")[0] in current.unreadable

    fresh = {"identity_hashes": sweep.hashes, "text_hashes": sweep.text_hashes,
             "scored": dict.fromkeys(sweep.hashes, sweep.stamp)}
    basis: dict[str, Any] = {"embedding_space": sweep.space, "embedding_model": sweep.model}
    for part, new in fresh.items():
        entries = {ref: value for ref, value in old[part].items() if exists(ref)}
        entries.update((ref, new[ref]) for ref in sweep.rescored if ref in new)
        basis[part] = entries
    return basis


def persist_found(cid: str, sweep: Sweep, *,
                  stillborn: Callable[[], bool] = lambda: False) -> dict:
    """Persist 1 (§11.1 step 2): what the sweep discovered, plus every cached
    record it did not retract, re-judged against continuity.json as it stands
    under the lock -- so a dismissal, merge or link that landed after
    discovery is honoured. Model-only nominations wait for persist 2.

    Returns ``{written, superseded, gone, cancelled, continuity, candidates}``,
    `candidates` counting the ``live`` records."""
    def build(stored: dict, current: pending.Current) -> tuple[dict, dict]:
        cached = stored["records"]
        records = {key: rec for key, rec in cached.items() if not _retracted(key, rec, sweep)}
        records.update((key, _carried(rec, cached.get(key)))
                       for key, rec in sweep.discovered.items())
        rows = _soft(scenes_read.list_scenes, None, cid)
        scenes = None if rows is None else {row["id"] for row in rows}
        records = {key: _unvoided(rec, scenes) for key, rec in records.items()}
        return records, _found_basis(sweep, current, stored["basis"])

    return _commit(cid, sweep, build, stillborn)


def persist_proposals(cid: str, sweep: Sweep, proposals: dict[str, dict], *,
                      stillborn: Callable[[], bool] = lambda: False) -> dict:
    """Persist 2 (§11.1 step 4): the model's proposals, set only on records
    still cached and still ``live`` -- one applied, dismissed or merged since
    persist 1 is never brought back (§22). A model-only nomination lands here
    or nowhere: with its proposal, and only while its fingerprint holds. One
    the model declined is ``settled``: kept, shown to nobody (Decision 9).

    Same hold and result as `persist_found`; the basis is left as it is."""
    def build(stored: dict, current: pending.Current) -> tuple[dict, dict]:
        records = dict(stored["records"])
        for key, proposal in proposals.items():
            cached = records.get(key)
            if cached is not None:
                if _soft(pending.verdict, "", current, cached) == "live":
                    records[key] = {**cached, "proposal": proposal}
                continue
            nominated = sweep.model_only.get(key)
            if nominated is not None and _soft(
                    pending.fingerprint, None, current, nominated["kind"],
                    nominated["refs"]) == nominated["fingerprint"]:
                records[key] = {**nominated, "proposal": proposal}
        return records, stored["basis"]

    return _commit(cid, sweep, build, stillborn)


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


# -------------------------------------------------------------- adjudication

#: What the model may answer, per vocabulary (spec §11.3). §5.3 allows
#: ``continues`` and ``subthread_of`` between threads only, so the same-type
#: list is read per type (deviation 18), and no vocabulary but a same-type one
#: offers ``duplicate`` (§3.6).
DECISIONS: dict[str, tuple[str, ...]] = {
    "same_thread": ("duplicate", "continuation", "subthread", "related", "distinct",
                    "uncertain"),
    "same_commitment": ("duplicate", "related", "distinct", "uncertain"),
    "cross": ("pays_off", "related", "distinct", "uncertain"),
    "thread": ("close", "keep_open", "uncertain"),
    "commitment": ("fulfilled", "broken", "expired", "keep_open", "uncertain"),
    "temporal": ("before", "on", "after", "by", "unrelated", "uncertain"),
}

#: What each vocabulary asks, as a candidate's heading in the user prompt.
LABELS: dict[str, str] = {
    "same_thread": "two plot threads",
    "same_commitment": "two commitments",
    "cross": "a plot thread and a commitment",
    "thread": "whether a plot thread is finished",
    "commitment": "whether a commitment is resolved",
    "temporal": "a commitment and a dated event",
}

#: A pair word -> the link relation it proposes (§5.3); ``duplicate`` proposes
#: an alias, which is not a link.
_RELATION_OF = {"duplicate": "", "continuation": "continues", "subthread": "subthread_of",
                "related": "related_to", "pays_off": "pays_off"}
#: The words whose meaning depends on which record is which (§11.3).
_DIRECTED = frozenset({"duplicate", "continuation", "subthread", "pays_off"})
#: The lifecycle words that propose a status, and so need positive evidence
#: (§11.4); ``keep_open`` writes no status.
_STATUS_OF = {"close": "closed", "fulfilled": "fulfilled", "broken": "broken",
              "expired": "expired"}
_LETTERS = ("A", "B")
#: A record's type as its prompt line names it, so a letter can never be read
#: as the other record (a cross pair stores the commitment first). An event's
#: line already begins ``event:``.
_TYPE_OF = {"thread": "plot thread", "commitment": "commitment"}
#: A leading ``candidate`` word, as the user prompt prints a key (``Candidate c1``).
_CANDIDATE_WORD = re.compile(r"^candidate(?![a-z0-9])[\s:#.-]*")


def vocabulary(record: dict) -> str:
    """The `DECISIONS` key a cached record is asked under: its lifecycle type,
    ``temporal`` for a commitment beside an event, the refs' type for a
    duplicate, else ``cross``."""
    kind = record.get("kind")
    if kind == "possible_thread_closure":
        return "thread"
    if kind == "possible_commitment_resolution":
        return "commitment"
    if pending.is_temporal(record):
        return "temporal"
    if kind == "possible_duplicate":
        refs = record.get("refs") or [""]
        first = refs[0] if isinstance(refs[0], str) else ""
        return "same_commitment" if first.startswith("commitment:") else "same_thread"
    return "cross"


# ---------------------------------------------------------------- selection


def _number(value) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def _count(value) -> int:
    return len(value) if isinstance(value, list) else 0


def _priority(item: dict) -> tuple:
    """Decision 13: overdue resolutions, then duplicates strongest first, then
    the rest by kind; the id breaks every tie."""
    kind, signals = item["kind"], item["signals"]
    if kind == "possible_commitment_resolution" and signals.get("reason") == "overdue":
        return (0, (), item["id"])
    if kind == "possible_duplicate":
        shared = sum(_count(signals.get(key))
                     for key in ("shared_actors", "shared_scenes", "shared_anchors"))
        return (1, (-bool(signals.get("title_exact")), -bool(signals.get("slug_equal")),
                    -shared, -_number(signals.get("lexical")),
                    -_number(signals.get("cosine"))), item["id"])
    return (2, (candidates.KINDS.index(kind),), item["id"])


def _item(key: str, record: dict) -> dict:
    return {"id": key, "kind": record["kind"], "refs": list(record["refs"]),
            "signals": dict(record["signals"]), "fingerprint": record["fingerprint"]}


def select(cid: str, sweep: Sweep) -> list[dict]:
    """What the one model call is asked (§11.1 step 3, Decision 13), at most
    `RECONCILE_MAX_CANDIDATES`, by priority.

    Read from the cache as persist 1 left it: every record with no proposal
    whose verdict is ``live``. Then the sweep's model-only nominations that are
    not cached with a proposal, each re-checked against the `pending.Current`
    loaded here: a dismissal or a link that landed after discovery is honoured,
    so nothing suppressed, satisfied or gone is ever sent. With continuity.json
    malformed nothing is."""
    if sweep.continuity == "malformed":
        return []
    current = _soft(pending.Current.load, None, cid)
    if current is None or current.continuity_malformed:
        return []
    stored = candidates.read(cid)["records"]
    chosen: dict[str, dict] = {}
    for key, record in stored.items():
        if record["proposal"] is None and _soft(pending.verdict, "", current, record) == "live":
            chosen[key] = _item(key, record)
    for key, record in sweep.model_only.items():
        cached = stored.get(key)
        if key in chosen or (cached is not None and cached["proposal"] is not None):
            continue
        if _soft(pending.verdict, "", current, record) == "live":
            chosen[key] = _item(key, record)
    return sorted(chosen.values(), key=_priority)[:RECONCILE_MAX_CANDIDATES]


# ------------------------------------------------------------------ payload


def snippet_line(ref: str, fields: dict) -> str:
    """One record's line as the prompt shows it. A thread or commitment is its
    `snippets/` absorb line with no latest beat (the beats follow it, each with
    its scene); `fields` is `pending.side`'s ``{title, status, kind, due}``. An
    event is ``event: <name> (<date>)`` from ``{title, due}``."""
    prefix, _, rid = ref.partition(":")
    if prefix == "thread":
        return prompts.render("snippets/plot_thread_line/absorb.j2",
                              t={"id": rid, "title": fields["title"], "status": fields["status"],
                                 "latest_beat": ""})
    if prefix == "commitment":
        return prompts.render("snippets/commitment_line/absorb.j2",
                              c={"id": rid, "title": fields["title"],
                                 "kind": fields["kind"] or "promise", "status": fields["status"],
                                 "due": fields["due"], "latest_beat": ""})
    return f"event: {fields['title']} ({fields['due']})" if fields["due"] else \
        f"event: {fields['title']}"


def _days(n: int) -> str:
    return "1 day" if n == 1 else f"{n} days"


def _when(days) -> str:
    """``in 3 days`` / ``today`` / ``5 days ago``, or "" for no day."""
    if not isinstance(days, int) or isinstance(days, bool):
        return ""
    if days == 0:
        return "today"
    return f"in {_days(days)}" if days > 0 else f"{_days(-days)} ago"


def _pressure_text(reading: dict | None) -> str:
    """A record's deadline or staleness in words; "" when nothing presses."""
    if not reading or (reading.get("state") == "ok" and reading.get("in_days") is None):
        return ""
    state = str(reading.get("state") or "").replace("_", " ")
    when = _when(reading.get("in_days"))
    return f"{state}, {when}" if when else state


def _play_key(row: dict) -> tuple:
    """A scene's place in play order: legacy ids (outside `scene_ids`' grammar,
    which replaced them) first by creation stamp, then numbered ids by number."""
    parsed = scene_ids.parse_sid(row["id"])
    if parsed is None:
        return (0, 0, fieldtext.text(row.get("created")), row["id"])
    return (1, parsed["number"], "", row["id"])


def play_order(rows: list[dict]) -> list[dict]:
    """`scenes_read.list_scenes` rows (newest-updated first) in play order --
    what the prompt's recent window and the candidates read's scene list are
    both ordered by."""
    return sorted(rows, key=_play_key)


def _live_scenes(cid: str) -> list[str]:
    """The scenes that exist, in play order -- the set persist 1's
    `pending.evidence_ok` checks a proposal against, so nothing outside it is
    shown as evidence. Empty when the scene list cannot be read."""
    none: list[dict] = []
    rows = _soft(scenes_read.list_scenes, none, cid)
    return [row["id"] for row in play_order(rows)]


class _Context:
    """What `build_payload` reads once for every record it shows."""

    def __init__(self, cid: str, refs: list[str], live: list[str]):
        self.cid = cid
        self.live = frozenset(live)
        self.current: pending.Current = pending.Current.load(cid)
        self.pressure = pressure_by_ref(cid)
        ledgered = [r for r in refs if not r.startswith("event:")]
        self.involved: dict[str, dict] = _soft(involvement.of, {}, cid, ledgered)
        self._names: dict[str, str] = {}

    def title(self, ref: str) -> str:
        return pending.label(self.current, [ref])

    def name(self, actor: str) -> str:
        if actor not in self._names:
            fallback = actor.partition(":")[2] or actor
            self._names[actor] = _soft(relationships.actor_name, fallback, self.cid, actor) \
                or fallback
        return self._names[actor]


def _beats(rec: dict) -> list[dict]:
    return [{"scene": fieldtext.text(b.get("scene")), "text": fieldtext.text(b.get("text"))}
            for b in (rec.get("beats") or [])[-RECONCILE_BEATS:] if isinstance(b, dict)]


def _record_view(ctx: _Context, letter: str, ref: str) -> dict:
    """One record as the prompt shows it (§11.2): its line, last beats with
    their scenes, pressure, effective links and involvement actors. An event
    carries a line only.

    The stored text it shows shares one `RECONCILE_RECORD_BYTES` budget
    (`similarity.clip_fields`), in priority: the id, kind, status, title and
    due its line is made of, then its beats newest first, then its links and
    actor names. A beat, link or name the budget no longer reaches is left out
    rather than shown blank. The reply names a record by its letter, never its
    id, so a cut id loses nothing `parse_output` reads."""
    prefix, _, rid = ref.partition(":")
    view: dict[str, Any] = {"letter": letter, "ref": ref, "type": _TYPE_OF.get(prefix, ""),
                            "line": similarity.clip_fields([ref], RECONCILE_RECORD_BYTES)[0],
                            "beats": [], "pressure": "", "links": [], "actors": []}
    side = pending.side(ctx.current, ref)
    if side is None:
        return view
    rec = ctx.current.records.get(ref)          # None for an event
    newest: list[dict] = []
    links: list[str] = []
    names: list[str] = []
    if rec is not None:
        actors = ctx.involved.get(ref, {}).get("actors") or []
        newest = [{**b, "scene": b["scene"] if b["scene"] in ctx.live else ""}
                  for b in reversed(_beats(rec))]
        links = [f"{ctx.title(link['a'])} {link['relation']} {ctx.title(link['b'])}"
                 for link in ctx.current.links if ref in (link["a"], link["b"])]
        names = [ctx.name(a) for a in actors[:RECONCILE_ACTORS]]
    rid, kind, status, title, due, *rest = similarity.clip_fields(
        [rid, side["kind"], side["status"], side["title"], side["due"],
         *(b["text"] for b in newest), *links, *names], RECONCILE_RECORD_BYTES)
    texts, rest = rest[:len(newest)], rest[len(newest):]
    view["line"] = snippet_line(f"{prefix}:{rid}", {**side, "kind": kind, "status": status,
                                                     "title": title, "due": due})
    if rec is None:
        return view
    beats = [{**b, "text": text} for b, text in zip(newest, texts, strict=True)
             if text or not b["text"]]
    view.update(beats=beats[::-1], pressure=_pressure_text(ctx.pressure.get(ref)),
                links=[text for text in rest[:len(links)] if text],
                actors=[text for text in rest[len(links):] if text])
    return view


def _lifecycle_text(signals: dict) -> str:
    reason = signals.get("reason")
    if reason == "stale":
        days = signals.get("days_since")
        if isinstance(days, int) and not isinstance(days, bool):
            return f"no new beat in {_days(days)}"
        return "no new beat for a long while"
    if reason == "overdue":
        text = ("the event it is tied to has been reached"
                if signals.get("via") == "linked_deadline" else "its due date has passed")
        when = _when(signals.get("in_days"))
        return f"{text} ({when})" if when.endswith("ago") else text
    if reason == "touched":
        return "moved in the latest scene"
    if reason == "temporal":
        when = _when(signals.get("in_days"))
        return ("the commitment's due could not be placed on the calendar; the event is "
                + (when or "upcoming"))
    return ""


def _pair_text(ctx: _Context, signals: dict) -> str:
    """A pair's similarity signals as fixed-order phrases: why it was found,
    never a verdict (the system prompt says so)."""
    parts: list[str] = []
    if signals.get("title_exact"):
        parts.append("same title")
    if signals.get("slug_equal"):
        parts.append("same slug")
    for key, label in (("lexical", "word overlap"), ("cosine", "meaning")):
        if _number(signals.get(key)):
            parts.append(f"{label} {_number(signals.get(key)):.2f}")
    actors = [a for a in signals.get("shared_actors") or [] if isinstance(a, str)]
    if actors:
        parts.append("shared characters: " + ", ".join(ctx.name(a) for a in actors))
    if _count(signals.get("shared_scenes")):
        parts.append(f"shared scenes: {_count(signals.get('shared_scenes'))}")
    anchors = [a for a in signals.get("shared_anchors") or [] if isinstance(a, str)]
    if anchors:
        parts.append("shared dates: " + ", ".join(ctx.title(a) for a in anchors))
    return "; ".join(parts)


def _signal_text(ctx: _Context, item: dict) -> str:
    signals = item["signals"]
    if item["kind"] in candidates.LIFECYCLE_KINDS or signals.get("reason") == "temporal":
        return _lifecycle_text(signals)
    return _pair_text(ctx, signals)


def _campaign_date(cid: str) -> str:
    built = _soft(pressure.build, None, cid, None, None, frozenset())
    if built is None:
        return _soft(clock.now, "", cid)
    return built["friendly"] or built["now"] or ""


def _scene_lines(cid: str, beat_scenes: set[str], live: list[str]) -> list[dict]:
    """Chronicle one-lines for every beat scene shown and the last
    `RECONCILE_RECENT_SCENES` live scenes, in play order, each cut to
    `RECONCILE_SCENE_LINE_BYTES`. Only scenes that
    exist are shown (`delete_scene` leaves the chronicle line behind), and a
    scene with no line is not."""
    read: Any = _soft(chronicle.read_chronicle, {}, cid)
    chron: dict = read if isinstance(read, dict) else {}
    recent = set(live[-RECONCILE_RECENT_SCENES:]) if RECONCILE_RECENT_SCENES > 0 else set()
    out: list[dict] = []
    for sid in live:
        if sid not in beat_scenes and sid not in recent:
            continue
        rec = chron.get(sid)
        line = fieldtext.text(rec.get("one_line")).strip() if isinstance(rec, dict) else ""
        line = embed_space.clip(line, RECONCILE_SCENE_LINE_BYTES)
        if line:
            out.append({"id": sid, "one_line": line})
    return out


def build_payload(cid: str, selected: list[dict]) -> dict:
    """The one call's bounded input (§11.2, Decision 13) for `select`'s items,
    keyed ``c1``... in selection order. Only the records the candidates name
    are sent, with their last beats and the chronicle lines around them --
    never a transcript -- and every stored text is cut to a byte bound
    (`_record_view`, `_scene_lines`; a signal line to `RECONCILE_RECORD_BYTES`),
    so the counts that bound the selection bound the prompt too. `known_scenes` (beat scenes and chronicle lines shown)
    is the only evidence `parse_output` accepts (§11.4). Both come from the
    scenes that exist: a beat in a deleted scene is shown without its scene,
    so the parser never accepts evidence persist 1 would void."""
    live = _live_scenes(cid)
    ctx = _Context(cid, sorted({ref for item in selected for ref in item["refs"]}), live)
    out: list[dict] = []
    for n, item in enumerate(selected, 1):
        records = [_record_view(ctx, letter, ref) for letter, ref in zip(_LETTERS, item["refs"], strict=False)]
        out.append({"key": f"c{n}", "id": item["id"], "vocabulary": vocabulary(item),
                    "records": records,
                    "signal_text": embed_space.clip(_signal_text(ctx, item),
                                                    RECONCILE_RECORD_BYTES)})
    beat_scenes = {b["scene"] for c in out for r in c["records"] for b in r["beats"]} - {""}
    lines = _scene_lines(cid, beat_scenes, live)
    return {"now": _campaign_date(cid), "chronicle": lines, "candidates": out,
            "known_scenes": sorted(beat_scenes | {line["id"] for line in lines})}


def template_vars(payload: dict) -> dict:
    """`build_payload` output as `continuity_reconcile/user.j2` reads it: each
    candidate gains its heading `label` and its vocabulary's `words`."""
    return {"now": payload["now"], "chronicle": payload["chronicle"],
            "candidates": [{"key": c["key"], "label": LABELS[c["vocabulary"]],
                            "words": list(DECISIONS[c["vocabulary"]]),
                            "records": c["records"], "signal_text": c["signal_text"]}
                           for c in payload["candidates"]]}


def build_prompt(payload: dict) -> list[dict]:
    """The sweep's messages: one call for every selected candidate (§25.1)."""
    return [{"role": "system", "content": prompts.render("continuity_reconcile/system.j2")},
            {"role": "user", "content": prompts.render("continuity_reconcile/user.j2",
                                                       **template_vars(payload))}]


# ------------------------------------------------------------------ parsing


def _candidate_key(value) -> str:
    if not isinstance(value, str):
        return ""
    return _CANDIDATE_WORD.sub("", value.strip().casefold()).strip()


def _ref_of(value, refs: dict[str, str]) -> str:
    return refs.get(value.strip().upper(), "") if isinstance(value, str) else ""


def _evidence(value, known: set[str]) -> list[str]:
    """The cited scene ids the prompt showed, deduped in order; the rest dropped."""
    out: list[str] = []
    for sid in value if isinstance(value, list) else ():
        if isinstance(sid, str) and sid in known and sid not in out:
            out.append(sid)
    return out


def _allowed(relation: str, frm: str, to: str) -> bool:
    rule = effective.RELATIONS.get(relation)
    return rule is not None and frm.partition(":")[0] in rule[0] \
        and to.partition(":")[0] in rule[1]


def _temporal_word(word: str, refs: dict[str, str], base: dict) -> dict:
    """A temporal word runs from the commitment to the event, whatever letters
    the reply gave; ``unrelated`` and ``uncertain`` propose nothing."""
    event = next((r for r in refs.values() if r.startswith("event:")), "")
    owed = next((r for r in refs.values() if r.startswith("commitment:")), "")
    if word not in pending.TEMPORAL_RELATIONS:
        return {**base, "decision": word}
    if not event or not owed or not _allowed(word, owed, event):
        return base
    return {**base, "decision": word, "from": owed, "to": event, "relation": word}


def _pair(word: str, item: dict, refs: dict[str, str], base: dict) -> dict:
    """A pair word with its direction (§11.3). A directed word needs two
    different letters; ``related`` takes A to B when it was given none. A
    relation `effective.RELATIONS` refuses for ``(from, to)`` is ``uncertain``,
    so no proposal names a link `create_link` would reject."""
    if word not in _RELATION_OF:
        return {**base, "decision": word}
    frm, to = _ref_of(item.get("from"), refs), _ref_of(item.get("to"), refs)
    if word not in _DIRECTED and (not frm or not to or frm == to):
        frm, to = refs.get("A", ""), refs.get("B", "")
    if not frm or not to or frm == to:
        return base
    relation = _RELATION_OF[word]
    if relation and not _allowed(relation, frm, to):
        return base
    if not relation and frm.partition(":")[0] != to.partition(":")[0]:
        return base
    return {**base, "decision": word, "from": frm, "to": to, "relation": relation}


def _lifecycle_word(word: str, base: dict) -> dict:
    """A status word stands only with a reason and a known evidence scene (§11.4)."""
    if word in _STATUS_OF:
        if not base["reason"] or not base["evidence_scenes"]:
            return base
        return {**base, "decision": word, "status": _STATUS_OF[word]}
    return {**base, "decision": word}


def _decide(item: dict, cand: dict, known: set[str]) -> dict:
    """One reply element rebuilt field by field into a cache proposal (§6)."""
    vocab = cand["vocabulary"]
    word = item.get("decision")
    word = word.strip().casefold() if isinstance(word, str) else ""
    word = word if word in DECISIONS[vocab] else "uncertain"
    reason = item.get("reason")
    base = {"decision": "uncertain", "from": "", "to": "", "relation": "", "status": "",
            "reason": reason.strip()[:RECONCILE_REASON_CHARS] if isinstance(reason, str) else "",
            "evidence_scenes": _evidence(item.get("evidence_scenes"), known)}
    refs = {r["letter"]: r["ref"] for r in cand["records"]}
    if vocab in ("thread", "commitment"):
        return _lifecycle_word(word, base)
    if vocab == "temporal":
        return _temporal_word(word, refs, base)
    return _pair(word, item, refs, base)


def parse_output(text: str, payload: dict) -> dict[str, dict] | None:
    """The reply's proposals, ``{candidate id: proposal}``, or None when it
    holds no decodable object at all -- a failed run, whose deterministic
    findings stand, which is not the same as a decodable reply with nothing
    usable in it (``{}``, spec §24).

    A key is matched without its ``Candidate`` label; an unknown one is
    dropped, and a repeated one keeps its first answer. A word outside the
    candidate's vocabulary, a direction §5.3 does not allow, or a status word
    without a reason and a known evidence scene is ``uncertain``. Unknown
    scene ids are dropped, and the reason is clipped. Nothing here raises on
    bad JSON."""
    obj = absorb_parse.extract_object(text)
    if obj is None:
        return None
    items = obj.get("decisions")
    by_key = {c["key"]: c for c in payload["candidates"]}
    known = set(payload["known_scenes"])
    out: dict[str, dict] = {}
    seen: set[str] = set()
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict):
            continue
        key = _candidate_key(item.get("candidate"))
        cand = by_key.get(key)
        if cand is None or key in seen:
            continue
        seen.add(key)
        out[cand["id"]] = _decide(item, cand, known)
    return out
