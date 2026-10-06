"""The Continuity review routes (capstone spec §12, §21, §22).

`GET .../continuity/candidates` joins every cached finding to the records it
names as they stand now -- a pure read: no model, no embedding, no run, no
write. Apply, dismiss and restore act on one finding under one campaign-lock
hold; a write that lands part-way answers 500 naming what landed and moves the
write token itself, since the middleware stamps only a 2xx.

Findings are seeded through `reconcile.discover` and `persist_found` (plus
`persist_proposals` where a test needs the model's answer), so what is applied
is exactly what a sweep would have cached.
"""

from __future__ import annotations

import json
import uuid

import pytest

import grimoire.store as store
from grimoire import routes
from grimoire.routes import runs
from grimoire.store import locks
from grimoire.store.campaigns import paths as campaigns_paths
from grimoire.store.continuity import (
    candidates,
    canon,
    doc,
    effective,
    pending,
    pressure,
    reconcile,
    review,
    similarity,
)

from .review_runs import LEDGER_THREAD, RECOVER_THE_LEDGER
from .test_continuity_reconcile_routes import _held, _install, _key, _refresh, _settled

LEDGER = f"thread:{LEDGER_THREAD[0]}"
RECOVER = "thread:recover-the-harbour-ledger"
MAP = "thread:mara-s-map"
OATH = "commitment:mara-s-oath"
OATH2 = "commitment:mara-s-oath-2"
PAIR = canon.candidate_id("possible_duplicate", [LEDGER, RECOVER])
CLOSE_MAP = canon.candidate_id("possible_thread_closure", [MAP])
RESOLVE_OATH = canon.candidate_id("possible_commitment_resolution", [OATH])
OATHS = canon.candidate_id("possible_duplicate", [OATH, OATH2])


# ------------------------------------------------------------------ helpers


def _campaign(client) -> tuple[str, str]:
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Saltmarch docks"}).json()["id"]
    return cid, sid


def _threads(cid: str, sid: str) -> None:
    """Two open threads close enough to be a possible duplicate."""
    pid, title, beat = LEDGER_THREAD
    store.plot.set_movement(cid, pid, title, "open", beat, sid)
    store.plot.set_movement(cid, "recover-the-harbour-ledger", RECOVER_THE_LEDGER["title"],
                            "open", RECOVER_THE_LEDGER["beat"], sid)


def _sweep(cid: str, *, full: bool = True, touched=()) -> reconcile.Sweep:
    """Discover and persist what was found, as a Refresh's first half does."""
    sweep = reconcile.discover(cid, stamp=reconcile.generation(uuid.uuid4().hex),
                               full=full, touched=touched, embed=False)
    reconcile.persist_found(cid, sweep)
    return sweep


def _seeded(client) -> tuple[str, str]:
    cid, sid = _campaign(client)
    _threads(cid, sid)
    _sweep(cid)
    assert PAIR in candidates.read(cid)["records"]
    return cid, sid


def _proposal(decision: str, **over) -> dict:
    base = {"decision": decision, "from": "", "to": "", "relation": "", "status": "",
            "reason": "", "evidence_scenes": []}
    base.update(over)
    return base


def _read(client, cid: str) -> dict:
    r = client.get(f"/api/campaigns/{cid}/continuity/candidates")
    assert r.status_code == 200, r.text
    return r.json()


def _listed(client, cid: str) -> dict[str, dict]:
    return {c["id"]: c for c in _read(client, cid)["candidates"]}


def _apply(client, cid: str, key: str, body: dict):
    return client.post(f"/api/campaigns/{cid}/continuity/candidates/{key}/apply", json=body)


def _dismiss(client, cid: str, key: str, body: dict | None = None):
    return client.post(f"/api/campaigns/{cid}/continuity/candidates/{key}/dismiss",
                       json=body or {})


def _continuity(client, cid: str) -> dict:
    r = client.get(f"/api/campaigns/{cid}/continuity")
    assert r.status_code == 200, r.text
    return r.json()


def _root(cid: str):
    return campaigns_paths.campaign_root(cid)


def _files(cid: str) -> dict[str, bytes]:
    root = _root(cid)
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _move(cid: str, pid: str, *, title: str = "", status: str = "") -> None:
    """Retitle or re-status a thread, leaving its beats and last scene alone."""
    stored = store.plot.get(cid, pid)["last_scene"]
    store.plot.set_movement(cid, pid, title, status, "", stored)


def _retitle_recover(cid: str, title: str = "Recover the stolen harbour ledger") -> None:
    _move(cid, "recover-the-harbour-ledger", title=title)


def _dated_scene(client, cid: str, title: str, native: str) -> str:
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": title}).json()["id"]
    return store.scenes.set_datetime(cid, sid, native)["id"]


def _stale_map(client) -> tuple[str, str]:
    """A thread whose last beat is long past: a closure finding. Returns the
    campaign and the (dated) scene the beat is filed under."""
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = _dated_scene(client, cid, "Saltmarch docks", "2026-05-01")
    store.plot.set_movement(cid, "mara-s-map", "Mara's map", "open",
                            "The map turned up in Saltmarch.", sid)
    store.clock.advance(cid, to="2026-07-15")
    _sweep(cid)
    assert CLOSE_MAP in candidates.read(cid)["records"]
    return cid, sid


def _twin_oaths(client) -> str:
    """Two commitments of one title: only the second carries a deadline."""
    cid, sid = _campaign(client)
    store.commitments.set_movement(cid, "mara-s-oath", "Mara's oath to Winifred", "promise",
                                   "open", "", "Mara swore it at the gate.", sid)
    store.commitments.set_movement(cid, "mara-s-oath-2", "Mara's oath to Winifred",
                                   "promise", "open", "by midwinter",
                                   "Mara swore it again at the gate.", sid)
    _sweep(cid)
    assert OATHS in candidates.read(cid)["records"]
    return cid


# --------------------------------------------------------- the candidates read


class _Raising:
    def __getattr__(self, name):
        raise AssertionError("the candidates read must not reach an embeddings client")


def test_candidates_read_is_pure(client, monkeypatch):
    """§3.10, AC16: no model, no embedding, no run, no write."""
    cid, _sid = _seeded(client)

    def no_llm():
        raise AssertionError("the candidates read must not resolve an LLM")

    monkeypatch.setattr(similarity, "_CLIENT", _Raising())
    client.app.dependency_overrides[routes.get_llm] = no_llm
    token = store.revision.current(cid)
    before = _files(cid)

    body = _read(client, cid)

    assert PAIR in {c["id"] for c in body["candidates"]}
    assert client.app.state.runs.for_subject(runs.campaign_subject(cid)) == []
    assert body["run"] is None
    assert store.revision.current(cid) == token
    assert _files(cid) == before


def test_candidates_join_current_records_and_flag_stale(client):
    """§12.2: the records are joined at read time, so a retitled record shows
    its new title and the finding says its records have changed."""
    cid, _sid = _seeded(client)
    found = _listed(client, cid)[PAIR]
    assert (found["stale"], found["stale_reason"]) == (False, None)
    assert (found["kind"], found["group"]) == ("possible_duplicate", "overlaps")
    assert set(found) >= {"id", "kind", "group", "refs", "fingerprint", "stale",
                          "stale_reason", "signals", "proposal", "created", "records"}

    _retitle_recover(cid)

    found = _listed(client, cid)[PAIR]
    assert (found["stale"], found["stale_reason"]) == (True, "records")
    titles = {row["ref"]: row["title"] for row in found["records"]}
    assert titles[RECOVER] == "Recover the stolen harbour ledger"
    row = next(r for r in found["records"] if r["ref"] == LEDGER)
    assert row["status"] == "open" and row["beats"] and "due" in row


@pytest.mark.parametrize("hide", ["suppressed", "satisfied", "gone"])
def test_hidden_verdicts_are_not_listed(client, hide):
    cid, _sid = _seeded(client)
    assert PAIR in _listed(client, cid)
    if hide == "suppressed":
        fp = pending.fingerprint(pending.Current.load(cid), "possible_duplicate",
                                 [LEDGER, RECOVER])
        doc.put_suppression(cid, fp, {"kind": "possible_duplicate", "refs": [LEDGER, RECOVER],
                                      "decision": "dismiss", "created": ""})
    elif hide == "satisfied":
        r = client.post(f"/api/campaigns/{cid}/continuity/links",
                        json={"a": LEDGER, "b": RECOVER, "relation": "related_to"})
        assert r.status_code == 200, r.text
    else:
        r = client.delete(f"/api/campaigns/{cid}/ledger/threads/recover-the-harbour-ledger")
        assert r.status_code == 200, r.text

    assert PAIR in candidates.read(cid)["records"]
    assert PAIR not in _listed(client, cid)


def test_a_settled_nomination_is_not_listed(client):
    """A touched re-check the model answered "keep open" stays cached (so it is
    not re-asked) and is shown to nobody (Decision 9)."""
    cid, sid = _campaign(client)
    store.plot.set_movement(cid, "mara-s-map", "Mara's map", "open",
                            "The map turned up in Saltmarch.", sid)
    sweep = _sweep(cid, full=False, touched=[MAP])
    assert CLOSE_MAP in sweep.model_only
    reconcile.persist_proposals(cid, sweep, {CLOSE_MAP: _proposal("keep_open")})

    assert CLOSE_MAP in candidates.read(cid)["records"]
    assert CLOSE_MAP not in _listed(client, cid)


def test_candidates_read_reports_a_live_run(client):
    cid, _sid = _seeded(client)
    _key(client)
    held = _install(client, _held())
    resp = _refresh(client, cid)
    assert resp.status_code == 202, resp.text
    held.await_held()
    try:
        body = _read(client, cid)
        assert body["run"] is not None
        assert body["run"]["id"] == resp.json()["run"]["id"]
        assert body["run"]["state"] == "running"
    finally:
        held.release()
    assert _settled(client, cid, resp)["state"] == "landed"
    assert _read(client, cid)["run"] is None


def test_a_malformed_cache_reads_empty(client):
    """§26: the cache is rebuildable, so an unparseable one is no findings."""
    cid, _sid = _seeded(client)
    (_root(cid) / "continuity_candidates.json").write_text("{ no", encoding="utf-8")
    body = _read(client, cid)
    assert body["candidates"] == []
    assert body["diagnostics"]["cache_malformed"] is True
    assert body["generated"] == ""


def test_a_hand_edited_cache_with_bad_nested_fields_reads_200(client):
    cid, _sid = _seeded(client)
    path = _root(cid) / "continuity_candidates.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["records"][PAIR]["signals"] = "x"
    data["records"][PAIR]["proposal"] = {"decision": 3}
    path.write_text(json.dumps(data), encoding="utf-8")

    found = _listed(client, cid)[PAIR]
    assert found["signals"] == {} and found["proposal"] is None
    assert _read(client, cid)["diagnostics"]["cache_malformed"] is False


def test_a_hand_edited_pair_naming_one_record_twice_is_no_finding(client):
    """A duplicate whose two refs are one record passes no normalization it
    should: it is not listed, and applying or dismissing it is the gone
    refusal -- never an alias plan with no record to merge away (a 500)."""
    cid, _sid = _seeded(client)
    refs = [LEDGER, LEDGER]
    key = canon.candidate_id("possible_duplicate", refs)
    path = _root(cid) / "continuity_candidates.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    fp = pending.fingerprint(pending.Current.load(cid), "possible_duplicate", refs)
    data["records"][key] = {"kind": "possible_duplicate", "refs": refs, "fingerprint": fp,
                            "signals": {}, "proposal": None, "created": ""}
    path.write_text(json.dumps(data), encoding="utf-8")

    listed = _listed(client, cid)
    assert key not in listed and PAIR in listed
    r = _apply(client, cid, key, {"op": "alias", "canonical": LEDGER})
    assert r.status_code == 404, r.text
    assert r.json()["kind"] == "not_found"
    gone = _dismiss(client, cid, key, {"decision": "dismiss"})
    assert gone.status_code == 404, gone.text
    assert doc.get_alias(cid, LEDGER) is None


def test_a_malformed_continuity_file_hides_findings(client):
    """Decision 2: a malformed continuity.json reads as no dismissals, so every
    finding is `unknown` -- hidden and kept -- rather than resurrected."""
    cid, _sid = _seeded(client)
    (_root(cid) / "continuity.json").write_text("{ no", encoding="utf-8")
    body = _read(client, cid)
    assert body["candidates"] == []
    assert body["diagnostics"]["malformed"] == ["file"]
    assert PAIR in candidates.read(cid)["records"]


def test_a_renamed_evidence_scene_is_flagged(client):
    """A proposal citing a scene that no longer exists cannot be applied, and
    the read says so before the reader tries (Decision 23)."""
    cid, sid = _campaign(client)
    _threads(cid, sid)
    sweep = _sweep(cid)
    reconcile.persist_proposals(cid, sweep, {PAIR: _proposal(
        "duplicate", **{"from": RECOVER, "to": LEDGER}, reason="Same hunt.",
        evidence_scenes=[sid])})
    assert _listed(client, cid)[PAIR]["stale"] is False

    store.scenes.rename_scene(cid, sid, "The Saltmarch quay")

    found = _listed(client, cid)[PAIR]
    assert (found["stale"], found["stale_reason"]) == (True, "evidence")
    r = _apply(client, cid, PAIR, {"op": "alias", "canonical": LEDGER})
    assert r.status_code == 409, r.text
    assert (r.json()["kind"], r.json()["reason"]) == ("stale_candidate", "evidence")


def test_candidates_read_names_scenes_and_actors(client):
    cid, sid = _campaign(client)
    later = client.post(f"/api/campaigns/{cid}/scenes",
                        json={"title": "Winifred's study"}).json()["id"]
    pc, _version = store.pcs.create_pc(_root(cid), "Seraphine", [])
    store.appearances.appear(cid, sid, "pcs", pc, "default", "player")
    _threads(cid, sid)
    _sweep(cid)

    body = _read(client, cid)
    found = {c["id"]: c for c in body["candidates"]}[PAIR]
    actor = f"pcs:{pc}"
    assert actor in found["signals"]["shared_actors"]
    assert body["names"][actor] == "Seraphine"
    beat_scenes = {b["scene"] for row in found["records"] for b in row["beats"]}
    assert beat_scenes == {sid}
    assert body["names"][sid] == "Saltmarch docks"
    assert [s["id"] for s in body["scenes"]] == [later, sid]
    assert body["scenes"][0]["title"] == "Winifred's study"


def test_the_read_leaves_out_what_it_cannot_name(client):
    """Decision 23: `names` never stands an id in for a name. A gone evidence
    scene, an actor nobody can resolve and an event with no record get no
    entry -- the detail leaves them out rather than show a filename or a ref."""
    cid, sid = _campaign(client)
    _threads(cid, sid)
    sweep = _sweep(cid)
    reconcile.persist_proposals(cid, sweep, {PAIR: _proposal(
        "duplicate", **{"from": RECOVER, "to": LEDGER}, reason="Same hunt.",
        evidence_scenes=[sid])})
    cache = candidates.read(cid)
    cache["records"][PAIR]["signals"] = {
        **cache["records"][PAIR]["signals"],
        "shared_actors": ["characters:nobody-at-all"],
        "shared_anchors": ["event:no-such-day"]}
    candidates.write(cid, cache)
    store.scenes.rename_scene(cid, sid, "The Saltmarch quay")

    body = _read(client, cid)
    found = {c["id"]: c for c in body["candidates"]}[PAIR]
    assert found["proposal"]["evidence_scenes"] == [sid]
    assert sid not in body["names"]
    assert "characters:nobody-at-all" not in body["names"]
    assert "event:no-such-day" not in body["names"]
    assert all(name != key for key, name in body["names"].items())


def test_pressure_is_computed_outside_the_hold(client, monkeypatch):
    """§11.1: pressure can run calendar-plugin code, which never runs under a
    campaign lock -- a slow plugin must not hold up a save behind a read."""
    cid, _sid = _seeded(client)
    calls = []
    real = reconcile.pressure_by_ref

    def watched(c):
        assert not locks.campaign_lock(c)._is_owned()
        calls.append(c)
        return real(c)

    monkeypatch.setattr(reconcile, "pressure_by_ref", watched)
    found = _listed(client, cid)[PAIR]
    assert calls == [cid]
    assert {row["pressure"]["state"] for row in found["records"]} == {"ok"}


def _no_join(monkeypatch) -> list[str]:
    """Make every part of the join fail loudly, and record what was reached.
    `pressure_by_ref` swallows its own failures, so `pressure.build` records
    rather than relying on the raise to surface."""
    reached: list[str] = []

    def refuse(name):
        def call(*_a, **_k):
            reached.append(name)
            raise AssertionError(f"an empty cache must not reach {name}")
        return call

    monkeypatch.setattr(pressure, "build", refuse("pressure.build"))
    monkeypatch.setattr(reconcile, "pressure_by_ref", refuse("pressure_by_ref"))
    monkeypatch.setattr(pending.Current, "load", refuse("Current.load"))
    monkeypatch.setattr(pending, "findings", refuse("findings"))
    monkeypatch.setattr(store.scenes, "list_scenes", refuse("list_scenes"))
    return reached


def test_an_empty_cache_answers_without_the_join(client, monkeypatch):
    """The Ledger mounts this read for every section and re-reads it on every
    write, so a campaign no sweep has found anything in must not pay for the
    join: no pressure pass (user calendar-plugin code, aging over every
    thread), no current view, no scene list. What the top level reports with
    no findings still answers -- the diagnostics (a dangling alias is there
    whether or not a sweep ran), the cache's own flags, the live run."""
    cid, sid = _campaign(client)
    _threads(cid, sid)
    doc.put_alias(cid, MAP, {"to": "thread:gone", "created": "", "source": "manual",
                             "note": ""})
    expected_diagnostics = {**effective.diagnostics(cid),
                            "malformed": [], "cache_malformed": False}
    assert expected_diagnostics["dangling_aliases"]
    reached = _no_join(monkeypatch)

    body = _read(client, cid)

    assert reached == []
    assert body == {"generated": "", "matching": "basic",
                    "diagnostics": expected_diagnostics, "run": None,
                    "names": {}, "scenes": [], "candidates": []}


def test_a_swept_cache_with_no_findings_still_says_when(client, monkeypatch):
    """A sweep whose last finding went away leaves a cache with a stamp and no
    records: the read reports the stamp, and pays for no join either."""
    cid, _sid = _campaign(client)
    stamp = "2026-10-01T09:00:00+00:00"
    candidates.write(cid, {**candidates.empty(), "generated": stamp})
    assert not candidates.read(cid)["records"]
    reached = _no_join(monkeypatch)

    body = _read(client, cid)

    assert reached == []
    assert (body["generated"], body["candidates"]) == (stamp, [])


def test_an_unreadable_cache_reads_as_empty_and_says_so(client, monkeypatch):
    cid, _sid = _campaign(client)
    (_root(cid) / "continuity_candidates.json").write_text("{not json", encoding="utf-8")
    assert candidates.malformed(cid)
    reached = _no_join(monkeypatch)

    body = _read(client, cid)

    assert reached == []
    assert body["diagnostics"]["cache_malformed"] is True
    assert body["candidates"] == []


# ---------------------------------------------------------------- applying


def test_applying_a_duplicate_writes_an_alias_only(client):
    """§28.4: Keep A merges B into A and touches no ledger."""
    cid, _sid = _seeded(client)
    plot_before = (_root(cid) / "plot.json").read_bytes()
    rows = len(store.journal.read(cid))

    r = _apply(client, cid, PAIR, {"op": "alias", "canonical": LEDGER})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["applied"] == ["alias", "cache"]
    assert body["alias"]["ref"] == RECOVER and body["alias"]["to"] == LEDGER
    alias = doc.get_alias(cid, RECOVER)
    assert (alias["to"], alias["source"]) == (LEDGER, "review")
    assert (_root(cid) / "plot.json").read_bytes() == plot_before
    assert PAIR not in candidates.read(cid)["records"]
    written = store.journal.read(cid)
    assert len(written) == rows + 1
    assert written[-1]["kind"] == "continuity_alias"


def test_applying_a_duplicate_replaces_the_sources_broken_merge(client):
    """§26: a merge whose target is gone leaves the source its own record, so
    the review offers it as a duplicate -- and keeping the other record has to
    work, not answer that the source "is already merged elsewhere". The broken
    merge is replaced in one journalled row whose undo restores it."""
    cid, sid = _campaign(client)
    _threads(cid, sid)
    store.plot.set_movement(cid, "winifred-s-chart", "Winifred's chart", "open",
                            "Winifred lost the chart.", sid)
    chart = "thread:winifred-s-chart"
    r = client.post(f"/api/campaigns/{cid}/continuity/aliases",
                    json={"ref": RECOVER, "to": chart})
    assert r.status_code == 200, r.text
    # Removed outside the §5.7 cascade, as an undo of its creating row does.
    store.plot.restore(cid, "winifred-s-chart", None)
    assert RECOVER not in effective.live_canon(cid)
    broken = doc.get_alias(cid, RECOVER)
    _sweep(cid)
    assert PAIR in candidates.read(cid)["records"]
    rows = len(store.journal.read(cid))

    r = _apply(client, cid, PAIR, {"op": "alias", "canonical": LEDGER})

    assert r.status_code == 200, r.text
    assert doc.get_alias(cid, RECOVER)["to"] == LEDGER
    assert effective.live_canon(cid)[RECOVER] == LEDGER
    written = store.journal.read(cid)
    assert len(written) == rows + 1 and written[-1]["kind"] == "continuity_alias"
    store.undo.undo(cid, written[-1]["id"])
    assert doc.get_alias(cid, RECOVER) == broken


def test_apply_liveness_mismatch_then_accept(client):
    """§12.3: merging an open thread behind a closed one is confirmed first."""
    cid, sid = _campaign(client)
    _threads(cid, sid)
    _move(cid, LEDGER_THREAD[0], status="closed")
    _sweep(cid)

    r = _apply(client, cid, PAIR, {"op": "alias", "canonical": LEDGER})
    assert r.status_code == 409, r.text
    assert r.json()["kind"] == "liveness_mismatch"
    assert doc.get_alias(cid, RECOVER) is None

    r = _apply(client, cid, PAIR, {"op": "alias", "canonical": LEDGER,
                                   "accept_status_change": True})
    assert r.status_code == 200, r.text
    assert doc.get_alias(cid, RECOVER)["to"] == LEDGER


def test_apply_with_copy_due_writes_two_journalled_rows(client):
    """§5.1: the due copy is its own journalled ledger write, before the merge."""
    cid = _twin_oaths(client)
    rows = len(store.journal.read(cid))

    r = _apply(client, cid, OATHS, {"op": "alias", "canonical": OATH, "copy_due": True})

    assert r.status_code == 200, r.text
    assert r.json()["applied"] == ["due", "alias", "cache"]
    assert "dues" not in r.json()
    assert store.commitments.get(cid, "mara-s-oath")["due"] == "by midwinter"
    written = store.journal.read(cid)[rows:]
    assert [row["kind"] for row in written] == ["commitment", "continuity_alias"]


@pytest.mark.parametrize("beat", ["Mara burned the map.", ""])
def test_applying_a_closure_closes_and_keeps_last_scene(client, beat):
    """§12.4: a closure never moves `last_scene` backwards; a beat is filed
    under the evidence scene the reader chose."""
    cid, early = _stale_map(client)
    later = client.post(f"/api/campaigns/{cid}/scenes",
                        json={"title": "Winifred's study"}).json()["id"]
    store.plot.set_movement(cid, "mara-s-map", "", "", "", later)
    beats = len(store.plot.get(cid, "mara-s-map")["beats"])
    body = {"op": "close"}
    if beat:
        body.update(beat=beat, scene=early)

    r = _apply(client, cid, CLOSE_MAP, body)

    assert r.status_code == 200, r.text
    assert r.json()["applied"] == ["status", "cache"]
    after = store.plot.get(cid, "mara-s-map")
    assert after["status"] == "closed"
    assert after["last_scene"] == later
    if beat:
        assert after["beats"][-1] == {"scene": early, "text": beat}
    else:
        assert len(after["beats"]) == beats
    assert CLOSE_MAP not in candidates.read(cid)["records"]


def test_a_closing_beat_whose_scene_moved_is_refused_in_words(client):
    """§12.9, §30: the scene the reader picked for a beat was renamed under the
    open form. The refusal says so in words and names no scene id, and the
    candidates read the client re-reads on it offers the scene's new id."""
    cid, early = _stale_map(client)
    renamed = client.put(f"/api/campaigns/{cid}/scenes/{early}",
                         json={"title": "Winifred harbour"})
    assert renamed.status_code == 200, renamed.text
    moved = renamed.json()["id"]
    assert moved != early

    r = _apply(client, cid, CLOSE_MAP, {"op": "close", "beat": "Mara burned the map.",
                                        "scene": early})

    assert r.status_code == 400, r.text
    assert r.json()["kind"] == "bad_scene"
    assert early not in r.json()["detail"]
    assert r.json()["detail"] == "That scene has been renamed or removed; pick it again."
    assert store.plot.get(cid, "mara-s-map")["status"] != "closed"
    assert [s["id"] for s in _read(client, cid)["scenes"]] == [moved]


def test_applying_a_resolution_sets_the_status(client):
    cid, sid = _campaign(client)
    store.commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open",
                                   "2026-05-05", "Mara swore it.", sid)
    store.clock.advance(cid, to="2026-05-10")
    _sweep(cid)
    assert RESOLVE_OATH in _listed(client, cid)

    r = _apply(client, cid, RESOLVE_OATH, {"op": "resolve", "status": "fulfilled"})

    assert r.status_code == 200, r.text
    assert store.commitments.get(cid, "mara-s-oath")["status"] == "fulfilled"
    assert RESOLVE_OATH not in candidates.read(cid)["records"]


def test_temporal_accept_writes_the_link(client):
    """§28.4: an accepted temporal pairing is a reviewed link, which pressure
    then reads as the commitment's deadline -- and the next sweep does not ask
    again."""
    cid, sid = _campaign(client)
    store.clock.advance(cid, to="2026-05-10")
    store.commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open",
                                   "before the bells stop", "Mara swore it.", sid)
    event = "event:" + store.events.create(cid, "The coronation", "2026-05-13")
    key = canon.candidate_id("possible_relation", [OATH, event])
    sweep = _sweep(cid)
    assert key in sweep.model_only
    reconcile.persist_proposals(cid, sweep, {key: _proposal(
        "before", **{"from": OATH, "to": event}, relation="before",
        reason="The oath falls before the coronation.")})
    assert key in _listed(client, cid)

    r = _apply(client, cid, key, {"op": "link", "from": OATH, "to": event,
                                  "relation": "before"})

    assert r.status_code == 200, r.text
    assert r.json()["applied"] == ["link", "cache"]
    link = r.json()["link"]
    assert (link["a"], link["b"], link["relation"]) == (OATH, event, "before")
    items = pressure.build(cid, sources={"linked_deadline"})["items"]
    assert any(item["subject"] == OATH and item["relation"] == "before" for item in items)
    assert key not in _sweep(cid).model_only


def test_stale_apply_returns_409_with_current_records(client):
    """§28.4, §22 step 5: nothing is written, and the body carries the records
    as they are now so the reader can look again."""
    cid, _sid = _seeded(client)
    _retitle_recover(cid)
    before = _files(cid)

    r = _apply(client, cid, PAIR, {"op": "alias", "canonical": LEDGER})

    assert r.status_code == 409, r.text
    body = r.json()
    assert (body["kind"], body["reason"]) == ("stale_candidate", "records")
    assert body["detail"]
    current = body["current"]
    assert current["fingerprint"] != candidates.read(cid)["records"][PAIR]["fingerprint"]
    titles = {row["ref"]: row["title"] for row in current["records"]}
    assert titles[RECOVER] == "Recover the stolen harbour ledger"
    assert _files(cid) == before


def _overdue_oath_moved(client) -> tuple[str, dict]:
    """An overdue oath's resolution finding, read, and then made stale by a new
    beat. Returns the campaign and the row the read showed for the oath."""
    cid, sid = _campaign(client)
    store.clock.advance(cid, to="2026-05-01")
    store.commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open",
                                   "2026-05-05", "Mara swore it.", sid)
    store.clock.advance(cid, to="2026-05-10")
    _sweep(cid)
    (read,) = _listed(client, cid)[RESOLVE_OATH]["records"]
    assert read["pressure"]["state"] == "overdue"
    store.commitments.set_movement(cid, "mara-s-oath", "", "", "", None,
                                   "Mara hesitated.", sid)
    return cid, read


@pytest.mark.parametrize("act", ["apply", "dismiss"])
def test_a_stale_409_carries_each_rows_pressure(client, monkeypatch, act):
    """§12.9, §22 step 5: the 409's records are the read's rows (§12.2), so
    the detail laid over from them still says the oath is overdue -- and the
    pressure behind them is computed outside the hold, as the read's is."""
    cid, read = _overdue_oath_moved(client)
    calls = []
    real = reconcile.pressure_by_ref

    def watched(c):
        assert not locks.campaign_lock(c)._is_owned()
        calls.append(c)
        return real(c)

    monkeypatch.setattr(reconcile, "pressure_by_ref", watched)
    r = (_apply(client, cid, RESOLVE_OATH, {"op": "resolve", "status": "fulfilled"})
         if act == "apply" else _dismiss(client, cid, RESOLVE_OATH))

    assert r.status_code == 409, r.text
    assert r.json()["kind"] == "stale_candidate"
    (row,) = r.json()["current"]["records"]
    assert row["pressure"] == read["pressure"]
    assert calls == [cid]


def test_an_apply_that_lands_runs_no_pressure_pass(client, monkeypatch):
    """Pressure is only for a 409's rows: an apply that lands never pays for
    a calendar-plugin pass it has no row to put it on."""
    cid, _sid = _seeded(client)
    monkeypatch.setattr(reconcile, "pressure_by_ref", lambda c: pytest.fail("no pressure"))

    r = _apply(client, cid, PAIR, {"op": "alias", "canonical": LEDGER})

    assert r.status_code == 200, r.text


def test_resubmitting_against_the_current_fingerprint_applies(client):
    """§12.9: the reader looked at the 409's records and resubmits."""
    cid, _sid = _seeded(client)
    _retitle_recover(cid)
    stale = _apply(client, cid, PAIR, {"op": "alias", "canonical": LEDGER})
    assert stale.status_code == 409
    current = stale.json()["current"]["fingerprint"]

    r = _apply(client, cid, PAIR, {"op": "alias", "canonical": LEDGER,
                                   "expect_fingerprint": current})

    assert r.status_code == 200, r.text
    assert doc.get_alias(cid, RECOVER)["to"] == LEDGER


def test_keep_open_suppresses_with_its_decision(client):
    """§5.5: Keep open is a suppression carrying its decision, keyed by the
    finding's current fingerprint."""
    cid, _sid = _stale_map(client)
    fp = pending.fingerprint(pending.Current.load(cid), "possible_thread_closure", [MAP])

    r = _apply(client, cid, CLOSE_MAP, {"op": "keep_open"})

    assert r.status_code == 200, r.text
    assert r.json()["applied"] == ["suppression", "cache"]
    stored = doc.read(cid)["suppressions"][fp]
    assert (stored["decision"], stored["kind"], stored["refs"]) == (
        "keep_open", "possible_thread_closure", [MAP])
    assert store.plot.get(cid, "mara-s-map")["status"] == "open"
    (entry,) = _continuity(client, cid)["suppressions"]
    assert (entry["fingerprint"], entry["decision"], entry["live"]) == (fp, "keep_open", True)


# ------------------------------------------------- dismissing and restoring


def test_restore_brings_the_finding_back_at_the_next_refresh(client):
    """§12.7: a restore removes the dismissal; the finding returns when the
    next sweep finds it again."""
    cid, _sid = _seeded(client)
    r = _dismiss(client, cid, PAIR, {"decision": "dismiss"})
    assert r.status_code == 200, r.text
    fp = r.json()["fingerprint"]
    assert PAIR not in _listed(client, cid)
    _sweep(cid)
    assert PAIR not in _listed(client, cid)

    r = client.delete(f"/api/campaigns/{cid}/continuity/suppressions/{fp}")
    assert r.status_code == 200, r.text
    assert _continuity(client, cid)["suppressions"] == []
    assert PAIR not in _listed(client, cid)

    _sweep(cid)
    assert PAIR in _listed(client, cid)
    again = client.delete(f"/api/campaigns/{cid}/continuity/suppressions/{fp}")
    assert again.status_code == 404
    assert again.json()["kind"] == "not_found"


def _restore_after_refresh(client, cid: str, key: str, decision: str) -> dict:
    """Dismiss `key`, Refresh (a full sweep, which nominates no touched record
    and asks nothing with no connection), restore, Refresh again; the listing."""
    r = _dismiss(client, cid, key, {"decision": decision})
    assert r.status_code == 200, r.text
    fp = r.json()["fingerprint"]
    _sweep(cid)
    assert key not in _listed(client, cid)
    r = client.delete(f"/api/campaigns/{cid}/continuity/suppressions/{fp}")
    assert r.status_code == 200, r.text
    _sweep(cid)
    return _listed(client, cid)


@pytest.mark.parametrize("decision", ["dismiss", "keep_open"])
def test_a_restored_touched_closure_comes_back_with_its_proposal(client, decision):
    """§12.7 for a model-only finding (Decision 9): a touched re-check is
    nominated only by an incremental sweep, so no Refresh can find it again.
    Setting it aside keeps it cached, hidden by its ``suppressed`` verdict, and
    a restore brings it back with the proposal the model was paid for."""
    cid, sid = _campaign(client)
    store.plot.set_movement(cid, "mara-s-map", "Mara's map", "open",
                            "The map turned up in Saltmarch.", sid)
    sweep = _sweep(cid, full=False, touched=[MAP])
    closure = _proposal("close", status="closed", reason="The map was found.",
                        evidence_scenes=[sid])
    reconcile.persist_proposals(cid, sweep, {CLOSE_MAP: closure})
    assert CLOSE_MAP in _listed(client, cid)

    listed = _restore_after_refresh(client, cid, CLOSE_MAP, decision)

    assert CLOSE_MAP in listed
    assert candidates.read(cid)["records"][CLOSE_MAP]["proposal"] == closure


def test_a_restored_temporal_pair_comes_back_without_a_connection(client):
    """A temporal pair lands only with a proposal (Decision 9), so with no
    connection a Refresh never persists it again: the restore must find it
    still cached."""
    cid, sid = _campaign(client)
    store.clock.advance(cid, to="2026-05-10")
    store.commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open",
                                   "before the bells stop", "Mara swore it.", sid)
    event = "event:" + store.events.create(cid, "The coronation", "2026-05-13")
    key = canon.candidate_id("possible_relation", [OATH, event])
    sweep = _sweep(cid)
    assert key in sweep.model_only
    reconcile.persist_proposals(cid, sweep, {key: _proposal(
        "before", **{"from": OATH, "to": event}, relation="before",
        reason="The oath falls before the coronation.")})

    assert key in _restore_after_refresh(client, cid, key, "dismiss")


def _dismissed_touched_closure(client) -> tuple[str, str]:
    cid, sid = _campaign(client)
    store.plot.set_movement(cid, "mara-s-map", "Mara's map", "open",
                            "The map turned up in Saltmarch.", sid)
    sweep = _sweep(cid, full=False, touched=[MAP])
    reconcile.persist_proposals(cid, sweep, {CLOSE_MAP: _proposal(
        "close", status="closed", reason="The map was found.", evidence_scenes=[sid])})
    assert _dismiss(client, cid, CLOSE_MAP, {"decision": "dismiss"}).status_code == 200
    return cid, sid


def test_a_dismissed_model_only_finding_whose_record_moved_is_dropped(client):
    """Kept only while its dismissal holds: once the record moves, the old
    question is not the reader's any more. It is hidden at once -- a Ledger
    hand edit starts no sweep, so a ``stale`` reading would put the set-aside
    suggestion back on screen until the next refresh -- and the next persist
    drops it."""
    cid, _sid = _dismissed_touched_closure(client)
    r = client.put(f"/api/campaigns/{cid}/ledger/threads/mara-s-map", json={
        "title": "Mara's map", "status": "open", "beat": "Mara folded the map away."})
    assert r.status_code == 200, r.text

    assert CLOSE_MAP not in _listed(client, cid)
    assert _apply(client, cid, CLOSE_MAP, {"op": "close"}).status_code != 200
    assert store.plot.get(cid, "mara-s-map")["status"] == "open"

    _sweep(cid)

    assert CLOSE_MAP not in candidates.read(cid)["records"]
    assert CLOSE_MAP not in _listed(client, cid)


def test_a_restored_model_only_finding_reads_stale_once_its_record_moves(client):
    """The dismissal is what hides a moved record: restored, the finding is the
    reader's again, and a move under it reads ``stale`` like any finding."""
    cid, sid = _dismissed_touched_closure(client)
    (entry,) = _continuity(client, cid)["suppressions"]
    r = client.delete(f"/api/campaigns/{cid}/continuity/suppressions/{entry['fingerprint']}")
    assert r.status_code == 200, r.text
    store.plot.set_movement(cid, "mara-s-map", "Mara's map", "open",
                            "Mara folded the map away.", sid)

    listed = _listed(client, cid)

    assert CLOSE_MAP in listed
    assert listed[CLOSE_MAP]["stale_reason"] == "records"


def test_get_continuity_marks_suppressions_live(client):
    """§12.7: a dismissal for records that have since changed suppresses
    nothing, and the read says so."""
    cid, _sid = _seeded(client)
    assert _dismiss(client, cid, PAIR).status_code == 200
    (entry,) = _continuity(client, cid)["suppressions"]
    assert entry["live"] is True
    assert entry["titles"] == [LEDGER_THREAD[1], RECOVER_THE_LEDGER["title"]]

    _retitle_recover(cid)

    (entry,) = _continuity(client, cid)["suppressions"]
    assert entry["live"] is False
    assert entry["titles"][1] == "Recover the stolen harbour ledger"


def test_get_continuity_with_suppressions_reads_each_ledger_a_bounded_number_of_times(
        client, monkeypatch):
    cid, _sid = _seeded(client)
    assert _dismiss(client, cid, PAIR).status_code == 200
    calls: list[str] = []
    real = store.plot.read
    monkeypatch.setattr(store.plot, "read", lambda c: calls.append(c) or real(c))
    assert len(_continuity(client, cid)["suppressions"]) == 1
    one = len(calls)

    doc.put_suppression(cid, "fp1_" + "0" * 64, {
        "kind": "possible_thread_closure", "refs": [LEDGER], "decision": "keep_open",
        "created": ""})
    calls.clear()
    assert len(_continuity(client, cid)["suppressions"]) == 2
    assert len(calls) == one


# --------------------------------------------------------- partial writes


def test_apply_partial_write_names_what_landed(client, monkeypatch):
    """§22: only an I/O error stops the sequence part-way; the 500 names what
    landed, and the token moves because that write did land."""
    cid = _twin_oaths(client)
    token = store.revision.current(cid)

    def broken(*args, **kwargs):
        raise OSError("the disk failed")

    monkeypatch.setattr(review, "create_alias", broken)
    r = _apply(client, cid, OATHS, {"op": "alias", "canonical": OATH, "copy_due": True})

    assert r.status_code == 500, r.text
    body = r.json()
    assert (body["kind"], body["landed"]) == ("partial_apply", ["due"])
    assert body["detail"]
    assert store.commitments.get(cid, "mara-s-oath")["due"] == "by midwinter"
    assert store.revision.current(cid) != token


def test_a_keep_open_whose_cache_drop_fails_names_the_suppression(client, monkeypatch):
    """Decision 16: a keep_open apply settles last, so a cache drop that fails
    after the suppression landed is a `PartialSettleError` -- the 500 merges
    its parts into `landed`, the token moves, and the finding is hidden by the
    suppression that did land."""
    cid, _sid = _stale_map(client)
    token = store.revision.current(cid)

    def broken(*args, **kwargs):
        raise OSError("the disk failed")

    monkeypatch.setattr(candidates, "drop", broken)
    r = _apply(client, cid, CLOSE_MAP, {"op": "keep_open"})

    assert r.status_code == 500, r.text
    body = r.json()
    assert (body["kind"], body["landed"]) == ("partial_apply", ["suppression"])
    assert "suppression landed" in body["detail"]
    assert store.revision.current(cid) != token
    assert CLOSE_MAP in candidates.read(cid)["records"]
    assert CLOSE_MAP not in _listed(client, cid)


def test_an_alias_continuity_refusal_with_nothing_landed_is_malformed(client, monkeypatch):
    """continuity.json refusing the alias write under the hold, before any part
    landed, is a plain 409 `malformed`: the token stays and no file moves."""
    cid, _sid = _seeded(client)
    token = store.revision.current(cid)
    before = _files(cid)

    def refused(*args, **kwargs):
        raise doc.ContinuityError("continuity.json cannot be read")

    monkeypatch.setattr(doc, "put_alias", refused)
    r = _apply(client, cid, PAIR, {"op": "alias", "canonical": LEDGER})

    assert r.status_code == 409, r.text
    assert r.json()["kind"] == "malformed"
    assert store.revision.current(cid) == token
    assert _files(cid) == before


def test_an_alias_continuity_refusal_after_the_due_copy_is_partial(client, monkeypatch):
    """The same refusal after the due copy landed is not a refusal any more: the
    500 names the due copy, and the token moves because that write landed."""
    cid = _twin_oaths(client)
    token = store.revision.current(cid)

    def refused(*args, **kwargs):
        raise doc.ContinuityError("continuity.json cannot be read")

    monkeypatch.setattr(doc, "put_alias", refused)
    r = _apply(client, cid, OATHS, {"op": "alias", "canonical": OATH, "copy_due": True})

    assert r.status_code == 500, r.text
    body = r.json()
    assert (body["kind"], body["landed"]) == ("partial_apply", ["due"])
    assert "ContinuityError" in body["detail"]
    assert store.commitments.get(cid, "mara-s-oath")["due"] == "by midwinter"
    assert doc.get_alias(cid, OATH2) is None
    assert store.revision.current(cid) != token


def test_a_failed_dismiss_after_a_landed_write_moves_the_token(client, monkeypatch):
    """Decision 17: the suppression landed before the cache drop failed, so the
    write token moves -- and the finding is hidden by its suppression."""
    cid, _sid = _seeded(client)
    token = store.revision.current(cid)

    def broken(*args, **kwargs):
        raise OSError("the disk failed")

    monkeypatch.setattr(candidates, "drop", broken)
    r = _dismiss(client, cid, PAIR)

    assert r.status_code == 500, r.text
    body = r.json()
    assert (body["kind"], body["landed"]) == ("partial_dismiss", ["suppression"])
    assert store.revision.current(cid) != token
    assert PAIR not in _listed(client, cid)


@pytest.mark.parametrize(("error", "status", "kind"), [
    (doc.ContinuityError("continuity.json cannot be read"), 409, "malformed"),
    (OSError("the disk failed"), 500, "io"),
])
def test_a_dismiss_that_lands_nothing_leaves_the_token(client, monkeypatch, error, status,
                                                       kind):
    cid, _sid = _seeded(client)
    token = store.revision.current(cid)

    def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr(doc, "put_suppression", broken)
    r = _dismiss(client, cid, PAIR)

    assert r.status_code == status, r.text
    assert r.json()["kind"] == kind
    assert store.revision.current(cid) == token
    assert PAIR in _listed(client, cid)
