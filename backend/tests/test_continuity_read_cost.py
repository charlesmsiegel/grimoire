"""What the capstone's read paths cost (spec §25.3, §25.4, §3.10; Slice G
Decisions 7, 8, 9 and 17).

"Once per request" is read as a constant number of whole-file reads
(Decision 7): each whole file is read a constant number of times, whatever the
campaign's record, scene and finding counts; no one scene file is read more
often as the campaign grows; and no transcript, card version or image directory
is read at all. Every test here seeds the same campaign at two sizes and
compares the counts, rather than pinning a number a harmless refactor would
move.

Every reader is counted through a pass-through recorder, never a raiser: a
raiser inside a tolerant read (`_soft`, `_attempt`, a section's `_tolerant`) is
swallowed and the test passes with the read still made.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections import Counter

import pytest

import grimoire.embeddings
from grimoire import routes
from grimoire.routes import common, todo
from grimoire.store import (
    appearances,
    assets,
    calendars,
    campaigns,
    characters,
    chronicle,
    clock,
    commitments,
    embed_space,
    events,
    overlay,
    pcs,
    plot,
    relationships,
    scene_ideas,
    suggest,
    worlds,
)
from grimoire.store import scenes as scenes_pkg
from grimoire.store.appearances import cast as appearances_cast
from grimoire.store.continuity import (
    candidates,
    canon,
    drivers,
    graph,
    pending,
    pressure,
    reconcile,
    review,
    similarity,
)
from grimoire.store.continuity import doc as continuity_doc
from grimoire.store.scenes import read as scenes_read
from tests import llm_fakes

SIZES = (1, 12)

#: Whole-file readers: each must be read as often at 12 as at 1.
WHOLE = {
    "plot.read": (plot, "read"),
    "commitments.read": (commitments, "read"),
    "events.read": (events, "read"),
    "doc.read": (continuity_doc, "read"),
    "candidates.read": (candidates, "read"),
    "chronicle.read_chronicle": (chronicle, "read_chronicle"),
    "relationships.read": (relationships, "read"),
    "scene_ideas.read": (scene_ideas, "read"),
    "cast.roster": (appearances_cast, "roster"),
}
#: Per-scene readers, counted by scene id.
PER_SCENE = ("get_time_history", "get_location_history", "read_scene_meta")
#: Readers no read path may reach: a transcript, a card version, an image
#: directory, or a full actor read (which reaches both of the last two).
ZERO = {
    "read_scene": None,
    "read_scene_window": None,
    "characters.read_card": (characters, "read_card"),
    "characters.read_character": (characters, "read_character"),
    "pcs.read_pc": (pcs, "read_pc"),
    "assets.list_images": (assets, "list_images"),
}
#: Every scene reader is re-exported BY VALUE from `store/scenes/__init__.py`,
#: so a call through `store.scenes.read_scene` never reaches a patch on
#: `store.scenes.read.read_scene`. Both bindings are patched, around one
#: original captured here, once, so a second `_reads` never wraps a wrapper.
SCENE_READERS = ("get_time_history", "get_location_history", "read_scene_meta",
                 "read_scene", "read_scene_window", "list_scenes")
_SCENE_ORIGINALS = {name: getattr(scenes_read, name) for name in SCENE_READERS}
_ORIGINALS = {key: getattr(owner, name) for key, (owner, name) in
              [*WHOLE.items(), *((k, v) for k, v in ZERO.items() if v is not None)]}


def _url(cid: str, route: str) -> str:
    return f"/api/campaigns/{cid}/{route}"


#: The six read paths Decision 7 names, each with the readers it is known to
#: use at n=1 -- the positive control that keeps a mis-wired spy from passing.
ROUTES = {
    "ledger": ("ledger", ("plot.read", "commitments.read", "chronicle.read_chronicle",
                          "relationships.read", "doc.read")),
    "continuity": ("continuity", ("plot.read", "commitments.read", "events.read",
                                  "doc.read")),
    "candidates": ("continuity/candidates", ("plot.read", "commitments.read",
                                             "candidates.read", "doc.read")),
    "drivers": ("continuity/drivers", ("plot.read", "commitments.read", "events.read")),
    "graph": ("continuity/graph", ("plot.read", "commitments.read", "events.read",
                                   "doc.read", "candidates.read", "chronicle.read_chronicle",
                                   "relationships.read", "scene_ideas.read",
                                   "cast.roster")),
    "scene-ideas": ("scene-ideas?greetings=false", ("plot.read", "scene_ideas.read")),
}


def _fixed(native: str) -> int:
    return calendars.fixed_of(calendars.get_provider({"provider": "gregorian"}), native)


def _seed(client, n: int) -> tuple[str, list[str]]:
    """Realm (Mara born --05-12, the PC Seraphine born --05-14) and the
    campaign Saltmarch, its clock at 2026-05-10 with no holiday library; `n`
    scenes seating both, `n` threads and `n` commitments each moved in its own
    scene, `n` events in the next 30 days, `n` saved ideas each serving a
    thread plus one anchored to Seraphine's birthday, one `pays_off` link, and
    `n` live `possible_duplicate` findings over consecutive threads.

    The findings need `n + 1` threads to pair `n` times, so the last scene also
    moves one more thread ("Mara's errand n"). `client` is taken only so the
    store is the fixture's."""
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    mara, mara_v = characters.create_character(wroot, "Mara")
    characters.set_birthdate(wroot, mara, "--05-12")
    seraphine, seraphine_v = pcs.create_pc(wroot, "Seraphine", [], persona={
        **pcs.blank_persona("Seraphine"), "birthdate": "--05-14"})
    cid = campaigns.create_campaign("Saltmarch", wid, calendar="gregorian")
    root = campaigns.campaign_root(cid)
    cfg = calendars.read_calendar(root)
    cfg["primary"] = {**cfg["primary"], "region": "", "custom_holidays": []}
    calendars.write_calendar(root, cfg)
    clock.advance(cid, to="2026-05-10")

    sids = []
    for i in range(n):
        sid = scenes_pkg.create_scene(cid, f"Saltmarch scene {i}")
        appearances.appear(cid, sid, "pcs", seraphine, seraphine_v, "player", narrate=False)
        appearances.appear(cid, sid, "characters", mara, mara_v, "npc", narrate=False)
        sids.append(sid)
    for i in range(n + 1):
        plot.set_movement(cid, f"mara-s-errand-{i}", f"Mara's errand {i}", "open",
                          f"Mara runs errand {i} in Saltmarch.", sids[min(i, n - 1)])
    for i in range(n):
        commitments.set_movement(cid, f"winifred-s-debt-{i}", f"Winifred's debt {i}",
                                 "promise", "open", None, f"Winifred owes debt {i}.", sids[i])
        day = dt.date(2026, 5, 11) + dt.timedelta(days=(2 * i) % 30)
        events.create(cid, f"Saltmarch market {i}", day.isoformat())
    for i in range(n):
        prov = suggest.idea_provenance(
            cid, [{"ref": f"thread:mara-s-errand-{i}", "action": "advance"}], None)
        scene_ideas.add(cid, f"Mara's errand {i}", "Mara runs the errand.",
                        drivers=prov["drivers"], time_anchor=prov["time_anchor"])
    birthday = f"birthday:pcs:{seraphine}:{_fixed('2026-05-14')}"
    prov = suggest.idea_provenance(cid, [], {"ref": birthday, "relation": "on"})
    assert prov["time_anchor"] is not None
    scene_ideas.add(cid, "Seraphine's birthday", "Mara brings a gift.",
                    drivers=prov["drivers"], time_anchor=prov["time_anchor"])
    review.create_link(cid, "thread:mara-s-errand-0", "commitment:winifred-s-debt-0",
                       "pays_off")

    current = pending.Current.load(cid)
    data = candidates.empty()
    for i in range(n):
        refs = [f"thread:mara-s-errand-{i}", f"thread:mara-s-errand-{i + 1}"]
        fp = pending.fingerprint(current, "possible_duplicate", refs)
        assert fp is not None
        data["records"][canon.candidate_id("possible_duplicate", refs)] = {
            "kind": "possible_duplicate", "refs": refs, "fingerprint": fp,
            "signals": {"shared_actors": [f"characters:{mara}", f"pcs:{seraphine}"],
                        "shared_scenes": [sids[min(i, n - 1)]]},
            "proposal": None, "created": ""}
    candidates.write(cid, data)
    assert [r["id"] for r in reconcile.play_order(scenes_read.list_scenes(cid))] == sids
    return cid, sids


def _reads(monkeypatch, cid: str, sid: str) -> dict[str, Counter]:
    """Count every reader in `WHOLE`, `PER_SCENE` and `ZERO`, plus
    `list_scenes`. A scene reader's Counter is keyed by the scene id it read;
    every other reader's by the campaign it read."""
    counts: dict[str, Counter] = {key: Counter() for key in
                                  [*WHOLE, *PER_SCENE, *ZERO, "list_scenes"]}

    def counting(key: str, real, by_scene: bool):
        def wrapper(*args, **kwargs):
            counts[key][args[1] if by_scene and len(args) > 1 else args[0] if args else ""] += 1
            return real(*args, **kwargs)
        return wrapper

    for name in SCENE_READERS:
        wrapped = counting(name, _SCENE_ORIGINALS[name], name != "list_scenes")
        monkeypatch.setattr(scenes_pkg, name, wrapped)
        monkeypatch.setattr(scenes_read, name, wrapped)
    for key, target in [*WHOLE.items(), *ZERO.items()]:
        if target is not None:
            monkeypatch.setattr(target[0], target[1], counting(key, _ORIGINALS[key], False))

    # Calibration: one call through each binding must count twice, or a spy
    # that missed a binding would pass every must-be-zero check below.
    scenes_pkg.read_scene(cid, sid)
    scenes_read.read_scene(cid, sid)
    assert sum(counts["read_scene"].values()) == 2
    for c in counts.values():
        c.clear()
    return counts


def _total(c: Counter) -> int:
    return sum(c.values())


def _measure(client, monkeypatch, n: int, read) -> dict[str, Counter]:
    cid, sids = _seed(client, n)
    with monkeypatch.context() as m:
        counts = _reads(m, cid, sids[0])
        read(cid, sids)
        return {k: Counter(v) for k, v in counts.items()}


# ---- Decision 7: a constant number of whole-file reads ------------------------
@pytest.mark.parametrize("route", list(ROUTES))
def test_read_paths_read_each_file_a_constant_number_of_times(client, monkeypatch, route):
    path, known = ROUTES[route]

    def read(cid, _sids):
        r = client.get(_url(cid, path))
        assert r.status_code == 200, r.text

    small, large = (_measure(client, monkeypatch, n, read) for n in SIZES)
    for key in WHOLE:
        assert _total(large[key]) == _total(small[key]), (route, key, small[key], large[key])
    # `list_scenes` reads every scene's frontmatter head (`_scene_row`), not
    # through a PER_SCENE reader, so a sweep per record or finding grows with
    # the campaign while every per-scene maximum above stays put.
    assert _total(large["list_scenes"]) == _total(small["list_scenes"]), (
        route, small["list_scenes"], large["list_scenes"])
    for key in PER_SCENE:
        assert max(large[key].values(), default=0) == max(small[key].values(), default=0), (
            route, key, small[key], large[key])
    for counts in (small, large):
        for key in ZERO:
            assert _total(counts[key]) == 0, (route, key, counts[key])
    for key in known:
        assert _total(small[key]) >= 1, (route, key)


def test_the_continuity_chores_read_a_constant_number_of_files(client, monkeypatch):
    """§18.2, §25.4: the two Todo continuity chores and their item lists read
    the same files at both sizes, and never a scene."""
    def read(cid, _sids):
        ctx = todo._Ctx(cid)
        assert todo._chore_continuity_overlaps(ctx) is not None
        todo._chore_continuity_closures(ctx)
        assert todo._items_continuity_overlaps(cid)
        todo._items_continuity_closures(cid)

    small, large = (_measure(client, monkeypatch, n, read) for n in SIZES)
    for key in WHOLE:
        assert _total(large[key]) == _total(small[key]), (key, small[key], large[key])
    for counts in (small, large):
        for key in ("read_scene", "read_scene_window", "list_scenes"):
            assert _total(counts[key]) == 0, (key, counts[key])
    assert _total(small["candidates.read"]) >= 1


def test_continuity_review_reads_name_actors_without_cards(client, monkeypatch):
    cid, sids = _seed(client, 1)
    with monkeypatch.context() as m:
        counts = _reads(m, cid, sids[0])
        r = client.get(_url(cid, "continuity/candidates"))
        assert r.status_code == 200, r.text
        names = r.json()["names"]
        assert names["characters:mara"] == "Mara"
        assert names["pcs:seraphine"] == "Seraphine"
        for key in ("pcs.read_pc", "characters.read_character", "assets.list_images"):
            assert _total(counts[key]) == 0, (key, counts[key])


def test_the_saved_idea_read_names_a_pc_birthday_without_images(client, monkeypatch):
    cid, sids = _seed(client, 1)
    with monkeypatch.context() as m:
        counts = _reads(m, cid, sids[0])
        r = client.get(_url(cid, "scene-ideas?greetings=false"))
        assert r.status_code == 200, r.text
        [anchored] = [i for i in r.json() if i.get("time_anchor")]
        assert anchored["time_anchor"]["kind"] == "birthday"
        assert anchored["time_anchor"]["label"] == "Seraphine"
        assert _total(counts["assets.list_images"]) == 0


def test_the_graph_reads_location_names_once(client, monkeypatch):
    """Decision 8: the whole-kind overlay read, once per build, at both sizes."""
    calls: list[tuple] = []
    real = overlay.list_entities

    def recording(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    for n in SIZES:
        cid, _sids = _seed(client, n)
        calls.clear()
        with monkeypatch.context() as m:
            m.setattr(overlay, "list_entities", recording)
            graph.build(cid)
        assert calls.count((cid, "locations")) == 1, (n, calls)


# ---- §3.10, AC13, AC16: no read path reaches a model or an embedding -----------
_NO_MODEL = {
    **{name: ("GET", f"campaigns/{{cid}}/{path}", None) for name, (path, _k) in ROUTES.items()},
    "briefing": ("GET", "campaigns/{cid}/scenes/{sid}/briefing", None),
    "advance-preview": ("POST", "campaigns/{cid}/advance/preview", {"days": 7}),
    "todo-campaign": ("GET", "todo?campaign={cid}", None),
    "todo": ("GET", "todo", None),
    "todo-overlaps": ("GET", "todo/continuity-overlaps/items?campaign={cid}", None),
    "todo-closures": ("GET", "todo/continuity-closures/items?campaign={cid}", None),
    "shell": ("GET", "shell?campaign={cid}", None),
}


def _armed(client, monkeypatch):
    """Every way a read could reach a model or an embedding, recorded.

    The connection is configured first: every model call in the app resolves
    it (`_require_connection`) before it reaches the client, so without one a
    call on a tolerant path (`_soft`, `_attempt`, a section's `_tolerant`) is
    stopped by the 409 and swallowed before the fake can count it -- the
    raiser-inside-`_soft` trap one layer up."""
    r = client.put("/api/llm-connections/openrouter", json={"api_key": "sk-test"})
    assert 200 <= r.status_code < 300, r.text
    monkeypatch.setattr(embed_space, "resolve", lambda *a, **k: {
        "model": "m", "base_url": "http://embeddings.invalid", "key": "", "space": "s"})
    fake_embeddings = llm_fakes.FakeEmbeddings()
    monkeypatch.setattr(similarity, "_CLIENT", fake_embeddings)
    class_calls: list = []
    real_embed = grimoire.embeddings.EmbeddingsClient.embed

    def recording(self, *args, **kwargs):
        class_calls.append(args)
        return real_embed(self, *args, **kwargs)

    monkeypatch.setattr(grimoire.embeddings.EmbeddingsClient, "embed", recording)
    fake = llm_fakes.from_entries([{"when": {"system_contains": "\x00no request matches"},
                                    "reply": ""}])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    return fake, fake_embeddings, class_calls


@pytest.mark.parametrize("name", list(_NO_MODEL))
def test_no_read_only_path_reaches_a_model_or_an_embedding(client, monkeypatch, name):
    cid, sids = _seed(client, 2)
    fake, fake_embeddings, class_calls = _armed(client, monkeypatch)

    method, path, body = _NO_MODEL[name]
    url = "/api/" + path.format(cid=cid, sid=sids[0])
    r = client.request(method, url, json=body)
    assert 200 <= r.status_code < 300, (name, r.status_code, r.text)
    assert fake.calls == 0
    assert fake_embeddings.calls == []
    assert class_calls == []


def test_the_no_model_recorders_fire_on_a_tolerant_path(client, monkeypatch):
    """Positive control for the test above: a model call and an embedding
    made the app's way (resolve the connection, then call) inside the real
    `drivers._soft`, on a read path, under the same arming, move both
    recorders. Were either still stopped before its fake, the test above
    would pass with the call made."""
    cid, _sids = _seed(client, 2)
    fake, fake_embeddings, _class_calls = _armed(client, monkeypatch)

    def model_call():
        conn = common._require_connection("continuity_reconcile", cid)
        asyncio.run(fake.complete([{"role": "system", "content": "control"}], conn))

    def embedding_call():
        space = embed_space.resolve()
        similarity._CLIENT.embed(["control"], space["model"], space["key"], space["base_url"])

    def tolerant_matching():
        drivers._soft(model_call, None)
        drivers._soft(embedding_call, None)
        return "basic"

    monkeypatch.setattr(drivers, "matching", tolerant_matching)
    r = client.get(_url(cid, "continuity/drivers"))
    assert r.status_code == 200, r.text
    assert fake.calls == 1
    assert fake_embeddings.calls == [["control"]]


# ---- §25.4, Decision 17: the shell does no continuity review work ---------------
def test_the_shell_read_does_no_continuity_review_work(client, monkeypatch):
    recorded: list[str] = []
    measured = []
    for n in SIZES:
        cid, sids = _seed(client, n)
        for sid in sids[:-1]:
            scenes_pkg.mark_absorbed(cid, sid, "", "")
        with monkeypatch.context() as m:
            # Counters first, so each recorder wraps whatever `_reads` put on
            # `candidates.read` rather than being replaced by it.
            counts = _reads(m, cid, sids[0])
            for owner, attr in ((pending, "findings"), (candidates, "read"),
                                (reconcile, "discover"), (pressure, "build"),
                                (drivers, "snapshot")):
                real = getattr(owner, attr)

                def recorder(*args, _real=real, _attr=attr, **kwargs):
                    recorded.append(_attr)
                    return _real(*args, **kwargs)

                m.setattr(owner, attr, recorder)
            r = client.get(f"/api/shell?campaign={cid}")
            assert r.status_code == 200, r.text
            block = r.json()["campaign"]
            measured.append((sids[-1], {k: Counter(v) for k, v in counts.items()}))
        assert not [k for k in block if "continuity" in k or "candidate" in k], block
    assert recorded == []
    (open_small, small), (open_large, large) = measured
    assert set(small["read_scene"]) == {open_small}
    assert set(large["read_scene"]) == {open_large}
    assert small["read_scene"][open_small] == large["read_scene"][open_large] >= 1
    assert _total(small["read_scene_window"]) == _total(large["read_scene_window"]) == 0
