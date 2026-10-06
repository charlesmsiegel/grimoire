"""`store/continuity/reconcile.py` -- the reconciliation sweep (capstone spec §11).

Discovery is deterministic and read-only: it scores pairs and nominates
lifecycle re-checks, and it writes nothing (§3.2: scores rank, never write).
Every candidate / non-candidate test first asserts the signal that is supposed
to decide it, so a later floor change fails at that assertion rather than
silently somewhere else.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import zlib

import pytest

from grimoire import embeddings
from grimoire.store import (
    calendars,
    campaigns,
    clock,
    commitments,
    config,
    embed_space,
    errors,
    events,
    llm_connections,
    locks,
    plot,
    revision,
    scenes,
    vectors,
    worlds,
)
from grimoire.store.campaigns import paths as campaigns_paths
from grimoire.store.continuity import (
    candidates,
    canon,
    doc,
    pending,
    reconcile,
    review,
    similarity,
)
from tests.llm_fakes import FakeEmbeddings
from tests.review_runs import LEDGER_THREAD, RECOVER_THE_LEDGER, SALTMARCH_TITHE
from tests.test_continuity_pressure import _BROKEN_PROVIDER_SRC

LEDGER = f"thread:{LEDGER_THREAD[0]}"
RECOVER = "thread:recover-the-harbour-ledger"
TITHE = "thread:the-saltmarch-tithe"
MAP = "thread:mara-s-map"
OATH = "commitment:mara-s-oath"

SIGNAL_KEYS = {"title_exact", "slug_equal", "lexical", "cosine", "shared_actors",
               "shared_scenes", "shared_anchors", "via"}


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Run", wid)


@pytest.fixture
def s0(cid):
    return scenes.create_scene(cid, "Saltmarch docks")


def _root(cid):
    return campaigns_paths.campaign_root(cid)


def _ledger(cid, scene):
    pid, title, beat = LEDGER_THREAD
    plot.set_movement(cid, pid, title, "open", beat, scene)


def _recover(cid, scene):
    plot.set_movement(cid, "recover-the-harbour-ledger", RECOVER_THE_LEDGER["title"],
                      "open", RECOVER_THE_LEDGER["beat"], scene)


def _tithe(cid, scene):
    plot.set_movement(cid, "the-saltmarch-tithe", SALTMARCH_TITHE["title"], "open",
                      SALTMARCH_TITHE["beat"], scene)


def _map(cid, scene, beat="The map turned up in Saltmarch."):
    plot.set_movement(cid, "mara-s-map", "Mara's map", "open", beat, scene)


def _subjects(cid):
    return {s.ref: s for kind in ("thread", "commitment") for s in similarity.pool(cid, kind)}


def _signals(cid, a, b):
    subjects = _subjects(cid)
    return similarity.lexical(subjects[a], subjects[b])


def _sweep(cid, *, full=True, touched=(), stamp="00000000000000000010-run"):
    return reconcile.discover(cid, stamp=stamp, full=full, touched=touched, embed=False)


def _write_basis(cid, hashes, *, scored=None, space="", texts=None):
    data = candidates.empty()
    data["basis"] = {"embedding_space": space, "embedding_model": "",
                     "identity_hashes": dict(hashes), "scored": dict(scored or {}),
                     "text_hashes": dict(texts or {})}
    candidates.write(cid, data)


def _persisted(cid, sweep):
    """The basis a first persist after `sweep` would hold, every ref rescored."""
    _write_basis(cid, sweep.hashes, texts=sweep.text_hashes, space=sweep.space)


def _counting(monkeypatch):
    calls = []
    real = similarity.lexical

    def counted(a, b):
        calls.append((a.ref, b.ref))
        return real(a, b)

    monkeypatch.setattr(similarity, "lexical", counted)
    return calls


def _pair_ids(found, kind):
    return {key for key, rec in found.items() if rec["kind"] == kind}


def _dated_scene(cid, title, native):
    sid = scenes.create_scene(cid, title)
    return scenes.set_datetime(cid, sid, native)["id"]


def _broken_calendar(cid, tmp_path):
    directory = tmp_path / "calendars"
    directory.mkdir(exist_ok=True)
    (directory / "broken_test.py").write_text(_BROKEN_PROVIDER_SRC, encoding="utf-8")
    root = _root(cid)
    cfg = calendars.read_calendar(root)
    cfg["primary"] = {"provider": "broken-test-calendar", "region": "",
                      "custom_holidays": [], "anchor": None}
    calendars.write_calendar(root, cfg)


def _link(cid, a, b, relation):
    doc.put_link(cid, canon.link_id(relation, a, b),
                 {"a": a, "b": b, "relation": relation, "created": "", "scene": "", "note": ""})


def _suppress(cid, kind, refs, decision="dismiss"):
    fp = pending.fingerprint(pending.Current.load(cid), kind, refs)
    assert fp is not None
    doc.put_suppression(cid, fp, {"kind": kind, "refs": list(refs), "decision": decision,
                                  "created": ""})


# ------------------------------------------------------------------ basics


def test_generation_sorts_as_a_string_and_ends_with_the_run_id():
    first = reconcile.generation("a")
    second = reconcile.generation("b")
    assert first.endswith("-a") and second.endswith("-b")
    assert len(first.partition("-")[0]) == 20
    assert first < second


def test_constants_are_structural():
    assert reconcile.SWEEPS == ("full", "incremental")
    assert reconcile.RECONCILE_TOP_K == similarity.IDENTITY_TOP_K
    assert reconcile.RECONCILE_BEATS == pending.ROW_BEATS
    assert reconcile.RECONCILE_MAX_PAIRS == 20_000
    assert reconcile.RECONCILE_MAX_CANDIDATES == 24
    assert reconcile.RECONCILE_TEMPORAL_EVENTS == 3


# ------------------------------------------------------------------- pairs


def test_close_same_type_records_become_a_possible_duplicate(cid, s0):
    _ledger(cid, s0)
    _recover(cid, s0)
    assert _signals(cid, LEDGER, RECOVER)["tokens"] >= similarity.TOKEN_FLOOR

    sweep = _sweep(cid)
    key = canon.candidate_id("possible_duplicate", [LEDGER, RECOVER])
    record = sweep.discovered[key]
    assert record["kind"] == "possible_duplicate"
    assert sorted(record["refs"]) == sorted([LEDGER, RECOVER])
    assert set(record["signals"]) == SIGNAL_KEYS
    assert record["signals"]["via"] == "lexical"
    assert record["proposal"] is None
    assert record["fingerprint"] == pending.fingerprint(
        pending.Current.load(cid), "possible_duplicate", record["refs"])
    assert sweep.continuity == "ok"
    assert sweep.model_only == {}


def test_thread_and_commitment_overlap_is_a_relation_never_a_duplicate(cid, s0):
    plot.set_movement(cid, "mara-s-oath", "Mara's oath", "open", "Mara swore it.", s0)
    commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open", "",
                             "Mara swore it.", s0)
    assert _signals(cid, "thread:mara-s-oath", OATH)["title_equal"] is True

    sweep = _sweep(cid)
    assert _pair_ids(sweep.discovered, "possible_duplicate") == set()
    assert _pair_ids(sweep.discovered, "possible_relation") == {
        canon.candidate_id("possible_relation", ["thread:mara-s-oath", OATH])}


def test_far_records_are_not_paired(cid, s0):
    _ledger(cid, s0)
    _tithe(cid, s0)
    signals = _signals(cid, LEDGER, TITHE)
    assert signals["tokens"] < similarity.WEAK_TOKEN
    assert signals["chars"] < similarity.WEAK_CHAR

    sweep = _sweep(cid)
    assert {r["kind"] for r in sweep.discovered.values()} <= set(candidates.LIFECYCLE_KINDS)
    assert sweep.pairs_scored == 1


def test_incremental_pairs_only_changed_records_against_all(cid, s0, monkeypatch):
    _ledger(cid, s0)
    _recover(cid, s0)
    _tithe(cid, s0)
    _map(cid, s0)
    _persisted(cid, _sweep(cid))
    _map(cid, s0, beat="Mara traced the coast on it.")

    calls = _counting(monkeypatch)
    sweep = _sweep(cid, full=False)
    assert calls and all(MAP in pair for pair in calls)
    assert len(calls) == 3
    assert sweep.rescored == {MAP}


def test_a_full_sweep_scores_every_unordered_pair_once(cid, s0, monkeypatch):
    _ledger(cid, s0)
    _recover(cid, s0)
    _tithe(cid, s0)
    _map(cid, s0)
    commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open", "",
                             "Mara swore it.", s0)
    n = 5
    calls = _counting(monkeypatch)
    sweep = _sweep(cid)
    assert len(calls) == n * (n - 1) // 2
    assert len({frozenset(pair) for pair in calls}) == len(calls)
    assert sweep.pairs_scored == len(calls) and sweep.pairs_capped is False
    assert sweep.rescored == frozenset(_subjects(cid))


ERRAND = "thread:mara-s-errand"


def _errands(cid, scene):
    """Six near-identical errands, and a seventh plausible beside all of them."""
    for i in range(1, 7):
        plot.set_movement(cid, f"x{i}", f"Winifred's Saltmarch harbour errand {i}", "open",
                          "Winifred ran the Saltmarch harbour errand to the docks again.",
                          scene)
    plot.set_movement(cid, "mara-s-errand", "Mara's harbour errand", "open",
                      "Mara went to the harbour.", scene)
    return [f"thread:x{i}" for i in range(1, 7)]


def _duplicates(found, ref=None):
    return {frozenset(rec["refs"]) for rec in found.values()
            if rec["kind"] == "possible_duplicate" and (ref is None or ref in rec["refs"])}


def test_a_full_sweep_keeps_only_each_records_top_k(cid, s0):
    _errands(cid, s0)
    subjects = _subjects(cid)
    plausible = {}
    for a in subjects.values():
        for b in subjects.values():
            if a.ref < b.ref:
                signals = similarity.lexical(a, b)
                if similarity.admitted_by(signals) is not None:
                    plausible[frozenset((a.ref, b.ref))] = signals
    expected: set[frozenset] = set()
    for ref in subjects:
        mine = sorted((similarity.rank_key(signals, next(iter(pair - {ref}))), pair)
                      for pair, signals in plausible.items() if ref in pair)
        expected.update(pair for _, pair in mine[:reconcile.RECONCILE_TOP_K])
    assert expected < set(plausible)        # the bound bites: not every plausible pair

    assert _duplicates(_sweep(cid).discovered) == expected


def test_an_incremental_sweep_keeps_no_more_neighbours_than_a_full_one(cid, s0):
    others = _errands(cid, s0)
    for x in others:
        assert similarity.admitted_by(_signals(cid, ERRAND, x)) is not None

    full = _sweep(cid)
    expected = _duplicates(full.discovered, ERRAND)
    assert len(expected) == reconcile.RECONCILE_TOP_K
    _persisted(cid, full)
    basis = candidates.read(cid)["basis"]
    del basis["identity_hashes"][ERRAND]
    _write_basis(cid, basis["identity_hashes"], texts=basis["text_hashes"])

    sweep = _sweep(cid, full=False)
    assert sweep.rescored == {ERRAND}
    assert _duplicates(sweep.discovered, ERRAND) == expected


def test_a_capped_full_sweep_keeps_at_most_top_k_per_finished_ref(cid, s0, monkeypatch):
    others = _errands(cid, s0)
    monkeypatch.setattr(reconcile, "RECONCILE_MAX_PAIRS", 6)
    _write_basis(cid, dict.fromkeys(others, "old"))   # ERRAND is missing: scored first

    sweep = _sweep(cid)
    assert sweep.pairs_capped is True and sweep.rescored == {ERRAND}
    assert len(_duplicates(sweep.discovered)) == reconcile.RECONCILE_TOP_K


@pytest.mark.parametrize("garbled", ["plot.json", "commitments.json"])
def test_a_pool_that_could_not_be_read_rescores_nothing(cid, s0, garbled):
    plot.set_movement(cid, "mara-s-oath", "Mara's oath", "open", "Mara swore it.", s0)
    commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open", "",
                             "Mara swore it.", s0)
    commitments.set_movement(cid, "mara-s-vow", "Mara's oath", "promise", "open", "",
                             "Mara swore it.", s0)
    _ledger(cid, s0)
    _recover(cid, s0)
    assert _sweep(cid).rescored == frozenset(_subjects(cid))
    (_root(cid) / garbled).write_text("{ no", encoding="utf-8")

    sweep = _sweep(cid)
    assert sweep.rescored == frozenset()
    readable = "commitment:" if garbled == "plot.json" else "thread:"
    assert sweep.discovered          # what could be scored is still found
    assert all(ref.startswith(readable) for rec in sweep.discovered.values()
               for ref in rec["refs"])


def test_a_pool_that_raised_rescores_nothing(cid, s0, monkeypatch):
    _ledger(cid, s0)
    _recover(cid, s0)
    real = similarity.pool

    def pool(c, kind):
        if kind == "commitment":
            raise RuntimeError("a pool that will not build")
        return real(c, kind)

    monkeypatch.setattr(similarity, "pool", pool)
    sweep = _sweep(cid)
    assert sweep.rescored == frozenset()
    assert canon.candidate_id("possible_duplicate", [LEDGER, RECOVER]) in sweep.discovered


def _five(cid, scene):
    for i in range(1, 6):
        plot.set_movement(cid, f"r{i}", f"Saltmarch errand number {i}", "open",
                          f"Winifred ran errand {i}.", scene)
    return [f"thread:r{i}" for i in range(1, 6)]


def _stamp(i):
    return f"{i:020d}-s"


def test_a_capped_sweep_reaches_the_rest_next_time(cid, s0, monkeypatch):
    refs = _five(cid, s0)
    hashes = _sweep(cid).hashes
    basis_hashes = dict(hashes)
    scored = {ref: _stamp(1) for ref in refs}
    _write_basis(cid, basis_hashes, scored=scored)
    monkeypatch.setattr(reconcile, "RECONCILE_MAX_PAIRS", 5)

    calls = _counting(monkeypatch)
    union: set[str] = set()
    for i in range(2, 7):
        calls.clear()
        sweep = _sweep(cid, stamp=_stamp(i))
        assert sweep.pairs_capped is True
        assert len(sweep.rescored) == 1
        first = refs[i - 2]
        assert calls[0][0] == first
        assert sweep.rescored == {first}
        union |= sweep.rescored
        for ref in sweep.rescored:          # persist 1's basis, rescored refs only
            basis_hashes[ref] = sweep.hashes[ref]
            scored[ref] = sweep.stamp
        _write_basis(cid, basis_hashes, scored=scored)
    assert union == set(refs)


def _old_space_hash(text):
    return hashlib.sha256(("old\0" + text).encode("utf-8", "surrogatepass")).hexdigest()


def _space(monkeypatch, name):
    monkeypatch.setattr(similarity, "available",
                        lambda: {"space": name, "model": "m", "base_url": "", "key": ""})


def test_a_capped_sweep_after_a_space_change_reaches_the_tail(cid, s0, monkeypatch):
    refs = _five(cid, s0)
    subjects = _subjects(cid)
    old = {ref: _old_space_hash(subjects[ref].text) for ref in refs}
    texts = _sweep(cid).text_hashes
    _write_basis(cid, old, space="old", texts=texts)
    _space(monkeypatch, "new")
    monkeypatch.setattr(reconcile, "RECONCILE_MAX_PAIRS", 5)

    sweep = _sweep(cid, full=False, stamp=_stamp(2))
    assert sweep.space == "new"
    assert sweep.rescored == {refs[0]}
    assert sweep.model_only == {}           # every hash moved, no text did: nothing "touched"
    hashes = {**old, refs[0]: sweep.hashes[refs[0]]}
    _write_basis(cid, hashes, scored={refs[0]: sweep.stamp}, space="new", texts=texts)

    calls = _counting(monkeypatch)
    sweep = _sweep(cid, full=False, stamp=_stamp(3))
    assert calls[0][0] == refs[1]
    assert sweep.rescored == {refs[1]}
    # r2..r5 still carry old-space hashes, but none of their texts moved
    assert sweep.model_only == {}


def test_a_record_moved_across_a_space_change_is_still_touched(cid, s0, monkeypatch):
    _map(cid, s0)
    _ledger(cid, s0)
    _persisted(cid, _sweep(cid))
    _map(cid, s0, beat="Mara traced the coast on it.")
    _space(monkeypatch, "new")

    sweep = _sweep(cid, full=False)
    assert set(sweep.model_only) == {canon.candidate_id("possible_thread_closure", [MAP])}


def test_touched_includes_refs_moved_since_the_last_sweep(cid, s0):
    _map(cid, s0)
    _ledger(cid, s0)
    _persisted(cid, _sweep(cid))
    _map(cid, s0, beat="Mara traced the coast on it.")
    plot.set_movement(cid, "seraphine-s-letter", "Seraphine's letter", "open",
                      "Seraphine sealed it.", s0)

    sweep = _sweep(cid, full=False)
    key = canon.candidate_id("possible_thread_closure", [MAP])
    assert sweep.model_only[key]["signals"]["reason"] == "touched"
    assert sweep.model_only[key]["proposal"] is None
    letter = canon.candidate_id("possible_thread_closure", ["thread:seraphine-s-letter"])
    assert letter not in sweep.model_only
    ledger = canon.candidate_id("possible_thread_closure", [LEDGER])
    assert ledger not in sweep.model_only


def test_a_lone_surrogate_title_is_skipped_not_fatal(cid, s0):
    _ledger(cid, s0)
    _recover(cid, s0)
    plot.set_movement(cid, "mara-x", json.loads('"Mara\\ud83d"'), "open",
                      "Mara wrote something.", s0)

    sweep = _sweep(cid)
    assert canon.candidate_id("possible_duplicate", [LEDGER, RECOVER]) in sweep.discovered
    assert all("thread:mara-x" not in r["refs"] for r in sweep.discovered.values())


def test_a_changed_embedding_space_rescores_everything(cid, s0, monkeypatch):
    _ledger(cid, s0)
    _recover(cid, s0)
    _tithe(cid, s0)
    _persisted(cid, _sweep(cid))
    assert _sweep(cid, full=False).rescored == frozenset()

    _space(monkeypatch, "new")
    sweep = _sweep(cid, full=False)
    assert sweep.rescored == {LEDGER, RECOVER, TITHE}


# --------------------------------------------------------------- lifecycle


def test_a_campaign_with_no_present_checks_no_lifecycle_source(cid, s0):
    _map(cid, s0)
    assert clock.now(cid) == ""
    sweep = _sweep(cid)
    assert sweep.lifecycle_checked == {"stale": False, "overdue": False, "temporal": False}


def test_a_stale_thread_is_nominated_for_closure_not_closed(cid):
    sid = _dated_scene(cid, "Saltmarch docks", "2026-05-01")
    _map(cid, sid)
    clock.advance(cid, to="2026-07-15")

    sweep = _sweep(cid)
    key = canon.candidate_id("possible_thread_closure", [MAP])
    record = sweep.discovered[key]
    assert record["signals"]["reason"] == "stale"
    assert record["signals"]["days_since"] == 75
    assert record["proposal"] is None
    assert plot.get(cid, "mara-s-map")["status"] == "open"
    assert sweep.lifecycle_checked["stale"] is True


def test_a_passed_deadline_nominates_but_does_not_resolve(cid, s0):
    commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open",
                             "2026-05-05", "Mara swore it.", s0)
    clock.advance(cid, to="2026-05-10")

    sweep = _sweep(cid)
    key = canon.candidate_id("possible_commitment_resolution", [OATH])
    signals = sweep.discovered[key]["signals"]
    assert (signals["reason"], signals["via"], signals["in_days"]) == ("overdue", "deadline", -5)
    assert commitments.get(cid, "mara-s-oath")["status"] == "open"
    assert sweep.lifecycle_checked["overdue"] is True


def test_a_passed_linked_commitment_is_nominated(cid, s0):
    commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open", "",
                             "Mara swore it.", s0)
    clock.advance(cid, to="2026-05-10")
    event = "event:" + events.create(cid, "The coronation", "2026-05-12")
    events.fire(cid, [event.partition(":")[2]], "2026-05-10")
    _link(cid, OATH, event, "before")

    sweep = _sweep(cid)
    key = canon.candidate_id("possible_commitment_resolution", [OATH])
    signals = sweep.discovered[key]["signals"]
    assert (signals["reason"], signals["via"], signals["event"]) == (
        "overdue", "linked_deadline", event)


def test_touched_records_are_model_only(cid, s0):
    _map(cid, s0)
    sweep = _sweep(cid, full=False, touched=[MAP])
    key = canon.candidate_id("possible_thread_closure", [MAP])
    assert sweep.model_only[key]["signals"] == {"reason": "touched"}
    assert key not in sweep.discovered


def _temporal_key(commitment, event):
    return canon.candidate_id("possible_relation", [commitment, event])


def test_temporal_pairs_need_an_unparseable_due_and_a_readable_calendar(cid, s0, tmp_path,
                                                                          monkeypatch):
    clock.advance(cid, to="2026-05-10")
    commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open",
                             "before the bells stop", "Mara swore it.", s0)
    commitments.set_movement(cid, "winifred-s-debt", "Winifred's debt", "debt", "open",
                             "2026-06-01", "Winifred owes it.", s0)
    commitments.set_movement(cid, "seraphine-s-vow", "Seraphine's vow", "promise", "open",
                             "when the tide turns", "Seraphine vowed.", s0)
    near = ["event:" + events.create(cid, name, date) for name, date in (
        ("The coronation", "2026-05-13"), ("Saltmarch Eve", "2026-05-15"),
        ("Mara's audience", "2026-05-17"))]
    far = "event:" + events.create(cid, "The Realm fair", "2026-05-19")
    passed = "event:" + events.create(cid, "The Saltmarch market", "2026-05-01")
    _link(cid, "commitment:seraphine-s-vow", far, "before")
    assert len(near) == reconcile.RECONCILE_TEMPORAL_EVENTS

    sweep = _sweep(cid)
    expected = {_temporal_key(OATH, e) for e in near}
    assert expected <= set(sweep.model_only)
    assert _temporal_key(OATH, far) not in sweep.model_only
    assert _temporal_key(OATH, passed) not in sweep.model_only
    assert set(sweep.temporal_ids) == expected
    record = sweep.model_only[_temporal_key(OATH, near[0])]
    assert record["proposal"] is None and record["signals"]["in_days"] == 3
    assert not any("commitment:winifred-s-debt" in r["refs"] for r in sweep.model_only.values())
    assert not any("commitment:seraphine-s-vow" in r["refs"] for r in sweep.model_only.values())
    assert sweep.lifecycle_checked["temporal"] is True

    _broken_calendar(cid, tmp_path)
    sweep = _sweep(cid)
    assert sweep.temporal_ids == frozenset()
    assert not any(pending.is_temporal(r) for r in sweep.model_only.values())
    assert sweep.lifecycle_checked["temporal"] is False


def test_discovery_survives_a_garbled_ledger_and_a_raising_plugin(cid, s0, tmp_path):
    _ledger(cid, s0)
    _recover(cid, s0)
    clock.advance(cid, to="2026-05-10")
    (_root(cid) / "plot.json").write_text("{ no", encoding="utf-8")
    _broken_calendar(cid, tmp_path)

    sweep = _sweep(cid)
    assert not any(r.startswith("thread:") for rec in sweep.discovered.values()
                   for r in rec["refs"])
    assert sweep.lifecycle_checked == {"stale": False, "overdue": False, "temporal": False}


def _garble(cid, name):
    (_root(cid) / name).write_text("{ no", encoding="utf-8")


@pytest.mark.parametrize(("garbled", "unchecked"), [
    ("commitments.json", {"overdue", "temporal"}),
    ("events.json", {"overdue", "temporal"}),
    ("chronicle.json", {"stale"}),
    ("plot.json", {"stale"}),
])
def test_a_garbled_source_is_never_reported_checked(cid, garbled, unchecked):
    sid = _dated_scene(cid, "Saltmarch docks", "2026-05-01")
    _map(cid, sid)
    commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open",
                             "2026-05-05", "Mara swore it.", sid)
    events.create(cid, "The coronation", "2026-05-12")
    clock.advance(cid, to="2026-05-10")
    assert _sweep(cid).lifecycle_checked == {"stale": True, "overdue": True, "temporal": True}

    _garble(cid, garbled)
    checked = _sweep(cid).lifecycle_checked
    assert {source for source, ok in checked.items() if not ok} == unchecked


def test_model_only_nominations_are_verdict_filtered(cid, s0):
    clock.advance(cid, to="2026-05-10")
    _map(cid, s0)
    commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open",
                             "before the bells stop", "Mara swore it.", s0)
    dismissed, related, kept = ("event:" + events.create(cid, name, date) for name, date in (
        ("The coronation", "2026-05-13"), ("Saltmarch Eve", "2026-05-15"),
        ("Mara's audience", "2026-05-17")))
    _suppress(cid, "possible_relation", [OATH, dismissed])
    _link(cid, OATH, related, "related_to")
    _suppress(cid, "possible_thread_closure", [MAP], decision="keep_open")

    sweep = _sweep(cid, full=False, touched=[MAP])
    assert _temporal_key(OATH, dismissed) not in sweep.model_only
    assert _temporal_key(OATH, related) not in sweep.model_only
    assert _temporal_key(OATH, kept) in sweep.model_only
    assert canon.candidate_id("possible_thread_closure", [MAP]) not in sweep.model_only
    assert {_temporal_key(OATH, dismissed), _temporal_key(OATH, related)} <= sweep.temporal_ids


def test_a_malformed_continuity_file_discovers_nothing(cid, s0):
    _ledger(cid, s0)
    _recover(cid, s0)
    (_root(cid) / "continuity.json").write_text("{ no", encoding="utf-8")

    sweep = _sweep(cid)
    assert sweep.continuity == "malformed"
    assert sweep.discovered == {} and sweep.model_only == {}


def test_discovery_makes_no_store_write(cid):
    sid = _dated_scene(cid, "Saltmarch docks", "2026-05-01")
    _ledger(cid, sid)
    _recover(cid, sid)
    _map(cid, sid)
    commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open",
                             "before the bells stop", "Mara swore it.", sid)
    events.create(cid, "The coronation", "2026-07-20")
    clock.advance(cid, to="2026-07-15")
    _write_basis(cid, {})

    def snapshot():
        return {str(p): p.stat().st_mtime_ns for p in _root(cid).rglob("*") if p.is_file()}

    before = snapshot()
    sweep = _sweep(cid, full=False, touched=[MAP])
    assert sweep.discovered
    assert snapshot() == before


# --------------------------------------------------------------- embeddings
#
# One shared double, `llm_fakes.FakeEmbeddings`, installed over
# `similarity._CLIENT`; embeddings are turned on as
# `test_context_semantic.configure` does. The space is always asked of
# `embed_space.resolve()`, since it carries the connection's `rev`.


@pytest.fixture
def fake(monkeypatch):
    double = FakeEmbeddings()
    monkeypatch.setattr(similarity, "_CLIENT", double)
    return double


def _configure():
    conn = llm_connections.create_connection("openai_compatible", "Vectors",
                                             base_url="https://vectors.example/v1",
                                             api_key="sk-x", model="", post_process="none")
    config.write_config(embeddings_model="embed-1", embeddings_connection_id=conn)


def _vector_space():
    return embed_space.resolve()["space"]


def _chores(cid, scene, n=10):
    """`n` threads with distinct titles, refs ``thread:saltmarch-errand-<i>``."""
    for i in range(n):
        plot.set_movement(cid, f"saltmarch-errand-{i}", f"Saltmarch errand {i}", "open",
                          f"Errand {i} began.", scene)
    return {s.ref: s.text for s in similarity.pool(cid, "thread")}


def _embedded(cid, *, full=True, stamp="00000000000000000010-a"):
    return reconcile.discover(cid, stamp=stamp, full=full)


def _sent(double):
    return [text for call in double.calls for text in call]


def _load_spy(monkeypatch):
    calls = []
    real = vectors.load

    def spy(space, texts):
        calls.append(list(texts))
        return real(space, texts)

    monkeypatch.setattr(vectors, "load", spy)
    return calls


def _window_spy(monkeypatch):
    calls = []
    real = embed_space.warm_window

    def spy(uncached, seed, limit):
        got = real(uncached, seed, limit)
        calls.append({"uncached": list(uncached), "seed": seed, "limit": limit, "got": got})
        return got

    monkeypatch.setattr(embed_space, "warm_window", spy)
    return calls


def test_sweep_embeds_at_most_the_warm_limit(cid, s0, fake, monkeypatch):
    _configure()
    _chores(cid, s0)
    monkeypatch.setattr(reconcile, "RECONCILE_WARM_LIMIT", 4)
    loads = _load_spy(monkeypatch)

    sweep = _embedded(cid)
    assert 0 < len(_sent(fake)) <= 4
    assert len(loads) == 1
    assert sweep.embedding == "configured"
    assert (sweep.space, sweep.model) == (_vector_space(), "embed-1")

    _embedded(cid, stamp="00000000000000000020-b")
    assert len(loads) == 2                      # one read of the cache per sweep


def test_a_runs_passes_share_one_embedding_budget(cid, s0, monkeypatch):
    """§9.4: the cap and the `embeddings.TIMEOUT` window are the run's. A second
    discovery under the same `EmbedBudget` (a follow-on pass, §11.1) embeds
    nothing once the first spent the time, and that is no provider failure:
    what it left uncached rotates in on a later run."""
    _configure()
    _chores(cid, s0, n=3)
    monkeypatch.setattr(embeddings, "TIMEOUT", 0.2)

    def slow(text):
        time.sleep(0.15)                        # two texts spend the window
        return [1.0, 0.0]

    double = FakeEmbeddings(vector_for=slow)
    monkeypatch.setattr(similarity, "_CLIENT", double)
    budget = reconcile.EmbedBudget()
    reconcile.discover(cid, stamp="00000000000000000010-a", full=True, budget=budget)
    assert len(_sent(double)) == 2              # `warm_window`: a proper subset of three
    assert budget.left == reconcile.RECONCILE_WARM_LIMIT - 2
    assert budget.seconds <= 0

    plot.set_movement(cid, "saltmarch-errand-late", "Saltmarch late errand", "open",
                      "A late errand began.", s0)
    sweep = reconcile.discover(cid, stamp="00000000000000000020-b", full=True,
                               budget=budget)
    assert len(double.calls) == 1               # nothing sent past the run's window
    assert sweep.embedding == "configured"


def test_incremental_changed_texts_are_required(cid, s0, fake, monkeypatch):
    _configure()
    texts = _chores(cid, s0)
    monkeypatch.setattr(reconcile, "RECONCILE_WARM_LIMIT", 2)
    changed = "thread:saltmarch-errand-9"
    first = _sweep(cid)                         # embed=False: nothing cached yet
    _write_basis(cid, {**first.hashes, changed: "moved"}, space=first.space,
                 texts=first.text_hashes)
    uncached = [texts[ref] for ref in sorted(texts)]
    stamp = next(s for s in (f"0000000000000000001{i}-run" for i in range(10))
                 if texts[changed] not in embed_space.warm_window(
                     uncached, f"{cid}\0{s}", reconcile.RECONCILE_WARM_LIMIT))

    sweep = _embedded(cid, full=False, stamp=stamp)
    assert texts[changed] in fake.calls[0]
    assert len(_sent(fake)) <= 2
    assert sweep.rescored == {changed}


def test_warm_window_rotates_between_runs(cid, s0, monkeypatch):
    _configure()
    _chores(cid, s0)
    # A zero vector is never cached (`vectors.save`), so both sweeps see the
    # same ten uncached texts.
    double = FakeEmbeddings(vector_for=lambda t: [0.0, 0.0])
    monkeypatch.setattr(similarity, "_CLIENT", double)
    monkeypatch.setattr(reconcile, "RECONCILE_WARM_LIMIT", 4)
    windows = _window_spy(monkeypatch)
    one, two = "00000000000000000010-a", "00000000000000000020-b"
    assert (zlib.crc32(f"{cid}\0{one}".encode()) % 10
            != zlib.crc32(f"{cid}\0{two}".encode()) % 10)

    _embedded(cid, stamp=one)
    _embedded(cid, stamp=two)
    assert len(windows) == 2
    assert windows[0]["uncached"] == windows[1]["uncached"]
    assert len(windows[0]["uncached"]) == 10
    assert all(w["seed"].startswith(cid) for w in windows)
    assert windows[0]["got"] != windows[1]["got"]
    assert double.calls == [windows[0]["got"], windows[1]["got"]]


def test_per_chunk_saves_survive_a_later_failure(cid, s0, monkeypatch):
    _configure()
    _chores(cid, s0)
    monkeypatch.setattr(embeddings, "BATCH", 2)
    double = FakeEmbeddings(error=embeddings.EmbeddingsError("network", "x"), fail_after=1)
    monkeypatch.setattr(similarity, "_CLIENT", double)

    sweep = _embedded(cid)
    assert sweep.embedding == "failure"
    assert sweep.embedding_error == "network"
    [saved, _failed] = double.calls
    assert len(saved) == 2
    assert set(vectors.load(_vector_space(), saved)) == set(saved)


def test_embedding_failure_keeps_lexical_candidates(cid, s0, monkeypatch):
    _configure()
    _ledger(cid, s0)
    _recover(cid, s0)
    assert _signals(cid, LEDGER, RECOVER)["tokens"] >= similarity.TOKEN_FLOOR
    monkeypatch.setattr(similarity, "_CLIENT", FakeEmbeddings(
        error=embeddings.EmbeddingsError("network", "connection refused")))

    sweep = _embedded(cid)
    assert sweep.embedding == "failure"
    assert canon.candidate_id("possible_duplicate", [LEDGER, RECOVER]) in sweep.discovered
    rows = [r for r in errors.summary(campaign=cid)["rows"]
            if r.get("task") == "continuity-reconcile"]
    [row] = rows
    assert row["kind"] == "network"
    titles = (LEDGER_THREAD[1], LEDGER_THREAD[2], RECOVER_THE_LEDGER["title"],
              RECOVER_THE_LEDGER["beat"])
    for value in row.values():
        assert not any(title in str(value) for title in titles)


def test_off_width_vectors_are_forgotten(cid, s0, fake):
    _configure()
    texts = _chores(cid, s0, n=4)
    wide = texts["thread:saltmarch-errand-0"]
    vectors.save(_vector_space(), wide, [1.0, 0.0, 0.0])

    sweep = _embedded(cid)
    assert fake.calls and all(len(fake.vector_for(t)) == 2 for t in _sent(fake))
    assert vectors.load(_vector_space(), [wide]) == {}
    assert sweep.embedding == "configured"


def test_cosine_admits_a_pair_with_no_shared_words(cid, s0, fake):
    _configure()
    plot.set_movement(cid, "mara-s-map", "Mara's map", "open", "Stolen at dawn.", s0)
    plot.set_movement(cid, "winifred-s-chart", "Winifred's chart", "open", "Lost overboard.", s0)
    chart = "thread:winifred-s-chart"
    signals = _signals(cid, MAP, chart)
    assert signals["tokens"] < similarity.WEAK_TOKEN
    assert signals["chars"] < similarity.WEAK_CHAR
    texts = _subjects(cid)
    # One text cached, the other embedded by the sweep: both map to one vector.
    vectors.save(_vector_space(), texts[MAP].text, [1.0, 0.0])

    sweep = _embedded(cid)
    assert fake.calls == [[texts[chart].text]]
    record = sweep.discovered[canon.candidate_id("possible_duplicate", [MAP, chart])]
    assert record["signals"]["via"] == "semantic"
    assert isinstance(record["signals"]["cosine"], float)
    assert record["signals"]["cosine"] == pytest.approx(1.0)


def test_unconfigured_makes_no_embedding_call(cid, s0, fake):
    _ledger(cid, s0)
    _recover(cid, s0)
    sweep = _embedded(cid)
    assert fake.calls == []
    assert sweep.matching == "basic"
    assert sweep.embedding == "off"
    assert canon.candidate_id("possible_duplicate", [LEDGER, RECOVER]) in sweep.discovered


def test_a_vectorless_ref_is_not_rescored(cid, s0, fake, monkeypatch):
    """Decision 10: with a space configured, a ref left without a vector (past
    the warm limit here) is scored lexically but not reported rescored, so its
    basis entry stays and it is scored semantically once its vector exists."""
    _configure()
    texts = _chores(cid, s0, n=3)
    sweep = _embedded(cid)
    embedded = set(_sent(fake))
    assert len(embedded) == 2                   # the window is a proper subset
    assert sweep.pairs_scored == 3
    assert sweep.rescored == {ref for ref, text in texts.items() if text in embedded}


def test_an_embedding_failure_rescores_no_vectorless_ref(cid, s0, monkeypatch):
    _configure()
    _ledger(cid, s0)
    _recover(cid, s0)
    monkeypatch.setattr(similarity, "_CLIENT", FakeEmbeddings(
        error=embeddings.EmbeddingsError("network", "down")))
    sweep = _embedded(cid)
    assert sweep.embedding == "failure"
    assert sweep.pairs_scored == 1
    assert sweep.rescored == frozenset()


def test_an_unexpected_embedding_error_is_a_failure_not_a_raise(cid, s0, monkeypatch):
    _configure()
    _ledger(cid, s0)
    _recover(cid, s0)
    monkeypatch.setattr(similarity, "_CLIENT", FakeEmbeddings(error=RuntimeError("boom")))
    sweep = _embedded(cid)
    assert sweep.embedding == "failure"
    assert canon.candidate_id("possible_duplicate", [LEDGER, RECOVER]) in sweep.discovered
    assert [r["kind"] for r in errors.summary(campaign=cid)["rows"]
            if r.get("task") == "continuity-reconcile"] == ["unexpected"]


class _Refusing(FakeEmbeddings):
    """Refuses every chunk that holds `poison` -- a provider whose input limit
    that one text exceeds -- and answers every other chunk."""

    def __init__(self, poison):
        super().__init__()
        self.poison = poison

    def embed(self, texts, model, key, base_url, deadline=None):
        self.calls.append(list(texts))
        if self.poison in texts:
            raise embeddings.EmbeddingsError("bad_response", "refused")
        return [self.vector_for(t) for t in texts]


def _refused_letter(cid, s0, monkeypatch, *, moved=()):
    """Ten cached-nothing chores plus "Seraphine's letter", whose text the
    provider refuses; a basis current for every ref but the letter and
    `moved`. Returns the chores' texts, the letter's ref and the double."""
    _configure()
    texts = _chores(cid, s0)
    plot.set_movement(cid, "seraphine-s-letter", "Seraphine's letter", "open",
                      "Sealed and never sent.", s0)
    letter = "thread:seraphine-s-letter"
    poison = {s.ref: s.text for s in similarity.pool(cid, "thread")}[letter]
    double = _Refusing(poison)
    monkeypatch.setattr(similarity, "_CLIENT", double)
    first = _sweep(cid)                         # embed=False: nothing cached yet
    stale = {letter, *moved}
    _write_basis(cid, {r: ("moved" if r in stale else h) for r, h in first.hashes.items()},
                 space=first.space, texts=first.text_hashes)
    return texts, letter, double


def test_a_refused_changed_text_does_not_stop_the_warm_window(cid, s0, monkeypatch):
    """A changed ref's text the provider refuses costs that text, not the warm
    window: the window is embedded on the same sweep, and two sweeps cache
    every other text, though the letter stays required (and refused) on each."""
    texts, letter, _double = _refused_letter(cid, s0, monkeypatch)
    space = _vector_space()

    sweep = _embedded(cid, full=False, stamp="00000000000000000011-a")
    assert (sweep.embedding, sweep.embedding_error) == ("failure", "bad_response")
    assert len(vectors.load(space, list(texts.values()))) == 9    # a proper subset

    _embedded(cid, full=False, stamp="00000000000000000012-b")
    assert set(vectors.load(space, list(texts.values()))) == set(texts.values())
    assert letter not in sweep.rescored


def test_a_refused_changed_text_does_not_hold_back_the_other_changed_refs(
        cid, s0, monkeypatch):
    """The other changed refs sharing the refused text's chunk are reached by a
    retry over a rotating proper subset of the unsaved required texts, so a
    refused text does not keep every changed ref beside it vectorless (and so
    never rescored) on every incremental sweep."""
    mate = "thread:saltmarch-errand-0"
    texts, letter, double = _refused_letter(cid, s0, monkeypatch, moved=[mate])
    rescored: set[str] = set()
    # Stamps shaped like `reconcile.generation`'s: a run id that varies in
    # every bit, as a real one does (a crc32 offset over two texts is one
    # parity bit, which stamps differing in one digit's low bits never flip).
    for n in range(6):
        if mate in rescored:
            break
        run_id = hashlib.sha256(f"run-{n}".encode()).hexdigest()[:12]
        sweep = _embedded(cid, full=False, stamp=f"{20 + n:020d}-{run_id}")
        rescored |= sweep.rescored
    assert mate in rescored
    assert texts[mate] in vectors.load(_vector_space(), [texts[mate]])
    assert letter not in rescored
    # Every call sent at most the sweep's own texts: the retry never resends
    # the whole required set.
    assert all(len(call) <= 11 for call in double.calls)


def test_without_a_space_every_finished_ref_is_rescored(cid, s0):
    _ledger(cid, s0)
    _recover(cid, s0)
    sweep = _embedded(cid)
    assert sweep.embedding == "off"
    assert sweep.rescored == {LEDGER, RECOVER}


# ---------------------------------------------------------- pressure_by_ref


def test_pressure_by_ref_reads_commitment_deadlines_and_thread_staleness(cid):
    sid = _dated_scene(cid, "Saltmarch docks", "2026-05-01")
    _map(cid, sid)
    commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open",
                             "2026-07-10", "Mara swore it.", sid)
    clock.advance(cid, to="2026-07-15")

    out = reconcile.pressure_by_ref(cid)
    assert out[MAP] == {"state": "stale", "in_days": None, "friendly": ""}
    assert out[OATH]["state"] == "overdue" and out[OATH]["in_days"] == -5


def test_pressure_by_ref_is_soft(cid, s0, tmp_path):
    _map(cid, s0)
    (_root(cid) / "plot.json").write_text("{ no", encoding="utf-8")
    _broken_calendar(cid, tmp_path)
    assert isinstance(reconcile.pressure_by_ref(cid), dict)


# ----------------------------------------------------------------- persists
#
# The two persists (§11.1 steps 2 and 4, Decisions 4, 7 and 8): what a sweep
# found lands in the cache under the campaign lock, fenced on the run's
# generation, re-checked against the decisions continuity.json holds NOW, and
# never resurrecting a finding a reader settled between discovery and write.

PAIR = canon.candidate_id("possible_duplicate", [LEDGER, RECOVER])
CLOSE_MAP = canon.candidate_id("possible_thread_closure", [MAP])


def _cache(cid):
    return _root(cid) / "continuity_candidates.json"


FIRST = _stamp(10)


def _found(cid, scene, *, stamp=FIRST):
    _ledger(cid, scene)
    _recover(cid, scene)
    sweep = _sweep(cid, stamp=stamp)
    assert PAIR in sweep.discovered
    return sweep


def _proposal(decision="duplicate", *, frm="", to="", relation="", status="",
              reason="The same business.", evidence=()):
    return {"decision": decision, "from": frm, "to": to, "relation": relation,
            "status": status, "reason": reason, "evidence_scenes": list(evidence)}


def _records(cid):
    return candidates.read(cid)["records"]


def _verdicts(cid):
    return {key: v for key, _rec, v in pending.findings(cid)}


def test_persist_writes_discovered_and_bumps_the_token(cid, s0):
    sweep = _found(cid, s0)
    before = revision.current(cid)

    out = reconcile.persist_found(cid, sweep)
    assert out == {"written": True, "superseded": False, "gone": False, "cancelled": False,
                   "continuity": "ok", "candidates": len(sweep.discovered)}
    stored = candidates.read(cid)
    assert stored["generation"] == sweep.stamp
    assert stored["generated"]
    assert set(stored["records"]) == set(sweep.discovered)
    assert stored["records"][PAIR]["fingerprint"] == sweep.discovered[PAIR]["fingerprint"]
    assert revision.current(cid) != before


def test_a_persist_after_a_dismiss_does_not_resurrect(cid, s0):
    sweep = _found(cid, s0)
    _suppress(cid, "possible_duplicate", sweep.discovered[PAIR]["refs"])

    reconcile.persist_found(cid, sweep)
    assert PAIR not in _records(cid)


def test_a_persist_after_a_merge_drops_the_pair(cid, s0):
    sweep = _found(cid, s0)
    review.create_alias(cid, RECOVER, LEDGER)
    current = pending.Current.load(cid)
    assert pending.verdict(current, sweep.discovered[PAIR]) == "gone"

    reconcile.persist_found(cid, sweep)
    assert PAIR not in _records(cid)


def test_a_persist_after_a_link_drops_the_pair(cid, s0):
    sweep = _found(cid, s0)
    _link(cid, LEDGER, RECOVER, "related_to")
    assert pending.verdict(pending.Current.load(cid), sweep.discovered[PAIR]) == "satisfied"

    reconcile.persist_found(cid, sweep)
    assert PAIR not in _records(cid)


def test_a_record_changed_since_discovery_is_dropped(cid, s0):
    sweep = _found(cid, s0)
    plot.set_movement(cid, "recover-the-harbour-ledger", "Recover the Saltmarch harbour ledger",
                      "", "", s0)
    assert pending.verdict(pending.Current.load(cid), sweep.discovered[PAIR]) == "stale"

    reconcile.persist_found(cid, sweep)
    assert PAIR not in _records(cid)


def test_cached_proposals_carry_forward_when_the_fingerprint_holds(cid, s0):
    sweep = _found(cid, s0)
    reconcile.persist_found(cid, sweep)
    proposal = _proposal(frm=RECOVER, to=LEDGER)
    reconcile.persist_proposals(cid, sweep, {PAIR: proposal})
    created = _records(cid)[PAIR]["created"]

    again = _sweep(cid, stamp=_stamp(11))
    assert again.discovered[PAIR]["proposal"] is None
    reconcile.persist_found(cid, again)
    record = _records(cid)[PAIR]
    assert record["proposal"] == proposal
    assert record["created"] == created


def test_cached_proposals_are_dropped_when_it_moved(cid, s0):
    sweep = _found(cid, s0)
    reconcile.persist_found(cid, sweep)
    reconcile.persist_proposals(cid, sweep, {PAIR: _proposal(frm=RECOVER, to=LEDGER)})
    plot.set_movement(cid, "recover-the-harbour-ledger", "", "closed", "", s0)

    again = _sweep(cid, stamp=_stamp(11))
    assert again.discovered[PAIR]["fingerprint"] != sweep.discovered[PAIR]["fingerprint"]
    reconcile.persist_found(cid, again)
    record = _records(cid)[PAIR]
    assert record["proposal"] is None
    assert record["fingerprint"] == again.discovered[PAIR]["fingerprint"]


def test_a_newer_generation_supersedes_an_older_run(cid, s0):
    sweep = _found(cid, s0, stamp=reconcile.generation("a"))
    newer = reconcile.generation("b")
    assert sweep.stamp < newer <= f"{time.time_ns():020d}~"
    data = candidates.empty()
    data["generation"] = newer
    candidates.write(cid, data)
    before, token = _cache(cid).read_bytes(), revision.current(cid)

    out = reconcile.persist_found(cid, sweep)
    assert out["superseded"] is True and out["written"] is False
    out = reconcile.persist_proposals(cid, sweep, {PAIR: _proposal(frm=RECOVER, to=LEDGER)})
    assert out["superseded"] is True and out["written"] is False
    assert _cache(cid).read_bytes() == before
    assert revision.current(cid) == token


@pytest.mark.parametrize("stored", ["future", "not-a-generation"])
def test_a_future_dated_generation_does_not_fence_forever(cid, s0, stored):
    sweep = _found(cid, s0, stamp=reconcile.generation("a"))
    data = candidates.empty()
    if stored == "future":
        data["generation"] = f"{time.time_ns() + 3_600 * 10**9:020d}-x"
    else:
        # Sorts between this run's stamp and the clock, but is not a stamp.
        data["generation"] = f"{time.time_ns():020d}~x"
        assert sweep.stamp < data["generation"] <= f"{time.time_ns():020d}~"
    candidates.write(cid, data)

    out = reconcile.persist_found(cid, sweep)
    assert out["written"] is True and out["superseded"] is False
    assert candidates.read(cid)["generation"] == sweep.stamp


def test_persist_two_never_resurrects_an_applied_candidate(cid, s0):
    sweep = _found(cid, s0)
    reconcile.persist_found(cid, sweep)
    assert candidates.drop(cid, PAIR) is not None

    reconcile.persist_proposals(cid, sweep, {PAIR: _proposal(frm=RECOVER, to=LEDGER)})
    assert PAIR not in _records(cid)


def test_persist_two_adds_model_only_with_a_proposal_and_marks_declined_settled(cid, s0):
    clock.advance(cid, to="2026-05-10")
    _map(cid, s0)
    plot.set_movement(cid, "seraphine-s-letter", "Seraphine's letter", "open",
                      "Seraphine sealed it.", s0)
    letter = "thread:seraphine-s-letter"
    commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open",
                             "before the bells stop", "Mara swore it.", s0)
    coronation, eve = ("event:" + events.create(cid, name, date) for name, date in (
        ("The coronation", "2026-05-13"), ("Saltmarch Eve", "2026-05-15")))
    sweep = _sweep(cid, full=False, touched=[MAP, letter])
    dated, declined = _temporal_key(OATH, coronation), _temporal_key(OATH, eve)
    closing = canon.candidate_id("possible_thread_closure", [letter])
    assert {dated, declined, CLOSE_MAP, closing} <= set(sweep.model_only)

    reconcile.persist_found(cid, sweep)
    assert not set(sweep.model_only) & set(_records(cid))      # never without a proposal

    # The letter moves after discovery: its nomination no longer means what
    # the model was asked, so it is not added.
    plot.set_movement(cid, "seraphine-s-letter", "", "", "Seraphine burned it.", s0)
    out = reconcile.persist_proposals(cid, sweep, {
        dated: _proposal("before", frm=OATH, to=coronation, relation="before"),
        declined: _proposal("unrelated", reason=""),
        CLOSE_MAP: _proposal("keep_open", reason="Still unfinished."),
        closing: _proposal("close", status="closed", evidence=[s0]),
    })
    assert out["written"] is True
    verdicts = _verdicts(cid)
    assert {key: verdicts.get(key) for key in (dated, declined, CLOSE_MAP, closing)} == {
        dated: "live", declined: "settled", CLOSE_MAP: "settled", closing: None}
    assert out["candidates"] == sum(v == "live" for v in verdicts.values())


def test_stillborn_and_deleted_campaign_write_nothing(cid, s0):
    sweep = _found(cid, s0)
    proposals = {PAIR: _proposal(frm=RECOVER, to=LEDGER)}
    for out in (reconcile.persist_found(cid, sweep, stillborn=lambda: True),
                reconcile.persist_proposals(cid, sweep, proposals, stillborn=lambda: True)):
        assert out["cancelled"] is True and out["written"] is False
    assert not _cache(cid).exists()

    root = _root(cid)
    campaigns.delete_campaign(cid)
    for out in (reconcile.persist_found(cid, sweep),
                reconcile.persist_proposals(cid, sweep, proposals)):
        assert out["gone"] is True and out["written"] is False
    assert not root.exists()


def test_basis_keeps_old_hashes_for_unscored_refs(cid, s0, monkeypatch):
    refs = _five(cid, s0)
    deleted = "thread:winifred-s-lost-errand"
    old = {ref: f"old-{ref}" for ref in [*refs, deleted]}
    scored = {ref: _stamp(1) for ref in [*refs, deleted]}
    texts = {ref: f"text-{ref}" for ref in [*refs, deleted]}
    _write_basis(cid, old, scored=scored, texts=texts)
    monkeypatch.setattr(reconcile, "RECONCILE_MAX_PAIRS", 5)

    sweep = _sweep(cid, stamp=_stamp(2))
    assert sweep.pairs_capped is True and sweep.rescored == {refs[0]}
    reconcile.persist_found(cid, sweep)
    basis = candidates.read(cid)["basis"]
    assert basis["identity_hashes"][refs[0]] == sweep.hashes[refs[0]]
    assert basis["text_hashes"][refs[0]] == sweep.text_hashes[refs[0]]
    assert basis["scored"][refs[0]] == sweep.stamp
    for ref in refs[1:]:
        assert basis["identity_hashes"][ref] == old[ref]
        assert basis["text_hashes"][ref] == texts[ref]
        assert basis["scored"][ref] == scored[ref]
    # A ref that no longer exists is not carried.
    for key in ("identity_hashes", "text_hashes", "scored"):
        assert deleted not in basis[key]


def test_embedding_failure_keeps_prior_semantic_pairs_and_rescores_next_time(
        cid, s0, fake, monkeypatch):
    _configure()
    plot.set_movement(cid, "mara-s-map", "Mara's map", "open", "Stolen at dawn.", s0)
    plot.set_movement(cid, "winifred-s-chart", "Winifred's chart", "open", "Lost overboard.", s0)
    chart = "thread:winifred-s-chart"
    key = canon.candidate_id("possible_duplicate", [MAP, chart])
    # A full sweep warms a proper subset of the uncached texts: cache one.
    vectors.save(_vector_space(), _subjects(cid)[MAP].text, [1.0, 0.0])
    sweep = _embedded(cid, stamp=_stamp(10))
    assert sweep.discovered[key]["signals"]["via"] == "semantic"
    assert sweep.rescored == {MAP, chart}
    reconcile.persist_found(cid, sweep)
    before = candidates.read(cid)["basis"]

    _map(cid, s0, beat="Mara traced the coast on it.")
    monkeypatch.setattr(similarity, "_CLIENT", FakeEmbeddings(
        error=embeddings.EmbeddingsError("network", "down")))
    sweep = _embedded(cid, full=False, stamp=_stamp(11))
    assert sweep.embedding == "failure" and MAP not in sweep.rescored
    reconcile.persist_found(cid, sweep)
    stored = candidates.read(cid)
    assert key in stored["records"]
    for part in ("identity_hashes", "scored", "text_hashes"):
        assert stored["basis"][part][MAP] == before[part][MAP]

    monkeypatch.setattr(similarity, "_CLIENT", FakeEmbeddings())
    sweep = _embedded(cid, full=False, stamp=_stamp(12))
    assert MAP in sweep.rescored
    assert sweep.discovered[key]["signals"]["via"] == "semantic"


def test_warm_overflow_refs_are_not_marked_scored(cid, s0, fake, monkeypatch):
    _configure()
    texts = _chores(cid, s0, n=5)
    monkeypatch.setattr(reconcile, "RECONCILE_WARM_LIMIT", 2)

    sweep = _embedded(cid, full=False, stamp=_stamp(10))
    assert len(sweep.rescored) == 2
    reconcile.persist_found(cid, sweep)
    basis = candidates.read(cid)["basis"]
    waiting = set(texts) - sweep.rescored
    assert len(waiting) == 3
    for part in ("identity_hashes", "scored", "text_hashes"):
        assert set(basis[part]) == set(sweep.rescored)

    sweep = _embedded(cid, full=False, stamp=_stamp(11))
    assert sweep.rescored < waiting and len(sweep.rescored) == 2
    reconcile.persist_found(cid, sweep)
    last = waiting - sweep.rescored
    sweep = _embedded(cid, full=False, stamp=_stamp(12))
    assert sweep.rescored == last
    reconcile.persist_found(cid, sweep)
    assert set(candidates.read(cid)["basis"]["scored"]) == set(texts)


def _no_longer_found(monkeypatch, how):
    """Make a cached pair one a sweep that asks again will not rediscover:
    ``implausible`` (no clause admits it) or ``ranked_out`` (still plausible,
    but in no endpoint's top k)."""
    if how == "implausible":
        monkeypatch.setattr(similarity, "admitted_by", lambda signals: None)
    else:
        monkeypatch.setattr(reconcile, "RECONCILE_TOP_K", 0)


@pytest.mark.parametrize("how", ["implausible", "ranked_out"])
def test_a_rescored_pair_that_is_not_rediscovered_is_retracted(cid, s0, monkeypatch, how):
    reconcile.persist_found(cid, _found(cid, s0))
    _no_longer_found(monkeypatch, how)

    sweep = _sweep(cid, stamp=_stamp(11))
    assert {LEDGER, RECOVER} <= sweep.rescored and PAIR not in sweep.discovered
    reconcile.persist_found(cid, sweep)
    assert PAIR not in _records(cid)


@pytest.mark.parametrize("how", ["implausible", "ranked_out"])
def test_a_pair_no_sweep_asked_again_is_kept(cid, s0, monkeypatch, how):
    reconcile.persist_found(cid, _found(cid, s0))
    _no_longer_found(monkeypatch, how)

    sweep = _sweep(cid, full=False, stamp=_stamp(11))   # nothing changed
    assert sweep.rescored == frozenset() and PAIR not in sweep.discovered
    reconcile.persist_found(cid, sweep)
    assert PAIR in _records(cid)


def test_an_unreadable_ledger_keeps_its_findings(cid, s0):
    """Decision 2: a ledger a persist cannot read is never a reason to throw a
    finding (or the model work it carries) away."""
    reconcile.persist_found(cid, _found(cid, s0))
    proposal = _proposal(frm=RECOVER, to=LEDGER)
    reconcile.persist_proposals(cid, _sweep(cid, stamp=_stamp(10)), {PAIR: proposal})
    before = candidates.read(cid)
    (_root(cid) / "plot.json").write_text("{ no", encoding="utf-8")
    assert _verdicts(cid)[PAIR] == "unknown"

    reconcile.persist_found(cid, _sweep(cid, stamp=_stamp(11)))
    after = candidates.read(cid)
    assert after["records"][PAIR] == before["records"][PAIR]
    assert after["records"][PAIR]["proposal"] == proposal
    for ref in (LEDGER, RECOVER):
        assert (after["basis"]["identity_hashes"][ref]
                == before["basis"]["identity_hashes"][ref])


def test_a_full_refresh_during_an_outage_keeps_prior_semantic_pairs(
        cid, s0, fake, monkeypatch):
    """Decision 10 and §26 on a full sweep: the endpoint that still has its
    vector is rescored, its partner (moved, no vector) is not, and the
    semantic pair cannot be rediscovered without both vectors -- so it is
    kept, with its proposal, rather than retracted on half the evidence."""
    _configure()
    plot.set_movement(cid, "mara-s-map", "Mara's map", "open", "Stolen at dawn.", s0)
    plot.set_movement(cid, "winifred-s-chart", "Winifred's chart", "open", "Lost overboard.", s0)
    chart = "thread:winifred-s-chart"
    key = canon.candidate_id("possible_duplicate", [MAP, chart])
    vectors.save(_vector_space(), _subjects(cid)[MAP].text, [1.0, 0.0])
    sweep = _embedded(cid, stamp=_stamp(10))
    assert sweep.discovered[key]["signals"]["via"] == "semantic"
    reconcile.persist_found(cid, sweep)
    proposal = _proposal(frm=chart, to=MAP)
    reconcile.persist_proposals(cid, sweep, {key: proposal})

    plot.set_movement(cid, "winifred-s-chart", "", "", "Winifred found it in the hold.", s0)
    monkeypatch.setattr(similarity, "_CLIENT", FakeEmbeddings(
        error=embeddings.EmbeddingsError("network", "down")))
    sweep = _embedded(cid, stamp=_stamp(11))
    assert sweep.embedding == "failure"
    assert MAP in sweep.rescored and chart not in sweep.rescored
    assert key not in sweep.discovered
    reconcile.persist_found(cid, sweep)
    assert _records(cid)[key]["proposal"] == proposal


def _lantern(cid, scene):
    """Five near-identical errands and Mara's lantern, whose one close
    neighbour is r1: the r1/lantern pair is in the lantern's top k, not r1's."""
    for i in range(1, 6):
        plot.set_movement(cid, f"r{i}", f"Saltmarch errand number {i}", "open",
                          f"Winifred ran errand {i} at the docks.", scene)
    plot.set_movement(cid, "lantern", "Mara's lantern", "open",
                      "Winifred ran errand 1 with the lantern.", scene)
    return "thread:r1", "thread:lantern"


def test_an_incremental_sweep_keeps_a_pair_only_its_unchanged_endpoint_ranked(cid, s0):
    """An End Scene re-ranks only the ref it changed. A pair kept by its
    unchanged endpoint's top k is not asked again by that sweep, so it is
    kept (with its proposal) rather than retracted until the next Refresh
    finds it again with no proposal and pays to adjudicate it again."""
    r1, lantern = _lantern(cid, s0)
    key = canon.candidate_id("possible_duplicate", [r1, lantern])
    sweep = _sweep(cid, stamp=_stamp(10))
    assert key in sweep.discovered
    reconcile.persist_found(cid, sweep)
    proposal = _proposal(frm=lantern, to=r1)
    reconcile.persist_proposals(cid, sweep, {key: proposal})

    plot.set_movement(cid, "r1", "", "", "Winifred ran errand 1 again at the docks.", s0)
    sweep = _sweep(cid, full=False, touched=[r1], stamp=_stamp(11))
    assert sweep.rescored == {r1} and key not in sweep.discovered
    reconcile.persist_found(cid, sweep)
    assert _records(cid)[key]["proposal"] == proposal

    assert key in _sweep(cid, stamp=_stamp(12)).discovered   # a Refresh finds it too


def test_an_incremental_sweep_retracts_a_pair_its_changed_endpoint_no_longer_matches(
        cid, s0, monkeypatch):
    """The changed endpoint scored the pair with every signal it has (no
    space configured) and no clause admits it: that sweep asked and did not
    find, so the pair goes without waiting for a Refresh."""
    reconcile.persist_found(cid, _found(cid, s0))
    plot.set_movement(cid, "recover-the-harbour-ledger", "", "", "Mara asked after it again.",
                      s0)                               # a new beat: RECOVER moved
    monkeypatch.setattr(similarity, "admitted_by", lambda signals: None)

    sweep = _sweep(cid, full=False, stamp=_stamp(11))
    assert sweep.rescored == {RECOVER} and PAIR not in sweep.discovered
    reconcile.persist_found(cid, sweep)
    assert PAIR not in _records(cid)


@pytest.mark.parametrize("change", ["rewound", "broken_calendar"])
def test_a_lifecycle_finding_is_retracted_when_its_condition_clears(cid, tmp_path, change):
    sid = _dated_scene(cid, "Saltmarch docks", "2026-05-01")
    _map(cid, sid)
    later = _dated_scene(cid, "Saltmarch quay", "2026-07-10")
    plot.set_movement(cid, "seraphine-s-letter", "Seraphine's letter", "open",
                      "Seraphine sealed it.", later)
    letter = "thread:seraphine-s-letter"
    clock.advance(cid, to="2026-07-15")
    sweep = _sweep(cid, stamp=_stamp(10), full=False, touched=[letter])
    assert sweep.discovered[CLOSE_MAP]["signals"]["reason"] == "stale"
    touched = canon.candidate_id("possible_thread_closure", [letter])
    assert touched in sweep.model_only and touched not in sweep.discovered
    reconcile.persist_found(cid, sweep)
    reconcile.persist_proposals(cid, sweep, {touched: _proposal(
        "close", status="closed", reason="Sealed and sent.", evidence=[sid])})
    assert {CLOSE_MAP, touched} <= set(_records(cid))
    assert _records(cid)[touched]["signals"]["reason"] == "touched"

    if change == "rewound":
        clock.advance(cid, to="2026-05-02")
    else:
        _broken_calendar(cid, tmp_path)
    sweep = _sweep(cid, stamp=_stamp(11))
    assert CLOSE_MAP not in sweep.discovered
    assert sweep.lifecycle_checked["stale"] is (change == "rewound")
    reconcile.persist_found(cid, sweep)
    records = _records(cid)
    assert (CLOSE_MAP in records) is (change == "broken_calendar")
    assert touched in records                   # a touched finding is never retracted so


@pytest.mark.parametrize("change", ["event_passed", "linked_elsewhere", "broken_calendar"])
def test_a_temporal_finding_is_retracted_when_its_condition_clears(cid, s0, tmp_path, change):
    clock.advance(cid, to="2026-05-10")
    commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open",
                             "before the bells stop", "Mara swore it.", s0)
    coronation, eve = ("event:" + events.create(cid, name, date) for name, date in (
        ("The coronation", "2026-05-13"), ("Saltmarch Eve", "2026-05-15")))
    key = _temporal_key(OATH, coronation)
    sweep = _sweep(cid, stamp=_stamp(10))
    assert key in sweep.model_only
    reconcile.persist_found(cid, sweep)
    reconcile.persist_proposals(cid, sweep, {key: _proposal(
        "before", frm=OATH, to=coronation, relation="before")})
    assert _verdicts(cid)[key] == "live"

    if change == "event_passed":
        clock.advance(cid, to="2026-05-20")
    elif change == "linked_elsewhere":
        _link(cid, OATH, eve, "before")
    else:
        _broken_calendar(cid, tmp_path)
    assert _verdicts(cid)[key] == "live"         # nothing in the fingerprint moved
    sweep = _sweep(cid, stamp=_stamp(11))
    assert sweep.lifecycle_checked["temporal"] is (change != "broken_calendar")
    reconcile.persist_found(cid, sweep)
    assert (key in _records(cid)) is (change == "broken_calendar")


def test_a_renamed_evidence_scene_is_readjudicated_not_stuck(cid):
    sid = _dated_scene(cid, "Saltmarch docks", "2026-05-01")
    _map(cid, sid)
    clock.advance(cid, to="2026-07-15")
    sweep = _sweep(cid, stamp=_stamp(10))
    reconcile.persist_found(cid, sweep)
    reconcile.persist_proposals(cid, sweep, {CLOSE_MAP: _proposal(
        "close", status="closed", reason="Mara found it.", evidence=[sid])})
    assert _records(cid)[CLOSE_MAP]["proposal"]["evidence_scenes"] == [sid]
    assert CLOSE_MAP not in {s["id"] for s in reconcile.select(cid, sweep)}

    assert scenes.rename_scene(cid, sid, "Saltmarch quay") != sid
    sweep = _sweep(cid, stamp=_stamp(11))
    reconcile.persist_found(cid, sweep)
    assert _records(cid)[CLOSE_MAP]["proposal"] is None
    assert CLOSE_MAP in {s["id"] for s in reconcile.select(cid, sweep)}


def test_a_malformed_continuity_file_leaves_the_cache_untouched(cid, s0):
    sweep = _found(cid, s0)
    candidates.write(cid, candidates.empty())
    before, token = _cache(cid).read_bytes(), revision.current(cid)
    (_root(cid) / "continuity.json").write_text("{ no", encoding="utf-8")

    for out in (reconcile.persist_found(cid, sweep),
                reconcile.persist_proposals(cid, sweep, {PAIR: _proposal(frm=RECOVER,
                                                                         to=LEDGER)})):
        assert out["written"] is False and out["continuity"] == "malformed"
    assert _cache(cid).read_bytes() == before
    assert revision.current(cid) == token


def test_a_persist_that_changes_nothing_moves_no_token(cid, s0):
    sid = _dated_scene(cid, "Saltmarch quay", "2026-05-01")
    _map(cid, sid)
    clock.advance(cid, to="2026-07-15")
    assert reconcile.persist_found(cid, _found(cid, s0))["written"] is True
    token, mtime = revision.current(cid), _cache(cid).stat().st_mtime_ns

    out = reconcile.persist_found(cid, _sweep(cid, stamp=_stamp(11)))
    assert out["written"] is False and out["superseded"] is False
    assert out["candidates"] == len(_records(cid))
    assert revision.current(cid) == token
    assert _cache(cid).stat().st_mtime_ns == mtime


def test_a_capped_sweep_that_rotates_still_writes(cid, s0, monkeypatch):
    """`basis.scored` is compared by the order it gives, not by its stamps --
    but a capped sweep moves the ref it finished to the back of that order,
    and the next sweep must see that or rescore the same ref for ever
    (Review Focus 3)."""
    refs = _five(cid, s0)
    full = _sweep(cid, stamp=_stamp(1))
    _write_basis(cid, full.hashes, scored=dict.fromkeys(refs, _stamp(1)),
                 texts=full.text_hashes)
    monkeypatch.setattr(reconcile, "RECONCILE_MAX_PAIRS", 5)
    for i, ref in enumerate(refs):
        sweep = _sweep(cid, stamp=_stamp(10 + i))
        assert sweep.rescored == {ref}
        reconcile.persist_found(cid, sweep)
        assert candidates.read(cid)["basis"]["scored"][ref] == sweep.stamp


def test_persists_hold_the_campaign_lock(cid, s0, monkeypatch):
    sweep = _found(cid, s0)
    monkeypatch.setattr(locks, "LOCK_TIMEOUT", 0.2)
    held, release = threading.Event(), threading.Event()

    def holder():
        with locks.campaign_lock(cid):
            held.set()
            release.wait(10)

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    try:
        assert held.wait(10), "holder thread never took the lock"
        with pytest.raises(locks.StoreBusy):
            reconcile.persist_found(cid, sweep)
        with pytest.raises(locks.StoreBusy):
            reconcile.persist_proposals(cid, sweep, {PAIR: _proposal()})
    finally:
        release.set()
        t.join(10)
    assert not _cache(cid).exists()


def test_a_malformed_cache_is_overwritten(cid, s0):
    sweep = _found(cid, s0)
    _cache(cid).write_text("{ no", encoding="utf-8")
    assert candidates.malformed(cid)

    assert reconcile.persist_found(cid, sweep)["written"] is True
    assert not candidates.malformed(cid)
    assert PAIR in _records(cid)


def test_a_malformed_cache_is_overwritten_by_a_sweep_that_found_nothing(cid):
    _cache(cid).write_text("{ no", encoding="utf-8")
    out = reconcile.persist_found(cid, reconcile.Sweep(_stamp(10), True))
    assert out["written"] is True
    assert not candidates.malformed(cid)


# ------------------------------------------------------------- sweep cost
#
# §25.1 and §25.2 (Slice G Decisions 5 and 6): a sweep builds each record's
# identity text once, and a text the provider answers with no direction costs
# one re-send per sweep, inside the warm limit, and holds back nothing else.


def test_a_sweep_builds_each_identity_text_once(cid, s0, monkeypatch):
    _ledger(cid, s0)
    _recover(cid, s0)
    _tithe(cid, s0)
    _map(cid, s0)
    commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open",
                             "before the bells stop", "Mara swore it.", s0)
    calls = {"thread": 0, "commitment": 0}
    for kind in calls:
        real = similarity._TEXT[kind]

        def counted(record, _real=real, _kind=kind):
            calls[_kind] += 1
            return _real(record)

        monkeypatch.setitem(similarity._TEXT, kind, counted)

    sweep = _sweep(cid)

    assert sweep.pairs_scored > 0
    assert calls == {"thread": 4, "commitment": 1}


def test_a_zero_vector_text_costs_one_resend_per_sweep(cid, s0, monkeypatch):
    """Decision 5: `vectors.save` never caches a vector with no direction, so a
    changed ref whose text the provider answers with zeros stays required and
    is sent again on the next sweep -- once, within the warm limit -- while the
    other changed ref is embedded, cached and rescored on the first."""
    _configure()
    _chores(cid, s0)
    zero, other = "thread:saltmarch-errand-3", "thread:saltmarch-errand-7"
    double = FakeEmbeddings(
        vector_for=lambda t: [0.0, 0.0] if "Saltmarch errand 3\n" in t else [1.0, 0.0])
    monkeypatch.setattr(similarity, "_CLIENT", double)
    warmed = _embedded(cid)                     # caches every text but the zero one
    _persisted(cid, warmed)
    plot.set_movement(cid, "saltmarch-errand-3", "", "advanced", "Errand 3 went astray.", s0)
    plot.set_movement(cid, "saltmarch-errand-7", "", "advanced", "Errand 7 reached the gate.",
                      s0)
    texts = {s.ref: s.text for s in similarity.pool(cid, "thread")}

    sweeps = []
    for stamp in ("00000000000000000020-b", "00000000000000000030-c"):
        before = len(double.calls)
        sweeps.append(_embedded(cid, full=False, stamp=stamp))
        sent = [text for call in double.calls[before:] for text in call]
        assert sent.count(texts[zero]) == 1, stamp
        assert len(sent) <= reconcile.RECONCILE_WARM_LIMIT
    assert other in sweeps[0].rescored
    assert zero not in sweeps[0].rescored
    assert vectors.load(_vector_space(), [texts[zero]]) == {}
