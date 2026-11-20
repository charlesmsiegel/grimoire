"""081's story explorer ops over a played chat (``story.threads``, ``story.thread``): an early promise found by
its words, by its outcome and by the character it involves, followed to the ruling that decided it, with every
entry leading back to the turn it was recorded at. The table reads only what its own character received.

Synthetic cards and invented names; every model call is a scripted fake, and the story ops must make none.
"""

from __future__ import annotations

import pytest
from generation_fixtures import say
from scene_fixtures import MARA_VELL
from test_app_play import Device, actor_named, keep_newest, refused
from test_app_play_story import (  # noqa: F401 -- the played card library fixtures
    built,
    folder,
    start,
)

from grimoire.app.adapters.campaign import hub_of
from grimoire.core import (
    DIRECTOR,
    Belief,
    Event,
    Moment,
    Proposition,
    Report,
    Ruling,
    Subject,
)
from grimoire.core.identity import ChatRef, InstanceRef

PROMISE = "Mara will bring the harbour key back before the thaw"
SOLD = "Mara sells the harbour key to a smuggler at the night market"
SECRET = "Mara keeps a second key under the lighthouse stair"


def played(device: Device) -> tuple[ChatRef, list[str], InstanceRef, InstanceRef]:
    """A chat of three played turns (the opening and two posts): the chat, its turn ids, Mara and Anya."""
    scene = start(device)
    chat = scene["chat"]
    for text in ("Will you bring the key back?", "Where is the key?"):
        device("play.post", chat=chat, text=f'"{text}"', mode="speech")
        scene = keep_newest(device, device("generate", chat=chat))
    turns = [turn["turn"] for turn in scene["turns"]]
    mara = InstanceRef.parse(actor_named(scene, "Mara Vell"))
    anya = InstanceRef.parse([member["actor"] for member in scene["cast"] if member["actor"] != str(mara)][0])
    return ChatRef.parse(chat), turns, mara, anya


def ledger(device: Device, chat: ChatRef, turns: list[str], mara: InstanceRef, anya: InstanceRef) -> None:
    """The promise told to Anya at the first turn, Mara's own belief, and canon's ruling on how it ended."""
    campaign = chat.campaign_id
    at = [Moment(number, chat.chat_id, turn) for number, turn in enumerate(turns, start=1)]
    hub = hub_of(device.app.context)
    controls = hub.runner.open(chat).controls
    hub.evidence.append(campaign, [
        Proposition(campaign, "p-key", PROMISE, at[0], "director", about=(Subject("instance", str(mara)),)),
        Report(campaign, "told-key", anya, "p-key", "told", at[0], "scene:quay", sender=mara, attributed_to=mara,
               confirmed=True),
        Report(campaign, "made-key", mara, "p-key", "said", at[0], "scene:quay", True),
        Belief(campaign, "b-keep", mara, "p-key", "believed", at[0], DIRECTOR, ("made-key",)),
        Proposition(campaign, "p-stair", SECRET, at[1], "director", about=(Subject("instance", str(mara)),)),
    ], controls=controls, chat=chat).commit()
    hub.evidence.append(campaign, [
        Event(campaign, "e-sold", SOLD, at[2], "director", (mara,)),
        Ruling(campaign, "r-broken", "p-key", "refuted", at[2], "director", grounds=(Subject("event", "e-sold"),)),
    ], controls=controls, chat=chat).commit()


def test_an_early_promise_is_found_and_followed_to_the_turn_that_decided_it(folder):
    with Device(folder, [say("I will, before the thaw."), say("Gone, I'm afraid.")]) as device:
        chat, turns, mara, anya = played(device)
        ledger(device, chat, turns, mara, anya)
        calls = len(device.prompts())

        found = device("story.threads", chat=str(chat), audience="director", query="harbour KEY")
        assert found["problems"] == [] and found["total"] == 1
        (summary,) = found["threads"]
        assert (summary["proposition"], summary["standing"], summary["decided"]["turn"]) == ("p-key", "refuted",
                                                                                              turns[2])
        assert {actor["name"] for actor in summary["actors"]} == {"Mara Vell", "Anya Rook"}
        # how it ended finds the promise too, and the character filter keeps only what involves her
        assert [item["proposition"] for item in device("story.threads", chat=str(chat), audience="director",
                                                       query="night market")["threads"]] == ["p-key"]
        by_mara = device("story.threads", chat=str(chat), audience="director", actor=str(mara))
        assert [item["proposition"] for item in by_mara["threads"]] == ["p-key", "p-stair"]
        assert device("story.threads", chat=str(chat), audience="director", actor=str(anya))["total"] == 1

        view = device("story.thread", chat=str(chat), audience="director", proposition="p-key")["thread"]
        assert [(entry["kind"], entry["grade"]) for entry in view["entries"]] == [
            ("stated", "proposal"), ("report", "proposal"), ("report", "proposal"), ("belief", "belief"),
            ("event", "confirmed"), ("ruling", "confirmed")]
        assert [entry["source"]["number"] for entry in view["entries"]] == [1, 1, 1, 1, 3, 3]
        assert all(entry["source"]["chat"] == str(chat) and not entry["source"]["set_aside"]
                   for entry in view["entries"])
        assert view["outcome"]["standing"] == "refuted" and view["outcome"]["ruling"] == "r-broken"
        (ground,) = view["outcome"]["grounds"]
        assert ground["subject"] == "event:e-sold" and ground["entry"]["text"] == SOLD
        assert view["contradictions"] == [{"kind": "belief_against_canon", "records": ["ruling:r-broken",
                                                                                       "belief:b-keep"],
                                           "moment": {"sequence": 1, "scene": chat.chat_id, "turn": turns[0]}}]
        (report,) = [entry for entry in view["entries"] if entry["record"] == "evidence:told-key"]
        assert report["sender"]["name"] == "Mara Vell" and report["holder"]["name"] == "Anya Rook"
        (scene,) = view["scenes"]
        assert scene["chat"] == str(chat) and scene["turns"] == [turns[0], turns[2]]
        assert len(device.prompts()) == calls  # reading the story asked no model anything


def test_the_table_reads_only_what_its_own_character_received(folder):
    with Device(folder, [say("I will, before the thaw."), say("Gone, I'm afraid.")]) as device:
        chat, turns, mara, anya = played(device)
        ledger(device, chat, turns, mara, anya)

        table = device("story.threads", chat=str(chat))
        assert table["audience"] == "table" and table["total"] == 1
        (summary,) = table["threads"]
        assert (summary["proposition"], summary["standing"], summary["decided"]) == ("p-key", None, None)
        for text in (SOLD, SECRET, "refuted", "r-broken", "b-keep", "sender"):
            assert text not in repr(table), text
        # how canon says it ended is the director's: a search for it finds nothing at the table
        assert device("story.threads", chat=str(chat), query="night market")["total"] == 0

        view = device("story.thread", chat=str(chat), proposition="p-key")["thread"]
        assert [(entry["kind"], entry["grade"]) for entry in view["entries"]] == [("report", "proposal")]
        (report,) = view["entries"]
        assert report["attributed_to"]["name"] == "Mara Vell" and "sender" not in report
        assert report["source"]["number"] == 1
        assert view["outcome"] is None and view["contradictions"] == []
        for text in (SOLD, "refuted", "stated", "provenance", "director"):
            assert text not in repr(view), text
        # a thread the table never heard of is refused exactly like one that does not exist
        assert refused(device, "story.thread", chat=str(chat), proposition="p-stair").reason == "no_thread"
        assert refused(device, "story.thread", chat=str(chat), proposition="p-none").reason == "no_thread"


def test_malformed_requests_are_refused(folder):
    with Device(folder) as device:
        chat = start(device)["chat"]
        assert device("story.threads", chat=chat, audience="director") == {
            "chat": chat, "campaign": ChatRef.parse(chat).campaign_id, "audience": "director",
            "cast": device("story.threads", chat=chat)["cast"], "actor": None, "threads": [], "total": 0,
            "problems": []}
        for args in ({"audience": "everyone"}, {"limit": 0}, {"limit": 201}, {"limit": "5"}, {"query": 5},
                     {"query": "x" * 201}, {"actor": "nope"}, {"actor": "other-campaign:character:mara"}):
            assert refused(device, "story.threads", chat=chat, **args).reason == "invalid_field", args
        for proposition in (None, "", 5, "p" * 301):
            assert refused(device, "story.thread", chat=chat, audience="director",
                           proposition=proposition).reason == "invalid_field", proposition
        assert refused(device, "story.thread", chat=chat, audience="director", proposition="p-none").reason == \
            "no_thread"
        assert refused(device, "story.threads", chat="not a chat").reason == "invalid_field"


@pytest.mark.parametrize("op", ["threads", "thread"])
def test_the_ops_are_served_by_the_story_adapter(op):
    from grimoire.app.adapters import ADAPTERS

    (story,) = [adapter for adapter in ADAPTERS if adapter.name == "story"]
    assert story.owner == "081" and op in story.ops
    assert MARA_VELL  # the card fixtures this file plays are the play adapter's own


def test_a_turn_number_two_live_branches_share_is_not_linked_until_the_fork_is_resolved():
    from types import SimpleNamespace

    from grimoire.app.adapters.story import _turns

    def stored(turn_id: str, number: int) -> SimpleNamespace:
        return SimpleNamespace(record=SimpleNamespace(turn_id=turn_id, number=number))

    forked = SimpleNamespace(turns=(stored("t-1", 1), stored("t-2a", 2), stored("t-2b", 2)), set_aside=())
    read = SimpleNamespace(problems=[], hub=SimpleNamespace(runner=SimpleNamespace(
        scenes=SimpleNamespace(turns=lambda chat: forked))))
    chat = ChatRef.parse("campaign:quay#chat/forked")
    (numbers, set_aside), = _turns(read, {chat}).values()
    assert numbers == {"t-1": 1, "t-2a": None, "t-2b": None} and set_aside == set() and read.problems == []
