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

S1, S2 = "001--saltmarch", "002--tribunal"


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
    store.plot.set_movement(cid, "missing-map", "The missing map", "open", "Stolen.", S1)
    store.plot.set_movement(cid, "lost-chart", "The lost chart", "advanced", "Lost.", S2)
    store.plot.set_movement(cid, "old-feud", "The old feud", "closed", "Ended.", S1)
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
    ("nocolon", "thread:lost-chart", 400, "bad_ref"),
    ("thread:missing-map", "thread:missing-map", 400, "self_alias"),
    ("thread:missing-map", "commitment:mara-oath", 400, "wrong_type"),
    ("event:x", "event:y", 400, "wrong_type"),
    ("thread:missing-map", "thread:gone", 404, "not_found"),
    ("thread:gone", "thread:lost-chart", 404, "not_found"),
])
def test_create_alias_refusals(cid, ref, to, status, kind):
    refused = _refused(review.create_alias, cid, ref, to)
    assert (refused.status, refused.kind) == (status, kind)
    assert doc.read(cid)["aliases"] == {}


def test_create_alias_cycle_refused(cid):
    review.create_alias(cid, "thread:missing-map", "thread:lost-chart")
    refused = _refused(review.create_alias, cid, "thread:lost-chart", "thread:missing-map")
    assert (refused.status, refused.kind) == (409, "alias_cycle")


def test_create_alias_exists_refused_then_replaced(cid):
    store.plot.set_movement(cid, "torn-map", "The torn map", "open", "Torn.", S2)
    review.create_alias(cid, "thread:missing-map", "thread:lost-chart")
    before = len(store.journal.read(cid))
    refused = _refused(review.create_alias, cid, "thread:missing-map", "thread:torn-map")
    assert (refused.status, refused.kind, refused.extra) == (
        409, "alias_exists", {"to": "thread:lost-chart"})
    review.create_alias(cid, "thread:missing-map", "thread:torn-map", replace=True)
    rows = store.journal.read(cid)
    assert len(rows) == before + 1
    store.undo.undo(cid, rows[-1]["id"])
    assert doc.get_alias(cid, "thread:missing-map")["to"] == "thread:lost-chart"


def test_create_alias_unreadable_ledger_refused(cid):
    (_root(cid) / "plot.json").write_text("{ no", encoding="utf-8")
    refused = _refused(review.create_alias, cid, "thread:missing-map", "thread:lost-chart")
    assert (refused.status, refused.kind) == (409, "unreadable")


def test_create_alias_malformed_file_refused(cid):
    (_root(cid) / "continuity.json").write_text(json.dumps({"aliases": []}), encoding="utf-8")
    refused = _refused(review.create_alias, cid, "thread:missing-map", "thread:lost-chart")
    assert (refused.status, refused.kind) == (409, "malformed")


def test_create_alias_journals_and_undo_removes(cid):
    result = review.create_alias(cid, "thread:missing-map", "thread:lost-chart", note="same map")
    assert result["alias"]["to"] == "thread:lost-chart"
    assert result["alias"]["source"] == "manual"
    assert result["alias"]["note"] == "same map"
    row = store.journal.read(cid)[-1]
    assert row["kind"] == "continuity_alias"
    assert row["source"] == "manual"
    assert row["label"] == "The missing map → merged into The lost chart"
    assert [r["id"] for r in effective.threads(cid)] == ["lost-chart"]
    store.undo.undo(cid, row["id"])
    assert doc.get_alias(cid, "thread:missing-map") is None


def test_liveness_mismatch_then_accept(cid):
    refused = _refused(review.create_alias, cid, "thread:missing-map", "thread:old-feud")
    assert (refused.status, refused.kind) == (409, "liveness_mismatch")
    assert refused.extra["source"]["status"] == "open"
    assert refused.extra["canonical"] == {"ref": "thread:old-feud", "status": "closed",
                                          "kind": "", "due": ""}
    review.create_alias(cid, "thread:missing-map", "thread:old-feud",
                        accept_status_change=True)
    assert doc.get_alias(cid, "thread:missing-map")["to"] == "thread:old-feud"


def test_liveness_extra_carries_both_dues(cid):
    store.commitments.set_movement(cid, "mara-oath", "", "", "fulfilled", None, "", S2)
    refused = _refused(review.create_alias, cid, "commitment:mara-promise",
                       "commitment:mara-oath")
    assert refused.extra["source"]["due"] == "by midwinter"
    assert refused.extra["canonical"]["due"] == ""


def test_alias_to_an_alias_source_is_stored_as_given_and_judged_on_final_canonical(cid):
    review.create_alias(cid, "thread:lost-chart", "thread:old-feud", accept_status_change=True)
    refused = _refused(review.create_alias, cid, "thread:missing-map", "thread:lost-chart")
    assert refused.kind == "liveness_mismatch"
    assert refused.extra["canonical"]["ref"] == "thread:old-feud"
    review.create_alias(cid, "thread:missing-map", "thread:lost-chart",
                        accept_status_change=True)
    assert doc.get_alias(cid, "thread:missing-map")["to"] == "thread:lost-chart"
    assert effective.live_canon(cid)["thread:missing-map"] == "thread:old-feud"


def test_affected_lists_transitive_sources(cid):
    store.plot.set_movement(cid, "torn-map", "The torn map", "open", "Torn.", S2)
    review.create_alias(cid, "thread:torn-map", "thread:missing-map")
    result = review.create_alias(cid, "thread:missing-map", "thread:lost-chart")
    assert result["affected"] == ["thread:torn-map"]


def test_remove_alias_journals_two_sided_label(cid):
    review.create_alias(cid, "thread:missing-map", "thread:lost-chart")
    review.remove_alias(cid, "thread:missing-map")
    assert doc.get_alias(cid, "thread:missing-map") is None
    assert store.journal.read(cid)[-1]["label"] == (
        "The missing map — unmerged from The lost chart")
    refused = _refused(review.remove_alias, cid, "thread:missing-map")
    assert (refused.status, refused.kind) == (404, "not_found")


# ------------------------------------------------------------------ links


@pytest.mark.parametrize("a,b,relation,status,kind", [
    ("nocolon", "commitment:mara-oath", "pays_off", 400, "bad_ref"),
    ("thread:missing-map", "commitment:mara-oath", "same_as", 400, "invalid_relation"),
    ("commitment:mara-oath", "thread:missing-map", "pays_off", 400, "invalid_relation"),
    ("thread:missing-map", "commitment:gone", "pays_off", 404, "not_found"),
    ("thread:missing-map", "event:nope", "before", 404, "not_found"),
])
def test_create_link_refusals(cid, a, b, relation, status, kind):
    refused = _refused(review.create_link, cid, a, b, relation)
    assert (refused.status, refused.kind) == (status, kind)


def test_create_link_journals_and_round_trips(cid):
    result = review.create_link(cid, "thread:missing-map", "commitment:mara-oath", "pays_off",
                                scene=S1, note="the map is the price")
    link = result["link"]
    assert link["id"].startswith("l")
    assert (link["a"], link["b"], link["scene"]) == ("thread:missing-map", "commitment:mara-oath", S1)
    assert store.journal.read(cid)[-1]["label"] == "The missing map pays_off Mara's oath"
    review.remove_link(cid, link["id"])
    assert doc.get_link(cid, link["id"]) is None
    refused = _refused(review.remove_link, cid, link["id"])
    assert (refused.status, refused.kind) == (404, "not_found")


def test_create_link_dedupes_against_effective(cid):
    review.create_link(cid, "thread:lost-chart", "commitment:mara-oath", "pays_off")
    review.create_alias(cid, "thread:missing-map", "thread:lost-chart")
    refused = _refused(review.create_link, cid, "thread:missing-map",
                       "commitment:mara-oath", "pays_off")
    assert (refused.status, refused.kind) == (409, "link_exists")
    review.create_link(cid, "thread:lost-chart", "commitment:mara-oath", "related_to")
    refused = _refused(review.create_link, cid, "commitment:mara-oath",
                       "thread:lost-chart", "related_to")
    assert refused.kind == "link_exists"


def test_create_link_stores_canonical_endpoints(cid):
    review.create_alias(cid, "thread:missing-map", "thread:lost-chart")
    result = review.create_link(cid, "thread:missing-map", "commitment:mara-oath", "pays_off")
    assert result["link"]["a"] == "thread:lost-chart"
    assert result["given"] == {"a": "thread:missing-map", "b": "commitment:mara-oath"}


def test_self_link_refused_after_alias(cid):
    review.create_alias(cid, "thread:missing-map", "thread:lost-chart")
    refused = _refused(review.create_link, cid, "thread:missing-map", "thread:lost-chart",
                       "continues")
    assert (refused.status, refused.kind) == (400, "self_link")


# ------------------------------------------------------- deletion helpers


def test_merged_sources_lists_direct_sources(cid):
    review.create_alias(cid, "thread:missing-map", "thread:lost-chart")
    assert review.merged_sources(cid, "thread:lost-chart") == ["thread:missing-map"]
    assert review.merged_sources(cid, "thread:missing-map") == []


def test_merged_sources_strict_on_malformed(cid):
    (_root(cid) / "continuity.json").write_text("{ no", encoding="utf-8")
    with pytest.raises(doc.ContinuityError):
        review.merged_sources(cid, "thread:lost-chart")


def test_forget_ref_removes_aliases_and_links_and_journals_each(cid):
    store.plot.set_movement(cid, "torn-map", "The torn map", "open", "Torn.", S2)
    review.create_alias(cid, "thread:missing-map", "thread:lost-chart")
    review.create_alias(cid, "thread:lost-chart", "thread:torn-map")
    lid = review.create_link(cid, "thread:torn-map", "commitment:mara-oath",
                             "pays_off")["link"]["id"]
    before = len(store.journal.read(cid))
    removed = review.forget_ref(cid, "thread:lost-chart")
    assert sorted(removed) == ["thread:lost-chart", "thread:missing-map"]
    assert doc.read(cid)["aliases"] == {}
    assert doc.get_link(cid, lid) is not None
    assert len(store.journal.read(cid)) == before + 2
    assert all(r["label"].endswith("removed with deleted record")
               for r in store.journal.read(cid)[before:])
    assert review.forget_ref(cid, "thread:torn-map") == [lid]
    assert doc.get_link(cid, lid) is None


def test_describe_falls_back_to_ref(cid):
    assert review.describe(cid, "thread:missing-map") == "The missing map"
    assert review.describe(cid, "commitment:mara-oath") == "Mara's oath"
    assert review.describe(cid, "thread:gone") == "thread:gone"
    assert review.describe(cid, "nocolon") == "nocolon"
    (_root(cid) / "plot.json").write_text("{ no", encoding="utf-8")
    assert review.describe(cid, "thread:missing-map") == "thread:missing-map"
