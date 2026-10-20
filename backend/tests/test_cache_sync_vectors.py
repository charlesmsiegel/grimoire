"""The `vectors` hook and the three producers (spec 05-S6, sections 6.4-6.6).

A `tmp_path` store with placeholder names, the compiled cache on, and the
embeddings endpoint faked (`llm_fakes.FakeEmbeddings`) at every producer's
client and the hook's. Recall, the art catalog and library search record a
`vector:<projection>:<space digest>` row beside each vector they save; a sync
re-embeds only the next version of a unit that was embedded, in the batch's
space, text the cache lacks, under the producer's own task.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import embeddings, routes
from grimoire.main import create_app
from grimoire.store import (
    cache_sync,
    campaign_images,
    campaigns,
    compiled,
    config,
    entities,
    image_descriptions,
    image_store,
    llm_connections,
    locks,
    scenes,
    semsearch,
    usage,
    vectors,
    worlds,
)
from grimoire.store.cache_sync import _vectors as cache_sync_vectors
from grimoire.store.context import art, layout, semantic
from grimoire.store.inference import embed
from grimoire.store.inference import settings as inference_settings
from tests import cache_sync_fixtures as fx
from tests.inference_fixtures import embedding
from tests.llm_fakes import FakeEmbeddings, FakeOpenRouter

cache = fx.cache
SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture
def home(tmp_path, monkeypatch, cache):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def fake(monkeypatch):
    """One fake endpoint behind every producer and the hook."""
    client = FakeEmbeddings()
    for module in (semantic, art, semsearch, cache_sync_vectors):
        monkeypatch.setattr(module, "_CLIENT", client)
    return client


def _configure(model="embed-1"):
    conn = llm_connections.create_connection("openai_compatible", "Vectors",
                                             base_url="https://vectors.example/v1",
                                             api_key="sk-x", model="", post_process="none")
    config.write_config(semantic_recall_depth="2", semantic_recall_threshold="0.1")
    embedding(conn, model)
    return conn


def _space():
    return semantic.settings()["space"]


def _realm():
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    return wid, cid


def _candidate(wid, cid, eid):
    e = entities.read_entity(worlds.world_root(wid), "lore", eid)
    keys = [k.strip() for k in e["meta"].get("keys", "").split(",") if k.strip()]
    return {"kind": "lore", "id": eid, "name": e["meta"].get("name", eid), "keys": keys,
            "body": e["body"].strip()}


def _rows_at(home, path):
    with compiled.batch(home) as request:
        rows = request.materialized([path])[path]
        request.disabled = True
    return rows


def _lore(home, wid, name="Pact", body="The pact binds the coast."):
    eid = entities.create_entity(worlds.world_root(wid), "lore", name, body, keys="unsaid")
    return eid, worlds.world_root(wid) / "lore" / f"{eid}.md"


def _recall(wid, cid, eid, campaign=None):
    return semantic.recall_scored([_candidate(wid, cid, eid)], "Somebody speaks of the coast.",
                                  campaign=campaign or cid)


def _rel(home, path):
    return path.relative_to(home).as_posix()


def _png(rgb: bytes) -> bytes:
    """A 1x1 PNG of one colour: distinct pixels, so a distinct image object."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" + rgb)) + chunk(b"IEND", b""))


def _docs(fake_client):
    """Every document text the endpoint was sent (queries dropped)."""
    out = []
    for texts, queries in zip(fake_client.calls, fake_client.queries, strict=True):
        out.extend(texts[queries:])
    return out


# -- the producers record their rows ------------------------------------------


def test_recall_records_a_row_naming_its_projection_and_instance(home, fake):
    _configure()
    wid, cid = _realm()
    eid, path = _lore(home, wid)
    _recall(wid, cid, eid)
    [row] = _rows_at(home, path)
    assert row.kind == f"vector:{semantic.PROJECTION}:{compiled.space_digest(_space())}"
    assert json.loads(row.instance) == {"campaign": cid, "entity": ["lore", eid]}
    assert row.built_from == hashlib.sha256(path.read_bytes()).hexdigest()


def test_dropping_the_materialized_table_changes_no_producers_answer(home, fake, monkeypatch):
    _configure()
    wid, cid = _realm()
    eid, _path = _lore(home, wid)
    with_rows = _recall(wid, cid, eid)
    monkeypatch.setenv("GRIMOIRE_COMPILED_CACHE", "0")
    without = _recall(wid, cid, eid)
    assert [(e["id"], s) for e, s in with_rows] == [(e["id"], s) for e, s in without]


def test_library_search_records_one_row_per_embedded_passage(home, fake):
    _configure()
    _wid, cid = _realm()
    sid = scenes.create_scene(cid, "Arrival")
    for n in range(6):
        scenes.append_message(cid, sid, "user", f"Mara speaks line {n}. " + "word " * 120)
    semsearch.search_semantic("the coast", scope="campaign", root=cid)
    path = scenes.read.scene_path(cid, sid) if hasattr(scenes.read, "scene_path") else next(
        (home / "campaigns" / cid / "scenes").glob("*.md"))
    rows = [r for r in _rows_at(home, path) if semsearch.PROJECTION in r.kind]
    units = sorted(json.loads(r.instance)["unit"] for r in rows)
    texts = semsearch.document_texts(path, path.read_bytes())
    assert len(units) >= 2 and set(units) <= set(range(len(texts)))


def test_a_legacy_art_description_records_no_row_and_an_object_does(home, fake):
    text = "A hand-drawn map of the northern coastline."
    assert art._history("saltmarch", {"description": text}, text) == {}
    assert art.object_text(json.dumps({"description": f"  {text}  "}).encode()) == text


# -- the hook's policy ------------------------------------------------------------


def test_an_edited_entry_is_re_embedded_once_under_recall_and_recall_then_hits(home, fake):
    _configure()
    wid, cid = _realm()
    eid, path = _lore(home, wid)
    _recall(wid, cid, eid)
    entities.update_entity(worlds.world_root(wid), "lore", eid, body="The pact binds the coast and the sea.")
    before = len(fake.calls)
    report = cache_sync.sync_paths([_rel(home, path)], mode="explicit")
    item = report.paths[0]
    assert item.status == "refreshed" and list(item.kinds.values()) == ["built"]
    new_text = semantic.entry_text(_candidate(wid, cid, eid))
    assert _docs(fake)[-1] == new_text and len(fake.calls) == before + 1
    assert report.embedding["sent"] == 1 and report.embedding["requests"] == 1
    [row] = [r for r in usage.calls(days=1) if r.get("run_id", "") == "" and r["task"] == "semantic-recall"][-1:]
    assert row["campaign"] == cid
    fake.calls.clear()
    fake.queries.clear()
    _recall(wid, cid, eid)
    assert _docs(fake) == []                              # the query alone was sent


def test_a_row_in_another_space_is_stale_and_sends_nothing(home, fake):
    conn = _configure()
    wid, cid = _realm()
    eid, path = _lore(home, wid)
    _recall(wid, cid, eid)
    embedding(conn, "embed-2")                            # the role moved
    entities.update_entity(worlds.world_root(wid), "lore", eid, body="Edited after the move.")
    before = len(fake.calls)
    report = cache_sync.sync_paths([_rel(home, path)], mode="explicit")
    assert report.embedding["stale_space"] == 1 and len(fake.calls) == before
    assert list(report.paths[0].kinds.values()) == ["skipped"]


def test_with_embedding_off_nothing_is_sent(home, fake):
    _configure()
    wid, cid = _realm()
    eid, path = _lore(home, wid)
    _recall(wid, cid, eid)
    config.write_config(semantic_recall_depth="0")
    inference_settings.write("global", "", {"roles": {"embedding": {"selection": None}}},
                             confirm_embedding=True)
    entities.update_entity(worlds.world_root(wid), "lore", eid, body="Edited with embedding off.")
    before = len(fake.calls)
    report = cache_sync.sync_paths([_rel(home, path)], mode="explicit")
    assert report.embedding["embedding_off"] == 1 and len(fake.calls) == before


def test_a_role_moved_between_the_phases_sends_nothing(home, fake, monkeypatch):
    conn = _configure()
    wid, cid = _realm()
    eid, path = _lore(home, wid)
    _recall(wid, cid, eid)
    entities.update_entity(worlds.world_root(wid), "lore", eid, body="Edited before the move.")
    real = cache_sync._VectorsHook.local

    def local_then_move(self, ctx, hot):
        result = real(self, ctx, hot)
        embedding(conn, "embed-2")
        return result

    monkeypatch.setattr(cache_sync._VectorsHook, "local", local_then_move)
    before = len(fake.calls)
    report = cache_sync.sync_paths([_rel(home, path)], mode="explicit")
    assert len(fake.calls) == before and report.embedding["stale_space"] == 1
    assert list(report.paths[0].kinds.values()) == ["skipped"]


def test_dry_run_and_no_embed_send_nothing_and_say_what_would_go(home, fake):
    _configure()
    wid, cid = _realm()
    eid, path = _lore(home, wid)
    _recall(wid, cid, eid)
    entities.update_entity(worlds.world_root(wid), "lore", eid, body="Edited for the dry run.")
    before = len(fake.calls)
    dry = cache_sync.sync_paths([_rel(home, path)], mode="explicit", dry_run=True)
    deferred = cache_sync.sync_paths([_rel(home, path)], mode="explicit", embed=False)
    assert len(fake.calls) == before
    assert dry.embedding["would_send"] == 1 and list(dry.paths[0].kinds.values()) == ["planned"]
    assert deferred.embedding["deferred"] == 1


def _transcript(home, cid, posts):
    sid = scenes.create_scene(cid, "Arrival")
    for text in posts:
        scenes.append_message(cid, sid, "user", text)
    return next((home / "campaigns" / cid / "scenes").glob("*.md"))


def _post(n, extra=""):
    return f"Mara speaks of the {n} tide{extra}. " + "salt " * 150


def _embed_units(home, path, units):
    """Embed only the given passages of a transcript, as a search would."""
    data = path.read_bytes()
    texts = semsearch.document_texts(path, data)
    space = _space()
    for unit in units:
        vectors.save(space, texts[unit], [1.0, 0.0], source=compiled.WrittenInput("document", path, data),
                     projection=semsearch.PROJECTION, instance={"unit": unit})
    return texts


def test_a_changed_last_passage_embeds_one_passage(home, fake):
    _configure()
    _wid, cid = _realm()
    path = _transcript(home, cid, [_post(n) for n in range(5)])
    texts = _embed_units(home, path, range(len(semsearch.document_texts(path, path.read_bytes()))))
    assert len(texts) >= 3
    sid = path.stem
    scenes.append_message(cid, sid, "assistant", "And the tide turned.")
    before = len(fake.calls)
    report = cache_sync.sync_paths([_rel(home, path)], mode="explicit")
    assert len(fake.calls) == before + 1 and len(_docs(fake)[-1:]) == 1
    assert report.embedding["sent"] == 1


def test_a_partly_embedded_transcript_never_embeds_its_tail(home, fake):
    _configure()
    _wid, cid = _realm()
    path = _transcript(home, cid, [_post(n) for n in range(12)])
    texts = _embed_units(home, path, (1, 4, 9))
    assert len(texts) > 9
    data = path.read_text().replace("tide", "current")
    path.write_text(data)
    new = semsearch.document_texts(path, path.read_bytes())
    before = len(fake.calls)
    cache_sync.sync_paths([_rel(home, path)], mode="explicit")
    sent = _docs(fake)[sum(len(c) for c in fake.calls[:before]) - sum(fake.queries[:before]):]
    assert set(sent) <= {new[1], new[4], new[9]} and len(sent) == 3


def test_a_renamed_transcript_keeps_its_selectors(home, fake):
    _configure()
    _wid, cid = _realm()
    path = _transcript(home, cid, [_post(n) for n in range(12)])
    _embed_units(home, path, (1, 4, 9))
    new_path = path.with_name("renamed.md")
    path.rename(new_path)
    before = len(fake.calls)
    report = cache_sync.sync_paths([], mode="explicit",
                                   renamed={_rel(home, path): _rel(home, new_path)})
    assert len(fake.calls) == before                       # unchanged text: nothing to embed
    assert report.embedding["held"] == 3
    new_path.write_text(new_path.read_text().replace("tide", "current"))
    new = semsearch.document_texts(new_path, new_path.read_bytes())
    cache_sync.sync_paths([_rel(home, new_path)], mode="explicit")
    assert len(fake.calls) == before + 1
    assert set(fake.calls[-1]) == {new[1], new[4], new[9]}


def test_two_producers_rows_on_one_path_stay_apart(home, fake):
    _configure()
    wid, cid = _realm()
    eid, path = _lore(home, wid)
    _recall(wid, cid, eid)
    data = path.read_bytes()
    passages = semsearch.document_texts(path, data)
    vectors.save(_space(), passages[0], [1.0, 0.0], source=compiled.WrittenInput("document", path, data),
                 projection=semsearch.PROJECTION, instance={"unit": 0})
    entities.update_entity(worlds.world_root(wid), "lore", eid, body="Both producers see this edit.")
    before = len(fake.calls)
    written = cache_sync.sync_paths([_rel(home, path)], mode="write")
    kinds = written.paths[0].kinds
    assert sorted(kinds.values()) == ["built", "deferred"]   # recall now, search on explicit only
    assert len(fake.calls) == before + 1
    assert fake.calls[-1] == [semantic.entry_text(_candidate(wid, cid, eid))]
    explicit = cache_sync.sync_paths([_rel(home, path)], mode="explicit")
    assert sorted(explicit.paths[0].kinds.values()) == ["built", "hit"]
    new_passage = semsearch.document_texts(path, path.read_bytes())[0]
    assert fake.calls[-1] == [new_passage]


def test_a_connection_wide_failure_stops_the_rest(home, fake, monkeypatch):
    _configure()
    wid, cid = _realm()
    paths = []
    for n in range(3):
        eid, path = _lore(home, wid, name=f"Pact {n}", body=f"Clause {n}.")
        _recall(wid, cid, eid)
        entities.update_entity(worlds.world_root(wid), "lore", eid, body=f"Clause {n}, amended.")
        paths.append(_rel(home, path))
    monkeypatch.setattr(embeddings, "BATCH", 1)
    fake.error, fake.fail_after = embeddings.EmbeddingsError("auth", "no"), len(fake.calls)
    before = len(fake.calls)
    report = cache_sync.sync_paths(paths, mode="explicit")
    assert len(fake.calls) == before + 1
    assert report.embedding["failure"] == "auth" and report.embedding["not_sent"] == 2
    assert all(p.status == "failed" for p in report.paths[:1])


def test_a_late_rate_limit_keeps_the_earlier_chunks(home, fake, monkeypatch):
    _configure()
    wid, cid = _realm()
    paths, texts = [], []
    for n in range(3):
        eid, path = _lore(home, wid, name=f"Pact {n}", body=f"Clause {n}.")
        _recall(wid, cid, eid)
        entities.update_entity(worlds.world_root(wid), "lore", eid, body=f"Clause {n}, amended.")
        paths.append(_rel(home, path))
        texts.append(semantic.entry_text(_candidate(wid, cid, eid)))
    monkeypatch.setattr(embeddings, "BATCH", 1)
    fake.error, fake.fail_after = embeddings.EmbeddingsError("rate_limit", "slow down"), len(fake.calls) + 2
    rows_before = len([r for r in usage.calls(days=1) if r["task"] == "semantic-recall"])
    report = cache_sync.sync_paths(paths, mode="explicit")
    # The failed third chunk went out too: `sent` and `requests` count every
    # request that filed a ledger row, not only those that answered.
    assert report.embedding["sent"] == 3 and report.embedding["requests"] == 3
    assert report.embedding["failure"] == "rate_limit"
    held = vectors.load(_space(), texts)
    assert len(held) == 2
    rows_after = [r for r in usage.calls(days=1) if r["task"] == "semantic-recall"]
    assert len(rows_after) - rows_before == 3                # one per chunk sent, the third failed


def test_shared_text_is_unattributed_and_one_campaigns_is_charged_to_it(home, fake):
    _configure()
    wid, cid = _realm()
    other = campaigns.create_campaign("Winifred's Run", wid)
    shared_eid, shared_path = _lore(home, wid, name="Shared", body="Both campaigns read this.")
    own_eid, own_path = _lore(home, wid, name="Own", body="Only Saltmarch read this.")
    _recall(wid, cid, shared_eid)
    _recall(wid, cid, own_eid)
    # The other campaign's recall only hit the vector Saltmarch embedded, so it
    # recorded no row of its own (rows are written where a vector is saved);
    # its row is stated here as a campaign that embedded it would have.
    data = shared_path.read_bytes()
    vectors.save(_space(), semantic.record_text(shared_eid, data), [1.0, 0.0],
                 source=compiled.WrittenInput("body", shared_path, data),
                 projection=semantic.PROJECTION,
                 instance={"campaign": other, "entity": ["lore", shared_eid]})
    for eid in (shared_eid, own_eid):
        entities.update_entity(worlds.world_root(wid), "lore", eid, body="Edited for attribution.")
    entities.update_entity(worlds.world_root(wid), "lore", own_eid, body="Edited, Saltmarch's alone.")
    cache_sync.sync_paths([_rel(home, shared_path), _rel(home, own_path)], mode="explicit")
    rows = [r for r in usage.calls(days=1) if r["task"] == "semantic-recall"][-2:]
    assert sorted(r.get("campaign", "") for r in rows) == ["", cid]


def test_under_write_through_a_lore_edit_re_embeds_recall_and_nothing_for_search(home, fake):
    _configure()
    wid, cid = _realm()
    eid, path = _lore(home, wid)
    _recall(wid, cid, eid)
    data = path.read_bytes()
    vectors.save(_space(), semsearch.document_texts(path, data)[0], [1.0, 0.0],
                 source=compiled.WrittenInput("document", path, data),
                 projection=semsearch.PROJECTION, instance={"unit": 0})
    entities.update_entity(worlds.world_root(wid), "lore", eid, body="A write-through edit.")
    before = len(fake.calls)
    cache_sync.sync_paths([_rel(home, path)], mode="write")
    assert fake.calls[before:] == [[semantic.entry_text(_candidate(wid, cid, eid))]]


# -- the registry and a fresh process ----------------------------------------------


def test_every_producer_projection_is_claimed_in_a_fresh_process(home):
    script = ("from grimoire.store import cache_sync\n"
              "kinds = ['vector:lore-entry:x', 'vector:art-description:x', 'vector:search-passage:x']\n"
              "missing = [k for k in kinds if not any(h.owns(k) for h in cache_sync.HOOKS)]\n"
              "from grimoire.store import compiled\n"
              "missing += [k for k, kind in compiled.KINDS.items() if kind.path_inputs\n"
              "            and not any(h.owns(k) for h in cache_sync.HOOKS)]\n"
              "print(missing)\n")
    env = {**os.environ, "PYTHONPATH": str(SRC), "GRIMOIRE_HOME": str(home)}
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env,
                          timeout=120, check=True)
    assert done.stdout.strip() == "[]"


def test_the_cli_in_a_fresh_process_leaves_vectors_recall_then_hits(home, fake):
    _configure()
    wid, cid = _realm()
    eid, path = _lore(home, wid)
    _recall(wid, cid, eid)
    entities.update_entity(worlds.world_root(wid), "lore", eid, body="Edited by an agent.")
    script = ("import sys\n"
              "from grimoire.store import cache_sync, compiled, maintenance_reports\n"
              "from grimoire.store.compiled import _runtime as compiled_runtime\n"
              "compiled_runtime._build = lambda: 'b' * 64\n"  # the fixture's cache identity
              "maintenance_reports.device_key = lambda root: 'a' * 64\n"
              "from tests.llm_fakes import FakeEmbeddings\n"
              "from grimoire.store.cache_sync import _vectors as cache_sync_vectors\n"
              "cache_sync_vectors._CLIENT = FakeEmbeddings()\n"
              "from grimoire.cache import main\n"
              f"sys.exit(main(['sync', '--json', {_rel(home, path)!r}]))\n")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(SRC), str(SRC.parent)]),
           "GRIMOIRE_HOME": str(home)}
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env,
                          timeout=120, check=False)
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert report["paths"][0]["status"] == "refreshed" and report["embedding"]["sent"] == 1
    fake.calls.clear()
    fake.queries.clear()
    _recall(wid, cid, eid)
    assert _docs(fake) == []                              # the vector was there already


# -- history recorded where no lock is held ----------------------------------------


def test_a_turn_parks_recall_history_and_its_handoff_records_it(monkeypatch, tmp_path, cache, fake):
    """A turn embeds recall's candidates under its campaign lock, where 03's
    cache takes no SQL: the row waits, and the turn's write-set handoff --
    off the loop, outside every lock -- records it."""


    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    held = []
    real = semantic._history
    monkeypatch.setattr(semantic, "_history",
                        lambda *a: held.append(locks.campaign_locks_held()) or real(*a))
    app = create_app()
    with TestClient(app) as client:
        client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-secret"})
        wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
        cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
        eid, path = _lore(tmp_path, wid)
        _configure()
        sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Arrival"}).json()["id"]
        client.app.dependency_overrides[routes.get_llm] = lambda: FakeOpenRouter(["The tide turns."])
        assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat",
                           json={"content": "Mara looks to the sea."}).status_code == 200
        client.get(f"/api/campaigns/{cid}/scenes/{sid}")     # a later boundary, for good measure
    assert held == [True]
    assert not vectors.has_deferred()
    [row] = _rows_at(tmp_path, path)
    assert json.loads(row.instance) == {"campaign": cid, "entity": ["lore", eid]}


def test_parked_history_for_a_source_edited_since_is_dropped(home, fake, monkeypatch):
    _configure()
    wid, cid = _realm()
    eid, path = _lore(home, wid)
    monkeypatch.setattr(compiled, "unavailable_reason", lambda: "campaign_lock_held")
    _recall(wid, cid, eid)
    assert vectors.has_deferred()
    monkeypatch.undo()
    monkeypatch.setenv("GRIMOIRE_HOME", str(home))
    entities.update_entity(worlds.world_root(wid), "lore", eid, body="Edited before the flush.")
    assert vectors.flush_deferred() == 0 and not vectors.has_deferred()
    assert _rows_at(home, path) == ()


def test_the_art_catalog_names_the_image_object(home):
    _wid, cid = _realm()
    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
           b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01"
           b"\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82")
    campaign_images.put_image(cid, "coastline", png, "png")
    image_descriptions.set_in(campaign_images.images_dir(cid), "coastline",
                              "A hand-drawn map of the northern coastline.")
    [cand] = art._library_candidates(cid)
    text = art.embed_space.clip(cand["description"], art.DOC_BYTES)
    got = art._history(cid, cand, text)
    assert got["source"].path == image_store.object_path(cand["image_id"])
    assert got["projection"] == art.PROJECTION and got["instance"] == {"campaign": cid}
    assert art.projection_texts(got["source"].path, got["source"].data, got["instance"]) == [text]


# -- second review regressions -----------------------------------------------------


def test_a_stop_inside_the_network_phase_leaves_unsent_paths_unsynced(home, fake, monkeypatch):
    _configure()
    wid, cid = _realm()
    paths = []
    for n in range(3):
        eid, path = _lore(home, wid, name=f"Pact {n}", body=f"Clause {n}.")
        _recall(wid, cid, eid)
        entities.update_entity(worlds.world_root(wid), "lore", eid, body=f"Clause {n}, amended.")
        paths.append(_rel(home, path))
    monkeypatch.setattr(embeddings, "BATCH", 1)
    sent_before = len(fake.calls)
    report = cache_sync.sync_paths(paths, mode="explicit",
                                   stop=lambda: len(fake.calls) > sent_before)
    assert report.stopped == "cancelled" and not report.ok
    assert sorted(p.status for p in report.paths) == ["refreshed", "unsynced", "unsynced"]
    assert report.embedding["not_sent"] == 2


def test_a_connection_wide_failure_names_its_kind_on_every_unsent_path(home, fake, monkeypatch):
    _configure()
    wid, cid = _realm()
    paths = []
    for n in range(3):
        eid, path = _lore(home, wid, name=f"Pact {n}", body=f"Clause {n}.")
        _recall(wid, cid, eid)
        entities.update_entity(worlds.world_root(wid), "lore", eid, body=f"Clause {n}, amended.")
        paths.append(_rel(home, path))
    monkeypatch.setattr(embeddings, "BATCH", 1)
    fake.error, fake.fail_after = embeddings.EmbeddingsError("auth", "no"), len(fake.calls)
    report = cache_sync.sync_paths(paths, mode="explicit")
    assert all(p.status == "failed" for p in report.paths) and not report.ok
    assert {o for p in report.paths for o in p.kinds.values()} == {"failed:auth"}


def test_the_lazy_cap_counts_texts_sent_in_unit_order(home, fake, monkeypatch):
    _configure()
    _wid, cid = _realm()
    path = _transcript(home, cid, [_post(n) for n in range(12)])
    texts = _embed_units(home, path, range(11))
    monkeypatch.setitem(cache_sync.VECTOR_PROJECTIONS, semsearch.PROJECTION, cache_sync.Projection(
        semsearch.PROJECTION, "semantic-search", "explicit", semsearch.projection_texts, 2, units=True))
    # Only units 9 and 10 change; the nine held ones cost nothing against the cap.
    data = path.read_text()
    tail = texts[9][-40:]
    path.write_text(data.replace(tail, tail.upper()))
    new = semsearch.document_texts(path, path.read_bytes())
    changed = [u for u in range(11) if new[u] != texts[u]]
    before = len(fake.calls)
    cache_sync.sync_paths([_rel(home, path)], mode="explicit")
    sent = [t for call in fake.calls[before:] for t in call]
    assert set(sent) == {new[u] for u in changed[:2]}


def test_a_vector_the_cache_could_not_keep_is_never_reported_built(home, fake, monkeypatch):
    """`vectors.save` drops a write it cannot make (a full or read-only cache):
    a text is `built` only once it reads back, so neither the report nor
    `--verify` can pass it."""
    _configure()
    wid, cid = _realm()
    eid, path = _lore(home, wid)
    _recall(wid, cid, eid)
    entities.update_entity(worlds.world_root(wid), "lore", eid, body="Verified edit.")
    monkeypatch.setattr(vectors, "save", lambda *a, **k: None)      # a save that silently loses it
    report = cache_sync.sync_paths([_rel(home, path)], mode="explicit", verify=True)
    assert list(report.paths[0].kinds.values()) == ["failed:not_saved"] and not report.ok


def test_write_through_defers_recall_when_recall_is_off(home, fake):
    _configure()
    wid, cid = _realm()
    eid, path = _lore(home, wid)
    _recall(wid, cid, eid)
    config.write_config(semantic_recall_depth="0")
    entities.update_entity(worlds.world_root(wid), "lore", eid, body="Edited with recall off.")
    before = len(fake.calls)
    written = cache_sync.sync_paths([_rel(home, path)], mode="write")
    assert len(fake.calls) == before and list(written.paths[0].kinds.values()) == ["deferred"]
    cache_sync.sync_paths([_rel(home, path)], mode="explicit")   # the user's own request
    assert len(fake.calls) == before + 1


def test_the_art_catalog_is_off_where_the_prompt_layout_switches_its_section_off(home):
    assert cache_sync._art_on()
    layout.write_layout([{"id": "available_art", "label": "", "enabled": False}])
    assert cache_sync._art_on()                  # a stored layout is a bypass until switched on
    config.write_config(prompt_layout_enabled="on")
    assert not cache_sync._art_on()
    assert not cache_sync.VECTOR_PROJECTIONS[art.PROJECTION].enabled()
    layout.write_layout([{"id": "available_art", "label": "", "enabled": True}])
    assert cache_sync._art_on()


def test_a_dry_run_leaves_parked_history_parked(home, fake, monkeypatch):
    flushed = []
    monkeypatch.setattr(vectors, "flush_deferred", lambda: flushed.append(1) or 0)
    cache_sync.sync_paths([], mode="explicit", dry_run=True)
    assert flushed == []
    cache_sync.sync_paths([], mode="explicit")
    assert flushed == [1]


def test_a_dry_run_with_no_embed_previews_the_deferral(home, fake):
    _configure()
    wid, cid = _realm()
    eid, path = _lore(home, wid)
    _recall(wid, cid, eid)
    entities.update_entity(worlds.world_root(wid), "lore", eid, body="Edited, embeddings off.")
    dry = cache_sync.sync_paths([_rel(home, path)], mode="explicit", dry_run=True, embed=False)
    assert dry.embedding["would_send"] == 0 and dry.embedding["deferred"] == 1
    assert list(dry.paths[0].kinds.values()) == ["deferred"]


def test_a_rename_between_kinds_and_campaigns_moves_the_recall_instance(home):
    kind = f"vector:{semantic.PROJECTION}:abc"
    row = json.dumps({"campaign": "saltmarch", "entity": ["lore", "pact"]})
    moved = cache_sync.VECTORS.remap(kind, row, "worlds/realm/lore/pact.md", "worlds/realm/items/pact.md")
    assert moved == {"campaign": "saltmarch", "entity": ["items", "pact"]}
    moved = cache_sync.VECTORS.remap(kind, row, "campaigns/saltmarch/lore/pact.md",
                                     "campaigns/winifred/lore/oath.md")
    assert moved == {"campaign": "winifred", "entity": ["lore", "oath"]}
    other = json.dumps({"campaign": "saltmarch", "entity": ["lore", "elsewhere"]})
    assert cache_sync.VECTORS.remap(kind, other, "worlds/realm/lore/pact.md",
                                    "worlds/realm/items/pact.md") == json.loads(other)


def test_a_cross_kind_rename_is_warmed_from_its_new_file(home, fake):
    _configure()
    wid, cid = _realm()
    eid, path = _lore(home, wid)
    _recall(wid, cid, eid)
    new_path = worlds.world_root(wid) / "items" / f"{eid}.md"
    new_path.parent.mkdir(parents=True, exist_ok=True)
    path.rename(new_path)
    report = cache_sync.sync_paths([], mode="explicit", renamed={_rel(home, path): _rel(home, new_path)})
    assert report.embedding["held"] == 1 and report.ok
    [row] = _rows_at(home, new_path)
    assert json.loads(row.instance)["entity"] == ["items", eid]


def test_a_built_row_is_summarised_once_and_never_as_planned(home, fake):
    _configure()
    wid, cid = _realm()
    eid, path = _lore(home, wid)
    _recall(wid, cid, eid)
    entities.update_entity(worlds.world_root(wid), "lore", eid, body="Edited for the summary.")
    report = cache_sync.sync_paths([_rel(home, path)], mode="explicit")
    summary = report.hooks["vectors"]
    assert summary.get("built") == 1 and "planned" not in summary


def test_a_dry_run_counts_a_text_two_rows_want_once(home, fake):
    _configure()
    wid, cid = _realm()
    other = campaigns.create_campaign("Winifred's Run", wid)
    eid, path = _lore(home, wid)
    _recall(wid, cid, eid)
    data = path.read_bytes()
    text = semantic.record_text(eid, data)        # a hit records no row: give the other reader one
    vectors.load(_space(), [text], sources={text: compiled.WrittenInput("source", path, data)},
                 projection=semantic.PROJECTION, instances={text: {"campaign": other, "entity": ["lore", eid]}})
    assert len(_rows_at(home, path)) == 2
    entities.update_entity(worlds.world_root(wid), "lore", eid, body="One edit, two readers.")
    dry = cache_sync.sync_paths([_rel(home, path)], mode="explicit", dry_run=True)
    real = cache_sync.sync_paths([_rel(home, path)], mode="explicit")
    assert dry.embedding["would_send"] == 1 == real.embedding["sent"]


def test_every_entry_sharing_a_text_records_its_own_history(home, fake):
    _configure()
    wid, cid = _realm()
    first, first_path = _lore(home, wid, name="Pact", body="The pact binds the coast.")
    second, second_path = _lore(home, wid, name="Pact", body="The pact binds the coast.")
    assert first != second
    semantic.recall_scored([_candidate(wid, cid, first), _candidate(wid, cid, second)],
                           "Somebody speaks of the coast.", campaign=cid)
    assert len(fake.calls) == 1                       # one text, embedded once
    assert len(_rows_at(home, first_path)) == 1 and len(_rows_at(home, second_path)) == 1


def test_a_world_record_moved_into_a_campaign_is_warmed_for_that_campaign(home, fake):
    _configure()
    wid, cid = _realm()
    other = campaigns.create_campaign("Winifred's Run", wid)
    eid, path = _lore(home, wid)
    _recall(wid, cid, eid)
    data = path.read_bytes()
    text = semantic.record_text(eid, data)
    vectors.load(_space(), [text], sources={text: compiled.WrittenInput("source", path, data)},
                 projection=semantic.PROJECTION, instances={text: {"campaign": other, "entity": ["lore", eid]}})
    assert len(_rows_at(home, path)) == 2
    new_path = home / "campaigns" / cid / "lore" / f"{eid}.md"
    new_path.parent.mkdir(parents=True, exist_ok=True)
    path.rename(new_path)
    report = cache_sync.sync_paths([], mode="explicit", renamed={_rel(home, path): _rel(home, new_path)})
    [row] = _rows_at(home, new_path)                  # both readers' rows coalesce into one
    assert json.loads(row.instance) == {"campaign": cid, "entity": ["lore", eid]}
    assert report.embedding["held"] == 1 and report.ok


def test_every_document_holding_a_passage_records_its_own_history(home, fake):
    _configure()
    wid, _cid = _realm()
    paths = [_lore(home, wid, name="Pact", body="The pact binds the coast.")[1] for _ in range(2)]
    texts = [semsearch.document_texts(p, p.read_bytes()) for p in paths]
    [shared] = sorted(set(texts[0]) & set(texts[1]))     # the same passage in both documents
    _missing, _hits, sources = semsearch._uncached(_space(), semsearch._records("world", wid))
    assert sorted(path for path, _unit in sources[shared]) == sorted(paths)
    semsearch._save_passages(_space(), [shared], [[1.0, 0.0]], sources)
    for path, units in zip(paths, texts, strict=True):
        recorded = {json.loads(r.instance)["unit"] for r in _rows_at(home, path)
                    if semsearch.PROJECTION in r.kind}
        assert units.index(shared) in recorded


def test_every_image_object_sharing_a_description_records_its_own_history(home):
    _configure()
    _wid, cid = _realm()
    for name, colour in (("coastline", b"\x00\x00\x00"), ("harbour", b"\xff\xff\xff")):
        campaign_images.put_image(cid, name, _png(colour), "png")
        image_descriptions.set_in(campaign_images.images_dir(cid), name,
                                  "A hand-drawn map of the northern coastline.")
    cands = art._library_candidates(cid)
    assert len({c["image_id"] for c in cands}) == 2
    texts = [art.embed_space.clip(c["description"], art.DOC_BYTES) for c in cands]
    assert len(set(texts)) == 1
    art._save_warmed({"space": _space()}, cands, texts, texts[:1], [[1.0, 0.0]], {}, cid)
    for cand in cands:
        assert _rows_at(home, image_store.object_path(cand["image_id"]))


def test_a_reply_that_lands_after_the_root_moved_is_never_saved(home, fake, monkeypatch, tmp_path):
    _configure()
    wid, cid = _realm()
    eid, path = _lore(home, wid)
    _recall(wid, cid, eid)
    entities.update_entity(worlds.world_root(wid), "lore", eid, body="Edited before a move.")
    text = semantic.record_text(eid, path.read_bytes())
    space = _space()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    real = cache_sync._embed_chunk

    def moved_meanwhile(*args, **kwargs):
        got = real(*args, **kwargs)
        monkeypatch.setenv("GRIMOIRE_HOME", str(elsewhere))
        return got

    monkeypatch.setattr(cache_sync_vectors, "_embed_chunk", moved_meanwhile)
    report = cache_sync.sync_paths([_rel(home, path)], mode="explicit", root=home)
    assert report.stopped == "root_moved" and not report.ok
    assert report.paths[0].status == "unsynced"
    assert not vectors.load(space, [text])            # nothing landed in the other library


def test_a_text_two_producers_want_is_sent_once_under_a_fixed_task():
    recall = cache_sync.VECTOR_PROJECTIONS[semantic.PROJECTION]
    catalog = cache_sync.VECTOR_PROJECTIONS[art.PROJECTION]
    for first, second in ((recall, catalog), (catalog, recall)):
        plan: dict = {}
        cache_sync._wanted(plan, "One shared text.", first)
        cache_sync._wanted(plan, "One shared text.", second)
        assert list(plan) == ["One shared text."]
        assert plan["One shared text."].task == "semantic-recall"


@pytest.mark.parametrize("gate", ["explicit_only", "disabled", "stale_space", "embedding_off"])
def test_a_deferred_vector_row_never_decodes_its_instance_or_projects(home, monkeypatch, gate):
    """The spend/space gates precede readers, including readers of obsolete history."""
    rel = "worlds/realm/lore/pact.md"
    path = home / rel
    path.parent.mkdir(parents=True)
    path.write_bytes(b"The pact binds the coast.")
    projected = []
    original = cache_sync.VECTOR_PROJECTIONS[semantic.PROJECTION]
    projection = cache_sync.Projection(
        original.name, original.task, "explicit" if gate == "explicit_only" else "quiet",
        lambda *args: projected.append(args) or ["The pact"], original.limit,
        enabled=lambda: gate != "disabled")
    monkeypatch.setitem(cache_sync.VECTOR_PROJECTIONS, projection.name, projection)
    digest = "a" * 64
    row = compiled.Materialized(path, f"vector:{projection.name}:{digest}", "{invalid", "", 0)
    ctx = cache_sync.WarmContext(
        root=home, mode="write", dry_run=False, verify=False, embedding=True,
        space=None if gate == "embedding_off" else {"space": "unused"},
        space_digest="b" * 64 if gate == "stale_space" else digest, stop=lambda: False)

    result = cache_sync.VECTORS.local(ctx, {rel: (row,)})

    expected = "deferred" if gate in ("explicit_only", "disabled") else "skipped"
    assert result.outcomes == {(rel, row.kind): expected}
    assert result.plan is None and projected == []
    assert result.embedding == ({gate: 1} if expected == "skipped" else {})


def test_a_vector_reply_keeps_the_local_source_snapshot_when_the_record_changes(home, fake, monkeypatch):
    """An in-flight edit must stay stale: its new bytes never vouch for the earlier text."""
    _configure()
    wid, cid = _realm()
    eid, path = _lore(home, wid)
    _recall(wid, cid, eid)
    entities.update_entity(worlds.world_root(wid), "lore", eid, body="Edited before the request.")
    before_request = path.read_bytes()
    earlier_text = semantic.record_text(eid, before_request)
    real = cache_sync._embed_chunk

    def edited_meanwhile(*args, **kwargs):
        entities.update_entity(worlds.world_root(wid), "lore", eid, body="Edited during the request.")
        return real(*args, **kwargs)

    with monkeypatch.context() as during:
        during.setattr(cache_sync_vectors, "_embed_chunk", edited_meanwhile)
        report = cache_sync.sync_paths([_rel(home, path)], mode="explicit", verify=True)

    assert report.ok and report.embedding["sent"] == 1
    [row] = _rows_at(home, path)
    assert row.built_from == hashlib.sha256(before_request).hexdigest()
    assert row.built_from != hashlib.sha256(path.read_bytes()).hexdigest()
    assert earlier_text in vectors.load(_space(), [earlier_text])
    later_text = semantic.record_text(eid, path.read_bytes())
    assert not vectors.load(_space(), [later_text])
    again = cache_sync.sync_paths([_rel(home, path)], mode="explicit", verify=True)
    assert again.ok and again.embedding["sent"] == 1 and _docs(fake)[-1] == later_text
    [current] = _rows_at(home, path)
    assert current.built_from == hashlib.sha256(path.read_bytes()).hexdigest()


def _edited_shared_sources(home):
    """Two independently hot records now need the same replacement text."""
    _configure()
    wid, cid = _realm()
    records = [_lore(home, wid) for _ in range(2)]
    semantic.recall_scored([_candidate(wid, cid, eid) for eid, _path in records],
                           "Somebody speaks of the coast.", campaign=cid)
    for eid, _path in records:
        entities.update_entity(worlds.world_root(wid), "lore", eid, body="One edit, two source records.")
    texts = {semantic.record_text(eid, path.read_bytes()) for eid, path in records}
    assert len(texts) == 1
    return [path for _eid, path in records], texts.pop()


def test_a_vector_arriving_before_send_rewrites_every_claimed_sources_history(home, fake, monkeypatch):
    paths, text = _edited_shared_sources(home)
    real = cache_sync._VectorsHook.network
    space = _space()

    def arrived_meanwhile(self, ctx, plan):
        vectors.save(space, text, [1.0, 0.0])       # another reader won the race, without these rows
        return real(self, ctx, plan)

    monkeypatch.setattr(cache_sync._VectorsHook, "network", arrived_meanwhile)
    before = len(fake.calls)
    report = cache_sync.sync_paths([_rel(home, path) for path in paths], mode="explicit", verify=True)

    assert report.ok and len(fake.calls) == before
    assert report.embedding["sent"] == report.embedding["requests"] == 0
    assert [item.status for item in report.paths] == ["current", "current"]
    assert all(list(item.kinds.values()) == ["hit"] for item in report.paths)
    for path in paths:
        [row] = _rows_at(home, path)
        assert row.built_from == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("reply", [None, [], [[1.0, 0.0], [1.0, 0.0]]], ids=["missing", "short", "long"])
def test_a_malformed_chunk_reply_fails_all_sources_without_rewriting_history(home, fake, reply):
    """The grouped door's result is still checked before any of its vectors can land."""
    paths, text = _edited_shared_sources(home)
    before = {path: _rows_at(home, path) for path in paths}
    ctx = cache_sync._context(home, "explicit", dry_run=False, verify=True, embed=True, stop=lambda: False)
    local = cache_sync.VECTORS.local(ctx, {_rel(home, path): before[path] for path in paths})
    result = cache_sync.NetworkResult()

    failure = cache_sync.VECTORS._landed(_space(), [text], embed.GroupResult(reply), True, local.plan, result)

    assert failure == ""                  # an input's bad reply does not stop later chunks
    assert result.embedding == {"requests": 1, "sent": 1}
    assert result.outcomes == {(_rel(home, path), row.kind): "failed:bad_response"
                               for path, rows in before.items() for row in rows}
    assert not vectors.load(_space(), [text])
    for path in paths:
        assert _rows_at(home, path) == before[path]
