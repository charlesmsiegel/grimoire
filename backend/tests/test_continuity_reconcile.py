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

import pytest

from grimoire.store import (
    calendars,
    campaigns,
    clock,
    commitments,
    events,
    plot,
    scenes,
    worlds,
)
from grimoire.store.campaigns import paths as campaigns_paths
from grimoire.store.continuity import candidates, canon, doc, pending, reconcile, similarity
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


def _write_basis(cid, hashes, *, scored=None, space=""):
    data = candidates.empty()
    data["basis"] = {"embedding_space": space, "embedding_model": "",
                     "identity_hashes": dict(hashes), "scored": dict(scored or {})}
    candidates.write(cid, data)


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
    _write_basis(cid, _sweep(cid).hashes)
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
    _write_basis(cid, old, space="old")
    _space(monkeypatch, "new")
    monkeypatch.setattr(reconcile, "RECONCILE_MAX_PAIRS", 5)

    sweep = _sweep(cid, full=False, stamp=_stamp(2))
    assert sweep.space == "new"
    assert sweep.rescored == {refs[0]}
    assert sweep.model_only == {}           # every hash moved: none of it is "touched"
    hashes = {**old, refs[0]: sweep.hashes[refs[0]]}
    _write_basis(cid, hashes, scored={refs[0]: sweep.stamp}, space="new")

    calls = _counting(monkeypatch)
    sweep = _sweep(cid, full=False, stamp=_stamp(3))
    assert calls[0][0] == refs[1]
    assert sweep.rescored == {refs[1]}


def test_touched_includes_refs_moved_since_the_last_sweep(cid, s0):
    _map(cid, s0)
    _ledger(cid, s0)
    _write_basis(cid, _sweep(cid).hashes)
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
    _write_basis(cid, _sweep(cid).hashes)
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
