"""Spec 11 §5, the Decision stage (roadmap 11-S6), with `llm_fakes`.

Each NPC compose step's withheld history may be put to a Decision model, but
only the parts the record cannot settle and the actor had a structural way to
learn, capped per step and per round, under one deadline, never on a replay,
and fail-closed: every failure leaves the part withheld and the turn playing.
The corpus's own recipes (`evals/epistemic/cases.py`) plant the stores.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest
from fastapi import HTTPException

from evals.epistemic import cases, runner, synth
from grimoire import content_parts, routes, store
from grimoire.llm_errors import LLMError
from grimoire.routes import character_turns, common, history_recall
from grimoire.routes import epistemic as stage
from grimoire.store import epistemic_access, routing
from grimoire.store.context import assemble, epistemic
from grimoire.store.context import epistemic_items as items_mod
from tests.llm_fakes import FakeLLM, decision_reply

#: What a deadline spent before the first call reads as (set below, from
#: what the code does, so the test pins it exactly).
DEADLINE_BASIS = "decision_failed"

CASE = {c.id: c for c in cases.CASES}


@pytest.fixture
def home(client):
    """A store whose Decision role resolves (an OpenRouter key), with the
    switch on."""
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    store.write_config(epistemic_decide="on")
    return client


def plant(case_id: str):
    """The case planted and everyone seated; (cast, items, checks)."""
    cast = runner._cast()
    items, checks = CASE[case_id].build(cast)
    runner._seat(cast)
    return cast, items, checks


def view_for(cast, actor, items):
    return synth.for_compose(cast.now.cid, cast.now.sid, actor, items, ceiling=6000)


def refine(cast, view, fake, *, asked=0):
    return asyncio.run(stage.refine(view, cid=cast.now.cid, sid=cast.now.sid, client=fake,
                                    post=None, round_id="", asked=asked))


def answering(*answers):
    return FakeLLM([[decision_reply(*({epistemic_access.ACCESS_ID: a} for a in answers))]])


def shown(view) -> str:
    return "\n".join(s.text for c in view.visible() for s in c.slices)


def compose_text(cast, actor, view) -> str:
    messages, _ = assemble.compose_turn(cast.now.cid, cast.now.sid, actor_ref=actor,
                                        history_view=view)
    return "\n".join(content_parts.text_of(m["content"]) for m in messages)


def rows(cid):
    return [r for r in store.usage.calls(campaign=cid) if r.get("task") == stage.TASK]


# ---------------------------------------------------------------- the call


def test_a_known_answer_reaches_a_meter_and_the_actors_prompt(home):
    cast, items, _ = plant("7")
    view = view_for(cast, cast.mara, items)
    assert "TOLDNEEDLE7" not in compose_text(cast, cast.mara, view)
    fake = answering("known")
    out = refine(cast, view, fake)
    # One call, metered under the task (the `escalation=` regression: a
    # refine that raised before its meter would file nothing).
    assert fake.calls == 1 and len(rows(cast.now.cid)) == 1
    assert out.sent == 1 and out.response_id
    assert "TOLDNEEDLE7" in shown(out.view) and out.view.decided == 1
    [part] = out.record["parts"]
    assert (part["access"], part["basis"], part["channel"]) == ("known", "decision", "told")
    text = compose_text(cast, cast.mara, out.view)
    assert "TOLDNEEDLE7" in text
    # No basis code reaches the prompt, nor the item the model was asked.
    sent = json.dumps(fake.requests[0]["messages"])
    for code in [b for b in epistemic.BASES if "_" in b]:
        assert code not in text, code
        assert f'"{code}"' not in sent, code
    # The channel opened the item: the later post Mara witnessed is in it.
    assert "TAVERNNEEDLE7" in sent and "TOLDNEEDLE7" in sent


@pytest.mark.parametrize(("answer", "access", "basis"), [
    ("known", "known", "decision"),
    ("experienced", "known", "decision"),   # never `witnessed`
    ("suspected", "suspected", "decision"),
    ("narrator_only", "unknown", "decision"),
    (None, "unknown", "decision_abstained"),
    ("bogus", "unknown", "decision_abstained"),
])
def test_each_answer_reads_as_the_spec_says(home, answer, access, basis):
    cast, items, _ = plant("7")
    out = refine(cast, view_for(cast, cast.mara, items), answering(answer))
    [part] = out.record["parts"]
    assert (part["access"], part["basis"]) == (access, basis)
    assert ("TOLDNEEDLE7" in shown(out.view)) == (access != "unknown")
    if access == "suspected":
        assert "TOLDNEEDLE7" in compose_text(cast, cast.mara, out.view).split(
            "Suspected, unconfirmed:")[1]


def test_a_model_never_produces_witnessed_or_narrator_only():
    for answer, (access, _basis) in stage._READING.items():
        assert access in ("known", "suspected", "unknown"), answer
    with pytest.raises(ValueError):
        epistemic._answered("k", epistemic._Classes({0: ("unknown", "no_record")},
                                                    ("unknown", "summary_needs_grant")),
                            [epistemic.Slice("posts", (0,), "unknown", ("no_record",), "")],
                            {("k", "posts", (0,)): ("witnessed", "decision")})


# ---------------------------------------------------------------- the gate


@pytest.mark.parametrize("case_id", ["2", "4", "5", "6", "13"])
def test_no_call_when_nothing_is_eligible(home, case_id):
    cast, items, _ = plant(case_id)
    fake = answering("known")
    out = refine(cast, view_for(cast, cast.mara, items), fake)
    assert fake.calls == 0 and out.sent == 0
    assert out.record == {"task": stage.TASK, "asked": 0, "skipped": "", "parts": []}


def test_the_narrator_is_never_asked(home):
    cast, items, _ = plant("7")
    fake = answering("known")
    view = view_for(cast, None, items)
    out = refine(cast, view, fake)
    assert out.view is view and out.record is None and fake.calls == 0


def test_a_part_with_no_channel_is_never_asked(home):
    # Case 7 without the tavern: nobody later could have told Mara.
    cast, items, _ = plant("7")
    fake = answering("known")
    out = refine(cast, view_for(cast, cast.mara, items[:1]), fake)
    assert fake.calls == 0 and out.record["parts"] == []


def test_a_legacy_later_scene_opens_no_told_channel(home, monkeypatch):
    cast, items, _ = plant("7")
    monkeypatch.setattr(items_mod, "_number",
                        lambda sid: None if "tavern" in sid else 1)
    assert items_mod.eligible_for(view_for(cast, cast.mara, items)) == ()


def test_a_summary_after_a_withheld_post_is_never_asked(home):
    # Plan gate finding 1: a whole-scene grant and a post the user marked
    # unaware. The summary may paraphrase that post, so a model may not
    # reopen it -- whatever it answers.
    cast = runner._cast()
    st = cases._src(cast, "The Chapel")
    cases._here(st, cast, "Mara", "Winifred")
    st.say("Winifred whispers the WHISPERNEEDLE2 to the priest.")
    summary = "Winifred confided the WHISPERNEEDLE2 secret."
    st.absorb(summary)
    synth.grant(st, cast.mara, "Mara", "knows")
    synth.grant(st, cast.mara, "Mara", "unaware", keys=(synth.key(st, 0),))
    runner._seat(cast)
    fake = answering("known")
    out = refine(cast, view_for(cast, cast.mara, [st.item(summary=summary)]), fake)
    assert fake.calls == 0
    assert "WHISPERNEEDLE2" not in compose_text(cast, cast.mara, out.view)


# ---------------------------------------------------------------- caps


def _many(n: int):
    """`n` legacy scenes Mara stood in: one eligible part each."""
    cast = runner._cast()
    aid, vid = cast.ids["Mara"]
    items = []
    for i in range(n):
        st = cases._src(cast, f"The Shed {i}")
        synth.enter(st, aid, vid)
        st.say(f"Something stirs: MANYNEEDLE{i}.")
        synth.forget_intervals(st.cid, aid, st.sid)
        items.append(st.item())
    runner._seat(cast)
    return cast, items


def test_the_step_cap_skips_past_eight_in_rank_order(home):
    cast, items = _many(10)
    out = refine(cast, view_for(cast, cast.mara, items),
                 answering(*["known"] * stage.EPISTEMIC_DECIDE_ITEMS))
    bases = [p["basis"] for p in out.record["parts"]]
    assert bases == ["decision"] * 8 + ["decision_skipped"] * 2
    assert out.sent == 8
    assert "MANYNEEDLE7" in shown(out.view) and "MANYNEEDLE8" not in shown(out.view)


def test_the_round_cap_is_shared_and_survives_a_resumed_round(home):
    cast, items = _many(6)
    cid, sid = cast.now.cid, cast.now.sid
    rnd = store.responses.new_round(cid, sid, eligible=[], automatic=False, post=None,
                                    run_id="t")
    store.responses.update_round(cid, sid, rnd["id"], epistemic_asked=13)
    # A resumed round reads its count back from the record.
    resumed = store.responses.unfinished(cid, sid)
    assert stage.asked_in(resumed) == 13
    turn = character_turns._Progress(resumed, (), None)
    fake = answering("known", "known", "known")
    _view, record, _rid = asyncio.run(character_turns._refine_step(
        cid, sid, fake, turn, cast.mara, view_for(cast, cast.mara, items), ""))
    assert record["asked"] == 3
    assert [p["basis"] for p in record["parts"]].count("decision_skipped") == 3
    assert store.responses.unfinished(cid, sid)[stage.ROUND_FIELD] == 16
    # Spent: the next step asks nothing.
    turn.round_record = store.responses.unfinished(cid, sid)
    again = refine(cast, view_for(cast, cast.mara, items), fake,
                   asked=stage.asked_in(turn.round_record))
    assert fake.calls == 1 and again.record["skipped"] == "round_cap"


def test_a_step_is_asked_once_per_round_and_speaker(home):
    cast, items, _ = plant("7")
    cid, sid = cast.now.cid, cast.now.sid
    rnd = store.responses.new_round(cid, sid, eligible=[], automatic=False, post=None,
                                    run_id="t")
    turn = character_turns._Progress(rnd, (), None)
    fake = answering("known")
    view = view_for(cast, cast.mara, items)

    async def run():
        first = await character_turns._refine_step(cid, sid, fake, turn, cast.mara, view, "")
        second = await character_turns._refine_step(cid, sid, fake, turn, cast.mara, view, "")
        return first, second
    first, second = asyncio.run(run())
    assert fake.calls == 1 and second[1] is first[1]


@pytest.mark.parametrize("why", ["appended", "continuation", "no view", "narrator"])
def test_no_call_on_a_continuation_or_a_replay(home, why):
    cast, items, _ = plant("7")
    cid, sid = cast.now.cid, cast.now.sid
    turn = character_turns._Progress({"id": "r1"}, (), None)
    view = view_for(cast, cast.mara, items)
    actor = cast.mara
    if why == "appended":
        turn.appended = (("Roll", "system", "result"),)
    elif why == "continuation":
        turn.continuation = "roll"
    elif why == "no view":   # `_round_history` hands a replay none
        view = None
    else:
        actor = "grimoire"
    fake = answering("known")
    asyncio.run(character_turns._refine_step(cid, sid, fake, turn, actor, view, ""))
    assert fake.calls == 0


# ---------------------------------------------------------------- failure


def _withheld(out) -> bool:
    return "TOLDNEEDLE7" not in shown(out.view)


def test_a_failed_call_withholds_and_says_why(home):
    cast, items, _ = plant("7")
    fake = FakeLLM([["x"]], error=LLMError("rate_limit", "slow down"))
    out = refine(cast, view_for(cast, cast.mara, items), fake)
    assert _withheld(out) and out.record["skipped"]
    assert out.record["parts"][0]["access"] == "unknown"


def test_a_spent_deadline_is_refused_unsent_and_never_noted(home, monkeypatch):
    # No `_noting` by construction: `plan_deadline` takes no `on_timeout`, so
    # the refusal is the app's clock, never the connection's health.
    cast, items, _ = plant("7")
    clock = iter([0.0, 100.0, 100.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr(common, "_plan_clock", lambda: next(clock, 100.0))
    fake = answering("known")
    out = refine(cast, view_for(cast, cast.mara, items), fake)
    assert fake.calls == 0 and _withheld(out)
    assert out.record["parts"][0]["basis"] == DEADLINE_BASIS
    health = store.llm_connections.read_connection("openrouter").get("health") or {}
    assert health.get("status") != "failing"


def test_a_route_that_does_not_resolve_skips(home, monkeypatch):
    cast, items, _ = plant("7")

    def refuse(*a, **k):
        raise HTTPException(409, {"kind": "incapable", "detail": "no"})
    monkeypatch.setattr(stage, "require_inference", refuse)
    fake = answering("known")
    out = refine(cast, view_for(cast, cast.mara, items), fake)
    assert fake.calls == 0 and _withheld(out) and out.sent == 0
    assert out.record["parts"][0]["basis"] == "decision_skipped"


def test_a_crash_building_items_withholds(home, monkeypatch):
    cast, items, _ = plant("7")

    def broken(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(items_mod, "questions", broken)
    fake = answering("known")
    out = refine(cast, view_for(cast, cast.mara, items), fake)
    assert fake.calls == 0 and _withheld(out)
    assert out.record["skipped"] == "error:RuntimeError"
    assert out.record["parts"][0]["basis"] == "decision_failed"


def test_a_part_that_moved_before_the_answer_stays_withheld(home):
    cast, items, _ = plant("7")
    view = view_for(cast, cast.mara, items)
    [part] = items_mod.eligible_for(view)
    stale = {(part.key, part.part, tuple(i + 1 for i in part.posts)): ("known", "decision")}
    out = epistemic.classify(view.perspective, items, decided=stale)
    assert "TOLDNEEDLE7" not in shown(out)


# ---------------------------------------------------------------- off


def test_off_reads_nothing_past_the_switch(home, monkeypatch):
    cast, items, _ = plant("7")
    store.write_config(epistemic_decide="off")
    monkeypatch.setattr(items_mod, "eligible_for", lambda *a: pytest.fail("read past the switch"))
    view = view_for(cast, cast.mara, items)
    fake = answering("known")
    out = refine(cast, view, fake)
    assert out.view is view and out.record is None and out.response_id == ""
    assert fake.calls == 0


def test_the_switch_is_hidden_from_settings(home):
    assert store.config.read_config()["epistemic_decide"] == "on"
    assert routing.policy(stage.TASK) == routing.TaskPolicy()
    assert "epistemic_decide" not in store.play_decisions.effective("")
    assert "epistemic_decide" not in store.play_decisions.VOCABULARIES


def test_a_campaigns_own_value_wins(home):
    cast, _items, _ = plant("7")
    store.campaigns.set_campaign_switches(cast.now.cid, {"epistemic_decide": "off"},
                                          {"epistemic_decide": ("on", "off")})
    assert not store.play_decisions.epistemic_decide(cast.now.cid)
    assert store.play_decisions.epistemic_decide("")


# ---------------------------------------------------------------- the record


def test_the_record_round_trips_and_holds_no_withheld_text(home):
    cast, items, _ = plant("7")
    out = refine(cast, view_for(cast, cast.mara, items), answering("narrator_only"))
    record = epistemic.to_record(out.view)
    dumped = json.dumps(record)
    assert "TOLDNEEDLE7" not in dumped and "TAVERNNEEDLE7" in dumped
    back = epistemic.from_record(json.loads(dumped))
    assert compose_text(cast, cast.mara, back) == compose_text(cast, cast.mara, out.view)
    assert epistemic.from_record({"version": 99}) is None
    assert epistemic.from_record({**record, "items": [{"scene": 3}]}) is None


def test_a_resume_after_a_rename_composes_with_the_stored_view(home):
    cast, items, _ = plant("7")
    out = refine(cast, view_for(cast, cast.mara, items), answering("known"))
    stored = {"snapshot": {"history_view": epistemic.to_record(out.view)}}
    cid, sid = cast.now.cid, cast.now.sid

    class Run:
        scene_identity = store.scenes.identity.scene_identity(cid, sid)
    view = character_turns._stored_view(cid, sid, Run(), stored)
    assert view is not None and "TOLDNEEDLE7" in compose_text(cast, cast.mara, view)
    # The sid moved (a rename), the identity did not: rebound, not refused.
    moved = character_turns._stored_view(cid, sid, Run(), {"snapshot": {
        "history_view": {**stored["snapshot"]["history_view"],
                         "perspective": {**stored["snapshot"]["history_view"]["perspective"],
                                         "sid": "0001--old-name"}}}})
    assert moved is not None and moved.perspective.sid == sid
    # Another scene's view is no view at all.
    Run.scene_identity = "another"
    assert character_turns._stored_view(cid, sid, Run(), stored) is None
    assert character_turns._stored_view(cid, sid, Run(), {}) is None


def test_the_route_spelled_in_routing_is_the_kits(home):
    route = routing.route(stage.TASK)
    assert route is epistemic_access.ROUTE and route.key == "epistemic"
    assert routing.policy(stage.TASK).escalate_to == ""
    assert epistemic_access.policy().question == epistemic_access.ACCESS_ID


# ---------------------------------------------------------------- the turn


_HANDOFF = '\n```handoff\n{"next":null}\n```'


def _chat(home, cast, monkeypatch, fake, *, on=True):
    store.write_config(epistemic_decide="on" if on else "off")
    items = CASE["7"].build(cast)[0]
    runner._seat(cast)

    async def gather(*a, actor_ref=None, **k):
        return view_for(cast, actor_ref, items)
    monkeypatch.setattr(history_recall, "gather_view", gather)
    home.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cast.now.cid}/scenes/{cast.now.sid}"
    response = home.post(base + "/chat", json={"content": "Mara, what news?",
                                               "speaker_ref": cast.mara})
    assert response.status_code == 200, response.text
    assert '"error"' not in response.text, response.text
    rid = [m for m in store.scenes.read_scene(cast.now.cid, cast.now.sid)["messages"]
           if m.get("response_id")][-1]["response_id"]
    return store.responses.get(cast.now.cid, cast.now.sid, rid, private=True)


def test_a_turn_asks_once_composes_the_answer_and_records_it(home, monkeypatch):
    cast = runner._cast()
    fake = FakeLLM([[decision_reply({epistemic_access.ACCESS_ID: "known"})],
                    ["Mara answers." + _HANDOFF]])
    record = _chat(home, cast, monkeypatch, fake)
    decide, chat = fake.requests[0], fake.requests[1]
    assert "TOLDNEEDLE7" in json.dumps(decide["messages"])
    assert "TOLDNEEDLE7" in json.dumps(chat["messages"])
    # The record rides the response beside `intent`, the view its snapshot,
    # and the call is attributed to the response it shaped.
    assert record["epistemic"]["parts"][0]["access"] == "known"
    stored = epistemic.from_record(record["snapshot"]["history_view"])
    assert stored is not None and stored.decided == 1
    [row] = rows(cast.now.cid)
    assert row.get("response_id") == record["id"]


def test_a_turn_with_the_switch_off_sends_what_it_sent_before(home, monkeypatch):
    cast = runner._cast()
    fake = FakeLLM([["Mara answers." + _HANDOFF]])
    record = _chat(home, cast, monkeypatch, fake, on=False)
    assert "epistemic" not in record and not rows(cast.now.cid)
    assert "TOLDNEEDLE7" not in json.dumps(fake.requests[0]["messages"])
    assert record["snapshot"]["history_view"]["decided"] == 0



# ---------------------------------------------------------------- review follow-ups


def test_items_read_the_prompt_view_never_the_display(home):
    # The regex-guard pin, as a source test (11-S6 amendment): the item's
    # excerpt is the part's posts as 09 handed them over -- 08's prompt-phase
    # view -- so a display-only rule never reaches it, and a prompt rule does.
    cast = runner._cast()
    rules = [{"name": "Shown only", "pattern": "customs seal", "replacement": "DISPLAYONLY",
              "targets": ["model", "user"], "applies": ["display"]},
             {"name": "Sent", "pattern": "slip past", "replacement": "PROMPTVIEW slip past",
              "targets": ["model", "user"], "applies": ["prompt"]}]
    response = home.put(f"/api/campaigns/{cast.now.cid}/regex", json={"rules": rules})
    assert response.status_code == 200, response.text
    items, _checks = CASE["7"].build(cast)
    runner._seat(cast)
    fake = answering("known")
    refine(cast, view_for(cast, cast.mara, items), fake)
    sent = json.dumps(fake.requests[0]["messages"])
    assert "PROMPTVIEW" in sent and "DISPLAYONLY" not in sent


def _captured(cid, sid):
    entries = [e for e in store.prompt_log.list_entries(cid, sid) if e["task"] == stage.TASK]
    assert len(entries) == 1, entries
    payload = store.prompt_log.read_entry(cid, entries[0]["id"], scene=sid)
    return json.loads(payload["sections"][-1]["text"])


def test_the_capture_holds_the_parts_and_answers(home):
    cast, items, _ = plant("7")
    refine(cast, view_for(cast, cast.mara, items), answering("suspected"))
    envelope = _captured(cast.now.cid, cast.now.sid)
    [part] = envelope["notes"][""]["parts"]
    assert (part["access"], part["basis"]) == ("suspected", "decision")
    assert envelope["calls"]


def test_a_skip_is_said_in_the_capture(home, monkeypatch):
    cast, items, _ = plant("7")

    def refuse(*a, **k):
        raise HTTPException(409, {"kind": "incapable", "detail": "no"})
    monkeypatch.setattr(stage, "require_inference", refuse)
    refine(cast, view_for(cast, cast.mara, items), answering("known"))
    envelope = _captured(cast.now.cid, cast.now.sid)
    assert envelope["notes"][""]["skipped"] and not envelope["calls"]


def test_a_failed_round_write_never_fails_the_turn(home, monkeypatch):
    cast, items, _ = plant("7")
    cid, sid = cast.now.cid, cast.now.sid
    rnd = store.responses.new_round(cid, sid, eligible=[], automatic=False, post=None,
                                    run_id="t")
    turn = character_turns._Progress(rnd, (), None)

    def busy(*a, **k):
        raise OSError("disk")
    monkeypatch.setattr(store.responses, "update_round", busy)
    view, record, _rid = asyncio.run(character_turns._refine_step(
        cid, sid, answering("known"), turn, cast.mara, view_for(cast, cast.mara, items), ""))
    assert record["asked"] == 1 and "TOLDNEEDLE7" in shown(view)
    # Fail closed: the rest of the request reads the round as spent.
    assert stage.asked_in(turn.round_record) == stage.EPISTEMIC_ROUND_ITEMS


def test_a_stop_during_the_call_sends_no_generation(home):
    cast, items, _ = plant("7")
    turn = character_turns._Progress({"id": "r1"}, (), None)

    class Run:
        cancel_requested = True
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(character_turns._refine_step(
            cast.now.cid, cast.now.sid, answering("known"), turn, cast.mara,
            view_for(cast, cast.mara, items), "", run=Run()))


@pytest.mark.parametrize("basis", ["moved", "unverifiable", "unkeyed", "override",
                                   "override_group", "decision"])
def test_a_part_on_a_settled_basis_is_never_eligible(basis):
    assert not epistemic.eligible(epistemic.Slice("posts", (0,), "unknown", (basis,), ""))
    assert not epistemic.eligible(
        epistemic.Slice("posts", (0, 1), "unknown", ("no_record", basis), ""))
    assert epistemic.eligible(epistemic.Slice("posts", (0,), "unknown", ("no_record",), ""))


def test_a_summary_that_is_not_the_chronicles_now_is_never_asked(home):
    # The chronicle's summary moved on since retrieval: no grant and no
    # answer could show that text, so it is not paid for.
    cast, items, _ = plant("8")
    [item] = items
    stale = dataclasses.replace(item, summary="An older SECRETNEEDLE8 summary.")
    view = view_for(cast, cast.mara, [stale])
    summary = next(s for c in view.items for s in c.slices if s.part == "summary")
    assert summary.basis == ("moved",) and not epistemic.eligible(summary)
    fake = answering("known")
    assert refine(cast, view, fake).record["parts"] == [] and fake.calls == 0


def test_an_answer_never_moves_a_scene_class_an_override_settled():
    own = epistemic._Classes({0: ("unknown", "no_record")}, ("known", "override"))
    slices = [epistemic.Slice("summary", (), "unknown", ("summary_needs_grant",), "")]
    out = epistemic._answered("k", own, slices,
                              {("k", "summary", ()): ("unknown", "decision")})
    assert out.scene == ("known", "override")


def test_a_chronicle_cast_member_stood_there(home):
    # Named only by the chronicle's cast, with no appearance record at all.
    cast = runner._cast()
    st = cases._src(cast, "The Weighhouse")
    st.say("The CASTNEEDLE crates are weighed.")
    aid = cast.ids["Mara"][0]
    store.chronicle.absorb(cast.now.cid, {"id": st.sid, "one_line": "x", "summary": "x",
                                          "keywords": [], "cast": [f"characters/{aid}"],
                                          "location": "", "date": ""})
    runner._seat(cast)
    view = view_for(cast, cast.mara, [st.item()])
    assert view.items[0].stood
