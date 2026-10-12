"""The live set of scene SearchDocuments (roadmap 08-C2a, `searchdocs/live.py`)
and `record_embedded` (08-C2b's no-network half).

Pinned: answers are identical with 03's compiled cache empty, warm, off and
after a restart; a superseded document is never returned; each call makes one
batch lookup over its live keys; an unreadable head is skipped and the rest
answer; stored documents leave store-relative `materialized` rows, and losing
the cache changes no answer; warm memos parse no ledger; the cache-off memo is
bounded; a campaign delete purges the documents. Placeholder names only.
"""

import importlib
import json
import shutil

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app
from grimoire.store import (
    campaigns,
    chronicle,
    compiled,
    facts,
    paths,
    scenes,
    statcache,
    vectors,
    worlds,
)
from grimoire.store.searchdocs import embedded, live, rolling, scene, slices


@pytest.fixture(autouse=True)
def _trust_fresh_files(monkeypatch):
    monkeypatch.setattr(statcache, "RACY_WINDOW_NS", 0)
    live._MEMO.clear()
    live._RECORDED.clear()


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Run", wid)


def _scene(cid, title, *, absorbed=False):
    sid = scenes.create_scene(cid, title)
    scenes.append_message(cid, sid, "user", f"A post in {title}.")
    if absorbed:
        chronicle.absorb(cid, {"id": sid, "one_line": f"{title} happened.",
                               "summary": f"Mara and Seraphine in {title}.",
                               "keywords": ["ledger"], "cast": [], "location": "", "date": ""})
        scenes.mark_absorbed(cid, sid, f"{title} happened.", f"Mara and Seraphine in {title}.")
    return sid


def _three(cid):
    return [_scene(cid, "Saltmarch", absorbed=True), _scene(cid, "The Harbour", absorbed=True),
            _scene(cid, "Winifred's Boat")]


def _instance(cid, identity):
    """A `materialized` row's instance, as 03 stores it: canonical JSON."""
    return json.dumps({"campaign": cid, "identity": identity}, separators=(",", ":"),
                      sort_keys=True)


def _answer(ls):
    return [(d.sid, d.closed_by, d.doc.key, d.doc.text, d.doc.meta) for d in ls.documents]


def _restart():
    compiled.close()
    for pool in (slices._POOL, rolling._POOL, live._MEMO, live._RECORDED):
        pool.clear()


def test_answers_match_with_the_cache_empty_warm_off_and_after_a_restart(cid, monkeypatch):
    _three(cid)
    cold = _answer(live.scene_documents(cid))
    warm = _answer(live.scene_documents(cid))
    _restart()
    restarted = _answer(live.scene_documents(cid))
    monkeypatch.setenv("GRIMOIRE_COMPILED_CACHE", "0")
    off = _answer(live.scene_documents(cid))
    assert cold == warm == restarted == off
    assert [row[0] for row in cold] == sorted(row[0] for row in cold)    # play order


def test_a_warm_call_is_served_from_the_cache(cid, monkeypatch):
    _three(cid)
    live.scene_documents(cid)
    _restart()
    monkeypatch.setattr(scene, "render", lambda sl: pytest.fail("a stored document re-rendered"))
    assert len(live.scene_documents(cid).documents) == 3


def test_a_superseded_document_is_never_returned(cid):
    sid, *_ = _three(cid)
    before = {d.sid: d.doc for d in live.scene_documents(cid).documents}
    chronicle.set_line(cid, sid, "A corrected line.")
    after = {d.sid: d.doc for d in live.scene_documents(cid).documents}
    assert after[sid].key != before[sid].key
    assert "In brief: A corrected line." in after[sid].text
    assert all(after[s] == before[s] for s in before if s != sid)


def test_one_batch_lookup_per_call_over_live_keys_only(cid, monkeypatch):
    sids = _three(cid)
    calls = []
    real = compiled.Batch.lookup_many

    def counting(self, keys):
        calls.append(list(keys))
        return real(self, keys)
    monkeypatch.setattr(compiled.Batch, "lookup_many", counting)
    live.scene_documents(cid, exclude=frozenset({sids[0]}))
    assert len(calls) == 1 and len(calls[0]) == 2
    inputs = slices.CampaignInputs.load(cid)
    expected = {live._cache_key(slices.scene_slices(inputs, slices.read_head(cid, s)))
                for s in sids[1:]}
    assert set(calls[0]) == expected


def test_only_restricts_and_exclude_drops(cid):
    a, b, c = _three(cid)
    assert [d.sid for d in live.scene_documents(cid, only=frozenset({b})).documents] == [b]
    assert [d.sid for d in live.scene_documents(cid, exclude=frozenset({b})).documents] == [a, c]


def test_an_unreadable_head_is_skipped_and_the_rest_answer(cid):
    sids = _three(cid)
    bad = scenes.paths._scenes_dir(cid) / "009--broken.md"
    bad.mkdir()                      # a "scene" whose head cannot be read
    got = live.scene_documents(cid)
    assert got.skipped == ("009--broken",)
    assert [d.sid for d in got.documents] == sids


def test_stored_documents_leave_store_relative_materialized_rows(cid):
    sid, *_ = _three(cid)
    ls = live.scene_documents(cid)
    doc = next(d for d in ls.documents if d.sid == sid)
    scene_path = scenes.paths._scene_path(cid, sid)
    assert scene_path in doc.paths
    assert campaigns.campaign_root(cid) / "chronicle.json" in doc.paths
    with compiled.batch() as request:
        rows = request.materialized(list(doc.paths))
    for path in doc.paths:
        kinds = [(m.kind, m.instance) for m in rows[path]]
        assert (scene.KIND, _instance(cid, doc.doc.meta["identity"])) in kinds, path
    assert all(str(m.path).startswith(str(paths.home())) for r in rows.values() for m in r)


def test_losing_the_cache_changes_no_answer(cid):
    _three(cid)
    before = _answer(live.scene_documents(cid))
    _restart()
    shutil.rmtree(paths.home() / ".cache" / "compiled", ignore_errors=True)
    assert _answer(live.scene_documents(cid)) == before


def test_warm_memos_parse_no_ledger_and_an_edit_reparses_only_its_own(cid):
    sid, *_ = _three(cid)
    live.scene_documents(cid)
    before = dict(slices.parse_count)
    live.scene_documents(cid)
    assert slices.parse_count == before
    facts.record(cid, "The ledger counts the tide tax twice.", "", sid)
    live.scene_documents(cid)
    changed = {k for k in slices.parse_count if slices.parse_count[k] != before.get(k)}
    assert changed == {"facts.json"}


def test_the_cache_off_memo_is_bounded(cid, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_COMPILED_CACHE", "0")
    monkeypatch.setattr(live, "DOC_MEMO_ENTRIES", 2)
    for title in ("One", "Two", "Three", "Four"):
        _scene(cid, title)
    first = _answer(live.scene_documents(cid))
    assert len(live._MEMO) <= 2
    assert _answer(live.scene_documents(cid)) == first


def test_record_embedded_writes_rows_on_every_input_path(cid):
    sid, other, _ = _three(cid)
    ls = live.scene_documents(cid)
    docs = {d.sid: d for d in ls.documents}
    space = {"space": "openrouter\0text-embedding-3-small"}
    vectors.save(space["space"], docs[sid].doc.text, [0.6, 0.8])
    embedded.record_embedded(cid, list(ls.documents), space=space)
    kind = embedded.vector_kind(space)
    assert kind == f"vector:searchdocs.scene:{compiled.space_digest(space['space'])}"
    with compiled.batch() as request:
        rows = request.materialized(list(docs[sid].paths) + list(docs[other].paths))
    instance = _instance(cid, docs[sid].doc.meta["identity"])
    for path in docs[sid].paths:
        assert (kind, instance) in [(m.kind, m.instance) for m in rows[path]], path
    # The document nobody embedded gets no vector row, even on a shared file.
    other_instance = _instance(cid, docs[other].doc.meta["identity"])
    assert all((m.kind, m.instance) != (kind, other_instance)
               for path in docs[other].paths for m in rows[path])


def test_record_embedded_never_raises(cid):
    _three(cid)
    ls = live.scene_documents(cid)
    embedded.record_embedded(cid, list(ls.documents), space={})
    embedded.record_embedded(cid, list(ls.documents), space={"space": None})
    embedded.record_embedded(cid, [], space={"space": "x"})


def test_a_campaign_that_is_not_there_raises(cid):
    with pytest.raises(campaigns.CampaignNotFound):
        live.scene_documents("no-such-campaign")


def test_a_campaign_delete_purges_its_documents(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    with TestClient(create_app()) as client:
        wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
        cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
        _three(cid)
        live.scene_documents(cid)
        inputs = slices.CampaignInputs.load(cid)
        keys = [live._cache_key(slices.scene_slices(inputs, slices.read_head(cid, row["id"])))
                for row in scenes.list_scenes(cid)]
        with compiled.batch() as request:
            assert all(v.hit for v in request.lookup_many(keys).values.values())
        assert client.delete(f"/api/campaigns/{cid}").status_code in (200, 204)
        with compiled.batch() as request:
            assert not any(v.hit for v in request.lookup_many(keys).values.values())


def test_a_head_with_invalid_utf8_is_skipped_and_the_rest_answer(cid):
    sids = _three(cid)
    bad = scenes.paths._scenes_dir(cid) / "009--garbled.md"
    bad.write_bytes(b"---\ntitle: \xff\xfe broken\n---\n\n**You:** hi\n")
    got = live.scene_documents(cid)
    assert got.skipped == ("009--garbled",)
    assert [d.sid for d in got.documents] == sids


def test_a_hit_after_a_re_pad_records_the_new_path(cid, monkeypatch):
    """A re-pad moves the scene file and keeps the key, so the answer is a
    hit; the hit still has to leave rows on the path the scene now has, or a
    sync of that path would never find the document."""
    sid, *_ = _three(cid)
    live.scene_documents(cid)
    old = scenes.paths._scene_path(cid, sid)
    padded = "0" + sid
    new = old.with_name(f"{padded}.md")
    old.rename(new)
    chron = campaigns.campaign_root(cid) / "chronicle.json"
    data = json.loads(chron.read_text(encoding="utf-8"))
    data[padded] = data.pop(sid)
    chron.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(scene, "render", lambda sl: pytest.fail("a re-pad re-rendered"))
    doc = next(d for d in live.scene_documents(cid).documents if d.sid == padded)
    with compiled.batch() as request:
        rows = request.materialized([new])
    assert (scene.KIND, _instance(cid, doc.doc.meta["identity"])) in \
        [(m.kind, m.instance) for m in rows[new]]


def test_a_warm_call_writes_no_rows(cid, monkeypatch):
    _three(cid)
    live.scene_documents(cid)
    monkeypatch.setattr(compiled.Batch, "mark_materialized",
                        lambda *a, **k: pytest.fail("a warm call wrote a row"))
    live.scene_documents(cid)


def test_a_document_wider_than_a_staging_batch_keeps_every_row(cid, monkeypatch):
    """03 refuses rows past its per-request bound silently, so a document with
    more input paths than one batch holds is split, never cut short."""
    monkeypatch.setattr(live, "_STAGED_ROWS", 3)
    sid, *_ = _three(cid)
    doc = next(d for d in live.scene_documents(cid).documents if d.sid == sid)
    assert len(doc.paths) > 3
    with compiled.batch() as request:
        rows = request.materialized(list(doc.paths))
    instance = _instance(cid, doc.doc.meta["identity"])
    for path in doc.paths:
        assert (scene.KIND, instance) in [(m.kind, m.instance) for m in rows[path]], path


def test_rows_a_flush_did_not_commit_are_retried(cid, monkeypatch):
    """A refused or failed cache write is not remembered as written: the next
    call that finds the document writes its rows again."""
    sid, *_ = _three(cid)
    real = compiled.Batch.flush
    monkeypatch.setattr(compiled.Batch, "flush", lambda self: None)   # stages, commits nothing
    live.scene_documents(cid)
    assert not live._RECORDED
    monkeypatch.setattr(compiled.Batch, "flush", real)
    doc = next(d for d in live.scene_documents(cid).documents if d.sid == sid)
    assert live._RECORDED
    with compiled.batch() as request:
        rows = request.materialized([doc.paths[0]])
    assert (scene.KIND, _instance(cid, doc.doc.meta["identity"])) in \
        [(m.kind, m.instance) for m in rows[doc.paths[0]]]


def test_a_malformed_appearances_ledger_drops_only_the_running_summary(cid):
    sid = scenes.create_scene(cid, "Saltmarch")
    for i in range(4):
        scenes.append_message(cid, sid, "user", f"Post {i}.")
    scenes.set_rolling_summary(cid, sid, "Folded.", 4, "x" * 64, "y" * 64)
    (campaigns.campaign_root(cid) / "appearances.json").write_text("[1, 2]", encoding="utf-8")
    doc = next(d for d in live.scene_documents(cid).documents if d.sid == sid)
    assert doc.doc.meta["summary_source"] == "none"
    assert "appearances.json" in doc.doc.meta["degraded"]


def test_rows_already_present_after_a_restart_count_as_recorded(cid, monkeypatch):
    """After a restart the rows are in the cache and 03 stages nothing for
    them; that is "already written", not a failure to retry on every call."""
    _three(cid)
    live.scene_documents(cid)
    _restart()
    live.scene_documents(cid)
    assert live._RECORDED
    monkeypatch.setattr(compiled.Batch, "mark_materialized",
                        lambda *a, **k: pytest.fail("a warm call after a restart re-wrote rows"))
    live.scene_documents(cid)
