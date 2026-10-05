"""`store/continuity/pending.py` -- what a cached finding means NOW (Decision 1).

The candidate cache records what a sweep found; whether that finding is still
worth a reader's attention is a question about the current ledgers and
continuity.json, asked by the GET, Todo, apply and both persists. This module
is the one place that answers it, so the tests here pin the current
fingerprint (§5.5, §28.1), the verdict order (Decision 2), and the two readers
built over them -- `rows` and `findings` -- against the shapes a hand edit can
leave behind.
"""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app
from grimoire.store import scene_refs
from grimoire.store.campaigns import paths as campaigns_paths
from grimoire.store.continuity import candidates, canon, doc, effective, pending

S1, S2, S3, S4 = "001--saltmarch", "002--realm", "003--winifred", "004--seraphine"
MAP, CHART, LETTER = "thread:maras-map", "thread:winifreds-chart", "thread:seraphines-letter"
OATH = "commitment:maras-oath"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def cid(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    store.plot.set_movement(cid, "maras-map", "Mara's map", "open", "The map was stolen.", S1)
    store.plot.set_movement(cid, "winifreds-chart", "Winifred's chart", "open",
                            "Winifred lost it.", S2)
    store.commitments.set_movement(cid, "maras-oath", "Mara's oath", "promise", "open",
                                   "", "She swore it.", S1)
    return cid


def _root(cid):
    return campaigns_paths.campaign_root(cid)


def _alias(cid, src, to):
    doc.put_alias(cid, src, {"to": to, "created": "", "source": "manual", "note": ""})


def _link(cid, a, b, relation):
    doc.put_link(cid, canon.link_id(relation, a, b),
                 {"a": a, "b": b, "relation": relation, "created": "", "scene": "", "note": ""})


def _event(cid, name="The coronation", date="2026-05-09"):
    return "event:" + store.events.create(cid, name, date, "")


def _record(cid, kind, refs, *, fingerprint=None, proposal=None, signals=None):
    """A cached record whose stored fingerprint is the current one, unless given."""
    if fingerprint is None:
        fingerprint = pending.fingerprint(pending.Current.load(cid), kind, refs) or "fp1_none"
    return {"kind": kind, "refs": list(refs), "fingerprint": fingerprint,
            "signals": signals or {}, "proposal": proposal, "created": ""}


def _proposal(decision, **over):
    base = {"decision": decision, "from": "", "to": "", "relation": "", "status": "",
            "reason": "", "evidence_scenes": []}
    base.update(over)
    return base


# ------------------------------------------------------------- later_scene


@pytest.mark.parametrize(("stored", "other", "expected"), [
    ("003--a", "005--b", "005--b"),
    ("005--b", "003--a", "005--b"),
    ("", "003--a", "003--a"),
    ("x", "y", "x"),
])
def test_later_scene_by_play_order(stored, other, expected):
    assert effective.later_scene(stored, other) == expected


# ------------------------------------------------------------ fingerprints


def test_pair_fingerprint_ignores_a_new_beat(cid):
    before = pending.fingerprint(pending.Current.load(cid), "possible_duplicate", [MAP, CHART])
    store.plot.set_movement(cid, "maras-map", "", "", "Mara found a page.", S3)
    after = pending.fingerprint(pending.Current.load(cid), "possible_duplicate", [MAP, CHART])
    assert before is not None and before == after


def test_lifecycle_fingerprint_changes_with_the_latest_beat(cid):
    kind = "possible_thread_closure"
    before = pending.fingerprint(pending.Current.load(cid), kind, [MAP])
    store.plot.set_movement(cid, "maras-map", "", "", "Mara found a page.", S3)
    after = pending.fingerprint(pending.Current.load(cid), kind, [MAP])
    assert before is not None and after is not None and before != after


def test_a_scene_rename_does_not_move_a_lifecycle_fingerprint(cid):
    kind = "possible_thread_closure"
    before = pending.fingerprint(pending.Current.load(cid), kind, [MAP])
    scene_refs.repoint(cid, {S1: "001--the-coronation"})
    assert store.plot.read(cid)["maras-map"]["last_scene"] == "001--the-coronation"
    assert pending.fingerprint(pending.Current.load(cid), kind, [MAP]) == before


def test_canonicalized_aliases_give_a_stable_pair_fingerprint(cid):
    before = pending.fingerprint(pending.Current.load(cid), "possible_duplicate", [MAP, CHART])
    store.plot.set_movement(cid, "seraphines-letter", "Seraphine's letter", "open",
                            "Seraphine sealed it.", S3)
    _alias(cid, LETTER, MAP)
    current = pending.Current.load(cid)
    assert [a["ref"] for a in current.records[MAP]["aliases"]] == [LETTER]
    assert pending.fingerprint(current, "possible_duplicate", [MAP, CHART]) == before


def test_temporal_side_changes_when_the_event_moves(cid):
    ev = _event(cid)
    before = pending.fingerprint(pending.Current.load(cid), "possible_relation", [OATH, ev])
    store.events.update(cid, ev.partition(":")[2], date="2026-06-01")
    after = pending.fingerprint(pending.Current.load(cid), "possible_relation", [OATH, ev])
    assert before is not None and after is not None and before != after


def test_a_missing_side_has_no_fingerprint(cid):
    current = pending.Current.load(cid)
    assert pending.fingerprint(current, "possible_duplicate", [MAP, "thread:nobody"]) is None
    assert pending.fingerprint(current, "possible_thread_closure", ["thread:nobody"]) is None


# ---------------------------------------------------------------- verdicts


def _delete_thread(cid, pid):
    data = store.plot.read(cid)
    data.pop(pid)
    (_root(cid) / "plot.json").write_text(json.dumps(data), encoding="utf-8")


def _gone_deleted(cid):
    record = _record(cid, "possible_duplicate", [MAP, CHART])
    _delete_thread(cid, "winifreds-chart")
    return record


def _gone_alias_source(cid):
    record = _record(cid, "possible_thread_closure", [CHART])
    _alias(cid, CHART, MAP)
    return record


def _gone_aliased_together(cid):
    record = _record(cid, "possible_duplicate", [MAP, CHART])
    _alias(cid, CHART, MAP)
    return record


def _satisfied_link(cid):
    record = _record(cid, "possible_duplicate", [MAP, CHART])
    _link(cid, CHART, MAP, "related_to")
    return record


def _satisfied_reverse_link(cid):
    record = _record(cid, "possible_relation", [OATH, MAP])
    _link(cid, MAP, OATH, "pays_off")
    return record


def _satisfied_closed(cid):
    store.plot.set_movement(cid, "maras-map", "", "closed", "", S2)
    return _record(cid, "possible_thread_closure", [MAP], fingerprint="fp1_anything")


def _satisfied_resolved(cid):
    store.commitments.set_movement(cid, "maras-oath", "", "", "fulfilled", None, "", S2)
    return _record(cid, "possible_commitment_resolution", [OATH], fingerprint="fp1_anything")


def _suppressed(cid):
    record = _record(cid, "possible_duplicate", [MAP, CHART])
    doc.put_suppression(cid, record["fingerprint"], {
        "kind": record["kind"], "refs": record["refs"], "decision": "dismiss", "created": ""})
    return record


def _stale(cid):
    return _record(cid, "possible_duplicate", [MAP, CHART], fingerprint="fp1_old")


def _settled_touched(cid):
    return _record(cid, "possible_thread_closure", [MAP], signals={"reason": "touched"},
                   proposal=_proposal("keep_open"))


def _settled_temporal(cid):
    ev = _event(cid)
    return _record(cid, "possible_relation", [OATH, ev], proposal=_proposal("unrelated"))


def _live_pair(cid):
    return _record(cid, "possible_duplicate", [MAP, CHART], proposal=_proposal("duplicate"))


def _live_touched_close(cid):
    return _record(cid, "possible_thread_closure", [MAP], signals={"reason": "touched"},
                   proposal=_proposal("close"))


def _live_stale_keep_open(cid):
    # A deterministic nomination the model declined is still the reader's call.
    return _record(cid, "possible_thread_closure", [MAP], signals={"reason": "stale"},
                   proposal=_proposal("keep_open"))


def _unknown_plot(cid):
    record = _record(cid, "possible_duplicate", [MAP, CHART])
    (_root(cid) / "plot.json").write_text("{ no", encoding="utf-8")
    return record


def _unknown_events(cid):
    ev = _event(cid)
    record = _record(cid, "possible_relation", [OATH, ev])
    (_root(cid) / "events.json").write_text("{ no", encoding="utf-8")
    return record


def _unknown_surrogate(cid):
    record = _record(cid, "possible_duplicate", [MAP, CHART], fingerprint="fp1_old")
    store.plot.set_movement(cid, "maras-map", json.loads('"Mara\\ud83d"'), "", "", S2)
    return record


@pytest.mark.parametrize(("setup", "expected"), [
    (_gone_deleted, "gone"),
    (_gone_alias_source, "gone"),
    (_gone_aliased_together, "gone"),
    (_satisfied_link, "satisfied"),
    (_satisfied_reverse_link, "satisfied"),
    (_satisfied_closed, "satisfied"),
    (_satisfied_resolved, "satisfied"),
    (_suppressed, "suppressed"),
    (_stale, "stale"),
    (_settled_touched, "settled"),
    (_settled_temporal, "settled"),
    (_live_pair, "live"),
    (_live_touched_close, "live"),
    (_live_stale_keep_open, "live"),
    (_unknown_plot, "unknown"),
    (_unknown_events, "unknown"),
    (_unknown_surrogate, "unknown"),
])
def test_verdicts(cid, setup, expected):
    record = setup(cid)
    assert pending.verdict(pending.Current.load(cid), record) == expected


@pytest.mark.parametrize("kind", candidates.KINDS)
def test_a_malformed_continuity_file_makes_every_kind_unknown(cid, kind):
    refs = {"possible_duplicate": [MAP, CHART], "possible_relation": [MAP, OATH],
            "possible_thread_closure": [MAP], "possible_commitment_resolution": [OATH]}[kind]
    record = _record(cid, kind, refs)
    (_root(cid) / "continuity.json").write_text("{ no", encoding="utf-8")
    current = pending.Current.load(cid)
    assert current.continuity_malformed
    assert pending.verdict(current, record) == "unknown"


def test_a_malformed_section_makes_the_view_malformed(cid):
    (_root(cid) / "continuity.json").write_text(json.dumps({"suppressions": []}),
                                                 encoding="utf-8")
    assert pending.Current.load(cid).continuity_malformed


def test_current_names_unreadable_ledgers_by_ref_prefix(cid):
    (_root(cid) / "plot.json").write_text("{ no", encoding="utf-8")
    (_root(cid) / "events.json").write_text("{ no", encoding="utf-8")
    current = pending.Current.load(cid)
    assert current.unreadable == frozenset({"thread", "event"})
    assert OATH in current.records


def test_current_takes_the_callers_ledgers(cid, monkeypatch):
    ledgers = effective.Ledgers.load(cid)

    def refuse(_cid):
        raise AssertionError("loaded the ledgers a second time")

    monkeypatch.setattr(effective.Ledgers, "load", classmethod(lambda cls, c: refuse(c)))
    current = pending.Current.load(cid, ledgers=ledgers)
    assert MAP in current.records


# ---------------------------------------------------------------- findings


def test_findings_skips_the_ledgers_when_the_cache_is_empty(cid, monkeypatch):
    def refuse(cls, _cid):
        raise AssertionError("loaded the ledgers for an empty cache")

    monkeypatch.setattr(effective.Ledgers, "load", classmethod(refuse))
    assert pending.findings(cid) == []


def _cache(cid, records):
    data = candidates.empty()
    data["records"] = records
    candidates.write(cid, data)


def test_findings_returns_every_cached_record_with_its_verdict(cid):
    live = _record(cid, "possible_duplicate", [MAP, CHART])
    stale = _record(cid, "possible_thread_closure", [MAP], fingerprint="fp1_old")
    _cache(cid, {"b-live": live, "a-stale": stale})
    assert pending.findings(cid) == [("a-stale", stale, "stale"), ("b-live", live, "live")]


def test_findings_survives_a_record_nothing_normalized(cid, monkeypatch):
    pair = _record(cid, "possible_duplicate", [MAP, CHART])
    closure = _record(cid, "possible_thread_closure", [MAP])
    _cache(cid, {"pair": pair, "closure": closure})
    real = pending.verdict

    def flaky(current, record):
        if record["kind"] == "possible_thread_closure":
            raise TypeError("a shape nothing foresaw")
        return real(current, record)

    monkeypatch.setattr(pending, "verdict", flaky)
    assert [(i, v) for i, _, v in pending.findings(cid)] == [("closure", "gone"),
                                                              ("pair", "live")]


def test_evidence_ok():
    known = {S1, S2}
    assert pending.evidence_ok(None, known)
    assert pending.evidence_ok(_proposal("close", evidence_scenes=[S1, S2]), known)
    assert pending.evidence_ok(_proposal("keep_open"), known)
    assert not pending.evidence_ok(_proposal("close", evidence_scenes=[S1, S3]), known)


# -------------------------------------------------------------------- rows


def test_rows_for_a_gone_ref_do_not_raise(cid):
    _alias(cid, CHART, MAP)
    rows = pending.rows(pending.Current.load(cid), ["thread:nobody", CHART, "event:nothing"])
    assert [(r["ref"], r["kind"], r["title"], r["gone"]) for r in rows] == [
        ("thread:nobody", "thread", "thread:nobody", True),
        (CHART, "thread", "Winifred's chart", True),
        ("event:nothing", "event", "event:nothing", True),
    ]
    for row in rows:
        assert row["status"] == "" and row["beats"] == [] and row["aliases"] == []
        assert row["last_scene"] == {"id": "", "title": ""}


def test_rows_join_current_records(cid):
    store.plot.set_movement(cid, "maras-map", "", "advanced", "Mara found a page.", S2)
    store.plot.set_movement(cid, "maras-map", "", "", "Mara read the margins.", S3)
    store.plot.set_movement(cid, "seraphines-letter", "Seraphine's letter", "open",
                            "Seraphine sealed it.", S4)
    _alias(cid, LETTER, MAP)
    store.commitments.set_movement(cid, "maras-oath", "", "", "", "Saltmarch Eve", "", S2)
    ev = _event(cid)
    titles = {S4: "Seraphine's letter", S2: "The realm"}
    rows = pending.rows(pending.Current.load(cid), [MAP, OATH, ev],
                        scene_title=lambda sid: titles.get(sid, ""),
                        pressure={OATH: {"state": "upcoming", "in_days": 3, "friendly": ""}})
    thread, oath, event = rows
    assert thread["kind"] == "thread" and thread["title"] == "Mara's map"
    assert thread["status"] == "advanced" and not thread["gone"]
    assert [a["ref"] for a in thread["aliases"]] == [LETTER]
    assert thread["beats"] == [{"scene": S2, "text": "Mara found a page."},
                               {"scene": S3, "text": "Mara read the margins."},
                               {"scene": S4, "text": "Seraphine sealed it."}]
    assert thread["latest_beat"] == "Seraphine sealed it."
    assert thread["last_scene"] == {"id": S4, "title": "Seraphine's letter"}
    assert thread["due"] == "" and thread["pressure"] is None
    assert oath["kind"] == "commitment" and oath["title"] == "Mara's oath"
    assert oath["due"] == "Saltmarch Eve"
    assert oath["last_scene"] == {"id": S2, "title": "The realm"}
    assert oath["pressure"] == {"state": "upcoming", "in_days": 3, "friendly": ""}
    assert event["kind"] == "event" and event["title"] == "The coronation"
    assert event["status"] == "" and event["beats"] == [] and event["due"]


def test_label_joins_titles(cid):
    current = pending.Current.load(cid)
    assert pending.label(current, [MAP, CHART]) == "Mara's map / Winifred's chart"
    assert pending.label(current, [MAP, "thread:nobody"]) == "Mara's map / thread:nobody"


# ------------------------------------------------------------------ tables


def test_tables_cover_every_kind():
    assert set(pending.GROUP_OF) == set(candidates.KINDS) == set(pending.CHORE_OF)
    assert pending.GROUP_OF == {
        "possible_duplicate": "overlaps", "possible_relation": "overlaps",
        "possible_thread_closure": "closures",
        "possible_commitment_resolution": "resolutions"}
    assert pending.CHORE_OF == {
        "possible_duplicate": "continuity-overlaps", "possible_relation": "continuity-overlaps",
        "possible_thread_closure": "continuity-closures",
        "possible_commitment_resolution": "continuity-closures"}
    assert set(pending.VISIBLE) == {"live", "stale"}


def test_decision_labels_are_hedged():
    assert pending.DECISION_LABELS["duplicate"] == "Suggested: same business (merge)"
    assert pending.DECISION_LABELS["distinct"] == "Suggested: different business"
    assert pending.DECISION_LABELS["close"] == "Suggested: may be finished"
    assert pending.DECISION_LABELS["uncertain"] == "No clear suggestion"
    assert pending.DECISION_LABELS["unrelated"] == "No clear suggestion"
    for word in pending.TEMPORAL_RELATIONS:
        assert pending.DECISION_LABELS[word] == f"Suggested: {word}"
