"""`store/continuity/review.py` -- validated, journalled alias and link writes.

Every refusal here is one a reader can act on (spec §5.1, §5.3, §5.7), and every
write lands in the journal so it can be undone from the Changes panel.
"""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app
from grimoire.store.campaigns import paths as campaigns_paths
from grimoire.store.continuity import doc, effective, review

S1, S2 = "001--saltmarch", "002--realm"


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
    store.plot.set_movement(cid, "maras-map", "Mara's map", "open", "Stolen.", S1)
    store.plot.set_movement(cid, "winifreds-chart", "Winifred's chart", "advanced", "Lost.", S2)
    store.plot.set_movement(cid, "realm-feud", "The Realm feud", "closed", "Ended.", S1)
    store.commitments.set_movement(cid, "mara-promise", "Mara's promise", "promise", "open",
                                   "by midwinter", "She swore.", S1)
    store.commitments.set_movement(cid, "mara-oath", "Mara's oath", "promise", "open",
                                   None, "She swore again.", S2)
    return cid


def _root(cid):
    return campaigns_paths.campaign_root(cid)


def _refused(fn, *args, **kwargs) -> review.RefusedError:
    with pytest.raises(review.RefusedError) as caught:
        fn(*args, **kwargs)
    return caught.value


# --------------------------------------------------------------- aliases


@pytest.mark.parametrize("ref,to,status,kind", [
    ("nocolon", "thread:winifreds-chart", 400, "bad_ref"),
    ("thread:maras-map", "thread:maras-map", 400, "self_alias"),
    ("thread:maras-map", "commitment:mara-oath", 400, "wrong_type"),
    ("event:x", "event:y", 400, "wrong_type"),
    ("thread:maras-map", "thread:gone", 404, "not_found"),
    ("thread:gone", "thread:winifreds-chart", 404, "not_found"),
])
def test_create_alias_refusals(cid, ref, to, status, kind):
    refused = _refused(review.create_alias, cid, ref, to)
    assert (refused.status, refused.kind) == (status, kind)
    assert doc.read(cid)["aliases"] == {}


def test_create_alias_cycle_refused(cid):
    review.create_alias(cid, "thread:maras-map", "thread:winifreds-chart")
    refused = _refused(review.create_alias, cid, "thread:winifreds-chart", "thread:maras-map")
    assert (refused.status, refused.kind) == (409, "alias_cycle")


def test_create_alias_exists_refused_then_replaced(cid):
    store.plot.set_movement(cid, "seraphines-map", "Seraphine's map", "open", "Torn.", S2)
    review.create_alias(cid, "thread:maras-map", "thread:winifreds-chart")
    before = len(store.journal.read(cid))
    refused = _refused(review.create_alias, cid, "thread:maras-map", "thread:seraphines-map")
    assert (refused.status, refused.kind, refused.extra) == (
        409, "alias_exists", {"to": "thread:winifreds-chart"})
    review.create_alias(cid, "thread:maras-map", "thread:seraphines-map", replace=True)
    rows = store.journal.read(cid)
    assert len(rows) == before + 1
    store.undo.undo(cid, rows[-1]["id"])
    assert doc.get_alias(cid, "thread:maras-map")["to"] == "thread:winifreds-chart"


def test_create_alias_unreadable_ledger_refused(cid):
    (_root(cid) / "plot.json").write_text("{ no", encoding="utf-8")
    refused = _refused(review.create_alias, cid, "thread:maras-map", "thread:winifreds-chart")
    assert (refused.status, refused.kind) == (409, "unreadable")


def test_create_alias_malformed_file_refused(cid):
    (_root(cid) / "continuity.json").write_text(json.dumps({"aliases": []}), encoding="utf-8")
    refused = _refused(review.create_alias, cid, "thread:maras-map", "thread:winifreds-chart")
    assert (refused.status, refused.kind) == (409, "malformed")


def test_create_alias_journals_and_undo_removes(cid):
    result = review.create_alias(cid, "thread:maras-map", "thread:winifreds-chart", note="same map")
    assert result["alias"]["to"] == "thread:winifreds-chart"
    assert result["alias"]["source"] == "manual"
    assert result["alias"]["note"] == "same map"
    row = store.journal.read(cid)[-1]
    assert row["kind"] == "continuity_alias"
    assert row["source"] == "manual"
    assert row["label"] == "Mara's map → merged into Winifred's chart"
    assert [r["id"] for r in effective.threads(cid)] == ["winifreds-chart"]
    store.undo.undo(cid, row["id"])
    assert doc.get_alias(cid, "thread:maras-map") is None


def test_liveness_mismatch_then_accept(cid):
    refused = _refused(review.create_alias, cid, "thread:maras-map", "thread:realm-feud")
    assert (refused.status, refused.kind) == (409, "liveness_mismatch")
    assert refused.extra["source"]["status"] == "open"
    assert refused.extra["canonical"] == {"ref": "thread:realm-feud", "status": "closed",
                                          "kind": "", "due": ""}
    review.create_alias(cid, "thread:maras-map", "thread:realm-feud",
                        accept_status_change=True)
    assert doc.get_alias(cid, "thread:maras-map")["to"] == "thread:realm-feud"


def test_liveness_extra_carries_both_dues(cid):
    store.commitments.set_movement(cid, "mara-oath", "", "", "fulfilled", None, "", S2)
    refused = _refused(review.create_alias, cid, "commitment:mara-promise",
                       "commitment:mara-oath")
    assert refused.extra["source"]["due"] == "by midwinter"
    assert refused.extra["canonical"]["due"] == ""


def test_alias_to_an_alias_source_is_stored_as_given_and_judged_on_final_canonical(cid):
    review.create_alias(cid, "thread:winifreds-chart", "thread:realm-feud", accept_status_change=True)
    refused = _refused(review.create_alias, cid, "thread:maras-map", "thread:winifreds-chart")
    assert refused.kind == "liveness_mismatch"
    assert refused.extra["canonical"]["ref"] == "thread:realm-feud"
    review.create_alias(cid, "thread:maras-map", "thread:winifreds-chart",
                        accept_status_change=True)
    assert doc.get_alias(cid, "thread:maras-map")["to"] == "thread:winifreds-chart"
    assert effective.live_canon(cid)["thread:maras-map"] == "thread:realm-feud"


def test_affected_lists_transitive_sources(cid):
    store.plot.set_movement(cid, "seraphines-map", "Seraphine's map", "open", "Torn.", S2)
    review.create_alias(cid, "thread:seraphines-map", "thread:maras-map")
    result = review.create_alias(cid, "thread:maras-map", "thread:winifreds-chart")
    assert result["affected"] == ["thread:seraphines-map"]


def test_remove_alias_journals_two_sided_label(cid):
    review.create_alias(cid, "thread:maras-map", "thread:winifreds-chart")
    review.remove_alias(cid, "thread:maras-map")
    assert doc.get_alias(cid, "thread:maras-map") is None
    assert store.journal.read(cid)[-1]["label"] == (
        "Mara's map — unmerged from Winifred's chart")
    refused = _refused(review.remove_alias, cid, "thread:maras-map")
    assert (refused.status, refused.kind) == (404, "not_found")


# ------------------------------------------------------------------ links


@pytest.mark.parametrize("a,b,relation,status,kind", [
    ("nocolon", "commitment:mara-oath", "pays_off", 400, "bad_ref"),
    ("thread:maras-map", "commitment:mara-oath", "same_as", 400, "invalid_relation"),
    ("commitment:mara-oath", "thread:maras-map", "pays_off", 400, "invalid_relation"),
    ("thread:maras-map", "commitment:gone", "pays_off", 404, "not_found"),
    ("thread:maras-map", "event:nope", "before", 404, "not_found"),
])
def test_create_link_refusals(cid, a, b, relation, status, kind):
    refused = _refused(review.create_link, cid, a, b, relation)
    assert (refused.status, refused.kind) == (status, kind)


def test_create_link_journals_and_round_trips(cid):
    result = review.create_link(cid, "thread:maras-map", "commitment:mara-oath", "pays_off",
                                scene=S1, note="the map is the price")
    link = result["link"]
    assert link["id"].startswith("l")
    assert (link["a"], link["b"], link["scene"]) == ("thread:maras-map", "commitment:mara-oath", S1)
    assert store.journal.read(cid)[-1]["label"] == "Mara's map pays off Mara's oath"
    review.remove_link(cid, link["id"])
    assert doc.get_link(cid, link["id"]) is None
    refused = _refused(review.remove_link, cid, link["id"])
    assert (refused.status, refused.kind) == (404, "not_found")


@pytest.mark.parametrize("a,b,relation,words", [
    ("thread:maras-map", "commitment:mara-oath", "pays_off", "pays off"),
    ("thread:maras-map", "thread:winifreds-chart", "subthread_of", "is a subthread of"),
    ("thread:maras-map", "commitment:mara-oath", "related_to", "is related to"),
])
def test_link_journal_labels_use_relation_words(client, cid, a, b, relation, words):
    """§30: a journal label reads as a sentence in the History rail, so the
    relation is written in words, never as the store's token."""
    titles = {"thread:maras-map": "Mara's map", "thread:winifreds-chart": "Winifred's chart",
              "commitment:mara-oath": "Mara's oath"}
    sentence = f"{titles[a]} {words} {titles[b]}"
    lid = review.create_link(cid, a, b, relation)["link"]["id"]
    created = store.journal.read(cid)[-1]["label"]
    review.remove_link(cid, lid)
    removed = store.journal.read(cid)[-1]["label"]
    review.create_link(cid, a, b, relation)
    before = len(store.journal.read(cid))
    r = client.delete(f"/api/campaigns/{cid}/ledger/threads/maras-map")
    assert r.status_code == 200, r.text
    forgotten = [row["label"] for row in store.journal.read(cid)[before:]
                 if row.get("kind") == "continuity_link"]
    assert created == sentence
    assert removed == f"{sentence} — removed"
    assert forgotten == [f"{sentence} — removed with deleted record"]
    assert not any("_" in label for label in (created, removed, *forgotten))


def test_create_link_dedupes_against_effective(cid):
    review.create_link(cid, "thread:winifreds-chart", "commitment:mara-oath", "pays_off")
    review.create_alias(cid, "thread:maras-map", "thread:winifreds-chart")
    refused = _refused(review.create_link, cid, "thread:maras-map",
                       "commitment:mara-oath", "pays_off")
    assert (refused.status, refused.kind) == (409, "link_exists")
    review.create_link(cid, "thread:winifreds-chart", "commitment:mara-oath", "related_to")
    refused = _refused(review.create_link, cid, "commitment:mara-oath",
                       "thread:winifreds-chart", "related_to")
    assert refused.kind == "link_exists"


def test_create_link_stores_canonical_endpoints(cid):
    review.create_alias(cid, "thread:maras-map", "thread:winifreds-chart")
    result = review.create_link(cid, "thread:maras-map", "commitment:mara-oath", "pays_off")
    assert result["link"]["a"] == "thread:winifreds-chart"
    assert result["given"] == {"a": "thread:maras-map", "b": "commitment:mara-oath"}


def test_self_link_refused_after_alias(cid):
    review.create_alias(cid, "thread:maras-map", "thread:winifreds-chart")
    refused = _refused(review.create_link, cid, "thread:maras-map", "thread:winifreds-chart",
                       "continues")
    assert (refused.status, refused.kind) == (400, "self_link")


# ------------------------------------------------------- deletion helpers


def test_merged_sources_lists_direct_sources(cid):
    review.create_alias(cid, "thread:maras-map", "thread:winifreds-chart")
    assert review.merged_sources(cid, "thread:winifreds-chart") == ["thread:maras-map"]
    assert review.merged_sources(cid, "thread:maras-map") == []


def test_merged_sources_strict_on_malformed(cid):
    (_root(cid) / "continuity.json").write_text("{ no", encoding="utf-8")
    with pytest.raises(doc.ContinuityError):
        review.merged_sources(cid, "thread:winifreds-chart")


def test_forget_ref_removes_aliases_and_links_and_journals_each(cid):
    store.plot.set_movement(cid, "seraphines-map", "Seraphine's map", "open", "Torn.", S2)
    review.create_alias(cid, "thread:maras-map", "thread:winifreds-chart")
    review.create_alias(cid, "thread:winifreds-chart", "thread:seraphines-map")
    lid = review.create_link(cid, "thread:seraphines-map", "commitment:mara-oath",
                             "pays_off")["link"]["id"]
    before = len(store.journal.read(cid))
    removed = review.forget_ref(cid, "thread:winifreds-chart")
    assert sorted(removed) == ["thread:maras-map", "thread:winifreds-chart"]
    assert doc.read(cid)["aliases"] == {}
    assert doc.get_link(cid, lid) is not None
    assert len(store.journal.read(cid)) == before + 2
    assert all(r["label"].endswith("removed with deleted record")
               for r in store.journal.read(cid)[before:])
    assert review.forget_ref(cid, "thread:seraphines-map") == [lid]
    assert doc.get_link(cid, lid) is None


def test_describe_falls_back_to_ref(cid):
    assert review.describe(cid, "thread:maras-map") == "Mara's map"
    assert review.describe(cid, "commitment:mara-oath") == "Mara's oath"
    assert review.describe(cid, "thread:gone") == "thread:gone"
    assert review.describe(cid, "nocolon") == "nocolon"
    (_root(cid) / "plot.json").write_text("{ no", encoding="utf-8")
    assert review.describe(cid, "thread:maras-map") == "thread:maras-map"


def test_forget_event_ref_needs_only_readable_links(cid):
    eid = store.events.create(cid, "The coronation", "2026-05-09", "")
    review.create_link(cid, "commitment:mara-oath", f"event:{eid}", "before")
    data = doc.read(cid)
    (_root(cid) / "continuity.json").write_text(
        json.dumps({"aliases": [], "links": data["links"]}), encoding="utf-8")
    assert len(review.forget_ref(cid, f"event:{eid}")) == 1
    assert doc.read(cid)["links"] == {}


def test_successful_merge_surfaces_a_due_the_canonical_lacks(cid):
    result = review.create_alias(cid, "commitment:mara-promise", "commitment:mara-oath")
    assert result["dues"] == {"source": "by midwinter", "canonical": ""}
    plain = review.create_alias(cid, "thread:maras-map", "thread:winifreds-chart")
    assert "dues" not in plain


def test_forget_ref_uses_the_given_name(cid):
    review.create_alias(cid, "thread:maras-map", "thread:winifreds-chart")
    store.plot.restore(cid, "winifreds-chart", None)
    review.forget_ref(cid, "thread:winifreds-chart", name="Winifred's chart")
    assert store.journal.read(cid)[-1]["label"] == (
        "Mara's map → merged into Winifred's chart — removed with deleted record")


def test_create_link_refuses_an_occupied_null_id(cid):
    from grimoire.store.continuity import canon
    lid = canon.link_id("pays_off", "thread:maras-map", "commitment:mara-oath")
    (_root(cid) / "continuity.json").write_text(json.dumps({"links": {lid: None}}),
                                                encoding="utf-8")
    refused = _refused(review.create_link, cid, "thread:maras-map", "commitment:mara-oath",
                       "pays_off")
    assert (refused.status, refused.kind) == (409, "link_exists")
    assert doc.read(cid)["links"] == {lid: None}


# ------------------------------------- a review staged before a merge (§22)
#
# Slice C redirects a row naming an alias source at STAGING time. A review
# staged before the merge still names the source, and its `before` token was
# taken from the source, so redirecting it at save would write a beat onto the
# canonical that `check_conflicts` never vouched for. The save is refused
# instead, naming the rows (Slice D Decision 19).

MAP, CHART, FEUD = "mara-s-map", "winifred-s-chart", "the-realm-feud"


@pytest.fixture
def staged(client):
    """(cid, sid): "Mara's map" and "Winifred's chart" seeded in an earlier
    scene, a key on the active connection, and a scene "Saltmarch" with two
    posts whose absorb moves "Mara's map" and "The Realm feud"."""
    from grimoire import routes

    from .llm_fakes import from_entries

    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-active"})
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    s0 = store.scenes.create_scene(cid, "Saltmarch docks")
    store.plot.set_movement(cid, MAP, "Mara's map", "open", "Stolen.", s0)
    store.plot.set_movement(cid, CHART, "Winifred's chart", "open", "Lost.", s0)
    store.plot.set_movement(cid, FEUD, "The Realm feud", "open", "Begun.", s0)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Saltmarch"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "Mara unrolled the map.")
    store.scenes.append_message(cid, sid, "assistant", "Winifred traced the coast.")
    extraction = json.dumps({
        "one_line": "o", "summary": "s", "keywords": [], "timeline_events": [],
        "plot_movements": [{"id": MAP, "status": "advanced",
                            "beat": "Mara found the coast on the map."},
                           {"id": FEUD, "status": "advanced",
                            "beat": "Winifred named the Realm feud aloud."}]})
    fake = from_entries([{"when": {"system_contains":
                                   "You are absorbing a completed role-play scene"},
                          "reply": extraction}])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    return cid, sid


def _absorbed(client, cid, sid) -> dict:
    from . import review_runs

    r = review_runs.absorb(client, cid, sid)
    assert r.status_code == 200, r.json()
    return r.json()


def _save(client, cid, sid, body, edits=None):
    return client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle", json={
        "one_line": body["one_line"], "summary": body["summary"],
        "keywords": body["keywords"], "timeline_events": body["timeline_events"],
        "edits": body["edits"] if edits is None else edits,
        "commit_token": body["commit_token"]})


def _merge(client, cid):
    r = client.post(f"/api/campaigns/{cid}/continuity/aliases",
                    json={"ref": f"thread:{MAP}", "to": f"thread:{CHART}"})
    assert r.status_code == 200, r.json()


def _plot_index(body, pid):
    return next(i for i, e in enumerate(body["edits"])
                if e["kind"] == "plot" and e["target"]["id"] == pid)


def test_a_review_staged_before_a_merge_is_refused_naming_the_merged_rows(client, staged):
    cid, sid = staged
    body = _absorbed(client, cid, sid)
    index = _plot_index(body, MAP)
    assert len(body["edits"]) > 1, "the batch needs a second row to keep"
    _merge(client, cid)
    before = store.plot.get(cid, MAP)
    r = _save(client, cid, sid, body)
    assert r.status_code == 409, r.json()
    answer = r.json()
    assert answer["kind"] == "edits_target_merged"
    assert answer["detail"] == ("Mara's map was merged into Winifred's chart after this "
                                "review was staged. Reject that row (or re-absorb) and "
                                "save again.")
    assert answer["edits"] == [{
        "index": index, "id": MAP, "label": body["edits"][index]["label"],
        "source": f"thread:{MAP}", "canonical": f"thread:{CHART}",
        "canonical_title": "Winifred's chart"}]
    assert store.plot.get(cid, MAP) == before
    assert store.chronicle.get_record(cid, sid) is None
    assert store.plot.get(cid, FEUD)["beats"][-1]["text"] == "Begun."


def test_saving_without_the_merged_rows_succeeds(client, staged):
    cid, sid = staged
    body = _absorbed(client, cid, sid)
    index = _plot_index(body, MAP)
    _merge(client, cid)
    before = store.plot.get(cid, MAP)
    # Refused first, on the same token: the refusal leaves it unspent.
    assert _save(client, cid, sid, body).status_code == 409
    kept = [e for i, e in enumerate(body["edits"]) if i != index]
    r = _save(client, cid, sid, body, edits=kept)
    assert r.status_code == 200, r.json()
    assert store.plot.get(cid, MAP) == before
    assert store.chronicle.get_record(cid, sid) is not None
    assert store.plot.get(cid, FEUD)["beats"][-1]["text"] == "Winifred named the Realm feud aloud."


def test_a_review_staged_after_the_merge_saves(client, staged):
    cid, sid = staged
    _merge(client, cid)
    body = _absorbed(client, cid, sid)
    edit = body["edits"][_plot_index(body, CHART)]
    assert "merged into Winifred's chart" in edit["label"]
    r = _save(client, cid, sid, body)
    assert r.status_code == 200, r.json()
    assert store.plot.get(cid, CHART)["beats"][-1]["text"] == "Mara found the coast on the map."


def test_a_replay_of_a_completed_save_is_not_refused(client, staged):
    cid, sid = staged
    body = _absorbed(client, cid, sid)
    first = _save(client, cid, sid, body)
    assert first.status_code == 200, first.json()
    _merge(client, cid)
    again = _save(client, cid, sid, body)
    assert again.status_code == 200, again.json()
    assert again.json() == first.json()


def test_merged_edit_targets_lists_plot_and_commitment_sources(cid):
    review.create_alias(cid, "thread:maras-map", "thread:winifreds-chart")
    review.create_alias(cid, "commitment:mara-promise", "commitment:mara-oath")
    edits = [
        {"kind": "plot", "target": {"kind": "plot", "id": "winifreds-chart"}, "label": "kept"},
        {"kind": "plot", "target": {"kind": "plot", "id": "maras-map"}, "label": "Map row"},
        {"kind": "lore", "target": {"kind": "lore", "id": "maras-map"}, "label": "lore"},
        {"kind": "commitment", "target": {"kind": "commitments", "id": "mara-promise"},
         "label": "Promise row"},
        {"kind": "plot", "target": "nonsense", "label": "malformed"},
        "not an edit",
    ]
    assert review.merged_edit_targets(cid, edits) == [
        {"index": 1, "id": "maras-map", "label": "Map row", "source": "thread:maras-map",
         "canonical": "thread:winifreds-chart", "canonical_title": "Winifred's chart"},
        {"index": 3, "id": "mara-promise", "label": "Promise row",
         "source": "commitment:mara-promise", "canonical": "commitment:mara-oath",
         "canonical_title": "Mara's oath"},
    ]


def test_merged_edit_targets_is_empty_over_a_malformed_file(cid):
    review.create_alias(cid, "thread:maras-map", "thread:winifreds-chart")
    (_root(cid) / "continuity.json").write_text("{ no", encoding="utf-8")
    edits = [{"kind": "plot", "target": {"kind": "plot", "id": "maras-map"}, "label": "x"}]
    assert review.merged_edit_targets(cid, edits) == []
