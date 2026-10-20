"""Scene-document vectors and the `searchdocs` hot rebuild (roadmap 08-S5:
08-C2b, 08-C2c, 08-C3a).

A `tmp_path` store with the compiled cache on and the embeddings endpoint
faked at the hook's client. `vectors_for` reads and saves under the key 09's
turn warm uses; 05's explicit sync rebuilds the documents an edited file's rows
name and re-embeds only those embedded in the batch's space whose text moved.
Placeholder names only.
"""

from __future__ import annotations

import json
import re
import shutil

import pytest

from grimoire.llm_errors import LLMError
from grimoire.store import (
    cache_sync,
    campaigns,
    chronicle,
    compiled,
    embed_space,
    entities,
    llm_connections,
    scenes,
    statcache,
    usage,
    vectors,
    worlds,
)
from grimoire.store.compiled import _contracts as compiled_contracts
from grimoire.store.inference import settings as inference_settings
from grimoire.store.searchdocs import embedded, live, rolling, slices
from grimoire.store.searchdocs import hook as searchdocs_hook
from tests import cache_sync_fixtures as fx
from tests.inference_fixtures import embedding
from tests.llm_fakes import FakeEmbeddings

cache = fx.cache


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(statcache, "RACY_WINDOW_NS", 0)
    for pool in (slices._POOL, rolling._POOL, live._MEMO, live._RECORDED, live._INPUTS):
        pool.clear()


@pytest.fixture
def home(tmp_path, monkeypatch, cache):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def fake(monkeypatch):
    client = FakeEmbeddings(vector_for=lambda t: [1.0, float(len(t) % 7) + 0.5])
    monkeypatch.setattr(searchdocs_hook, "_CLIENT", client)
    return client


def _configure(model="embed-1"):
    conn = llm_connections.create_connection("openai_compatible", "Vectors",
                                             base_url="https://vectors.example/v1",
                                             api_key="sk-x", model="", post_process="none")
    embedding(conn, model)
    return conn


def _campaign(name="Saltmarch", wid=None):
    wid = wid or worlds.create_world("Realm")
    return wid, campaigns.create_campaign(name, wid)


def _absorbed(cid, title, summary):
    sid = scenes.create_scene(cid, title)
    scenes.append_message(cid, sid, "user", f"{title} begins.")
    _summarize(cid, sid, summary)
    scenes.mark_absorbed(cid, sid, "x", summary)
    return sid


def _summarize(cid, sid, summary):
    chronicle.absorb(cid, {"id": sid, "one_line": "x", "summary": summary, "keywords": [],
                           "cast": [], "location": "", "date": ""})


def _docs(cid):
    return {d.sid: d for d in live.scene_documents(cid).documents}


def _embed(cid, *docs):
    """Embed each document on its own (a window over one miss is that miss)."""
    space = embed_space.endpoint()
    for doc in docs:
        got = embedded.vectors_for(cid, [doc], space=space, client=searchdocs_hook._CLIENT,
                                   limit=searchdocs_hook.REINDEX_LIMIT)
        assert got.error is None and doc.doc.key in got.vectors


def _rel(home, path):
    return path.relative_to(home).as_posix()


def _sent(fake):
    return [t for call in fake.calls for t in call]


def _kinds(report, rel):
    return next(p for p in report.paths if p.given == rel).kinds


# -- vectors_for (08-C2b, 08-C3a) ------------------------------------------------


def test_vectors_for_shares_the_key_with_the_turn_warm(home, fake):
    _configure()
    _wid, cid = _campaign()
    a = _absorbed(cid, "Saltmarch", "Mara counts the tide tax.")
    b = _absorbed(cid, "The Harbour", "Seraphine finds the ledger.")
    docs = _docs(cid)
    space = embed_space.endpoint()
    vectors.save(space["space"], docs[a].doc.text, [0.6, 0.8])    # 09's warm, standing in
    got = embedded.vectors_for(cid, [docs[a], docs[b]], space=space, client=fake, limit=5)
    assert _sent(fake) == [docs[b].doc.text]                      # a's was found, not sent
    assert set(got.vectors) == {docs[a].doc.key, docs[b].doc.key}
    assert docs[b].doc.text in vectors.load(space["space"], [docs[b].doc.text])


def test_vectors_for_sends_at_most_limit_and_a_refused_text_stops_nothing(home, monkeypatch):
    _configure()
    _wid, cid = _campaign()
    sids = [_absorbed(cid, f"Scene {n}", f"Mara's summary number {n}.") for n in range(5)]
    docs = _docs(cid)
    stuck = docs[sids[2]].doc.text
    client = FakeEmbeddings(vector_for=lambda t: [0.0, 0.0] if t == stuck else [1.0, 0.5])
    space = embed_space.endpoint()
    for _round in range(30):
        got = embedded.vectors_for(cid, list(docs.values()), space=space, client=client, limit=2)
        assert got.sent <= 2
    held = vectors.load(space["space"], [d.doc.text for d in docs.values()])
    assert stuck not in held
    assert {d.doc.text for d in docs.values()} - {stuck} <= set(held)


def test_vectors_for_files_one_campaign_row_with_no_scene(home, fake):
    _configure()
    _wid, cid = _campaign()
    a = _absorbed(cid, "Saltmarch", "Mara counts the tide tax.")
    embedded.vectors_for(cid, [_docs(cid)[a]], space=embed_space.endpoint(), client=fake, limit=5)
    rows = [r for r in usage.calls(days=1) if r["task"] == "history-index"]
    assert len(rows) == 1
    assert rows[0]["campaign"] == cid and not rows[0].get("scene")
    assert fake.queries == [0]


def test_vectors_for_returns_what_is_held_and_the_failure_kind(home):
    _configure()
    _wid, cid = _campaign()
    a = _absorbed(cid, "Saltmarch", "Mara counts the tide tax.")
    b = _absorbed(cid, "The Harbour", "Seraphine finds the ledger.")
    docs = _docs(cid)
    space = embed_space.endpoint()
    vectors.save(space["space"], docs[a].doc.text, [0.6, 0.8])
    client = FakeEmbeddings(error=LLMError("auth", "no"))
    got = embedded.vectors_for(cid, [docs[a], docs[b]], space=space, client=client, limit=5)
    assert got.error == "auth" and got.embedded == 0
    assert set(got.vectors) == {docs[a].doc.key}


def test_record_embedded_writes_the_space_kind_with_the_identity(home, fake):
    _configure()
    _wid, cid = _campaign()
    a = _absorbed(cid, "Saltmarch", "Mara counts the tide tax.")
    doc = _docs(cid)[a]
    _embed(cid, doc)
    space = embed_space.endpoint()
    kind = f"vector:searchdocs.scene:{compiled.space_digest(space['space'])}"
    assert embedded.vector_kind(space) == kind
    chron = campaigns.campaign_root(cid) / "chronicle.json"
    scene_file = live.scene_file(cid, doc)
    with compiled.batch(home) as request:
        rows = request.materialized([chron, scene_file])
        request.disabled = True
    # The scene's own file names the scene; a shared ledger, the campaign.
    assert [json.loads(r.instance) for r in rows[scene_file] if r.kind == kind] == \
        [{"campaign": cid, "identity": doc.doc.meta["identity"]}]
    assert [json.loads(r.instance) for r in rows[chron] if r.kind == kind] == [{"campaign": cid}]


# -- the hook (08-C2c) -----------------------------------------------------------


def _three(home, fake):
    """Three absorbed scenes, documents built, the first two embedded."""
    _configure()
    _wid, cid = _campaign()
    sids = [_absorbed(cid, "Saltmarch", "Mara counts the tide tax."),
            _absorbed(cid, "The Harbour", "Seraphine finds the ledger."),
            _absorbed(cid, "Winifred's Boat", "Winifred rows out.")]
    docs = _docs(cid)
    _embed(cid, docs[sids[0]], docs[sids[1]])
    fake.calls.clear()
    fake.queries.clear()
    return cid, sids, _rel(home, campaigns.campaign_root(cid) / "chronicle.json")


def test_a_chronicle_sync_re_embeds_only_the_embedded_document_that_moved(home, fake):
    cid, sids, chron = _three(home, fake)
    _summarize(cid, sids[0], "Mara counts the tide tax twice.")    # embedded, moved
    _summarize(cid, sids[2], "Winifred rows out at dawn.")          # never embedded, moved
    report = cache_sync.sync_paths([chron], mode="explicit")
    docs = _docs(cid)
    assert _sent(fake) == [docs[sids[0]].doc.text]
    kinds = _kinds(report, chron)
    assert kinds["searchdocs.scene"] == "built"
    space = embed_space.endpoint()
    assert kinds[embedded.vector_kind(space)] == "built"
    assert report.embedding["sent"] == 1 and report.embedding["requests"] == 1
    rows = [r for r in usage.calls(days=1) if r["task"] == "history-index"]
    assert rows[-1]["campaign"] == cid and not rows[-1].get("scene")


def test_a_sync_with_nothing_moved_sends_nothing_and_hits(home, fake):
    _cid, _sids, chron = _three(home, fake)
    report = cache_sync.sync_paths([chron], mode="explicit")
    assert fake.calls == []
    assert set(_kinds(report, chron).values()) == {"hit"}


def test_a_world_location_edit_rebuilds_each_campaign_and_an_own_copy_embeds_nothing(home, fake):
    _configure()
    wid = worlds.create_world("Realm")
    _wid, first = _campaign("Saltmarch", wid)
    _wid, second = _campaign("The Harbour", wid)
    eid = entities.create_entity(worlds.world_root(wid), "locations", "Saltmarch harbour")
    by_campaign = {}
    for cid in (first, second):
        sid = _absorbed(cid, "Arrival", "Mara arrives.")
        scenes.set_location(cid, sid, eid)
        by_campaign[cid] = sid
    # The second campaign keeps its own copy, under the same name.
    entities.create_entity(campaigns.campaign_root(second), "locations", "Saltmarch harbour")
    assert (campaigns.campaign_root(second) / "locations" / f"{eid}.md").exists()
    for cid in (first, second):
        _embed(cid, _docs(cid)[by_campaign[cid]])
    fake.calls.clear()
    world_file = worlds.world_root(wid) / "locations" / f"{eid}.md"
    entities.update_entity(worlds.world_root(wid), "locations", eid, name="The old harbour")
    report = cache_sync.sync_paths([_rel(home, world_file)], mode="explicit")
    renamed = _docs(first)[by_campaign[first]]
    assert "The old harbour" in renamed.doc.text
    assert _sent(fake) == [renamed.doc.text]                     # the own copy sent nothing
    assert "Saltmarch harbour" in _docs(second)[by_campaign[second]].doc.text
    assert _kinds(report, _rel(home, world_file))["searchdocs.scene"] == "built"


def test_a_row_left_by_a_scene_no_longer_live_embeds_nothing(home, fake):
    cid, sids, chron = _three(home, fake)
    space = embed_space.endpoint()
    with compiled.batch(home) as request:
        request.mark_materialized(home / chron, embedded.vector_kind(space),
                                  instance={"campaign": cid, "identity": "f" * 32})
    _summarize(cid, sids[2], "Winifred rows out at dawn.")
    cache_sync.sync_paths([chron], mode="explicit")
    assert fake.calls == []


def test_a_moved_space_and_embedding_off_send_nothing(home, fake):
    cid, sids, chron = _three(home, fake)
    conn = next(c["id"] for c in llm_connections.list_connections() if c["name"] == "Vectors")
    embedding(conn, "embed-2")                                    # the role moved
    _summarize(cid, sids[0], "Mara counts the tide tax twice.")
    report = cache_sync.sync_paths([chron], mode="explicit")
    assert fake.calls == [] and report.embedding["stale_space"] == 2
    inference_settings.write("global", "", {"roles": {"embedding": {"selection": None}}},
                             confirm_embedding=True)
    _summarize(cid, sids[1], "Seraphine finds the ledger twice.")
    report = cache_sync.sync_paths([chron], mode="explicit")
    assert fake.calls == [] and report.embedding["embedding_off"] == 2


def test_write_through_defers_the_hook(home, fake):
    cid, sids, chron = _three(home, fake)
    _summarize(cid, sids[0], "Mara counts the tide tax twice.")
    report = cache_sync.sync_paths([chron], mode="write")
    assert fake.calls == []
    assert set(_kinds(report, chron).values()) == {"deferred"}


def test_a_dry_run_stores_and_sends_nothing_and_the_report_holds_no_text(home, fake, monkeypatch):
    cid, sids, chron = _three(home, fake)
    _summarize(cid, sids[0], "Mara counts the tide tax twice.")
    stores = []
    real = live._store
    monkeypatch.setattr(live, "_store", lambda *a, **k: stores.append(a) or real(*a, **k))
    report = cache_sync.sync_paths([chron], mode="explicit", dry_run=True)
    assert fake.calls == [] and stores == []
    assert set(_kinds(report, chron).values()) == {"planned"}
    assert report.embedding["would_send"] == 1
    assert "tide tax" not in json.dumps(report.to_dict() if hasattr(report, "to_dict")
                                        else str(report))


def test_the_local_phase_makes_no_network_call(home, fake):
    cid, sids, chron = _three(home, fake)
    _summarize(cid, sids[0], "Mara counts the tide tax twice.")
    fake.error = AssertionError("the local phase sent a request")
    path = home / chron
    with compiled.batch(home) as request:
        rows = request.materialized([path])[path]
        request.disabled = True
    got = searchdocs_hook.rebuild({chron: rows}, space=embed_space.endpoint(),
                                  space_digest=compiled.space_digest(embed_space.endpoint()["space"]),
                                  embedding=True, dry_run=False, halted=lambda: None)
    assert fake.calls == [] and got.plan
    assert all(isinstance(o, str) and "tide" not in o for _r, _k, o in got.outcomes)


def test_the_hook_is_registered_in_order_and_owns_its_kinds():
    names = [h.name for h in cache_sync.HOOKS]
    assert names == ["files", "overview", "searchdocs", "vectors"]
    assert cache_sync.SEARCHDOCS.on_write == "explicit"
    assert cache_sync.SEARCHDOCS.owns("searchdocs.scene")
    assert cache_sync.SEARCHDOCS.owns("vector:searchdocs.scene:" + "a" * 64)
    assert not cache_sync.VECTORS.owns("vector:searchdocs.scene:" + "a" * 64)
    assert not cache_sync.SEARCHDOCS.owns("vector:semantic-recall:" + "a" * 64)


# -- review round fourteen -----------------------------------------------------


def test_a_shared_ledger_carries_one_row_per_campaign_however_many_scenes(home, fake,
                                                                           monkeypatch):
    """One row per scene on `chronicle.json` would pass 03's history bound in
    a long campaign and hide the file from every hook: a shared file carries
    one campaign row per kind, and a sync of it still reaches every scene."""
    monkeypatch.setattr(compiled_contracts, "MAX_HISTORY_ROWS", 4)
    _configure()
    _wid, cid = _campaign()
    sids = [_absorbed(cid, f"Scene {n}", f"Mara's summary number {n}.") for n in range(6)]
    docs = _docs(cid)
    for sid in sids:
        _embed(cid, docs[sid])
    chron = campaigns.campaign_root(cid) / "chronicle.json"
    with compiled.batch(home) as request:
        rows = request.materialized([chron])[chron]
        request.disabled = True
    assert sorted(json.loads(r.instance)["campaign"] for r in rows) == [cid, cid]
    fake.calls.clear()
    _summarize(cid, sids[4], "Mara's summary, rewritten.")
    report = cache_sync.sync_paths([_rel(home, chron)], mode="explicit")
    kinds = _kinds(report, _rel(home, chron))
    assert kinds["searchdocs.scene"] == "built"
    assert _sent(fake) == [_docs(cid)[sids[4]].doc.text]


def test_documents_outside_a_failed_window_stay_lazy(home, monkeypatch):
    _configure()
    _wid, cid = _campaign()
    sids = [_absorbed(cid, f"Scene {n}", f"Mara's summary number {n}.") for n in range(3)]
    docs = _docs(cid)
    plan = {cid: [(docs[sid], [(f"rel-{n}", "k")]) for n, sid in enumerate(sids)]}
    monkeypatch.setattr(searchdocs_hook, "_CLIENT", FakeEmbeddings(error=LLMError("network", "down")))
    got = searchdocs_hook.reembed(plan, space=embed_space.endpoint(), halted=lambda: None,
                                  root_moved=lambda: False)
    outcomes = sorted(o for _r, _k, o in got.outcomes)
    assert outcomes == ["failed:network", "failed:network", "lazy"]     # a window of two
    assert got.failure == "network"


def test_a_reply_after_the_root_moved_is_never_saved(home, fake):
    _configure()
    _wid, cid = _campaign()
    a = _absorbed(cid, "Saltmarch", "Mara counts the tide tax.")
    doc = _docs(cid)[a]
    space = embed_space.endpoint()
    got = searchdocs_hook.reembed({cid: [(doc, [("rel", "k")])]}, space=space,
                                  halted=lambda: None, root_moved=lambda: True)
    assert len(fake.calls) == 1                        # the request went out...
    assert got.outcomes == []                          # ...and its rows stay `planned`
    assert not vectors.load(space["space"], [doc.doc.text])



def test_an_identity_less_scene_is_rebuilt_and_re_embedded_by_its_sid(home, fake):
    """A scene from before the identity backfill: its own rows name it by sid,
    and a sync of its scene file or a shared ledger still reaches it."""
    _configure()
    _wid, cid = _campaign()
    sid = _absorbed(cid, "Saltmarch", "Mara counts the tide tax.")
    scene_file = scenes.paths._scene_path(cid, sid)
    text = scene_file.read_text(encoding="utf-8")
    scene_file.write_text(re.sub(r"(?m)^identity:.*\n", "", text), encoding="utf-8")
    doc = _docs(cid)[sid]
    assert doc.doc.meta["identity"] == ""
    _embed(cid, doc)
    with compiled.batch(home) as request:
        rows = request.materialized([scene_file])[scene_file]
        request.disabled = True
    assert {json.loads(r.instance)["sid"] for r in rows} == {sid}
    fake.calls.clear()
    _summarize(cid, sid, "Mara counts the tide tax twice.")
    chron = _rel(home, campaigns.campaign_root(cid) / "chronicle.json")
    report = cache_sync.sync_paths([chron], mode="explicit")
    assert _kinds(report, chron)["searchdocs.scene"] == "built"
    assert _sent(fake) == [_docs(cid)[sid].doc.text]


def test_a_root_moved_mid_rebuild_never_writes_into_the_new_library(home, fake, monkeypatch,
                                                                     tmp_path_factory):
    """The rebuild reads and stores against the batch's root; the move is
    still seen, and stops the sync."""
    cid, sids, chron = _three(home, fake)
    _summarize(cid, sids[0], "Mara counts the tide tax twice.")
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    for part in ("worlds", "campaigns"):          # a synced copy: the same campaign there
        shutil.copytree(home / part, elsewhere / part)
    before = {p for p in elsewhere.rglob("*") if p.is_file()}
    real = searchdocs_hook.live.build

    def moving(*args, **kwargs):
        monkeypatch.setenv("GRIMOIRE_HOME", str(elsewhere))
        return real(*args, **kwargs)
    monkeypatch.setattr(searchdocs_hook.live, "build", moving)
    report = cache_sync.sync_paths([chron], mode="explicit", root=home)
    written = {p for p in elsewhere.rglob("*") if p.is_file()} - before
    assert {p.relative_to(elsewhere).parts[0] for p in written} <= {"logs"}
    assert _kinds(report, chron)["searchdocs.scene"] == "built"
    assert report.stopped == "root_moved"
    assert fake.calls == []


def test_an_identity_less_scene_renamed_outside_the_app_keeps_its_rows(home, fake):
    """A stated rename copies the scene file's own row to the new path under
    the new sid, so the hook still finds the scene there."""
    _configure()
    _wid, cid = _campaign()
    sid = _absorbed(cid, "Saltmarch", "Mara counts the tide tax.")
    scene_file = scenes.paths._scene_path(cid, sid)
    text = scene_file.read_text(encoding="utf-8")
    scene_file.write_text(re.sub(r"(?m)^identity:.*\n", "", text), encoding="utf-8")
    _embed(cid, _docs(cid)[sid])
    new_sid = "002--the-harbour"
    new_file = scene_file.with_name(f"{new_sid}.md")
    scene_file.rename(new_file)
    old_rel, new_rel = _rel(home, scene_file), _rel(home, new_file)
    report = cache_sync.sync_paths([], mode="explicit", renamed={old_rel: new_rel})
    with compiled.batch(home) as request:
        rows = request.materialized([new_file])[new_file]
        request.disabled = True
    assert {json.loads(r.instance).get("sid") for r in rows} == {new_sid}
    assert _kinds(report, new_rel)["searchdocs.scene"] in {"built", "hit"}
