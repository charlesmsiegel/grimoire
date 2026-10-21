"""The rolling-summary intactness test, as a store function (roadmap 08-S1).

`store.rolling_intact` is the one place that decides whether a stored running
summary still describes the transcript under it. The route asks it for the
panel's `stale` and a fold's `prior`; `store/searchdocs` asks it before a
running summary may reach a scene's search document. These pin that the two
callers agree -- the function's answer IS the route's -- over the four ways a
covered prefix stops being what was folded: a hidden post, a cut, an edit and a
prompt rule. And that `regex.layers.signature`, which the searchdocs memo is
keyed on, moves when a rule file does.
"""


import pytest

import grimoire.store as store

THINK = {"name": "Strip thinking", "pattern": r"<think>[\s\S]*?</think>", "replacement": ""}


pytestmark = pytest.mark.unfaked_llm


def _scene(client, posts=10):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Saltmarch"}).json()["id"]
    for n in range(posts):
        if n % 2 == 0:
            store.scenes.append_message(cid, sid, "user", f"Post {n}.")
        else:
            store.scenes.append_reply(cid, sid, [{"speaker": "Mara",
                                                  "content": f"<think>plan {n}</think>Post {n}."}])
    return cid, sid


def _fold(cid, sid, covered=None):
    """Store a summary exactly as `_rolling_commit` would after a fold over
    the first `covered` posts, with no provider involved."""
    scene = store.scenes.read_scene(cid, sid)
    messages = scene["messages"]
    covered = len(messages) if covered is None else covered
    digest = store.rolling_intact.prefix_digest(cid, sid, messages[:covered], len(messages))
    facts = store.rolling_summary.facts_digest(store.chronicle.scene_facts(cid, sid))
    store.scenes.set_rolling_summary(cid, sid, "Mara and the ledger.", covered, digest, facts)


def _stale(cid, sid) -> bool:
    scene = store.scenes.read_scene(cid, sid)
    return store.rolling_intact.view(cid, sid, scene,
                                     store.chronicle.scene_facts(cid, sid))["stale"]


def _route_stale(client, cid, sid) -> bool:
    return client.get(f"/api/campaigns/{cid}/scenes/{sid}/rolling-summary").json()["stale"]


def _agree(client, cid, sid) -> bool:
    mine = _stale(cid, sid)
    assert mine == _route_stale(client, cid, sid)
    return mine


def test_a_fresh_fold_is_intact_and_the_route_agrees(client):
    cid, sid = _scene(client)
    _fold(cid, sid)
    assert _agree(client, cid, sid) is False
    view = store.rolling_intact.view(cid, sid, store.scenes.read_scene(cid, sid),
                                     store.chronicle.scene_facts(cid, sid))
    assert view["prior"] == "Mara and the ledger." and view["base"] == 10


def test_appending_keeps_it_intact(client):
    cid, sid = _scene(client)
    _fold(cid, sid)
    store.scenes.append_message(cid, sid, "user", "And another thing.")
    assert _agree(client, cid, sid) is False


def test_hiding_a_covered_post_makes_it_stale(client):
    cid, sid = _scene(client)
    _fold(cid, sid)
    assert store.scenes.set_excluded(cid, sid, 7, True)
    assert _agree(client, cid, sid) is True


def test_hiding_a_post_past_the_covered_prefix_does_not(client):
    cid, sid = _scene(client, posts=12)
    _fold(cid, sid, covered=10)
    assert store.scenes.set_excluded(cid, sid, 11, True)
    assert _agree(client, cid, sid) is False


def test_a_cut_into_the_covered_prefix_makes_it_stale(client):
    cid, sid = _scene(client)
    _fold(cid, sid)
    store.scenes.trim_continuation(cid, sid, 4)
    assert _agree(client, cid, sid) is True


def test_editing_a_covered_post_makes_it_stale(client):
    cid, sid = _scene(client)
    _fold(cid, sid)
    store.scenes.edit_message(cid, sid, 2, "Post 2, rewritten.")
    assert _agree(client, cid, sid) is True


def test_a_prompt_rule_over_a_covered_post_makes_it_stale(client):
    cid, sid = _scene(client)
    _fold(cid, sid)
    store.regex.layers.write_campaign(cid, {"rules": [{**THINK, "applies": ["display"]}]})
    assert _agree(client, cid, sid) is False          # display-only: not what was folded
    store.regex.layers.write_campaign(cid, {"rules": [THINK]})
    assert _agree(client, cid, sid) is True


def test_an_unsummarized_scene_is_not_stale(client):
    cid, sid = _scene(client)
    assert _agree(client, cid, sid) is False


def test_the_layers_signature_moves_when_a_rule_file_does(client, tmp_path):
    cid, _sid = _scene(client, posts=0)
    before = store.regex.layers.signature(cid)
    assert before is not None
    assert store.regex.layers.signature(cid) == before
    store.regex.layers.write_campaign(cid, {"rules": [THINK]})
    after_campaign = store.regex.layers.signature(cid)
    assert after_campaign != before
    store.regex.layers.write_level("global", "", {"rules": [{**THINK, "name": "Global"}]})
    assert store.regex.layers.signature(cid) != after_campaign


def test_the_layers_signature_covers_the_connections_named(client):
    cid, _sid = _scene(client, posts=0)
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-test"})
    plain = store.regex.layers.signature(cid)
    named = store.regex.layers.signature(cid, ["openrouter"])
    assert named != plain
    store.regex.layers.write_level("connection", "openrouter", {"rules": [THINK]})
    assert store.regex.layers.signature(cid, ["openrouter"]) != named
    assert store.regex.layers.signature(cid) == plain   # not a connection it was asked about


def test_the_layers_signature_is_none_for_an_id_no_resolver_accepts(client):
    assert store.regex.layers.signature("../escape") is None
