"""Roadmap 02-S3: the sampled next speaker (02-C2a) and the drawn stance
(02-C2b). The pick's issue mapping is read first and unchanged; only WHO is
drawn, through 01c's one sampler, and the draw's record is stored with the
speaker so nothing ever draws it again."""

import pytest

from grimoire import decisions, draws, routes, store
from grimoire.decisions import NONE_KEY, Answer, Decision, ItemResult
from grimoire.routes import character_turns
from grimoire.store import response_protocol as rp
from grimoire.store import turn_plan
from tests.llm_fakes import FakeLLM

MARA, WINI = "characters:mara", "characters:winifred"
ELIGIBLE = [{"ref": MARA, "name": "Mara"}, {"ref": WINI, "name": "Winifred"}]
OFFERED = [MARA, WINI, rp.GRIMOIRE_REF]
_HANDOFF = '\n```handoff\n{"next":null}\n```'


def _item():
    return rp.selector_item(ELIGIBLE, [{"speaker": "You", "content": "Hello"}])


def _native(answer, distribution=None, reason=""):
    return ItemResult({rp.SELECTOR_QUESTION: Answer(answer, reason,
                                                    distribution=distribution)},
                      backend="native", served=("openai", "openai", "decider"))


SPREAD = {MARA: 0.55, WINI: 0.38, rp.GRIMOIRE_REF: 0.04, NONE_KEY: 0.03}


# --- the pure helpers ----------------------------------------------------------


def test_the_eligibility_excludes_the_none_and_cuts_at_half_of_indifference():
    e = rp.pick_eligibility(OFFERED, ())
    assert e.exclude == (NONE_KEY,) and e.only is None
    assert e.cutoff == 1 / 6 and e.floor == 0.0
    assert rp.pick_eligibility(OFFERED, (MARA,)).only == (MARA,)


def test_a_sampled_pick_is_offered_never_the_none_never_below_the_cutoff():
    seen = set()
    for seed in range(400):
        drawn = rp.sampled_pick(_item(), _native(MARA, SPREAD), OFFERED, (), seed)
        assert drawn.basis == "sampled"
        assert drawn.value in (MARA, WINI)          # grimoire is under the cutoff
        assert draws.replay(drawn.record) == drawn.record["selected"]
        seen.add(drawn.value)
    assert seen == {MARA, WINI}


def test_an_addressed_actor_is_never_drawn_away():
    for seed in range(100):
        drawn = rp.sampled_pick(_item(), _native(MARA, SPREAD), OFFERED, (WINI,), seed)
        assert drawn.value == WINI
        assert drawn.record["eligibility"]["only"] == [WINI]


def test_an_answer_outside_the_addressed_set_with_no_report_is_kept():
    drawn = rp.sampled_pick(_item(), _native(MARA), OFFERED, (WINI,), 7)
    # Re-drawn with nothing narrowed but the none: the answer stands.
    assert (drawn.basis, drawn.value) == ("answer", MARA)
    assert drawn.record["eligibility"]["only"] is None
    assert drawn.record["eligibility"]["cutoff"] == 0.0


def test_a_cutoff_that_removes_every_addressed_actor_keeps_a_speaker():
    low = {MARA: 0.9, WINI: 0.05, rp.GRIMOIRE_REF: 0.03, NONE_KEY: 0.02}
    drawn = rp.sampled_pick(_item(), _native(MARA, low), OFFERED, (WINI,), 3)
    assert drawn.basis == "sampled" and drawn.value in (MARA, WINI, rp.GRIMOIRE_REF)
    assert drawn.record["eligibility"]["only"] is None


@pytest.mark.parametrize("answer,reason,detail,expected", [
    (None, "abstained", "", (None, None)),
    (None, "refused", "", (None, rp.INVALID_HANDOFF)),
    (None, "error", "", (None, rp.INVALID_HANDOFF)),
    (None, "unreadable", decisions.NOT_AN_OPTION, (None, rp.INELIGIBLE)),
])
def test_the_issue_mapping_comes_first_and_is_never_drawn_over(answer, reason, detail,
                                                                expected):
    result = ItemResult({rp.SELECTOR_QUESTION: Answer(answer, reason, detail=detail)},
                        backend="native")
    selection = rp.selection_of(result)
    assert selection == expected
    drawn = rp.sampled_pick(_item(), result, OFFERED, (), 5)
    assert drawn.basis == "none" and drawn.record["why"] == reason
    assert rp.next_of(selection, drawn) is None


def test_a_structured_answer_is_the_plain_answer():
    result = ItemResult({rp.SELECTOR_QUESTION: Answer(WINI)}, backend="structured")
    drawn = rp.sampled_pick(_item(), result, OFFERED, (), 5)
    assert (drawn.basis, drawn.record["why"], drawn.value) == ("answer", "no_report", WINI)
    assert rp.next_of(rp.selection_of(result), drawn) == WINI


def test_addressed_reads_the_trigger_less_its_author():
    assert rp.addressed("Mara, where were you?", ELIGIBLE, None) == (MARA,)
    assert rp.addressed("Mara and Winifred, both of you.", ELIGIBLE, None) == (MARA, WINI)
    assert rp.addressed("Winifred, as Mara says.", ELIGIBLE, WINI) == (MARA,)
    assert rp.addressed("", ELIGIBLE, None) == ()


def test_named_drops_the_author():
    entries = ELIGIBLE
    assert store.group_play.named("Mara? Winifred?", entries) == [MARA, WINI]
    assert store.group_play.named("Mara? Winifred?", entries, author_ref=MARA) == [WINI]


# --- the stance -------------------------------------------------------------------


def _stance_item():
    return turn_plan.intent_item("Mara", [], "")


def test_a_stance_is_drawn_from_a_report_and_never_the_none():
    dist = {"deflect": 0.5, "press": 0.3, "refuse": 0.15, NONE_KEY: 0.05}
    result = ItemResult({turn_plan.STANCE_ID: Answer("deflect", distribution=dist)},
                        backend="native")
    seen = set()
    for seed in range(300):
        stance, reason, record = turn_plan.drawn_stance(_stance_item(), result, seed)
        assert reason == "" and record["basis"] == "sampled"
        assert stance in ("deflect", "press", "refuse")
        seen.add(stance)
    assert {"deflect", "press"} <= seen
    assert turn_plan.stance_eligibility().cutoff == 1 / (2 * len(turn_plan.STANCES))


def test_an_abstained_stance_is_never_drawn():
    result = ItemResult({turn_plan.STANCE_ID: Answer(None, "abstained")}, backend="native")
    assert turn_plan.drawn_stance(_stance_item(), result, 1) == (None, "abstained", None)


def test_a_stance_with_no_report_is_the_answer():
    result = ItemResult({turn_plan.STANCE_ID: Answer("act")}, backend="structured")
    stance, _, record = turn_plan.drawn_stance(_stance_item(), result, 1)
    assert stance == "act" and record["basis"] == "answer"


# --- the route --------------------------------------------------------------------


def seed(client):
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    for name in ("Mara", "Winifred"):
        actor = client.post(f"/api/campaigns/{cid}/characters", json={"name": name}).json()
        client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"id": actor["character"]})
    return cid, sid, f"/api/campaigns/{cid}/scenes/{sid}"


def _round(cid, sid):
    rounds = store.responses._scope(cid, sid, store.responses._read(cid))["rounds"]
    return list(rounds.values())[-1]


def _decided(monkeypatch, result):
    asked = []

    async def decide(task, items, **_kwargs):
        asked.append(task)
        return Decision(items=(result,), backend=result.backend)

    monkeypatch.setattr(character_turns.operations, "decide", decide)
    return asked


def _reply(client, text):
    fake = FakeLLM([[text + _HANDOFF]])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    return fake


def test_off_stores_no_pick_and_takes_the_answer(client, monkeypatch):
    cid, sid, base = seed(client)
    _decided(monkeypatch, _native(MARA, SPREAD))
    _reply(client, "Winifred answers.")
    client.post(base + "/chat", json={"content": "Hello"})
    record = _round(cid, sid)
    assert "pick" not in record
    assert record["used"] == [MARA]


def test_on_draws_the_speaker_and_stores_the_record_with_it(client, monkeypatch):
    cid, sid, base = seed(client)
    store.write_config(decide_speaker_sampling="on")
    _decided(monkeypatch, _native(MARA, SPREAD))
    monkeypatch.setattr(draws, "_seed_source", lambda: 11)
    expected = rp.sampled_pick(_item(), _native(MARA, SPREAD), OFFERED, (), 11).value
    _reply(client, "An answer.")
    response = client.post(base + "/chat", json={"content": "Hello"})
    assert '"error"' not in response.text, response.text
    record = _round(cid, sid)
    pick = record["pick"]
    assert pick["seed"] == 11 and pick["purpose"] == rp.PICK_PURPOSE
    assert draws.replay(pick) == pick["selected"] == expected
    assert record["used"] == [expected]


def test_a_typed_note_naming_winifred_narrows_the_draw(client, monkeypatch):
    cid, sid, base = seed(client)
    store.write_config(decide_speaker_sampling="on")
    _decided(monkeypatch, _native(MARA, SPREAD))
    _reply(client, "Winifred answers.")
    client.post(base + "/chat", json={"content": "Winifred should answer this.",
                                      "director": True})
    record = _round(cid, sid)
    assert record["pick"]["eligibility"]["only"] == [WINI]
    assert record["used"] == [WINI]


def test_a_handed_back_pick_is_decided_and_never_asked_again(client, monkeypatch):
    cid, sid, base = seed(client)
    store.write_config(decide_speaker_sampling="on")
    asked = _decided(monkeypatch, ItemResult(
        {rp.SELECTOR_QUESTION: Answer(None, "abstained")}, backend="native"))
    _reply(client, "Unused.")
    client.post(base + "/chat", json={"content": "Hello"})
    record = _round(cid, sid)
    assert record["pick"]["basis"] == "none" and record["actor_ref"] is None
    assert asked == ["response-selector"]

    import asyncio
    actor, again = asyncio.run(character_turns._first_actor(cid, sid, None, record))
    assert actor is None and again is record
    assert asked == ["response-selector"]


def test_a_draw_defect_leaves_the_answer_standing(client, monkeypatch):
    cid, sid, base = seed(client)
    store.write_config(decide_speaker_sampling="on")
    _decided(monkeypatch, _native(WINI, SPREAD))

    def broken(*_args, **_kwargs):
        raise ValueError("a bad eligibility")

    monkeypatch.setattr(rp, "sampled_pick", broken)
    _reply(client, "Winifred answers.")
    client.post(base + "/chat", json={"content": "Hello"})
    record = _round(cid, sid)
    assert "pick" not in record and record["used"] == [WINI]
