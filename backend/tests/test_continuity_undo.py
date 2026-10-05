"""Continuity aliases and links are undoable (capstone spec §12.8).

The compare-and-swap in `undo.undo` covers only the one record being put back,
so it cannot see that restoring an alias would close a cycle with an alias made
since, or that restoring a link duplicates an equivalent one made since. The
restores in `continuity.doc` validate for exactly that, and a refusal reaches
the reader as a 409 rather than a cycle on disk or a 500.
"""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app
from grimoire.store.continuity import doc, effective, review


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


def _alias_target(ref):
    return {"w": "continuity_alias", "ref": ref}


def _link_target(lid):
    return {"w": "continuity_link", "id": lid}


def _journalled_alias(cid, ref, write):
    with store.undo.journalled(cid, _alias_target(ref), kind="continuity_alias",
                               ref={"kind": "continuity_alias", "id": ref},
                               field="alias", label=f"{ref} alias"):
        write()
    return store.journal.read(cid)[-1]["id"]


def _journalled_link(cid, lid, write):
    with store.undo.journalled(cid, _link_target(lid), kind="continuity_link",
                               ref={"kind": "continuity_link", "id": lid},
                               field="link", label=f"{lid} link"):
        write()
    return store.journal.read(cid)[-1]["id"]


def _link(a, b, relation):
    return {"a": a, "b": b, "relation": relation, "created": "", "scene": "", "note": ""}


def test_undo_alias_create_and_redo(cid):
    record = {"to": "thread:b", "created": "", "source": "manual", "note": ""}
    jid = _journalled_alias(cid, "thread:a", lambda: doc.put_alias(cid, "thread:a", record))
    reversal = store.undo.undo(cid, jid)
    assert doc.get_alias(cid, "thread:a") is None
    store.undo.undo(cid, reversal["id"])
    assert doc.get_alias(cid, "thread:a") == record


def test_undo_link_create_and_redo(cid):
    record = _link("thread:a", "commitment:x", "pays_off")
    jid = _journalled_link(cid, "l1", lambda: doc.put_link(cid, "l1", record))
    reversal = store.undo.undo(cid, jid)
    assert doc.get_link(cid, "l1") is None
    store.undo.undo(cid, reversal["id"])
    assert doc.get_link(cid, "l1") == record


def test_undo_restore_refuses_cycle(cid):
    doc.put_alias(cid, "thread:a", {"to": "thread:b"})
    jid = _journalled_alias(cid, "thread:a", lambda: doc.drop_alias(cid, "thread:a"))
    doc.put_alias(cid, "thread:b", {"to": "thread:a"})
    with pytest.raises(store.undo.UndoConflict) as refused:
        store.undo.undo(cid, jid)
    assert str(refused.value) != store.undo.CONFLICT
    assert doc.read(cid)["aliases"] == {"thread:b": {"to": "thread:a"}}


def test_undo_link_restore_refuses_effective_equivalent(cid):
    doc.put_link(cid, "l1", _link("thread:a", "commitment:x", "pays_off"))
    jid = _journalled_link(cid, "l1", lambda: doc.drop_link(cid, "l1"))
    doc.put_alias(cid, "thread:a", {"to": "thread:b"})
    doc.put_link(cid, "l2", _link("thread:b", "commitment:x", "pays_off"))
    with pytest.raises(store.undo.UndoConflict) as refused:
        store.undo.undo(cid, jid)
    assert str(refused.value) != store.undo.CONFLICT
    assert doc.get_link(cid, "l1") is None


@pytest.mark.parametrize("ref,value", [
    ("thread:a", {"to": "commitment:b"}),
    ("thread:a", {"to": "thread:a"}),
    ("thread:a", {"to": 3}),
    ("event:a", {"to": "event:b"}),
])
def test_restore_alias_refuses_invalid_records(cid, ref, value):
    with pytest.raises(doc.ContinuityError):
        doc.restore_alias(cid, ref, value)
    assert doc.read(cid)["aliases"] == {}


@pytest.mark.parametrize("value", [
    _link("commitment:x", "thread:a", "pays_off"),
    _link("thread:a", "thread:b", "same_as"),
    {"a": 3, "b": "thread:b", "relation": "continues"},
])
def test_restore_link_refuses_invalid_records(cid, value):
    with pytest.raises(doc.ContinuityError):
        doc.restore_link(cid, "l1", value)


def test_restore_tables_match_effective_relations():
    expected = {rel: (a, b) for rel, (a, b, _) in effective.RELATIONS.items()}
    symmetric = frozenset(
        rel for rel, (_, _, directional) in effective.RELATIONS.items() if not directional)
    assert expected == doc._RELATIONS
    assert symmetric == doc._SYMMETRIC


# A hand-edited record can be JSON null. The journal must tell "a null record
# was here" from "no record was here", or deleting one leaves no row to undo and
# undoing a replacement removes the key instead of putting the null back.


def _raw(cid, data):
    (store.campaigns.campaign_root(cid) / "continuity.json").write_text(
        json.dumps(data), encoding="utf-8")


def test_removing_a_null_alias_is_journalled_and_undo_restores_it(cid):
    _raw(cid, {"aliases": {"thread:a": None}})
    before = len(store.journal.read(cid))
    review.remove_alias(cid, "thread:a")
    rows = store.journal.read(cid)
    assert len(rows) == before + 1
    store.undo.undo(cid, rows[-1]["id"])
    assert doc.read(cid)["aliases"] == {"thread:a": None}


def test_replacing_a_null_alias_undo_restores_the_null(cid):
    store.plot.set_movement(cid, "a", "A", "open", "", "")
    store.plot.set_movement(cid, "b", "B", "open", "", "")
    _raw(cid, {"aliases": {"thread:a": None}})
    review.create_alias(cid, "thread:a", "thread:b", replace=True)
    store.undo.undo(cid, store.journal.read(cid)[-1]["id"])
    assert doc.read(cid)["aliases"] == {"thread:a": None}


def test_removing_a_null_link_is_journalled_and_undo_restores_it(cid):
    _raw(cid, {"links": {"l1": None}})
    review.remove_link(cid, "l1")
    store.undo.undo(cid, store.journal.read(cid)[-1]["id"])
    assert doc.read(cid)["links"] == {"l1": None}


@pytest.mark.parametrize("section", ["aliases", "links"])
def test_removing_a_malformed_object_record_undo_restores_it_verbatim(cid, section):
    key = "thread:a" if section == "aliases" else "l1"
    _raw(cid, {section: {key: {"note": "broken"}}})
    (review.remove_alias if section == "aliases" else review.remove_link)(cid, key)
    store.undo.undo(cid, store.journal.read(cid)[-1]["id"])
    assert doc.read(cid)[section] == {key: {"note": "broken"}}


@pytest.mark.parametrize("section", ["aliases", "links"])
def test_redoing_the_removal_of_a_null_record_removes_it(cid, section):
    key = "thread:a" if section == "aliases" else "l1"
    _raw(cid, {section: {key: None}})
    (review.remove_alias if section == "aliases" else review.remove_link)(cid, key)
    reversal = store.undo.undo(cid, store.journal.read(cid)[-1]["id"])
    assert doc.read(cid)[section] == {key: None}
    store.undo.undo(cid, reversal["id"])
    assert doc.read(cid)[section] == {}


def test_undo_link_restore_refuses_self_collapsing(cid):
    doc.put_link(cid, "l1", _link("thread:a", "thread:b", "continues"))
    jid = _journalled_link(cid, "l1", lambda: doc.drop_link(cid, "l1"))
    doc.put_alias(cid, "thread:a", {"to": "thread:b"})
    with pytest.raises(store.undo.UndoConflict) as refused:
        store.undo.undo(cid, jid)
    assert str(refused.value) != store.undo.CONFLICT
    assert doc.get_link(cid, "l1") is None


@pytest.mark.parametrize(("section", "key", "value"), [
    ("aliases", "thread:a", {"to": "commitment:b"}),
    ("aliases", "event:a", {"to": "event:b"}),
    ("aliases", "thread:a", {"to": "thread:a"}),
    ("links", "l1", _link("thread:a", "thread:b", "rhymes_with")),
    ("links", "l1", _link("event:a", "thread:b", "pays_off")),
], ids=["cross-type", "not-aliasable", "self", "unknown-relation", "wrong-endpoints"])
def test_removing_a_semantically_malformed_record_undo_restores_it_verbatim(
        cid, section, key, value):
    """A hand-edited record with every field present but a meaning the
    validated restore refuses was still accepted by the remove route and
    journalled -- so Undo has to put it back as it was, not answer 409."""
    _raw(cid, {section: {key: value}})
    (review.remove_alias if section == "aliases" else review.remove_link)(cid, key)
    store.undo.undo(cid, store.journal.read(cid)[-1]["id"])
    assert doc.read(cid)[section] == {key: value}


def test_undo_link_restore_refuses_while_aliases_are_unreadable(cid):
    """A link checked against an empty alias graph could be self-collapsing or
    a duplicate of one made since; creating a link refuses while the merges
    cannot be read, and putting one back refuses the same way."""
    doc.put_link(cid, "l1", _link("thread:a", "thread:b", "continues"))
    jid = _journalled_link(cid, "l1", lambda: doc.drop_link(cid, "l1"))
    _raw(cid, {"aliases": "garbled", "links": {}})
    with pytest.raises(store.undo.UndoConflict) as refused:
        store.undo.undo(cid, jid)
    assert str(refused.value) != store.undo.CONFLICT
    assert doc.read(cid)["links"] == {}
