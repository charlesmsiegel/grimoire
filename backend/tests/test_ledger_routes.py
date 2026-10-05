"""Hand edits to the continuity ledger (`routes/ledger.py`).

Until these routes the only writer of plot.json, commitments.json, facts.json
and relationships.json was `absorb/apply.py`, so a thread the model never
noticed had closed stayed open forever. What is checked here is the three
promises the module makes: the write lands, it is journalled as a MANUAL edit,
and Undo puts it back — that last one being the thing that makes a hand edit
accountable rather than the one change a campaign cannot explain.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def cid(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    return client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]


def _newest(client, cid) -> dict:
    """The most recent journal row. `GET /journal` answers newest-first."""
    rows = client.get(f"/api/campaigns/{cid}/journal").json()
    assert rows, "the edit was not journalled at all"
    return rows[0]


def _undo(client, cid, jid):
    r = client.post(f"/api/campaigns/{cid}/journal/{jid}/undo")
    assert r.status_code == 200, r.text
    return r


# --------------------------------------------------------------------- threads


def test_a_thread_can_be_opened_by_hand(client, cid):
    r = client.post(f"/api/campaigns/{cid}/ledger/threads",
                    json={"title": "Who fired the warehouse", "scene": "s1"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert pid == "who-fired-the-warehouse"
    rows = client.get(f"/api/campaigns/{cid}/ledger").json()["plot"]
    assert [t["title"] for t in rows] == ["Who fired the warehouse"]


def test_a_thread_needs_a_title(client, cid):
    assert client.post(f"/api/campaigns/{cid}/ledger/threads", json={"title": "  "}).status_code == 400


def test_closing_a_thread_keeps_its_status_visible_in_the_ledger(client, cid):
    # The request that started all of this: a thread the absorb never noticed
    # had ended.
    pid = client.post(f"/api/campaigns/{cid}/ledger/threads",
                      json={"title": "The debt to the Reeve"}).json()["id"]
    r = client.put(f"/api/campaigns/{cid}/ledger/threads/{pid}", json={"status": "closed"})
    assert r.status_code == 200, r.text
    assert [row["status"] for row in client.get(f"/api/campaigns/{cid}/ledger").json()["plot"]] == ["closed"]
    assert store.plot.open_threads(cid) == []
    assert store.plot.get(cid, pid)["status"] == "closed"


def test_closing_a_thread_does_not_blank_its_title(client, cid):
    # `set_movement` reads a blank title as "keep what is stored", and this
    # route relies on that: a payload that only closes a thread must not erase
    # what the thread was.
    pid = client.post(f"/api/campaigns/{cid}/ledger/threads",
                      json={"title": "The debt to the Reeve"}).json()["id"]
    client.put(f"/api/campaigns/{cid}/ledger/threads/{pid}", json={"status": "closed"})
    assert store.plot.get(cid, pid)["title"] == "The debt to the Reeve"


def test_editing_a_thread_can_append_a_beat_and_move_its_scene(client, cid):
    pid = client.post(f"/api/campaigns/{cid}/ledger/threads",
                      json={"title": "The debt", "scene": "s1"}).json()["id"]
    client.put(f"/api/campaigns/{cid}/ledger/threads/{pid}",
               json={"beat": "The Reeve asked again", "scene": "s4"})
    rec = store.plot.get(cid, pid)
    assert rec["last_scene"] == "s4"
    assert [b["text"] for b in rec["beats"]] == ["The Reeve asked again"]


def test_a_thread_edit_is_journalled_as_manual_and_undoes(client, cid):
    pid = client.post(f"/api/campaigns/{cid}/ledger/threads",
                      json={"title": "The debt"}).json()["id"]
    client.put(f"/api/campaigns/{cid}/ledger/threads/{pid}", json={"status": "closed"})
    row = _newest(client, cid)
    assert row["source"] == "manual"
    # A hand edit belongs to no scene. The route resolves `scene` into a label
    # object, so the empty id is what "no scene" looks like on the way out.
    assert row["scene"]["id"] == ""
    assert row["undoable"] is True
    _undo(client, cid, row["id"])
    assert store.plot.get(cid, pid)["status"] == "open"


def test_deleting_a_thread_removes_it_and_undo_brings_it_back(client, cid):
    pid = client.post(f"/api/campaigns/{cid}/ledger/threads",
                      json={"title": "A thread nobody pulled"}).json()["id"]
    assert client.delete(f"/api/campaigns/{cid}/ledger/threads/{pid}").status_code == 200
    assert store.plot.get(cid, pid) is None
    _undo(client, cid, _newest(client, cid)["id"])
    assert store.plot.get(cid, pid)["title"] == "A thread nobody pulled"


def test_an_unknown_thread_is_404_on_edit_and_delete(client, cid):
    assert client.put(f"/api/campaigns/{cid}/ledger/threads/ghost", json={}).status_code == 404
    assert client.delete(f"/api/campaigns/{cid}/ledger/threads/ghost").status_code == 404


# ----------------------------------------------------------------- commitments


def test_a_commitment_can_be_written_and_marked_done(client, cid):
    mid = client.post(f"/api/campaigns/{cid}/ledger/commitments",
                      json={"title": "Pay the Reeve by the spring tides",
                            "kind": "promise", "due": "the spring tides"}).json()["id"]
    assert store.commitments.get(cid, mid)["due"] == "the spring tides"
    client.put(f"/api/campaigns/{cid}/ledger/commitments/{mid}", json={"status": "fulfilled"})
    assert store.commitments.get(cid, mid)["status"] == "fulfilled"
    assert [row["status"] for row in client.get(f"/api/campaigns/{cid}/ledger").json()["commitments"]] == ["fulfilled"]
    assert store.commitments.open_commitments(cid) == []


def test_a_due_date_is_three_valued_all_the_way_down(client, cid):
    mid = client.post(f"/api/campaigns/{cid}/ledger/commitments",
                      json={"title": "Pay the Reeve", "due": "midnight"}).json()["id"]
    # Absent keeps it — the ordinary case for an edit that only moves a status.
    client.put(f"/api/campaigns/{cid}/ledger/commitments/{mid}", json={"status": "open"})
    assert store.commitments.get(cid, mid)["due"] == "midnight"
    # "" clears it: a scene that lifts a deadline without resolving the promise.
    client.put(f"/api/campaigns/{cid}/ledger/commitments/{mid}", json={"due": ""})
    assert store.commitments.get(cid, mid)["due"] == ""


def test_a_commitment_edit_is_journalled_and_undoes(client, cid):
    mid = client.post(f"/api/campaigns/{cid}/ledger/commitments",
                      json={"title": "Pay the Reeve"}).json()["id"]
    client.put(f"/api/campaigns/{cid}/ledger/commitments/{mid}", json={"status": "broken"})
    row = _newest(client, cid)
    assert row["source"] == "manual"
    _undo(client, cid, row["id"])
    assert store.commitments.get(cid, mid)["status"] == "open"


def test_deleting_a_commitment_undoes(client, cid):
    mid = client.post(f"/api/campaigns/{cid}/ledger/commitments",
                      json={"title": "A promise nobody made"}).json()["id"]
    client.delete(f"/api/campaigns/{cid}/ledger/commitments/{mid}")
    assert store.commitments.get(cid, mid) is None
    _undo(client, cid, _newest(client, cid)["id"])
    assert store.commitments.get(cid, mid)["title"] == "A promise nobody made"


# ------------------------------------------------------------------- the facts


def test_a_fact_can_be_recorded_by_hand(client, cid):
    r = client.post(f"/api/campaigns/{cid}/ledger/facts",
                    json={"text": "The bar drowns a cart at noon", "date": "spring", "scene": "s1"})
    assert r.status_code == 200, r.text
    fid = r.json()["id"]
    rows = client.get(f"/api/campaigns/{cid}/ledger").json()["facts"]
    assert [f["text"] for f in rows] == ["The bar drowns a cart at noon"]
    assert store.facts.get(cid, fid)["date"] == "spring"


def test_recording_a_fact_by_hand_undoes_to_nothing(client, cid):
    fid = client.post(f"/api/campaigns/{cid}/ledger/facts",
                      json={"text": "A fact typed in error"}).json()["id"]
    row = _newest(client, cid)
    assert row["source"] == "manual"
    _undo(client, cid, row["id"])
    assert store.facts.get(cid, fid) is None


def test_the_user_may_correct_a_fact_in_place(client, cid):
    # The whole point of `facts.set_text`: a mistyped fact is not a fact that
    # stopped being true, and retiring it would put a correction into the
    # history as though the fiction had changed.
    fid = client.post(f"/api/campaigns/{cid}/ledger/facts",
                      json={"text": "The ambassdor trusts the party", "scene": "s1"}).json()["id"]
    r = client.put(f"/api/campaigns/{cid}/ledger/facts/{fid}",
                   json={"text": "The ambassador trusts the party"})
    assert r.status_code == 200, r.text
    rec = store.facts.get(cid, fid)
    assert rec["text"] == "The ambassador trusts the party"
    assert rec["status"] == "active" and rec["superseded_by"] == ""


def test_a_correction_is_journalled_and_undoes(client, cid):
    fid = client.post(f"/api/campaigns/{cid}/ledger/facts",
                      json={"text": "The tide runs high"}).json()["id"]
    client.put(f"/api/campaigns/{cid}/ledger/facts/{fid}", json={"text": "The tide runs low"})
    row = _newest(client, cid)
    assert row["source"] == "manual"
    _undo(client, cid, row["id"])
    assert store.facts.get(cid, fid)["text"] == "The tide runs high"


def test_a_correction_cannot_blank_a_fact(client, cid):
    fid = client.post(f"/api/campaigns/{cid}/ledger/facts",
                      json={"text": "The tide runs high"}).json()["id"]
    assert client.put(f"/api/campaigns/{cid}/ledger/facts/{fid}",
                      json={"text": "  "}).status_code == 400
    assert store.facts.get(cid, fid)["text"] == "The tide runs high"


def test_retiring_a_fact_moves_it_to_the_retired_half(client, cid):
    fid = client.post(f"/api/campaigns/{cid}/ledger/facts",
                      json={"text": "The gate is watched", "scene": "s1"}).json()["id"]
    r = client.post(f"/api/campaigns/{cid}/ledger/facts/{fid}/retire", json={"scene": "s4"})
    assert r.status_code == 200, r.text
    body = client.get(f"/api/campaigns/{cid}/ledger").json()
    assert body["facts"] == []
    assert [f["text"] for f in body["retired"]] == ["The gate is watched"]
    assert store.facts.get(cid, fid)["retired_scene"] == "s4"


def test_retire_button_without_a_scene_retires_a_dated_fact(client, cid):
    fid = client.post(f"/api/campaigns/{cid}/ledger/facts",
                      json={"text": "The gate is watched", "scene": "s1"}).json()["id"]
    response = client.post(f"/api/campaigns/{cid}/ledger/facts/{fid}/retire", json={"scene": ""})
    assert response.status_code == 200, response.text
    assert store.facts.get(cid, fid)["status"] == "retired"


def test_retiring_an_already_retired_fact_says_so(client, cid):
    # A 409 rather than a silent no-op: a button that reports success and
    # changes nothing is worse than one that says why.
    fid = client.post(f"/api/campaigns/{cid}/ledger/facts",
                      json={"text": "The gate is watched"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/ledger/facts/{fid}/retire", json={})
    assert client.post(f"/api/campaigns/{cid}/ledger/facts/{fid}/retire",
                       json={}).status_code == 409


def test_a_fact_recorded_by_hand_can_supersede_another(client, cid):
    old = client.post(f"/api/campaigns/{cid}/ledger/facts",
                      json={"text": "The ambassador trusts the party", "scene": "s1"}).json()["id"]
    new = client.post(f"/api/campaigns/{cid}/ledger/facts",
                      json={"text": "The ambassador believes he was sold out",
                            "scene": "s4", "supersedes": old}).json()["id"]
    assert store.facts.get(cid, old)["superseded_by"] == new
    assert store.facts.get(cid, old)["status"] == "retired"


def test_deleting_a_fact_removes_it_and_undoes(client, cid):
    fid = client.post(f"/api/campaigns/{cid}/ledger/facts",
                      json={"text": "A fact the model invented"}).json()["id"]
    assert client.delete(f"/api/campaigns/{cid}/ledger/facts/{fid}").status_code == 200
    assert store.facts.get(cid, fid) is None
    _undo(client, cid, _newest(client, cid)["id"])
    assert store.facts.get(cid, fid)["text"] == "A fact the model invented"


def test_an_unknown_fact_is_404_everywhere(client, cid):
    assert client.put(f"/api/campaigns/{cid}/ledger/facts/f99", json={"text": "x"}).status_code == 404
    assert client.post(f"/api/campaigns/{cid}/ledger/facts/f99/retire", json={}).status_code == 404
    assert client.delete(f"/api/campaigns/{cid}/ledger/facts/f99").status_code == 404


# ----------------------------------------------------------- the relationships


def test_a_feeling_can_be_set_by_hand_and_undone(client, cid):
    r = client.put(f"/api/campaigns/{cid}/ledger/relationships",
                   json={"a": "mara", "b": "seraphine", "trust": 4, "affection": 2,
                         "tension": 1, "note": "owes her a boat"})
    assert r.status_code == 200, r.text
    assert store.relationships.get_feeling(cid, "mara", "seraphine")["trust"] == 4
    _undo(client, cid, _newest(client, cid)["id"])
    assert store.relationships.get_feeling(cid, "mara", "seraphine") is None


def test_a_bond_is_the_undirected_record(client, cid):
    client.put(f"/api/campaigns/{cid}/ledger/relationships",
               json={"a": "mara", "b": "seraphine", "bond": "sisters", "scene": "s1"})
    assert store.relationships.get_bond(cid, "mara", "seraphine")["type"] == "sisters"
    # ...and setting a bond does not invent a feeling.
    assert store.relationships.get_feeling(cid, "mara", "seraphine") is None


def test_a_standing_needs_both_people(client, cid):
    assert client.put(f"/api/campaigns/{cid}/ledger/relationships",
                      json={"a": "mara", "b": " "}).status_code == 400


def test_deleting_a_feeling_leaves_the_other_direction_alone(client, cid):
    # The two directions are separate readings, which is why the delete is
    # directional unless it says `bond`.
    client.put(f"/api/campaigns/{cid}/ledger/relationships",
               json={"a": "mara", "b": "seraphine", "trust": 4})
    client.put(f"/api/campaigns/{cid}/ledger/relationships",
               json={"a": "seraphine", "b": "mara", "trust": 1})
    client.delete(f"/api/campaigns/{cid}/ledger/relationships",
                  params={"a": "mara", "b": "seraphine"})
    assert store.relationships.get_feeling(cid, "mara", "seraphine") is None
    assert store.relationships.get_feeling(cid, "seraphine", "mara")["trust"] == 1


# --------------------------------------------------------------- the chronicle


def test_a_chronicle_line_can_be_corrected(client, cid):
    store.chronicle.absorb(cid, {"id": "s1", "one_line": "They meet at the gate",
                                 "summary": "a long summary", "date": "the third night"})
    r = client.put(f"/api/campaigns/{cid}/ledger/chronicle/s1",
                   json={"one_line": "They meet at the tide gate"})
    assert r.status_code == 200, r.text
    rec = store.chronicle.get_record(cid, "s1")
    assert rec["one_line"] == "They meet at the tide gate"
    # The long form and the reading around it are not this route's business.
    assert rec["summary"] == "a long summary"


def test_correcting_a_line_does_not_restamp_the_absorption(client, cid):
    # `absorbed` records when the pass read the transcript, which a hand edit
    # does not change.
    store.chronicle.absorb(cid, {"id": "s1", "one_line": "They meet", "summary": ""})
    was = store.chronicle.get_record(cid, "s1")["absorbed"]
    client.put(f"/api/campaigns/{cid}/ledger/chronicle/s1", json={"one_line": "They part"})
    assert store.chronicle.get_record(cid, "s1")["absorbed"] == was


def test_a_chronicle_edit_undoes(client, cid):
    store.chronicle.absorb(cid, {"id": "s1", "one_line": "They meet", "summary": ""})
    client.put(f"/api/campaigns/{cid}/ledger/chronicle/s1", json={"one_line": "They part"})
    _undo(client, cid, _newest(client, cid)["id"])
    assert store.chronicle.get_record(cid, "s1")["one_line"] == "They meet"


def test_a_scene_with_no_chronicle_record_is_404(client, cid):
    assert client.put(f"/api/campaigns/{cid}/ledger/chronicle/s9",
                      json={"one_line": "x"}).status_code == 404


# ------------------------------------------------------------------- the shape


def test_every_ledger_write_refuses_an_unknown_campaign(client):
    calls = [
        ("post", "/api/campaigns/ghost/ledger/threads", {"title": "x"}),
        ("put", "/api/campaigns/ghost/ledger/threads/t", {}),
        ("delete", "/api/campaigns/ghost/ledger/threads/t", None),
        ("post", "/api/campaigns/ghost/ledger/commitments", {"title": "x"}),
        ("put", "/api/campaigns/ghost/ledger/commitments/m", {}),
        ("delete", "/api/campaigns/ghost/ledger/commitments/m", None),
        ("post", "/api/campaigns/ghost/ledger/facts", {"text": "x"}),
        ("put", "/api/campaigns/ghost/ledger/facts/f1", {"text": "x"}),
        ("post", "/api/campaigns/ghost/ledger/facts/f1/retire", {}),
        ("delete", "/api/campaigns/ghost/ledger/facts/f1", None),
        ("put", "/api/campaigns/ghost/ledger/chronicle/s1", {"one_line": "x"}),
    ]
    for method, path, body in calls:
        r = getattr(client, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, f"{method} {path} answered {r.status_code}"


# ----------------------------------------- what the first review round found


def test_re_recording_the_same_fact_does_not_arm_an_undo_that_deletes_it(client, cid):
    # `facts.record` DEDUPES: the same text for the same scene returns the id
    # already holding it. Journalling that as a create armed an Undo that
    # deleted a fact which was there before the request.
    first = client.post(f"/api/campaigns/{cid}/ledger/facts",
                        json={"text": "The tide runs high", "scene": "s1"}).json()["id"]
    before = len(client.get(f"/api/campaigns/{cid}/journal").json())
    again = client.post(f"/api/campaigns/{cid}/ledger/facts",
                        json={"text": "The tide runs high", "scene": "s1"}).json()["id"]
    assert again == first
    # Nothing was created, so nothing is journalled as a creation.
    assert len(client.get(f"/api/campaigns/{cid}/journal").json()) == before
    assert store.facts.get(cid, first) is not None


def test_undoing_a_hand_supersession_puts_the_predecessor_back(client, cid):
    # `record` retires the predecessor and files the replacement in ONE write,
    # so a reversal that removed only the new fact left the old one retired by
    # an id that no longer existed.
    old = client.post(f"/api/campaigns/{cid}/ledger/facts",
                      json={"text": "The ambassador trusts the party", "scene": "s1"}).json()["id"]
    new = client.post(f"/api/campaigns/{cid}/ledger/facts",
                      json={"text": "The ambassador believes he was sold out",
                            "scene": "s4", "supersedes": old}).json()["id"]
    assert store.facts.get(cid, old)["status"] == "retired"

    rows = client.get(f"/api/campaigns/{cid}/journal").json()
    # Two rows, newest first: the predecessor's retirement, then the creation.
    _undo(client, cid, rows[0]["id"])
    _undo(client, cid, rows[1]["id"])
    assert store.facts.get(cid, new) is None
    restored = store.facts.get(cid, old)
    assert restored["status"] == "active"
    assert restored["superseded_by"] == ""


def test_moving_only_a_note_leaves_the_meters_where_they_were(client, cid):
    # `set_feeling` writes whole records, so a payload that omitted the meters
    # and let them default to 0 reset a 4/2/1 standing to nothing.
    client.put(f"/api/campaigns/{cid}/ledger/relationships",
               json={"a": "mara", "b": "seraphine", "trust": 4, "affection": 2,
                     "tension": 1, "note": "owes her a boat"})
    client.put(f"/api/campaigns/{cid}/ledger/relationships",
               json={"a": "mara", "b": "seraphine", "note": "paid the boat back"})
    held = store.relationships.get_feeling(cid, "mara", "seraphine")
    assert (held["trust"], held["affection"], held["tension"]) == (4, 2, 1)
    assert held["note"] == "paid the boat back"


def test_moving_only_a_meter_leaves_the_note_alone(client, cid):
    client.put(f"/api/campaigns/{cid}/ledger/relationships",
               json={"a": "mara", "b": "seraphine", "trust": 4, "note": "owes her a boat"})
    client.put(f"/api/campaigns/{cid}/ledger/relationships",
               json={"a": "mara", "b": "seraphine", "trust": 1})
    held = store.relationships.get_feeling(cid, "mara", "seraphine")
    assert held["trust"] == 1
    assert held["note"] == "owes her a boat"


def test_a_blank_note_still_clears_it(client, cid):
    # Omitted keeps, `""` clears: the two must not collapse into one.
    client.put(f"/api/campaigns/{cid}/ledger/relationships",
               json={"a": "mara", "b": "seraphine", "trust": 4, "note": "owes her a boat"})
    client.put(f"/api/campaigns/{cid}/ledger/relationships",
               json={"a": "mara", "b": "seraphine", "note": ""})
    assert store.relationships.get_feeling(cid, "mara", "seraphine")["note"] == ""


def test_a_thread_edit_that_names_no_status_leaves_the_stored_one(client, cid):
    # The route-side half of the frontend's dirty-only send: an absorb can
    # advance a thread while an editor is open, and a title fix must not
    # revert it.
    pid = client.post(f"/api/campaigns/{cid}/ledger/threads",
                      json={"title": "The debt", "status": "open"}).json()["id"]
    store.plot.set_movement(cid, pid, "", "advanced", "", "s9")   # as an absorb would
    client.put(f"/api/campaigns/{cid}/ledger/threads/{pid}", json={"title": "The debt, restated"})
    rec = store.plot.get(cid, pid)
    assert rec["title"] == "The debt, restated"
    assert rec["status"] == "advanced"      # the absorb's move survived
    assert rec["last_scene"] == "s9"


def test_correcting_a_retired_fact_past_its_retirement_is_refused(client, cid):
    fid = client.post(f"/api/campaigns/{cid}/ledger/facts",
                      json={"text": "The gate is watched", "scene": "003"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/ledger/facts/{fid}/retire", json={"scene": "004"})
    r = client.put(f"/api/campaigns/{cid}/ledger/facts/{fid}", json={"scene": "005"})
    assert r.status_code == 400
    assert store.facts.get(cid, fid)["scene"] == "003"


# ------------------------------------------------- merges (continuity capstone)
#
# A thread or commitment merged into another (an alias in continuity.json) is
# hidden behind its canonical, so a hand edit addressed to it lands on the
# canonical -- but only when the alias is LIVE: a merge into a record that is
# gone, or a hand-edited cross-type one, must never redirect a write into a
# record that does not exist. Deletes cascade, because these ids are slugs a
# later create can hand back.

from grimoire.store.continuity import doc as continuity_doc  # noqa: E402


def _thread_ids(client, cid, *titles):
    return [client.post(f"/api/campaigns/{cid}/ledger/threads",
                        json={"title": t}).json()["id"] for t in titles]


def _merge(client, cid, ref, to):
    r = client.post(f"/api/campaigns/{cid}/continuity/aliases", json={"ref": ref, "to": to})
    assert r.status_code == 200, r.text


def _beat_texts(cid, pid):
    return [b["text"] for b in (store.plot.get(cid, pid) or {}).get("beats", [])]


def test_put_thread_on_alias_source_writes_canonical(client, cid):
    src, canonical = _thread_ids(client, cid, "The missing map", "The lost chart")
    _merge(client, cid, f"thread:{src}", f"thread:{canonical}")
    r = client.put(f"/api/campaigns/{cid}/ledger/threads/{src}", json={"beat": "Mara found it."})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == canonical
    assert _beat_texts(cid, canonical) == ["Mara found it."]
    assert _beat_texts(cid, src) == []
    assert "via merged The missing map" in _newest(client, cid)["label"]


def test_put_thread_physical_flag_writes_source(client, cid):
    src, canonical = _thread_ids(client, cid, "The missing map", "The lost chart")
    _merge(client, cid, f"thread:{src}", f"thread:{canonical}")
    r = client.put(f"/api/campaigns/{cid}/ledger/threads/{src}", params={"physical": True},
                   json={"beat": "Mara found it."})
    assert r.status_code == 200 and r.json()["id"] == src
    assert _beat_texts(cid, src) == ["Mara found it."]


def test_put_thread_on_dangling_alias_source_writes_source(client, cid):
    (src,) = _thread_ids(client, cid, "The missing map")
    continuity_doc.put_alias(cid, f"thread:{src}", {"to": "thread:gone"})
    r = client.put(f"/api/campaigns/{cid}/ledger/threads/{src}", json={"beat": "Still here."})
    assert r.status_code == 200 and r.json()["id"] == src
    assert store.plot.get(cid, "gone") is None


def test_put_thread_on_wrong_type_alias_source_writes_source(client, cid):
    (src,) = _thread_ids(client, cid, "The missing map")
    mid = client.post(f"/api/campaigns/{cid}/ledger/commitments",
                      json={"title": "Mara's oath"}).json()["id"]
    continuity_doc.put_alias(cid, f"thread:{src}", {"to": f"commitment:{mid}"})
    r = client.put(f"/api/campaigns/{cid}/ledger/threads/{src}", json={"beat": "Still here."})
    assert r.status_code == 200 and r.json()["id"] == src
    assert store.plot.get(cid, mid) is None


def test_put_commitment_on_alias_source_writes_canonical(client, cid):
    src, canonical = [client.post(f"/api/campaigns/{cid}/ledger/commitments",
                                  json={"title": t}).json()["id"]
                      for t in ("Mara's promise", "Mara's oath")]
    _merge(client, cid, f"commitment:{src}", f"commitment:{canonical}")
    r = client.put(f"/api/campaigns/{cid}/ledger/commitments/{src}", json={"due": "midwinter"})
    assert r.status_code == 200 and r.json()["id"] == canonical
    assert store.commitments.get(cid, canonical)["due"] == "midwinter"
    assert store.commitments.get(cid, src)["due"] == ""


def test_delete_alias_target_refused_then_forced(client, cid):
    src, canonical = _thread_ids(client, cid, "The missing map", "The lost chart")
    _merge(client, cid, f"thread:{src}", f"thread:{canonical}")
    r = client.delete(f"/api/campaigns/{cid}/ledger/threads/{canonical}")
    assert r.status_code == 409
    assert r.json()["kind"] == "has_merged_records"
    assert r.json()["refs"] == [f"thread:{src}"]
    assert store.plot.get(cid, canonical) is not None
    r = client.delete(f"/api/campaigns/{cid}/ledger/threads/{canonical}",
                      params={"force": True})
    assert r.status_code == 200, r.text
    assert store.plot.get(cid, canonical) is None
    assert continuity_doc.get_alias(cid, f"thread:{src}") is None
    kinds = [row["kind"] for row in client.get(f"/api/campaigns/{cid}/journal").json()[:2]]
    assert sorted(kinds) == ["continuity_alias", "plot"]


def test_delete_source_removes_its_alias_and_links(client, cid):
    src, canonical = _thread_ids(client, cid, "The missing map", "The lost chart")
    mid = client.post(f"/api/campaigns/{cid}/ledger/commitments",
                      json={"title": "Mara's oath"}).json()["id"]
    r = client.post(f"/api/campaigns/{cid}/continuity/links",
                    json={"a": f"thread:{src}", "b": f"commitment:{mid}", "relation": "pays_off"})
    lid = r.json()["link"]["id"]
    _merge(client, cid, f"thread:{src}", f"thread:{canonical}")
    assert client.delete(f"/api/campaigns/{cid}/ledger/threads/{src}").status_code == 200
    assert continuity_doc.get_alias(cid, f"thread:{src}") is None
    assert continuity_doc.get_link(cid, lid) is None


def test_delete_refused_when_continuity_malformed(client, cid):
    (pid,) = _thread_ids(client, cid, "The missing map")
    path = store.campaigns.campaign_root(cid) / "continuity.json"
    path.write_text("{ no", encoding="utf-8")
    r = client.delete(f"/api/campaigns/{cid}/ledger/threads/{pid}")
    assert r.status_code == 409 and r.json()["kind"] == "malformed"
    assert store.plot.get(cid, pid) is not None
    assert path.read_text(encoding="utf-8") == "{ no"


def test_delete_commitment_target_refused(client, cid):
    src, canonical = [client.post(f"/api/campaigns/{cid}/ledger/commitments",
                                  json={"title": t}).json()["id"]
                      for t in ("Mara's promise", "Mara's oath")]
    _merge(client, cid, f"commitment:{src}", f"commitment:{canonical}")
    r = client.delete(f"/api/campaigns/{cid}/ledger/commitments/{canonical}")
    assert r.status_code == 409 and r.json()["kind"] == "has_merged_records"


def test_delete_event_removes_links_and_recreation_does_not_reattach(client, cid):
    mid = client.post(f"/api/campaigns/{cid}/ledger/commitments",
                      json={"title": "Mara's oath"}).json()["id"]
    eid = client.post(f"/api/campaigns/{cid}/events",
                      json={"name": "The Tribunal", "date": "2026-05-09"}).json()["id"]
    r = client.post(f"/api/campaigns/{cid}/continuity/links",
                    json={"a": f"commitment:{mid}", "b": f"event:{eid}", "relation": "before"})
    assert r.status_code == 200, r.text
    assert client.delete(f"/api/campaigns/{cid}/events/{eid}").status_code == 200
    again = client.post(f"/api/campaigns/{cid}/events",
                        json={"name": "The Tribunal", "date": "2026-06-01"}).json()["id"]
    assert again == eid
    body = client.get(f"/api/campaigns/{cid}/continuity").json()
    assert body["links"] == [] and body["raw_links"] == []


def test_put_thread_refused_when_aliases_malformed(client, cid):
    """A merge that cannot be read is no reason to write to a hidden record
    (spec §3.9): the edit is refused before anything is written."""
    src, canonical = _thread_ids(client, cid, "The missing map", "The lost chart")
    _merge(client, cid, f"thread:{src}", f"thread:{canonical}")
    path = store.campaigns.campaign_root(cid) / "continuity.json"
    path.write_text("{ no", encoding="utf-8")
    r = client.put(f"/api/campaigns/{cid}/ledger/threads/{src}", json={"status": "advanced"})
    assert r.status_code == 409 and r.json()["kind"] == "malformed"
    assert store.plot.get(cid, src)["status"] == "open"
    r = client.put(f"/api/campaigns/{cid}/ledger/threads/{src}", params={"physical": True},
                   json={"status": "advanced"})
    assert r.status_code == 200


def test_redirected_put_keeps_the_canonical_title(client, cid):
    """The ledger editor sends the row's title with every save, so a status-only
    edit of a merged record must not rename the record it lands on."""
    src, canonical = _thread_ids(client, cid, "The missing map", "The lost chart")
    _merge(client, cid, f"thread:{src}", f"thread:{canonical}")
    r = client.put(f"/api/campaigns/{cid}/ledger/threads/{src}",
                   json={"title": "The missing map", "status": "advanced"})
    assert r.status_code == 200 and r.json()["id"] == canonical
    assert store.plot.get(cid, canonical)["title"] == "The lost chart"
    assert store.plot.get(cid, canonical)["status"] == "advanced"


def test_cascade_journal_rows_name_the_deleted_record(client, cid):
    src, canonical = _thread_ids(client, cid, "The missing map", "The lost chart")
    _merge(client, cid, f"thread:{src}", f"thread:{canonical}")
    client.delete(f"/api/campaigns/{cid}/ledger/threads/{canonical}", params={"force": True})
    labels = [row["label"] for row in client.get(f"/api/campaigns/{cid}/journal").json()]
    assert "The missing map → merged into The lost chart — removed with deleted record" in labels
