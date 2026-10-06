"""The continuity routes (`routes/continuity.py`, capstone spec §21).

A pure read of the reviewed decisions and what the effective view made of
them, plus journalled alias and link writes whose refusals carry a
machine-readable kind. The read must answer whatever shape the files are in --
it is what a reader opens to find out what is wrong.
"""

import importlib
import json
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

import grimoire.embeddings
import grimoire.llm
import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app
from grimoire.store.campaigns import paths as campaigns_paths
from grimoire.store.continuity import doc, drivers, graph, pending
from tests.llm_fakes import from_entries
from tests.test_continuity_apply import _cache
from tests.test_continuity_graph import _recorded
from tests.test_continuity_pressure import _BROKEN_PROVIDER_SRC, _plugin, _primary


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
    for title in ("Mara's map", "Winifred's chart"):
        r = client.post(f"/api/campaigns/{cid}/ledger/threads", json={"title": title})
        assert r.status_code == 200, r.text
    r = client.post(f"/api/campaigns/{cid}/ledger/commitments", json={"title": "Mara's oath"})
    assert r.status_code == 200, r.text
    return cid


def _root(cid):
    return campaigns_paths.campaign_root(cid)


def _get(client, cid):
    r = client.get(f"/api/campaigns/{cid}/continuity")
    assert r.status_code == 200, r.text
    return r.json()


def test_get_continuity_empty(client, cid):
    body = _get(client, cid)
    assert body["aliases"] == [] and body["links"] == [] and body["raw_links"] == []
    assert body["suppressions"] == []
    assert body["malformed"] == [] and body["unreadable"] == []
    assert body["matching"] == "basic"
    assert set(body["diagnostics"]) == {"dangling_aliases", "broken_links", "hidden_links",
                                        "unreadable"}


def test_alias_round_trip_and_journal(client, cid):
    r = client.post(f"/api/campaigns/{cid}/continuity/aliases",
                    json={"ref": "thread:mara-s-map", "to": "thread:winifred-s-chart"})
    assert r.status_code == 200, r.text
    assert r.json()["alias"]["to"] == "thread:winifred-s-chart"
    (alias,) = _get(client, cid)["aliases"]
    assert alias["ref"] == "thread:mara-s-map"
    assert alias["canonical"] == "thread:winifred-s-chart"
    assert (alias["title"], alias["to_title"]) == ("Mara's map", "Winifred's chart")
    assert alias["dangling"] is False
    newest = client.get(f"/api/campaigns/{cid}/journal").json()[0]
    assert newest["kind"] == "continuity_alias"
    r = client.delete(f"/api/campaigns/{cid}/continuity/aliases",
                      params={"ref": "thread:mara-s-map"})
    assert r.status_code == 200, r.text
    assert _get(client, cid)["aliases"] == []


@pytest.mark.parametrize("ref,to,status,kind", [
    ("thread:mara-s-map", "thread:mara-s-map", 400, "self_alias"),
    ("thread:mara-s-map", "thread:gone", 404, "not_found"),
    ("thread:mara-s-map", "commitment:mara-s-oath", 400, "wrong_type"),
])
def test_alias_refusals_map_to_http(client, cid, ref, to, status, kind):
    r = client.post(f"/api/campaigns/{cid}/continuity/aliases", json={"ref": ref, "to": to})
    assert r.status_code == status, r.text
    assert r.json()["kind"] == kind


def test_alias_cycle_refused(client, cid):
    url = f"/api/campaigns/{cid}/continuity/aliases"
    client.post(url, json={"ref": "thread:mara-s-map", "to": "thread:winifred-s-chart"})
    r = client.post(url, json={"ref": "thread:winifred-s-chart", "to": "thread:mara-s-map"})
    assert r.status_code == 409
    assert r.json()["kind"] == "alias_cycle"


def test_liveness_mismatch_carries_extra(client, cid):
    client.put(f"/api/campaigns/{cid}/ledger/threads/winifred-s-chart", json={"status": "closed"})
    r = client.post(f"/api/campaigns/{cid}/continuity/aliases",
                    json={"ref": "thread:mara-s-map", "to": "thread:winifred-s-chart"})
    assert r.status_code == 409
    detail = r.json()
    assert detail["kind"] == "liveness_mismatch"
    assert detail["canonical"]["status"] == "closed"
    r = client.post(f"/api/campaigns/{cid}/continuity/aliases",
                    json={"ref": "thread:mara-s-map", "to": "thread:winifred-s-chart",
                          "accept_status_change": True})
    assert r.status_code == 200, r.text


def test_delete_alias_ref_with_slash(client, cid):
    store.plot.set_movement(cid, "a/b", "A slashed id", "open", "", "")
    r = client.post(f"/api/campaigns/{cid}/continuity/aliases",
                    json={"ref": "thread:a/b", "to": "thread:winifred-s-chart"})
    assert r.status_code == 200, r.text
    r = client.delete(f"/api/campaigns/{cid}/continuity/aliases", params={"ref": "thread:a/b"})
    assert r.status_code == 200, r.text
    assert doc.get_alias(cid, "thread:a/b") is None


def test_links_round_trip(client, cid):
    r = client.post(f"/api/campaigns/{cid}/continuity/links",
                    json={"a": "thread:mara-s-map", "b": "commitment:mara-s-oath",
                          "relation": "pays_off"})
    assert r.status_code == 200, r.text
    lid = r.json()["link"]["id"]
    body = _get(client, cid)
    (link,) = body["links"]
    assert (link["id"], link["a_title"], link["b_title"]) == (
        lid, "Mara's map", "Mara's oath")
    assert body["raw_links"][0]["state"] == "ok"
    r = client.post(f"/api/campaigns/{cid}/continuity/links",
                    json={"a": "thread:mara-s-map", "b": "commitment:mara-s-oath",
                          "relation": "pays_off"})
    assert r.status_code == 409 and r.json()["kind"] == "link_exists"
    assert client.delete(f"/api/campaigns/{cid}/continuity/links/{lid}").status_code == 200
    assert client.delete(f"/api/campaigns/{cid}/continuity/links/{lid}").status_code == 404


def test_link_refusals_carry_no_store_token(client, cid):
    """§30: a refusal is the reader's sentence -- no relation token, no quoted
    store word. `detail` is the copy; the app's handler hoists `kind` beside it."""
    r = client.post(f"/api/campaigns/{cid}/continuity/links",
                    json={"a": "thread:mara-s-map", "b": "event:the-coronation",
                          "relation": "pays_off"})
    assert r.status_code == 400, r.text
    assert r.json()["kind"] == "invalid_relation"
    created = r.json()["detail"]
    assert created == "That kind of link cannot join these two records."
    key = _cache(cid, "possible_relation", ["thread:mara-s-map", "commitment:mara-s-oath"])
    r = client.post(f"/api/campaigns/{cid}/continuity/candidates/{key}/apply",
                    json={"op": "link", "from": "commitment:mara-s-oath",
                          "to": "thread:mara-s-map", "relation": "pays_off"})
    assert r.status_code == 400, r.text
    assert r.json()["kind"] == "invalid_relation"
    applied = r.json()["detail"]
    assert applied == "That kind of link cannot join these records in that direction."
    for detail in (created, applied):
        assert "_" not in detail and "'" not in detail


def test_raw_links_carry_their_ends_titles(client, cid):
    """A raw link names its ends the way an effective one does (d3), so a
    broken link is not left to show its refs."""
    r = client.post(f"/api/campaigns/{cid}/continuity/aliases",
                    json={"ref": "thread:winifred-s-chart", "to": "thread:mara-s-map"})
    assert r.status_code == 200, r.text
    doc.put_link(cid, "l1", {"a": "thread:winifred-s-chart", "b": "thread:mara-s-map",
                             "relation": "related_to"})
    doc.put_link(cid, "l2", {"a": "commitment:mara-s-oath", "b": "event:gone",
                             "relation": "before"})
    raw = {link["id"]: link for link in _get(client, cid)["raw_links"]}
    assert raw["l1"]["state"] == "broken"
    assert (raw["l1"]["a_title"], raw["l1"]["b_title"]) == ("Winifred's chart", "Mara's map")
    assert raw["l2"]["state"] == "broken"
    assert (raw["l2"]["a_title"], raw["l2"]["b_title"]) == ("Mara's oath", "a missing event")


def test_an_untitled_record_is_named_untitled_in_every_review_group(client, cid):
    """§12.8, §30: a record that is there with no title reads "an untitled
    thread" on every surface the read feeds -- Dismissed findings, Reviewed
    links / merges -- never its ref, and never "a missing thread"."""
    data = store.plot.read(cid)
    data["mara-s-map"]["title"] = ""
    (_root(cid) / "plot.json").write_text(json.dumps(data), encoding="utf-8")
    refs = ["thread:mara-s-map", "thread:winifred-s-chart"]
    fp = pending.fingerprint(pending.Current.load(cid), "possible_duplicate", refs)
    assert fp
    doc.put_suppression(cid, fp, {"kind": "possible_duplicate", "refs": refs,
                                  "decision": "dismiss", "created": ""})
    doc.put_link(cid, "l1", {"a": "thread:mara-s-map", "b": "commitment:mara-s-oath",
                             "relation": "related_to"})
    body = _get(client, cid)
    (entry,) = body["suppressions"]
    assert entry["live"] is True
    assert entry["titles"] == ["an untitled thread", "Winifred's chart"]
    (link,) = body["links"]
    assert (link["a_title"], link["b_title"]) == ("an untitled thread", "Mara's oath")
    (raw,) = body["raw_links"]
    assert (raw["a_title"], raw["b_title"]) == ("an untitled thread", "Mara's oath")

    doc.put_alias(cid, "thread:mara-s-map", {"to": "thread:winifred-s-chart"})
    (alias,) = _get(client, cid)["aliases"]
    assert alias["dangling"] is False
    assert (alias["title"], alias["to_title"]) == ("an untitled thread", "Winifred's chart")


def test_get_continuity_survives_garbled_plot(client, cid):
    doc.put_alias(cid, "thread:mara-s-map", {"to": "thread:winifred-s-chart"})
    doc.put_link(cid, "l1", {"a": "thread:mara-s-map", "b": "commitment:mara-s-oath",
                             "relation": "pays_off"})
    (_root(cid) / "plot.json").write_text("{ no", encoding="utf-8")
    body = _get(client, cid)
    assert body["unreadable"] == ["plot"]
    assert set(body["diagnostics"]) == {"dangling_aliases", "broken_links", "hidden_links",
                                        "unreadable"}
    (alias,) = body["aliases"]
    assert alias["dangling"] is False
    assert alias["title"] == "a thread that cannot be read right now"
    assert body["raw_links"][0]["state"] == "ok"


def test_get_continuity_reports_non_dict_records(client, cid):
    (_root(cid) / "continuity.json").write_text(json.dumps(
        {"aliases": {"thread:mara-s-map": "x"}, "links": {"l1": 3},
         "suppressions": {"fp1_x": "y"}}), encoding="utf-8")
    body = _get(client, cid)
    (alias,) = body["aliases"]
    assert (alias["to"], alias["dangling"], alias["reason"]) == ("", True, "malformed_record")
    (raw,) = body["raw_links"]
    assert (raw["id"], raw["state"], raw["reason"]) == ("l1", "broken", "malformed_record")
    assert body["suppressions"] == [{"fingerprint": "fp1_x", "kind": "", "refs": [],
                                     "decision": "", "created": "", "live": False,
                                     "titles": []}]


def test_get_continuity_reports_malformed_sections(client, cid):
    (_root(cid) / "continuity.json").write_text(json.dumps({"aliases": []}), encoding="utf-8")
    assert _get(client, cid)["malformed"] == ["aliases"]


@pytest.mark.parametrize("method,path,body", [
    ("get", "/continuity", None),
    ("get", "/continuity/drivers", None),
    ("get", "/continuity/graph", None),
    ("post", "/continuity/aliases", {"ref": "thread:a", "to": "thread:b"}),
    ("delete", "/continuity/aliases?ref=thread:a", None),
    ("post", "/continuity/links", {"a": "thread:a", "b": "thread:b", "relation": "continues"}),
    ("delete", "/continuity/links/l1", None),
    ("post", "/continuity/reconcile", None),
    ("get", "/continuity/candidates", None),
    ("post", "/continuity/candidates/possible_duplicate-0123456789abcdef/apply",
     {"op": "alias", "canonical": "thread:a"}),
    ("post", "/continuity/candidates/possible_duplicate-0123456789abcdef/dismiss", {}),
    ("delete", "/continuity/suppressions/fp1_x", None),
])
def test_unknown_campaign_404_on_every_route(client, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    r = getattr(client, method)(f"/api/campaigns/nobody{path}", **kwargs)
    assert r.status_code == 404, r.text


def test_continuity_routes_not_captured_by_entities(client, cid):
    assert "aliases" in _get(client, cid)
    r = client.get(f"/api/campaigns/{cid}/continuity/candidates")
    assert r.status_code == 200, r.text
    assert r.json()["candidates"] == []


def test_get_continuity_reads_each_ledger_once(client, cid, monkeypatch):
    """Labels come from the ledgers the read already loaded, not one re-parse of
    plot.json per row -- this read is meant to be cheap (spec §3.10)."""
    url = f"/api/campaigns/{cid}/continuity/links"
    for title in ("Seraphine's map", "Realm feud"):
        client.post(f"/api/campaigns/{cid}/ledger/threads", json={"title": title})
    for a in ("mara-s-map", "seraphine-s-map", "realm-feud"):
        client.post(url, json={"a": f"thread:{a}", "b": "commitment:mara-s-oath",
                               "relation": "pays_off"})
    calls = []
    real = store.plot.read
    monkeypatch.setattr(store.plot, "read", lambda c: calls.append(c) or real(c))
    assert len(_get(client, cid)["links"]) == 3
    assert len(calls) == 1


# ---- drivers ----------------------------------------------------------------

S1 = "001--saltmarch"


def _drivers(client, cid, **params):
    r = client.get(f"/api/campaigns/{cid}/continuity/drivers", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_get_drivers_matches_the_store(client, cid):
    store.clock.advance(cid, to="2026-05-10")
    store.pcs.create_pc(_root(cid), "Seraphine", [])
    store.appearances.appear(cid, S1, "pcs", "seraphine", "default", "player")
    store.plot.set_movement(cid, "mara-s-map", "", "advanced", "Mara traced the coast.", S1)
    body = _drivers(client, cid)
    assert body == drivers.snapshot(cid)
    assert {"thread:mara-s-map", "thread:winifred-s-chart",
            "commitment:mara-s-oath"} <= {d["ref"] for d in body["drivers"]}
    offscreen = _drivers(client, cid, offscreen="true")
    assert offscreen == drivers.snapshot(cid, offscreen=True)
    assert offscreen != body


def test_drivers_route_survives_a_raising_plugin(client, cid, tmp_path):
    store.clock.advance(cid, to="2026-05-10")
    store.chronicle.absorb(cid, {"id": S1, "one_line": "", "date": "2026-03-31"})
    store.plot.set_movement(cid, "mara-s-map", "", "advanced", "Mara traced the coast.", S1)
    store.commitments.set_movement(cid, "mara-s-oath", "", "promise", "open", "2026-05-12",
                                   "Mara swore it at the gate.", S1)
    before = {d["ref"]: d for d in _drivers(client, cid)["drivers"]}
    assert before["commitment:mara-s-oath"]["pressure"]["state"] == "due_soon"
    assert before["thread:mara-s-map"]["pressure"]["state"] == "stale"

    _plugin(tmp_path, "broken_test", _BROKEN_PROVIDER_SRC)
    _primary(cid, "broken-test-calendar")
    body = _drivers(client, cid)
    assert body["fixed"] is None
    found = {d["ref"]: d for d in body["drivers"]}
    for ref in ("thread:mara-s-map", "commitment:mara-s-oath"):
        assert found[ref]["pressure"] == {"state": "ok", "in_days": None, "friendly": ""}
    assert body["anchors"] == []


class _NoEmbeddings:
    def __init__(self, *args, **kwargs):
        raise AssertionError("the drivers read must not build an embeddings client")


def test_drivers_route_makes_no_model_call(client, cid, monkeypatch):
    monkeypatch.setattr(store.embed_space, "resolve", lambda *a, **k: {
        "model": "m", "base_url": "http://embeddings.invalid", "key": "", "space": "s"})
    monkeypatch.setattr(grimoire.embeddings, "EmbeddingsClient", _NoEmbeddings)
    client.app.dependency_overrides[routes.get_llm] = lambda: from_entries([])
    try:
        body = _drivers(client, cid)
    finally:
        client.app.dependency_overrides.pop(routes.get_llm, None)
    assert body["matching"] == "semantic"


# ---- the story graph --------------------------------------------------------


def _graph(client, cid, **params):
    r = client.get(f"/api/campaigns/{cid}/continuity/graph", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _graph_campaign(cid):
    """A scene, a thread beat in it, an event and an active idea."""
    store.clock.advance(cid, to="2026-05-10")
    sid = store.scenes.create_scene(cid, "Saltmarch harbour")
    store.plot.set_movement(cid, "mara-s-map", "", "advanced", "Mara traced the coast.", sid)
    store.events.create(cid, "The coronation", "2026-05-13")
    store.scene_ideas.add(cid, "Mara's map", "Mara reads the map.",
                          drivers=[{"ref": "thread:mara-s-map", "action": "advance"}])
    return sid


def test_graph_route_is_not_captured_by_entities(client, cid):
    assert set(_graph(client, cid)) == {"now", "nodes", "edges", "omitted"}


def test_graph_route_matches_the_store(client, cid):
    _graph_campaign(cid)
    body = _graph(client, cid)
    assert body == graph.build(cid)
    assert _graph(client, cid, lens="cast") == body


class _RecordingEmbeddings:
    """Records every construction rather than raising: `_attempt` and B's
    `_soft` both catch `Exception`, which an `AssertionError` is, so a raiser
    would be swallowed and the read would still answer 200."""
    built: ClassVar[list] = []

    def __init__(self, *args, **kwargs):
        type(self).built.append((args, kwargs))


def _no_model_recorders(monkeypatch) -> list:
    """Record every model call at the method that makes it, on the class.

    Every real embedding call goes through a client built at import time
    (`similarity._CLIENT` and its siblings) and every LLM call through an
    `LLMClient` the app built before this test ran, so a swapped class sees
    neither -- and `get_graph` resolves no `routes.get_llm`, so an override
    there would record nothing either. Patching the class attribute covers the
    instances that already exist; the class swap still catches a fresh one."""
    calls: list = []
    _RecordingEmbeddings.built = []
    monkeypatch.setattr(store.embed_space, "resolve", lambda *a, **k: {
        "model": "m", "base_url": "http://embeddings.invalid", "key": "", "space": "s"})
    _recorded(monkeypatch, grimoire.embeddings.EmbeddingsClient, "embed", calls)
    for name in ("stream", "complete", "list_models", "check"):
        _recorded(monkeypatch, grimoire.llm.LLMClient, name, calls)
    _recorded(monkeypatch, grimoire.llm, "LLMClient", calls)
    monkeypatch.setattr(grimoire.embeddings, "EmbeddingsClient", _RecordingEmbeddings)
    return calls


def test_graph_route_makes_no_model_call(client, cid, monkeypatch):
    _graph_campaign(cid)
    calls = _no_model_recorders(monkeypatch)
    body = _graph(client, cid)
    assert _RecordingEmbeddings.built == []
    assert calls == []
    assert body["omitted"] == []
    assert {"scene", "thread", "event", "idea"} <= {n["kind"] for n in body["nodes"]}


def test_graph_route_survives_a_raising_plugin(client, cid, tmp_path):
    store.clock.advance(cid, to="2026-05-10")
    store.chronicle.absorb(cid, {"id": S1, "one_line": "", "date": "2026-03-31"})
    store.plot.set_movement(cid, "mara-s-map", "", "advanced", "Mara traced the coast.", S1)
    store.commitments.set_movement(cid, "mara-s-oath", "", "promise", "open", "2026-05-12",
                                   "Mara swore it at the gate.", S1)
    _plugin(tmp_path, "broken_test", _BROKEN_PROVIDER_SRC)
    _primary(cid, "broken-test-calendar")

    body = _graph(client, cid)
    assert "calendar" in body["omitted"]
    kinds = [n["kind"] for n in body["nodes"]]
    assert "holiday" not in kinds and "birthday" not in kinds
    assert {"thread:mara-s-map", "thread:winifred-s-chart"} <= {
        n["id"] for n in body["nodes"] if n["kind"] == "thread"}
