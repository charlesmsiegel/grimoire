"""Roadmap 02-S1: the plumbing every play decision stands on -- the three
switches and their campaign override, the one deadline per decision, the
response id minted ahead of `prepare`, and the intent block's place in a
composed turn (after the history, nothing at all when there is no plan)."""

import asyncio

import pytest

from grimoire import inference, store
from grimoire.llm_errors import LLMError
from grimoire.routes import common
from grimoire.store import (
    appearances,
    campaigns,
    characters,
    context,
    play_decisions,
    responses,
    scenes,
    turn_plan,
    worlds,
)


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Saltmarch", wid)


def _set_campaign_meta(cid, **fields):
    mp = store.campaigns.campaign_meta_path(cid)
    meta, body = store.parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta.update(fields)
    mp.write_text(store.dump_frontmatter(meta, body), encoding="utf-8")


# --- the switches (§3.6) ------------------------------------------------------


def test_the_speaker_draw_is_on_by_default_and_the_rest_off(monkeypatch, tmp_path):
    """The user's ratification (02-S6): the pick is sampled by default -- it
    adds no call -- and the two switches that each add a call stay off."""
    cid = _campaign(monkeypatch, tmp_path)
    assert play_decisions.speaker_sampling(cid) is True
    assert play_decisions.turn_plan_mode(cid) == "off"
    assert play_decisions.play_tool(cid) is False
    cfg = store.read_config()
    assert (cfg["decide_speaker_sampling"], cfg["decide_turn_plan"],
            cfg["decide_play_tool"]) == ("on", "off", "off")


def test_a_global_value_no_switch_knows_reads_as_off(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    store.config.write_config(decide_speaker_sampling="maybe")
    assert play_decisions.speaker_sampling(cid) is False


def test_the_global_switch_reaches_a_campaign_with_no_opinion(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    store.write_config(decide_speaker_sampling="on", decide_turn_plan="plan",
                       decide_play_tool="on")
    assert play_decisions.speaker_sampling(cid) is True
    assert play_decisions.turn_plan_mode(cid) == "plan"
    assert play_decisions.play_tool(cid) is True


def test_the_campaign_wins_either_way_and_a_mistake_defers(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    store.write_config(decide_turn_plan="intent", decide_speaker_sampling="on")
    _set_campaign_meta(cid, decide_turn_plan="off", decide_speaker_sampling="off")
    assert play_decisions.turn_plan_mode(cid) == "off"
    assert play_decisions.speaker_sampling(cid) is False
    _set_campaign_meta(cid, decide_turn_plan="plan")
    assert play_decisions.turn_plan_mode(cid) == "plan"
    # A value the switch does not know is no choice: the global decides.
    _set_campaign_meta(cid, decide_turn_plan="maybe", decide_speaker_sampling="yes")
    assert play_decisions.campaign_setting(
        cid, play_decisions.TURN_PLAN_KEY, play_decisions.TURN_PLAN_MODES) == ""
    assert play_decisions.turn_plan_mode(cid) == "intent"
    assert play_decisions.speaker_sampling(cid) is True


def test_a_hand_mangled_global_reads_as_off(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    store.write_config(decide_turn_plan="both", decide_play_tool="maybe")
    assert play_decisions.turn_plan_mode(cid) == "off"
    assert play_decisions.play_tool(cid) is False


def test_put_config_stores_the_switches_and_refuses_a_mistake(client):
    ok = client.put("/api/config", json={"decide_turn_plan": "intent",
                                         "decide_speaker_sampling": "on",
                                         "decide_play_tool": "off"})
    assert ok.status_code == 200, ok.text
    cfg = store.read_config()
    assert (cfg["decide_turn_plan"], cfg["decide_speaker_sampling"]) == ("intent", "on")
    for body in ({"decide_turn_plan": "both"}, {"decide_speaker_sampling": "maybe"},
                 {"decide_play_tool": "yes"}):
        refused = client.put("/api/config", json=body)
        assert refused.status_code == 400, body
    assert store.read_config()["decide_turn_plan"] == "intent"


# --- the deadline (§3.4) --------------------------------------------------------


def test_a_call_inside_the_deadline_runs(monkeypatch):
    monkeypatch.setattr(common, "_plan_clock", lambda: 100.0)

    async def call():
        return "answered"

    async def go():
        bound = common.plan_deadline()
        return await bound(call(), {})

    assert asyncio.run(go()) == "answered"


def test_a_call_after_the_deadline_is_refused_unsent(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(common, "_plan_clock", lambda: now[0])
    started = []

    async def call():
        started.append(True)
        return "answered"

    async def go():
        bound = common.plan_deadline()
        first = await bound(call(), {})
        now[0] += common.PLAN_CEILING_S + 1
        pending = call()
        with pytest.raises(common.DeadlineRefused) as refused:
            await bound(pending, {})
        return first, refused.value, pending

    first, refused, pending = asyncio.run(go())
    assert first == "answered" and started == [True]
    # Never awaited, and closed rather than left to warn.
    assert pending.cr_frame is None
    # Unsent: the decide chain ends here rather than trying a later stage, and
    # nothing reaches the error store.
    assert inference._refused_unsent(refused)
    assert getattr(refused, store.usage.NOT_A_FAILURE) is False


def test_an_overrun_is_the_calls_own_timeout(monkeypatch):
    monkeypatch.setattr(common, "PLAN_CEILING_S", 0.05)

    async def slow():
        await asyncio.sleep(5)

    async def go():
        bound = common.plan_deadline()
        with pytest.raises(LLMError) as overrun:
            await bound(slow(), {})
        return overrun.value

    error = asyncio.run(go())
    assert error.kind == "timeout" and not isinstance(error, common.DeadlineRefused)


def test_the_ceiling_never_exceeds_the_users_call_budget(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    store.write_config(llm_call_budget="5")
    assert common.plan_ceiling() == 5
    store.write_config(llm_call_budget="0")   # "no ceiling" there lifts nothing here
    assert common.plan_ceiling() == common.PLAN_CEILING_S


# --- the response id (§3.5) -------------------------------------------------------


def test_prepare_takes_a_minted_id_and_an_intent(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Scene")
    rnd = responses.new_round(cid, sid, eligible=[], automatic=False, post=1, run_id="r")
    rid = responses.mint_id()
    intent = {"task": "turn-intent", "skipped": "", "stance": "deflect"}
    record = responses.prepare(cid, sid, rnd["id"], "characters:mara", "Mara",
                               {"messages": []}, response_id=rid, intent=intent)
    assert record["id"] == rid
    assert responses.get(cid, sid, rid)["intent"] == intent
    plain = responses.prepare(cid, sid, rnd["id"], "characters:mara", "Mara", {"messages": []})
    assert plain["id"] != rid and "intent" not in responses.get(cid, sid, plain["id"])
    with pytest.raises(ValueError):
        responses.prepare(cid, sid, rnd["id"], "characters:mara", "Mara", {"messages": []},
                          response_id=rid)


# --- the block in a composed turn (§6.5) -----------------------------------------


@pytest.fixture
def cast_scene(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    wid = worlds.create_world("Realm")
    root = worlds.world_root(wid)
    for name in ("Mara", "Winifred"):
        characters.create_character(root, name, "main", characters.blank_card(name))
    cid = campaigns.create_campaign("Saltmarch", wid)
    sid = scenes.create_scene(cid, "Scene")
    for name in ("mara", "winifred"):
        appearances.appear(cid, sid, "characters", name, "main", "npc", narrate=False)
    scenes.append_message(cid, sid, "user", "Mara, where were you last night?")
    return cid, sid


MARA = "characters:mara"
PLAN = {"task": "turn-intent", "skipped": "", "stance": "deflect"}


def _wire(messages):
    return [dict(m) for m in messages]


def test_no_plan_is_byte_identical(cast_scene):
    cid, sid = cast_scene
    for compose in (lambda **kw: context.compose_turn(cid, sid, actor_ref=MARA, **kw),
                    lambda **kw: context.compose_director_turn(cid, sid, "Go on.",
                                                               actor_ref=MARA, **kw)):
        bare, bare_detail = compose()
        for plan in (None, {}, {**PLAN, "skipped": "missing_key", "stance": None},
                     {"task": "turn-intent", "skipped": "", "stance": None}):
            again, detail = compose(plan=plan)
            assert _wire(again) == _wire(bare)
            assert [r["id"] for r in detail["sections"]] == \
                [r["id"] for r in bare_detail["sections"]]


def test_the_block_follows_the_last_post(cast_scene):
    cid, sid = cast_scene
    bare, _ = context.compose_turn(cid, sid, actor_ref=MARA)
    messages, detail = context.compose_turn(cid, sid, actor_ref=MARA, plan=PLAN)
    text = turn_plan.block(PLAN, "Mara")
    assert "Mara's approach in this reply" in text
    assert turn_plan.stance_description("deflect") in text
    at = next(i for i, m in enumerate(messages) if m["content"] == text)
    assert messages[at]["role"] == "system"
    assert messages[at - 1]["content"].endswith("Mara, where were you last night?")
    # Everything else is where it was.
    assert _wire(messages[:at] + messages[at + 1:]) == _wire(bare)
    row = next(r for r in detail["sections"] if r["label"] == "Turn intent")
    assert row["text"] == text and row["tokens"] > 0


def test_a_director_turns_block_comes_before_its_note(cast_scene):
    cid, sid = cast_scene
    messages, _ = context.compose_director_turn(cid, sid, "Answer her.", actor_ref=MARA,
                                                plan=PLAN)
    text = turn_plan.block(PLAN, "Mara")
    at = next(i for i, m in enumerate(messages) if m["content"] == text)
    assert messages[at + 1] == {"role": "user", "content": "Answer her."}


def test_the_narrator_gets_no_block(cast_scene):
    cid, sid = cast_scene
    bare, _ = context.compose_turn(cid, sid, actor_ref="grimoire")
    planned, _ = context.compose_turn(cid, sid, actor_ref="grimoire", plan=PLAN)
    assert _wire(planned) == _wire(bare)


def test_a_layout_that_drops_every_section_keeps_the_block(cast_scene):
    cid, sid = cast_scene
    store.write_config(prompt_layout_enabled="on")
    context.layout.write_layout([{"id": s["id"], "enabled": False}
                                 for s in context.layout.describe(context.SECTIONS, [])])
    messages, _ = context.compose_turn(cid, sid, actor_ref=MARA, plan=PLAN)
    assert turn_plan.block(PLAN, "Mara") in [m["content"] for m in messages]


def test_a_value_the_vocabulary_does_not_know_sends_nothing():
    assert turn_plan.block({"stance": "monologue"}, "Mara") == ""
    assert turn_plan.block({"tension": True}, "Mara") == ""
    plan = {"stance": "press", "disclosure": "hint", "tension": 2}
    text = turn_plan.block(plan, "Mara")
    assert turn_plan.disclosure_description("hint") in text
    assert turn_plan.tension_description(2) in text



# --- exposure (02-S6) --------------------------------------------------------


def test_the_campaign_route_round_trips_inherits_and_refuses(client):
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    base = f"/api/campaigns/{cid}/play-decisions"
    body = client.get(base).json()
    assert body == {"speaker_sampling": "", "turn_plan": "", "play_tool": "",
                    "effective": {"speaker_sampling": "on", "turn_plan": "off",
                                  "play_tool": "off"}}
    body = client.put(base, json={"speaker_sampling": "off", "turn_plan": "plan"}).json()
    assert (body["speaker_sampling"], body["turn_plan"], body["play_tool"]) == ("off", "plan", "")
    assert body["effective"] == {"speaker_sampling": "off", "turn_plan": "plan",
                                 "play_tool": "off"}
    assert play_decisions.turn_plan_mode(cid) == "plan"
    # A field left out is left as it is; "" follows the global again.
    body = client.put(base, json={"turn_plan": ""}).json()
    assert (body["speaker_sampling"], body["turn_plan"]) == ("off", "")
    meta = campaigns.read_campaign(cid)["meta"]
    assert "decide_turn_plan" not in meta and meta["decide_speaker_sampling"] == "off"
    assert client.put(base, json={"turn_plan": "maybe"}).status_code == 400
    assert client.put(base, json={"play_tool": "plan"}).status_code == 400
    assert client.get("/api/campaigns/nowhere/play-decisions").status_code == 404
    assert client.put("/api/campaigns/nowhere/play-decisions",
                      json={"play_tool": "on"}).status_code == 404


def test_a_write_that_changes_nothing_leaves_the_file_alone(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    play_decisions.set_campaign(cid, {play_decisions.PLAY_TOOL_KEY: "on"})
    mp = store.campaigns.campaign_meta_path(cid)
    before = mp.read_bytes()
    play_decisions.set_campaign(cid, {play_decisions.PLAY_TOOL_KEY: "on"})
    assert mp.read_bytes() == before
    with pytest.raises(ValueError):
        play_decisions.set_campaign(cid, {"tracker": "on"})


def test_the_global_route_says_whether_the_pick_samples(client, monkeypatch):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    assert client.get("/api/play-decisions").json() == {"pick_samples": False}
    monkeypatch.setattr(inference, "reports_distribution", lambda _resolved: True)
    assert client.get("/api/play-decisions").json() == {"pick_samples": True}


@pytest.mark.parametrize("intent,line", [
    ({"stance": "deflect", "draw": {"basis": "sampled", "selected": "deflect",
                                    "distribution": [["engage", 0.4], ["deflect", 0.38]]}},
     "deflect (sampled, reported 38%)"),
    ({"stance": "press", "draw": {"basis": "answer", "selected": "press"}},
     "press (most likely)"),
    ({"stance": "deflect", "disclosure": "hint", "tension": 1}, "deflect · hint · hold steady"),
    # A plan whose stance abstained still sent its other answers (`block`).
    ({"stance": None, "disclosure": "hint", "tension": 1}, "hint · hold steady"),
    ({"task": "turn-intent", "skipped": "missing_key"}, ""),
    (None, ""),
])
def test_the_approach_line(intent, line):
    assert turn_plan.approach(intent) == line
