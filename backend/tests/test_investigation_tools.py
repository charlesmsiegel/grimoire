"""Roadmap 12-S1: the narrator-side toolset (spec 4, 14 "Toolset").

Every store is synthetic and placeholder-named; nothing here calls a model.
"""

import asyncio
import contextlib
import json

import pytest

from grimoire import tool_calls
from grimoire.store import campaigns, entities, facts, locks, plot, regex, scenes
from grimoire.store.history import retrieval
from grimoire.store.investigation import readers, state, tools
from tests.history_fixtures import absorbed, actor, played
from tests.history_fixtures import cid as cid


def _ctx(turn=1):
    return tool_calls.ToolContext(campaign="", scene_identity="", run_id="run1",
                                  deadline=float("inf"), root="", turn=turn)


def _call(run, name, **args):
    spec = tools.toolset(run).get(name)
    assert spec is not None, f"{name} is not offered in {run.mode}"
    out = spec.fn(args, _ctx())
    if asyncio.iscoroutine(out):
        out = asyncio.run(out)
    return out, json.loads(out.text)


@pytest.fixture
def run(cid):
    return state.RunState(cid=cid, mode="continuity")


# ---- modes ----
@pytest.mark.parametrize("mode,present,absent", [
    ("rp_narrator", {"search_history", "get_scene_excerpt", "finish_evidence"},
     {"read_scene_window", "get_pressure", "propose", "answer"}),
    ("continuity", {"read_scene_window", "get_pressure", "propose", "finish_no_change"},
     {"answer", "finish_evidence"}),
    ("question", {"read_scene_window", "answer"},
     {"get_pressure", "propose", "finish_no_change", "finish_evidence"}),
])
def test_each_tool_is_offered_only_in_its_modes(cid, mode, present, absent):
    offered = {t.name for t in tools.toolset(state.RunState(cid=cid, mode=mode)).tools}
    assert present <= offered
    assert not absent & offered
    assert "finish_insufficient_evidence" in offered


def test_terminal_tools_are_terminal_and_never_run(run):
    ts = tools.toolset(run)
    for name in tools.TERMINAL["continuity"]:
        spec = ts.get(name)
        assert spec.terminal
        with pytest.raises(tool_calls.ToolError):
            spec.fn({}, _ctx())
    assert ts.get("propose").effect == "propose"


def test_an_unknown_mode_is_refused(cid):
    with pytest.raises(ValueError):
        state.RunState(cid=cid, mode="rp_actor")


# ---- refs ----
@pytest.mark.parametrize("tool,arg,ref", [
    ("get_scene_summary", "scene", "scene:../../etc"),
    ("get_scene_summary", "scene", "scene:9999--nowhere"),
    ("get_scene_excerpt", "scene", "scene:a/b"),
    ("read_scene_window", "scene", "0001--bare"),
    ("get_record", "ref", "thread:../x"),
    ("get_record", "ref", "thread:missing"),
    ("get_record", "ref", "characters:mara"),
    ("get_record", "ref", "path:/etc/passwd"),
    ("get_record_history", "ref", "pair:characters:a"),
    ("get_related", "ref", "commitment:missing"),
    ("get_actor", "ref", "characters:nobody"),
    ("get_actor", "ref", "lore:x"),
    ("get_group", "ref", "groups:nobody"),
])
def test_unknown_and_path_shaped_refs_are_not_found(run, tool, arg, ref):
    args = {arg: ref}
    if tool == "get_scene_excerpt":
        args["query"] = "ledger"
    out, env = _call(run, tool, **args)
    assert env == {"ok": False, "error": "not_found"}
    assert run.steps[-1]["result"] == {"ok": False, "refs": [], "chars": len(out.text),
                                       "truncated": False, "error": "not_found"}


def test_a_pair_naming_an_actor_no_roster_holds_is_not_found(run):
    sid = played(run.cid)
    aid, _ = actor(run.cid, sid, "Mara")
    _, env = _call(run, "get_record_history",
                   ref=f"pair:characters:{aid}+characters:nobody")
    assert env == {"ok": False, "error": "not_found"}
    bid, _ = actor(run.cid, sid, "Seraphine")
    _, env = _call(run, "get_record_history", ref=f"pair:characters:{aid}+characters:{bid}")
    assert env["ok"] is True


def test_a_read_the_envelope_could_not_carry_is_not_a_read(run, monkeypatch):
    """`envelope_ok` drops an item whole only when nothing of it fits; the
    scene it was is then no scene the model saw, and is not inspected."""
    sid = played(run.cid)
    absorbed(run.cid, sid, "Mara gives Seraphine the silver ledger.")
    monkeypatch.setattr(tools, "envelope_ok",
                        lambda items: json.dumps({"ok": True, "items": []}))
    out, env = _call(run, "get_scene_summary", scene=f"scene:{sid}")
    assert env == {"ok": True, "items": []}
    assert sid not in run.inspected and out.refs == ()
    assert tools.item("e1", "scene:x", "t" * 5000, "")["title"] == "t" * tools.TITLE_CHARS


def test_out_of_range_arguments_are_bad_args(run):
    sid = played(run.cid)
    assert _call(run, "read_scene_window", scene=f"scene:{sid}", limit=40)[1] == \
        {"ok": False, "error": "bad_args"}
    assert _call(run, "get_scene_excerpt", scene=f"scene:{sid}", query="x" * 300)[1] == \
        {"ok": False, "error": "bad_args"}
    assert _call(run, "search_history", query="ledger", max=9)[1] == \
        {"ok": False, "error": "bad_args"}


def test_a_raising_reader_is_unavailable_and_the_run_goes_on(run, monkeypatch):
    def broken(*a, **k):
        raise OSError("disk")
    monkeypatch.setattr(readers, "pressure_items", broken)
    _out, env = _call(run, "get_pressure")
    assert env == {"ok": False, "error": "unavailable"}


# ---- transcript text ----
def test_a_hidden_post_and_a_director_note_never_reach_a_window(run):
    sid = played(run.cid, text="Mara opens the door.")
    scenes.append_message(run.cid, sid, "assistant", "The secret vault lies below.")
    scenes.append_message(run.cid, sid, "assistant", "Seraphine nods.")
    scenes.set_excluded(run.cid, sid, 1, True)
    _out, env = _call(run, "read_scene_window", scene=f"scene:{sid}", before=None, limit=12)
    text = env["items"][0]["text"]
    assert "Seraphine nods." in text and "Mara opens the door." in text
    assert "secret vault" not in text
    assert sid in run.inspected


def test_a_hidden_post_never_anchors_or_appears_in_an_excerpt(run):
    sid = played(run.cid, text="Mara mentions the harbour.")
    scenes.append_message(run.cid, sid, "assistant", "The secret vault lies below.")
    scenes.set_excluded(run.cid, sid, 1, True)
    _, env = _call(run, "get_scene_excerpt", scene=f"scene:{sid}", query="vault",
                   max_posts=6)
    assert env == {"ok": True, "items": []}
    assert sid not in run.inspected


def test_a_depth_ranged_rule_applies_at_the_transcripts_depth_in_a_window(run):
    sid = played(run.cid, text="Hello zero")
    scenes.append_message(run.cid, sid, "assistant", "Hello one")
    scenes.append_message(run.cid, sid, "user", "Hello two")
    regex.layers.write_campaign(run.cid, {"rules": [
        {"name": "Newest only", "pattern": "Hello", "replacement": "Howdy",
         "targets": ["model", "user"], "applies": ["prompt"], "max_depth": 0}]})
    # A window that stops before the newest post: none of its posts is at depth 0.
    _, env = _call(run, "read_scene_window", scene=f"scene:{sid}", before=2, limit=2)
    text = env["items"][0]["text"]
    assert "Hello zero" in text and "Hello one" in text and "Howdy" not in text
    _, env = _call(run, "read_scene_window", scene=f"scene:{sid}", before=None, limit=1)
    assert "Howdy two" in env["items"][0]["text"]


def test_an_excerpt_reads_the_prompt_view_and_marks_the_scene_read(run):
    sid = played(run.cid, text="Mara pays the ferry toll.")
    regex.layers.write_campaign(run.cid, {"rules": [
        {"name": "Swap", "pattern": "ferry", "replacement": "barge",
         "targets": ["model", "user"], "applies": ["prompt"]}]})
    _, env = _call(run, "get_scene_excerpt", scene=f"scene:{sid}", query="barge toll",
                   max_posts=4)
    (found,) = env["items"]
    assert "barge toll" in found["text"] and "ferry" not in found["text"]
    assert found["id"] in run.evidence and sid in run.inspected


def test_truncation_keeps_whole_posts_and_says_so(run):
    sid = played(run.cid, text="opening")
    for n in range(11):
        scenes.append_message(run.cid, sid, "assistant", f"Post {n}. " + "word " * 180)
    _, env = _call(run, "read_scene_window", scene=f"scene:{sid}", before=None, limit=12)
    (found,) = env["items"]
    assert found["truncated"] is True
    lines = found["text"].splitlines()
    assert lines[0].startswith("posts ") and "before=" in lines[0]
    # Every kept post is whole: it ends where the post ended.
    assert all(line.rstrip().endswith("word") for line in lines[1:])
    assert len(json.dumps(env, ensure_ascii=False)) <= tools.TOOL_RESULT_CHARS


# ---- records ----
def test_a_gm_only_entity_gives_its_name_and_never_its_body(run):
    root = campaigns.campaign_root(run.cid)
    eid = entities.create_entity(root, "lore", "Realm Oath", secrecy="gm-only",
                                 body="The oath is a lie.")
    _, env = _call(run, "get_record", ref=f"lore:{eid}")
    (found,) = env["items"]
    assert found["title"] == "Realm Oath"
    assert "lie" not in found["text"]
    public = entities.create_entity(root, "lore", "Saltmarch Tide", body="Tides turn.")
    assert "Tides turn." in _call(run, "get_record", ref=f"lore:{public}")[1]["items"][0]["text"]


def test_records_histories_and_relations_read_the_ledger(run):
    sid = played(run.cid)
    plot.set_movement(run.cid, "ledger", "silver ledger", "open", "Mara hides it.", sid)
    plot.set_movement(run.cid, "ledger", "silver ledger", "open", "Mara returns it.", sid)
    old = facts.record(run.cid, "The ledger is silver.", "", sid)
    facts.record(run.cid, "The ledger is gilded.", "", sid, supersedes=old)
    record = _call(run, "get_record", ref="thread:ledger")[1]["items"][0]["text"]
    assert "silver ledger" in record and "Mara returns it." in record
    beats = _call(run, "get_record_history", ref="thread:ledger")[1]["items"][0]["text"]
    assert beats.index("Mara hides it.") < beats.index("Mara returns it.")
    chain = _call(run, "get_record_history", ref=f"fact:{old}")[1]["items"][0]["text"]
    assert chain.index("silver") < chain.index("gilded")
    related = _call(run, "get_related", ref="thread:ledger")[1]
    assert related["ok"] is True


# ---- the scene-free readers ----
class _Watched:
    """A campaign lock whose full hold (`with campaign_lock(...)`) fails the
    test; the best-effort reader's bare `acquire` is let through."""

    def __init__(self, lock):
        self._lock = lock

    def __enter__(self):
        raise AssertionError("a tool took campaign_lock")

    def __exit__(self, *exc):
        return False

    def acquire(self, *a, **k):
        return self._lock.acquire(*a, **k)

    def release(self):
        self._lock.release()


@contextlib.contextmanager
def _no_full_lock(monkeypatch):
    real = locks.campaign_lock
    monkeypatch.setattr(locks, "campaign_lock", lambda cid, *a, **k: _Watched(real(cid)))
    yield


def test_actor_and_timeline_need_no_scene_and_never_take_the_full_lock(run, monkeypatch):
    first = played(run.cid, text="Mara arrives.")
    absorbed(run.cid, first, "Mara arrives in Saltmarch.")
    aid, _ = actor(run.cid, first, "Mara")
    second = played(run.cid, text="Mara leaves.")
    absorbed(run.cid, second, "Mara leaves Saltmarch.")
    with _no_full_lock(monkeypatch):
        _, env = _call(run, "get_actor", ref=f"characters:{aid}")
        assert env["items"][0]["title"] == "Mara"
        _, env = _call(run, "get_timeline", **{"from": None, "to": None})
        text = env["items"][0]["text"]
        assert text.index("arrives") < text.index("leaves")
        _, env = _call(run, "get_timeline", **{"from": f"scene:{second}", "to": None})
        assert "arrives" not in env["items"][0]["text"]


# ---- search ----
def test_search_is_pinned_to_this_campaign_and_the_narrator(run, monkeypatch):
    seen = {}
    real = retrieval.retrieve

    async def spy(cid, sid, query, **kwargs):
        seen.update(query=query, sid=sid, **kwargs)
        return await real(cid, sid, query, **kwargs)
    monkeypatch.setattr(retrieval, "retrieve", spy)
    old = played(run.cid, text="Mara buries the brass compass.")
    absorbed(run.cid, old, "Mara buries the brass compass.")
    _, env = _call(run, "search_history", query="brass compass", max=3)
    assert seen["query"].tier_limit == 2 and seen["query"].perspective == "narrator"
    assert seen["sid"] == "" and seen["depth"] == 3
    assert [i["ref"] for i in env["items"]] == [f"scene:{old}"]
    held = run.evidence[env["items"][0]["id"]]
    assert held.scene.sid == old
    # Appearing in a search list is not having read the scene.
    assert old not in run.inspected


def test_search_builds_its_query_under_the_best_effort_lock_and_retrieves_outside_it(
        run, monkeypatch):
    """The query is built from the store under `best_effort_campaign_lock`, as
    every reading tool's reads are; 09's `retrieve` gathers outside any hold,
    as a turn's recall does (its contract), so no hold spans its awaits."""
    held: list[bool] = []
    real_hold, real_retrieve = locks.best_effort_campaign_lock, retrieval.retrieve
    from grimoire.store.history import query as history_query
    real_for_text = history_query.for_text

    @contextlib.contextmanager
    def hold(cid, *a, **k):
        held.append(True)
        with real_hold(cid, *a, **k) as got:
            yield got
        held.pop()

    def for_text(*a, **k):
        assert held, "the query was built outside the lock"
        return real_for_text(*a, **k)

    async def retrieve(*a, **k):
        assert not held, "retrieve ran inside a campaign hold"
        return await real_retrieve(*a, **k)
    monkeypatch.setattr(locks, "best_effort_campaign_lock", hold)
    monkeypatch.setattr(history_query, "for_text", for_text)
    monkeypatch.setattr(retrieval, "retrieve", retrieve)
    sid = played(run.cid, text="Mara buries the brass compass.")
    absorbed(run.cid, sid, "Mara buries the brass compass.")
    with _no_full_lock(monkeypatch):
        _, env = _call(run, "search_history", query="brass compass", max=3)
    assert [i["ref"] for i in env["items"]] == [f"scene:{sid}"]


class _Room:
    """The loop's view of a run, as far as the search asks of it."""

    def __init__(self, room):
        self.room = room

    def room_usd(self):
        return self.room


@pytest.mark.parametrize("room,semantic", [(None, True), (0.5, False)])
def test_search_under_a_spend_ceiling_sends_no_embedding(run, monkeypatch, room, semantic):
    """An embedding is metered apart from the loop, so the ceiling cannot
    see it: under one the search is lexical and structural only."""
    seen = {}
    real = retrieval.retrieve

    async def spy(cid, sid, query, **kwargs):
        seen.update(kwargs)
        return await real(cid, sid, query, **{**kwargs, "embed_client": None})
    monkeypatch.setattr(retrieval, "retrieve", spy)
    run.embed_client = client = object()
    played(run.cid, text="Mara waits.")
    spec = tools.toolset(run).get("search_history")
    ctx = tool_calls.ToolContext(campaign="", scene_identity="", run_id="run1",
                                 deadline=float("inf"), root="", turn=1, run=_Room(room))
    asyncio.run(spec.fn({"query": "ledger", "max": 2}, ctx))
    assert (seen["embed_client"] is client) is semantic


def test_search_works_with_turn_recall_off(run):
    from grimoire.store import config
    config.write_config(history_recall_depth="0")
    old = played(run.cid, text="Winifred finds the amber seal.")
    absorbed(run.cid, old, "Winifred finds the amber seal.")
    _, env = _call(run, "search_history", query="amber seal", max=None)
    assert [i["ref"] for i in env["items"]] == [f"scene:{old}"]


def test_a_search_over_nothing_is_an_empty_success(run):
    played(run.cid, text="Mara waits.")
    assert _call(run, "search_history", query="zeppelin", max=2)[1] == \
        {"ok": True, "items": []}


def test_a_summary_counts_as_read(run):
    sid = played(run.cid)
    absorbed(run.cid, sid, "Mara gives Seraphine the silver ledger.")
    _, env = _call(run, "get_scene_summary", scene=f"scene:{sid}")
    assert "silver ledger" in env["items"][0]["text"]
    assert sid in run.inspected


# ---- the envelope ----
def test_the_envelope_never_passes_the_result_cap():
    big = [tools.item(f"e{n}", f"scene:{n}", "t", ("line\n" * 900)) for n in range(4)]
    text = tools.envelope_ok(big)
    assert len(text) <= tools.TOOL_RESULT_CHARS
    assert any(i["truncated"] for i in json.loads(text)["items"])
    with pytest.raises(ValueError):
        tools.envelope_error("withheld")


def test_a_closed_branch_scene_is_not_found(run, monkeypatch):
    sid = played(run.cid, text="Mara pays the debt.")
    real = readers.scenes_read.list_scenes

    def closed(cid):
        return [{**r, "closed_by": "002--kept"} if r["id"] == sid else r for r in real(cid)]
    monkeypatch.setattr(readers.scenes_read, "list_scenes", closed)
    assert _call(run, "read_scene_window", scene=f"scene:{sid}", before=None,
                 limit=4)[1] == {"ok": False, "error": "not_found"}
    _, env = _call(run, "get_timeline", **{"from": None, "to": None})
    assert sid not in env["items"][0]["text"]


def test_one_post_longer_than_the_result_is_cut_not_skipped(run):
    sid = played(run.cid, text="opening")
    scenes.append_message(run.cid, sid, "assistant", "Mara speaks. " + "word " * 2000)
    _, env = _call(run, "read_scene_window", scene=f"scene:{sid}", before=None, limit=1)
    (found,) = env["items"]
    assert found["truncated"] is True and "[post cut short]" in found["text"]
    assert "Mara speaks." in found["text"]
    assert sid in run.inspected
