"""Generation-fenced candidate cache commits and investigations, preserving whole campaign holds and revision publication."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

from ... import locks, paths, revision
from ...campaigns import paths as campaigns_paths
from ...scenes import read as scenes_read
from .. import candidates, pending, tolerance
from . import _discovery

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
    reads it. A model-only record the reader set aside is kept while its
    dismissal holds: no sweep re-finds it, so a restore (§12.7) brings back
    only what is still cached. Once its records move the dismissal no longer
    covers it, it reads ``stale`` like any record, and this drops it."""
    kept: dict[str, dict] = {}
    live = 0
    for key, record in records.items():
        verdict = tolerance.call(pending.verdict, "gone", current, record)
        if verdict in _KEPT or (verdict == "suppressed" and pending.model_only(record)):
            kept[key] = record
            live += verdict == "live"
    return kept, live


def _result(**changes: Any) -> dict:
    return {"written": False, "superseded": False, "gone": False, "cancelled": False,
            "continuity": "ok", "candidates": 0, **changes}


def _commit(cid: str, sweep: _discovery.Sweep, build: Build, stillborn: Callable[[], bool]) -> dict:
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
        candidates.write(cid, {"version": candidates.VERSION,
                               "questions": candidates.QUESTIONS,
                               "generated": paths.now_iso(),
                               "generation": sweep.stamp, "basis": basis, "records": records})
        revision.bump(cid)
        return _result(written=True, candidates=live)


def _retracted(key: str, record: dict, sweep: _discovery.Sweep) -> bool:
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


def _reasked(record: dict) -> dict:
    """A proposal stored ``uncertain`` under an older question shape
    (`candidates.QUESTIONS`) becomes None, so `select` asks it again under
    today's: before every question stood alone, a native endpoint answered
    the direction and the evidence none, and stored every directed or status
    verdict it gave as ``uncertain`` (spec 7.4). Every ``uncertain`` is asked
    again, a structured one included, since the shape it was asked under
    cannot be told from the proposal; once, since the next write stamps
    today's shape. A declined model-only nomination keeps its answer, as
    `_unvoided` keeps it, and so does one the reader set aside (`dismissed`).
    A dismissal itself is never touched: it lives in continuity.json, keyed by
    a fingerprint the question shape is no part of."""
    proposal = record["proposal"]
    if (proposal is None or proposal["decision"] != "uncertain"
            or pending.settled(record) or record.get("dismissed")):
        return record
    return {**record, "proposal": None}


def _found_basis(sweep: _discovery.Sweep, current: pending.Current, old: dict) -> dict:
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


def persist_found(cid: str, sweep: _discovery.Sweep, *,
                  stillborn: Callable[[], bool] = lambda: False) -> dict:
    """Persist 1 (§11.1 step 2): what the sweep discovered, plus every cached
    record it did not retract, re-judged against continuity.json as it stands
    under the lock -- so a dismissal, merge or link that landed after
    discovery is honoured. A cache stamped with an older question shape has
    its ``uncertain`` proposals asked again (`_reasked`). Model-only
    nominations wait for persist 2.

    Returns ``{written, superseded, gone, cancelled, continuity, candidates}``,
    `candidates` counting the ``live`` records."""
    def build(stored: dict, current: pending.Current) -> tuple[dict, dict]:
        cached = stored["records"]
        records = {key: rec for key, rec in cached.items() if not _retracted(key, rec, sweep)}
        records.update((key, _carried(rec, cached.get(key)))
                       for key, rec in sweep.discovered.items())
        rows = tolerance.call(scenes_read.list_scenes, None, cid)
        scenes = None if rows is None else {row["id"] for row in rows}
        records = {key: _unvoided(rec, scenes) for key, rec in records.items()}
        if stored["questions"] < candidates.QUESTIONS:
            records = {key: _reasked(rec) for key, rec in records.items()}
        return records, _found_basis(sweep, current, stored["basis"])

    return _commit(cid, sweep, build, stillborn)


def persist_proposals(cid: str, sweep: _discovery.Sweep, proposals: dict[str, dict], *,
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
                if tolerance.call(pending.verdict, "", current, cached) == "live":
                    records[key] = {**cached, "proposal": proposal}
                continue
            nominated = sweep.model_only.get(key)
            if nominated is not None and tolerance.call(
                    pending.fingerprint, None, current, nominated["kind"],
                    nominated["refs"]) == nominated["fingerprint"]:
                records[key] = {**nominated, "proposal": proposal}
        return records, stored["basis"]

    return _commit(cid, sweep, build, stillborn)


# ------------------------------------------------------------------ pressure



def persist_investigation(cid: str, key: str, proposal: dict, *,
                          started_fingerprint: str, started_proposal: dict | None,
                          stillborn: Callable[[], bool] = lambda: False) -> bool:
    """Roadmap 12's one write (§6.2): one investigation's proposal on the
    candidate it was about, and whether it landed.

    Under the campaign lock it writes only while the candidate is still
    cached, still ``live``, still at the fingerprint the run started from and
    still holding the proposal it held then -- anything else means the reader
    or a sweep moved it since, and the newer state stands. It replaces that one
    record's ``proposal`` and nothing else: the file's ``generation`` and
    ``basis`` are left as they are, so this write neither fences a sweep nor
    is fenced by one (a sweep that started earlier and lands later keeps or
    replaces it by its own rules). It stamps the campaign's revision itself,
    once, because the 202 that started the run never waits for it.
    `stillborn` is the run's stop, asked inside the hold as `_commit` asks
    it: a Stop pressed while this waited on the lock writes nothing."""
    with locks.campaign_lock(cid):
        if stillborn():
            return False
        if not campaigns_paths.campaign_exists(cid) or candidates.malformed(cid):
            return False
        stored = candidates.read(cid)
        cached = stored["records"].get(key)
        if cached is None or cached["fingerprint"] != started_fingerprint \
                or cached.get("proposal") != started_proposal:
            return False
        current = pending.Current.load(cid)
        if current.continuity_malformed \
                or tolerance.call(pending.verdict, "", current, cached) != "live":
            return False
        records = {**stored["records"], key: {**cached, "proposal": proposal}}
        candidates.write(cid, {**stored, "records": records})
        revision.bump(cid)
        return True
