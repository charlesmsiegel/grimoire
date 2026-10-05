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
    assert store.journal.read(cid)[-1]["label"] == "Mara's map pays_off Mara's oath"
    review.remove_link(cid, link["id"])
    assert doc.get_link(cid, link["id"]) is None
    refused = _refused(review.remove_link, cid, link["id"])
    assert (refused.status, refused.kind) == (404, "not_found")


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
