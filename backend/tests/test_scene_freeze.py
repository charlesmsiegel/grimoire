"""While a turn holds a scene, that scene's SHAPE does not change.

Detaching a turn is what makes this necessary. A turn composes its prompt from
the transcript as it stood and finalizes against the transcript as it is, and
it now spans minutes rather than one request -- so every route that renames,
edits, cuts or retcons a post is concurrent with generation. Left open, the
model answers a question that is no longer in the scene and the reply is
appended to history that moved underneath it.

Reserving before the first mutator (task 5) closes the races BEFORE a run.
These are the races DURING one.
"""

from __future__ import annotations

import pytest

import grimoire.store as store
from grimoire.routes import runs as runs_mod


@pytest.fixture
def held_scene(client):
    """A scene with a live `turn` run on it, and a post to aim edits at."""
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    store.scenes.append_message(cid, sid, "user", "Mara steps onto the dock.")
    identity = store.scenes.scene_identity(cid, sid)
    run, _ = client.app.state.runs.start_or_existing(
        ("scene", cid, identity), "turn", "chat", "a1", identity,
        {"campaign": "Saltmarch", "scene": "Mara"})
    return cid, sid, run


def test_every_shape_change_is_refused_while_a_run_holds_the_scene(held_scene, client):
    """One case per door, because the guard is applied per call site: a route
    that forgets it is open again and nothing else would say so.

    NOTE the methods -- these are not all POSTs. Getting one wrong makes the
    call 405 and the test pass while the guard is absent.
    """
    cid, sid, _ = held_scene
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    calls = [
        ("put",    base,                        {"title": "Winifred"}),
        ("delete", base,                        None),
        ("put",    f"{base}/messages/0",        {"content": "edited"}),
        ("delete", f"{base}/messages/0",        None),
        ("post",   f"{base}/messages/0/retcon", {"content": "retconned"}),
        ("post",   f"{base}/alternates/v-nope",  None),
        ("post",   f"{base}/replay",             {"index": 0}),
        ("post",   f"{base}/roll",               {"notation": "1d20"}),
        # The cast and moment routes. The UI disables these with `sceneLocked`,
        # which is an affordance and not a guarantee -- a second tab, another
        # device or a plain API call is not bound by it. They belong here
        # because `appear`/`leave`/`set_location` APPEND a transition line, and
        # the first `set_datetime` renames the scene outright.
        ("post",   f"{base}/cast",               {"kind": "characters", "id": "nobody",
                                                  "version": "default", "role": "npc"}),
        ("delete", f"{base}/cast/characters/x",  None),
        ("post",   f"{base}/cast/batch",         {"refs": []}),
        ("post",   f"{base}/cast/emergent",      {"name": "Winifred", "role": "npc"}),
        ("put",    f"{base}/location",           {"location": "saltmarch-docks"}),
        ("put",    f"{base}/datetime",           {"datetime": "1834-04-02"}),
        # The review save writes back into the scene and marks it absorbed.
        ("put",    f"{base}/chronicle",          {"one_line": "x", "summary": "y",
                                                  "keywords": [], "timeline_events": [],
                                                  "edits": [], "commit_token": "t"}),
        # `greetings.py` -- a different module, which is exactly why an
        # inventory built by reading `scenes.py` never reached it.
        ("post",   f"{base}/first-post",         {"text": "The lamps are lit."}),
        ("post",   f"{base}/start-from-greeting", {"greeting": "g1"}),
    ]
    for method, path, body in calls:
        r = getattr(client, method)(path, **({"json": body} if body else {}))
        assert r.status_code == 409, f"{method} {path} answered {r.status_code}"
        assert r.json().get("kind") == "scene_busy", f"{method} {path}: {r.json()}"


def test_the_refusal_names_the_run_so_the_client_can_offer_to_stop_it(held_scene, client):
    """`scene_busy` with no run id leaves the player told "no" with nothing to
    do about it. The id is what lets the client offer Stop."""
    cid, sid, run = held_scene
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}", json={"title": "Winifred"})
    assert r.json()["run_id"] == run.id


def test_shape_changes_are_allowed_again_once_the_run_is_terminal(held_scene, client):
    """The counterweight. A guard that never lifts is a scene nobody can ever
    rename, and a terminal run stays in the registry for the whole retention
    window -- so "has a run" is the wrong question and "has a LIVE run" is the
    right one."""
    cid, sid, run = held_scene
    run.finish("landed")

    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}", json={"title": "Winifred"})

    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Winifred"


def test_a_run_on_another_scene_does_not_freeze_this_one(held_scene, client):
    """The guard is per scene, not per campaign: a turn generating in one scene
    must not stop the player tidying another."""
    cid, _, _ = held_scene
    other = store.scenes.create_scene(cid, "Winifred")

    r = client.put(f"/api/campaigns/{cid}/scenes/{other}", json={"title": "Seraphine"})

    assert r.status_code == 200, r.text


def test_a_scene_with_no_identity_is_not_frozen_by_a_lookup_failure(held_scene, client,
                                                                    monkeypatch):
    """The guard resolves the scene's identity to find its runs. A scene that
    has none can hold no run, so it must not be refused -- a guard that fails
    closed on an unresolvable identity would make such a scene permanently
    uneditable."""
    cid, _, _ = held_scene
    other = store.scenes.create_scene(cid, "Winifred")
    monkeypatch.setattr(runs_mod.scenes, "scene_identity_strict", lambda *_a: None)

    r = client.put(f"/api/campaigns/{cid}/scenes/{other}", json={"title": "Seraphine"})

    assert r.status_code == 200, r.text


def _campaign_at_width_edge(cid: str, up_to: int = 999) -> None:
    """Scene files named so the next create crosses 999 -> 1000.

    Written directly rather than created through the store: `create_scene`
    parses and writes a whole transcript each time, and a thousand of them is
    minutes of test time to reach a boundary this only needs to *observe*.
    999 and not 99, because `scene_ids.MIN_WIDTH` is 3 -- scene 100 is written
    `100--...` with no repad at all, so a test built on 99 could never go red.
    """
    d = store.scenes.paths._scenes_dir(cid)
    d.mkdir(parents=True, exist_ok=True)
    for n in range(1, up_to + 1):
        (d / f"{n:03d}--filler.md").write_text(
            "---\ntitle: Filler\n---\n\n", encoding="utf-8")


def test_a_width_crossing_create_is_refused_while_any_scene_is_generating(held_scene,
                                                                          client):
    """`repad` renames EVERY scene in the campaign and repoints their sidecars,
    consulting no registry -- so a live turn loses the path it captured, and
    refusing the explicit rename route never covered this door at all."""
    cid, _, _ = held_scene
    _campaign_at_width_edge(cid)

    r = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Winifred"})

    assert r.status_code == 409, f"the repad was allowed: {r.status_code}"
    assert r.json()["kind"] == "scene_busy"


def test_an_ordinary_create_still_works_while_a_turn_generates(held_scene, client):
    """The counterweight, and the reason the guard is conditional. An ordinary
    create touches nothing but its own new file, so refusing every create
    during any turn would change how the app feels for a case that is not
    dangerous."""
    cid, _, _ = held_scene

    r = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Winifred"})

    assert r.status_code == 200, r.text


def test_a_replay_walk_cannot_be_stepped_while_a_turn_holds_the_scene(client):
    """Accept and cancel both move the transcript -- one keeps the replayed turn
    and releases the next original, the other puts every unreplayed post back --
    and neither goes through `POST /replay`, so guarding the cut alone left both
    open.

    The walk is started BEFORE the run, because starting it afterwards is the
    door the test above covers.
    """
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    store.scenes.append_message(cid, sid, "user", "Mara steps onto the dock.")
    store.scenes.append_message(cid, sid, "assistant", "The boards give underfoot.")
    store.scenes.append_message(cid, sid, "user", "She keeps walking.")
    store.replay.begin(cid, sid, 1)
    identity = store.scenes.scene_identity(cid, sid)
    client.app.state.runs.start_or_existing(
        ("scene", cid, identity), "turn", "chat", "a1", identity,
        {"campaign": "Saltmarch", "scene": "Mara"})

    base = f"/api/campaigns/{cid}/scenes/{sid}/replay"
    for path in (f"{base}/accept", f"{base}/cancel"):
        r = client.post(path)
        assert r.status_code == 409, f"{path} answered {r.status_code}"
        # NOT `replay_refused` or `no_replay`: the session is live and this
        # scene's, so a 409 alone would pass against an unguarded route.
        assert r.json()["kind"] == "scene_busy", f"{path}: {r.json()}"


def test_a_manual_check_is_refused_while_a_turn_holds_the_scene(client):
    """The 🎲 line is an append like any other, and the check route reaches it by
    a different path than the plain roll -- it resolves against the sheet first,
    so a guard placed only in `post_scene_roll` would leave this one open.

    Set up in full rather than aimed at a bogus check: an unresolvable one is a
    400 raised before the lock, which would pass with no guard present at all.
    """
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    client.put(f"/api/campaigns/{cid}/module", json={"module": "pool-basic"})
    chid = client.post(f"/api/worlds/{wid}/characters",
                       json={"name": "Mara"}).json()["character"]
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "Mara"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": chid, "version": "default",
                      "role": "npc"})
    store.sheets.write(cid, "characters", chid, "medium",
                       {"vigor": 3, "brawl": 2, "wits": 2, "occult": 1}, expected=None)
    body = {"check": "brawl", "actor": "characters:mara", "difficulty": 6}
    # It resolves when the scene is free -- otherwise the refusal below proves
    # nothing about the guard.
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/check",
                       json=body).status_code == 200

    identity = store.scenes.scene_identity(cid, sid)
    client.app.state.runs.start_or_existing(
        ("scene", cid, identity), "turn", "chat", "a1", identity,
        {"campaign": "Saltmarch", "scene": "Mara"})

    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/check", json=body)

    assert r.status_code == 409, r.text
    assert r.json()["kind"] == "scene_busy"


def test_the_storage_root_cannot_move_while_a_turn_is_generating(held_scene, client,
                                                                  tmp_path):
    """The hazard the frontend provider created: a run now survives navigation,
    so the player can leave the scene, open Configuration and move the library
    while their turn is still generating. Terminal persistence resolves its
    campaign and scene against `store.home()` minutes later -- so it either
    fails the identity fence and discards a finished reply, or writes into a
    copied library while the post that prompted it stays in the old one.

    Campaign-agnostic on purpose: the root is global, so a run anywhere is a run
    that would land in the wrong tree.
    """
    _, _, run = held_scene
    dest = tmp_path / "moved"

    r = client.put("/api/config/data-dir", json={"data_dir": str(dest)})

    assert r.status_code == 409, r.text
    assert r.json()["kind"] == "runs_in_flight"
    assert r.json()["run_id"] == run.id
    assert not dest.exists(), "the move went ahead anyway"


def test_the_storage_root_moves_once_nothing_is_running(held_scene, client, tmp_path):
    """The counterweight. A refusal that never lifts is a setting nobody can
    change, and a terminal run stays in the registry for its whole retention
    window -- so the question is "is anything LIVE", not "is anything here"."""
    _, _, run = held_scene
    run.finish("landed")
    dest = tmp_path / "moved"

    r = client.put("/api/config/data-dir", json={"data_dir": str(dest)})

    assert r.status_code == 200, r.text


def test_the_rollback_retires_the_marker_before_it_removes_the_post(monkeypatch,
                                                                     client):
    """Two files, so the campaign lock makes them one step for a concurrent
    reader and no step at all for a process that exits between them. One crash
    window is unavoidable; this pins which side of it the player lands on.

    Removing first and forgetting second leaves the post gone and the marker
    still saying `retained` -- recovery believes it, settles the attempt and
    discards the only copy of what the player typed. Forgetting first leaves a
    duplicate they can see and delete.
    """
    import grimoire.routes.scenes as scenes_mod

    order: list[str] = []
    real_forget = store.attempts.forget

    def spy_forget(*a, **kw):
        order.append("forget")
        return real_forget(*a, **kw)

    monkeypatch.setattr(scenes_mod.store.attempts, "forget", spy_forget)
    monkeypatch.setattr(scenes_mod.store.scenes, "remove_trailing_user_post",
                        lambda *a, **kw: order.append("remove") or True)

    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    identity = store.scenes.scene_identity(cid, sid)
    run = client.app.state.runs.start_or_existing(
        ("scene", cid, identity), "turn", "chat", "a1", identity, {})[0]

    scenes_mod._take_the_post_back(cid, sid, None, "Mara waits.", run)

    assert order == ["forget", "remove"], order


def test_a_send_cannot_reserve_while_the_storage_root_is_moving(client, tmp_path):
    """The other half of the refusal above, and the one a bare check left open.

    `any_live()` and then `set_data_dir` are two steps with the registry lock
    released in between, so a send reserving in that gap makes the move proceed
    with a run now live -- its setup writing into the old tree while its
    terminal write resolves the campaign against the new one. Held rather than
    checked, the reservation is what has to lose.

    Driven from inside the move: `set_data_dir` is patched to attempt the
    reservation at the one instant the flag is up, which is the interleaving
    itself rather than an imitation of it.
    """
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    identity = store.scenes.scene_identity(cid, sid)
    registry = client.app.state.runs
    attempted: list[object] = []

    real_set = store.set_data_dir

    def racing_set(path):
        try:
            registry.start_or_existing(("scene", cid, identity), "turn", "chat",
                                       "a-race", identity, {})
            attempted.append("reserved")
        except runs_mod.StoreMovingError:
            attempted.append("refused")
        return real_set(path)

    dest = tmp_path / "moved"
    import grimoire.routes.config as config_mod
    config_mod.store.set_data_dir = racing_set
    try:
        r = client.put("/api/config/data-dir", json={"data_dir": str(dest)})
    finally:
        config_mod.store.set_data_dir = real_set

    assert r.status_code == 200, r.text
    assert attempted == ["refused"], attempted
    assert registry.any_live() is None, "a run was reserved into a moving store"


def test_the_refusal_lifts_once_the_move_is_done(client, tmp_path):
    """A flag left up by a move that raised would make every later send fail
    with a transient-looking error until the app is restarted."""
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    identity = store.scenes.scene_identity(cid, sid)

    # A move that fails: the destination is a file, not a directory.
    bad = tmp_path / "not-a-dir"
    bad.write_text("x", encoding="utf-8")
    client.put("/api/config/data-dir", json={"data_dir": str(bad)})

    run, fresh = client.app.state.runs.start_or_existing(
        ("scene", cid, identity), "turn", "chat", "a1", identity, {})

    assert fresh and run.state == "running"
