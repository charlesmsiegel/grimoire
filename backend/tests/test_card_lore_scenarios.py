"""Imported card lore reaches 031's packs (037 x 031): every NPC in the scene sees it, SillyTavern style.

An imported ``grimoire.lorebook/1`` bound to a chat is ``common`` lore: once a key the scene shows
triggers an entry, every NPC's pack holds it, and a constant entry is always there. Launching an
imported scenario binds the lorebook it embeds, so its entries trigger in that scenario's scenes. A
lorebook no chat binds never reaches anyone. Every card here is synthetic. No model is called.
"""

from __future__ import annotations

import json

import pytest

from card_fixtures import LOREBOOK, SCENARIO, V3, card, encode, land, seed_world
from chat_fixtures import chat_library
from store_helpers import OWNER, owner_handle

from grimoire.chats import ChatModelError, Launch
from grimoire.context import Budget
from grimoire.contextstore import actor_context, chat_lore
from grimoire.core import DIRECTOR, snapshot_actor_view
from grimoire.core.identity import SourceRef
from grimoire.core.perspective import actor_knowledge
from grimoire.effective import chat_snapshot
from grimoire.evidencestore import EvidenceLibrary
from grimoire.generation import npc_brief, resolve_snapshot_voice
from grimoire.store import LibraryStore
from grimoire.voice import VoiceAnchor, VoiceCatalog, VoiceProfile

MARA = SourceRef("card:mara-vell", "character", "mara-vell")
MARA_LORE = SourceRef("card:mara-vell", "lorebook", "mara-vell-lore")
ANYA = SourceRef("card:anya-rook", "character", "anya-rook")
STORM = SourceRef("card:storm-over-port-vell", "scenario", "storm-over-port-vell")
STORM_LORE = SourceRef("card:storm-over-port-vell", "lorebook", "storm-over-port-vell-lore")
GAZETTEER = SourceRef("card:port-vell-gazetteer", "lorebook", "port-vell-gazetteer")
RULES = SourceRef("card:harbor-rules", "lorebook", "harbor-rules")

LIGHTHOUSE = "The lighthouse has been dark for a year."
STORMS = "Storms here last three days."
MARKET = "The fish market opens at dawn."
TIDES = "Tides here turn twice a day."
CAPTAIN = "The captain owes the guild a fortune."
FAIR = "The inn is quiet in fair weather."
EARLY, LATE = "The inn keeps a back room.", "The inn's cellar floods in spring."
BRIBES = "The harbor master takes bribes on wet nights."
FERRY = "The ferry runs unless it is both late and foggy."
ALMANAC = "The almanac lists the market's feast days."
LONGER = "Storms here now last a week."

BUDGET = Budget(400)
#: an authored voice for Mara's imported card, so her brief renders (where it comes from is 014's)
VOICES = VoiceCatalog([VoiceProfile(MARA.at("r1"), (VoiceAnchor("cadence", "cadence", "Mara speaks plainly."),))])


def _entry(content: str, *keys: str, order: int = 0, **settings: object) -> dict:
    return {"keys": list(keys), "content": content, "enabled": True, "insertion_order": order, "use_regex": False,
            "extensions": {}, **settings}


#: A standalone V3 lorebook exercising what 031's trigger model reads of a card entry.
HARBOR_RULES = {"spec": "lorebook_v3", "data": {"name": "Harbor rules", "extensions": {}, "entries": [
    _entry(LATE, "inn", order=30),
    _entry(TIDES, constant=True, order=5),
    _entry(CAPTAIN, "captain", selective=True, secondary_keys=["ship", "boat"], extensions={"selectiveLogic": 0}),
    _entry(FAIR, "inn", "bad\tkey", selective=True, secondary_keys=["storm"], extensions={"selectiveLogic": 2}),
    _entry("REGEX-TOKEN fog is a pattern here.", "/fo+g/", use_regex=True),
    _entry("DISABLED-TOKEN the inn is closed.", "inn", enabled=False),
    _entry("UNSAFE-TOKEN\u0007 rings a bell.", "inn"),
    _entry(EARLY, "inn", order=1),
    _entry(BRIBES, "harbor master", selective=True, secondary_keys=["rain", "docks"],
           extensions={"selectiveLogic": 3}),
    _entry(FERRY, "ferry", selective=True, secondary_keys=["late", "fog"], extensions={"selectiveLogic": 1}),
]}}


#: the two rules entries 031 cannot honour, named by place and reason, never by text
SKIPPED_RULES = {("skipped_entry", RULES.at("r1"), "entry-4:use_regex"),
                 ("skipped_entry", RULES.at("r1"), "entry-6:content")}


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed_world(opened)
        yield opened


@pytest.fixture
def chats(store):
    return chat_library(store, owner_handle())


def _anya() -> dict:
    return card(V3, name="Anya Rook", description="A harbor pilot who knows every channel.", character_book=None)


def _ledger(store, chat):
    return EvidenceLibrary(store, owner_handle(), owner=OWNER).read(chat.campaign_id, requested_by=DIRECTOR)


def _packs(store, started, scene: str, budget: Budget = BUDGET, actors=None) -> dict:
    """Each actor's pack for one scene, over one view and one director ledger read."""
    view, ledger = store.load(), _ledger(store, started.chat)
    cast = started.actors if actors is None else actors
    return {actor: actor_context(view, started.chat, ledger, actor, as_of=0, budget=budget, present=cast,
                                 scene_text=scene).pack
            for actor in cast}


def _problems(pack) -> set[tuple[str, SourceRef, str | None]]:
    return {(p.code, p.source, p.detail) for p in pack.problems}


def _lore(pack) -> list[str]:
    return [item.line for item in pack.lane("lore")]


def _table(store, chat, table_text: str, budget: Budget = BUDGET):
    return chat_lore(store.load(), chat, table_text=table_text, budget=budget).lore


def _bindings(chats, chat) -> list[tuple[str, SourceRef]]:
    return [(binding.role, binding.ref) for binding in chats.open_chat(chat).record.active_bindings()]


# -- every NPC in the scene ------------------------------------------------------------------------

def test_a_triggered_card_lore_entry_reaches_every_npc_in_the_scene(store, chats):
    land(store, encode(V3))
    land(store, encode(_anya()))
    started = chats.start_chat(Launch("character", MARA), attach=[(ANYA, "character"),
                                                                   (MARA_LORE, "lorebook")]).commit()
    packs = _packs(store, started, "The lighthouse is dark tonight, and the guild is restless.")
    assert len(packs) == 2
    for pack in packs.values():
        assert _lore(pack) == [LIGHTHOUSE]  # the disabled guild entry never triggers
        assert [item.audience for item in pack.lane("lore")] == ["common"]
        assert pack.problems == ()
    # a scene that shows no key triggers nothing, for anyone
    quiet = _packs(store, started, "The tide is late.")
    for pack in quiet.values():
        assert _lore(pack) == [] and pack.untriggered == ((MARA_LORE.at("r1"), 1),)
    table = _table(store, started.chat, "The lighthouse is dark tonight.")
    assert [(item.line, item.audience) for item in table.items] == [(LIGHTHOUSE, "common")]


def test_card_lore_reaches_an_npc_brief_as_scene_lore(store, chats):
    land(store, encode(V3))
    started = chats.start_chat(Launch("character", MARA), attach=[(MARA_LORE, "lorebook")]).commit()
    [mara] = started.actors
    [pack] = _packs(store, started, "The lighthouse is dark tonight.").values()
    snap = chat_snapshot(store.load(), started.chat).snapshot
    knowledge = actor_knowledge(_ledger(store, started.chat).knowledge, mara, as_of=0)
    brief = npc_brief(snapshot_actor_view(snap.instance(mara), snap, knowledge),
                      resolve_snapshot_voice(mara, snap, VOICES), secrets=(), context=pack, snapshot=snap)
    assert f"- {LIGHTHOUSE}" in brief.system


def test_a_constant_entry_is_in_every_pack_whatever_the_scene(store, chats):
    land(store, encode(V3))
    land(store, encode(_anya()))
    land(store, encode(HARBOR_RULES))
    started = chats.start_chat(Launch("character", MARA), attach=[(ANYA, "character"), (RULES, "lorebook")]).commit()
    for scene in ("", "Nothing at all happens."):
        for pack in _packs(store, started, scene).values():
            assert _lore(pack) == [TIDES]
            assert _problems(pack) == SKIPPED_RULES


def test_secondary_keys_selective_logic_and_insertion_order(store, chats):
    land(store, encode(V3))
    land(store, encode(HARBOR_RULES))
    started = chats.start_chat(Launch("character", MARA), attach=[(RULES, "lorebook")]).commit()

    def lore(scene: str) -> list[str]:
        [pack] = _packs(store, started, scene).values()
        assert _problems(pack) == SKIPPED_RULES  # a skipped entry is named, and never blanks the source
        return _lore(pack)

    assert CAPTAIN not in lore("The captain waits on the quay.")  # AND ANY: neither ship nor boat
    assert CAPTAIN in lore("The captain waits by her boat.")
    # constant first, then insertion order among equal hits; NOT ANY keeps the fair-weather entry
    assert lore("The inn is warm.") == [TIDES, FAIR, EARLY, LATE]
    assert lore("The inn shakes in the storm.") == [TIDES, EARLY, LATE]  # NOT ANY: storm shows
    assert BRIBES not in lore("The harbor master walks the docks.")  # AND ALL: no rain
    assert BRIBES in lore("The harbor master walks the docks in the rain.")
    assert FERRY in lore("The ferry is late.")  # NOT ALL: late, but no fog
    assert FERRY not in lore("The ferry is late in the fog.")
    everything = " ".join(lore("The inn, the fog, the foog and the captain on his ship."))
    assert "REGEX-TOKEN" not in everything and "DISABLED-TOKEN" not in everything
    assert "UNSAFE-TOKEN" not in everything


@pytest.mark.parametrize("logic, secondary", [
    (0, ["???"]),  # AND ANY with no usable key would need nothing
    (1, ["late", ""]),  # NOT ALL with a blank key
    (2, ["x" * 101]),  # NOT ANY whose exclusion is too long a key
    (3, ["storm", "dark\tnight"]),  # AND ALL that would need only the storm
    (0, [f"word{n}" for n in range(65)]),  # more secondary keys than 031 holds
    (3, "storm"),  # not a list: the importer drops it, which would leave the captain needing nothing
    (3, ["storm", 7]),  # a member that is not text: dropping it would leave AND ALL needing only the storm
])
def test_a_selective_entry_with_an_unusable_secondary_key_never_fires_and_is_named(store, chats, logic, secondary):
    book = {"spec": "lorebook_v3", "data": {"name": "Strict rules", "extensions": {}, "entries": [
        _entry(CAPTAIN, "captain", selective=True, secondary_keys=secondary, extensions={"selectiveLogic": logic}),
        _entry(TIDES, constant=True)]}}
    strict = land(store, encode(book)).report.main
    land(store, encode(V3))
    started = chats.start_chat(Launch("character", MARA), attach=[(strict, "lorebook")]).commit()
    for scene in ("The captain waits.", "The captain and the storm on a dark night, late, word1 x."):
        [pack] = _packs(store, started, scene).values()
        assert _lore(pack) == [TIDES]  # the rest of the book still plays
        assert _problems(pack) == {("skipped_entry", strict.at("r1"), "entry-0:secondary_keys")}
        assert CAPTAIN not in pack.render()


def test_an_insertion_order_too_large_for_a_float_is_clamped(store, chats):
    book = {"spec": "lorebook_v3", "data": {"name": "Huge orders", "extensions": {}, "entries": [
        _entry(LATE, "inn", order=10**400), _entry(EARLY, "inn", order=-(10**400)), _entry(FAIR, "inn")]}}
    huge = land(store, encode(book)).report.main
    land(store, encode(V3))
    started = chats.start_chat(Launch("character", MARA), attach=[(huge, "lorebook")]).commit()
    [pack] = _packs(store, started, "The inn is warm.").values()
    assert _lore(pack) == [EARLY, FAIR, LATE] and not pack.problems


def test_card_lore_sheds_under_the_lore_budget_in_rank_order(store, chats):
    land(store, encode(V3))
    land(store, encode(HARBOR_RULES))
    started = chats.start_chat(Launch("character", MARA), attach=[(RULES, "lorebook")]).commit()
    [pack] = _packs(store, started, "The inn is warm.", Budget(400, lane_caps={"lore": 30})).values()
    assert _lore(pack) == [TIDES, FAIR]
    assert [(o.lane, o.reason) for o in pack.omitted] == [("lore", "lane_cap"), ("lore", "lane_cap")]


# -- a scenario's own lorebook follows the scenario ------------------------------------------------

def _scenario(**changes: object) -> dict:
    return card(SCENARIO, **changes)


def _lore_of(store, chat, scene: str) -> list[str]:
    return [item.line for item in _table(store, chat, scene).items]


def test_a_launched_scenarios_own_lorebook_triggers_in_its_scenes(store, chats):
    land(store, encode(SCENARIO))
    land(store, encode(V3))
    started = chats.start_chat(Launch("scenario", STORM)).commit()
    assert started.actors == ()
    assert _bindings(chats, started.chat) == [("scenario", STORM.at("r1"))]  # selected with it, never bound
    table = _table(store, started.chat, "The storm worsens.")
    assert [(item.line, item.audience) for item in table.items] == [(STORMS, "common")]
    assert table.problems == ()  # the scenario record itself is read, and holds no lore
    _, etag = chats.read_chat(started.chat)
    joined = chats.attach_source(started.chat, MARA, "character", expected=etag).commit()
    [pack] = _packs(store, started, "The storm batters the inn.", actors=joined.actors).values()
    assert _lore(pack) == [STORMS] and pack.problems == ()


def test_a_scenario_attached_mid_chat_brings_its_lorebook(store, chats):
    land(store, encode(SCENARIO))
    land(store, encode(V3))
    started = chats.start_chat(Launch("character", MARA)).commit()
    assert _lore(_packs(store, started, "The storm batters the inn.")[started.actors[0]]) == []
    _, etag = chats.read_chat(started.chat)
    chats.attach_source(started.chat, STORM, "scenario", expected=etag).commit()
    [pack] = _packs(store, started, "The storm batters the inn.").values()
    assert _lore(pack) == [STORMS] and pack.problems == ()
    _, etag = chats.read_chat(started.chat)
    chats.detach_source(started.chat, STORM, "scenario", expected=etag).commit()
    [pack] = _packs(store, started, "The storm batters the inn.").values()
    assert _lore(pack) == []  # detaching the scenario takes its lore with it


def test_an_update_that_drops_the_lorebook_takes_its_lore_from_head_following_chats(store, chats):
    land(store, encode(SCENARIO))
    following = chats.start_chat(Launch("scenario", STORM), follow=True).commit()  # the explicit opt-in
    pinned = chats.start_chat(Launch("scenario", STORM.at("r1"))).commit()
    default = chats.start_chat(Launch("scenario", STORM)).commit()  # pinned by default: r1
    land(store, encode(_scenario(lorebook=None)), target=STORM.source)
    assert "lorebook" not in store.check_definition(STORM).envelope.payload  # r2 names none
    assert not isinstance(store.check_definition(STORM_LORE), str)  # the old book is kept, unnamed
    scene = "The storm worsens."
    assert _lore_of(store, following.chat, scene) == []
    assert ("binding_drift", STORM, "r1 -> r2") in {(p.code, p.source, p.detail)
                                                    for p in _table(store, following.chat, scene).problems}
    assert _lore_of(store, pinned.chat, scene) == [STORMS]  # r1 still names its book
    assert _lore_of(store, default.chat, scene) == [STORMS]  # so does the default chat, until a reviewed rebase
    fresh = chats.start_chat(Launch("scenario", STORM)).commit()
    assert _lore_of(store, fresh.chat, scene) == []


def test_an_update_that_renames_the_lorebook_moves_its_lore_with_the_scenario(store, chats):
    land(store, encode(_scenario(lorebook=None)), lorebook=encode(LOREBOOK))
    started = chats.start_chat(Launch("scenario", STORM), follow=True).commit()  # the explicit opt-in
    default = chats.start_chat(Launch("scenario", STORM)).commit()  # pinned to r1
    scene = "The fish market is shut."
    assert _lore_of(store, started.chat, scene) == _lore_of(store, default.chat, scene) == [MARKET]
    almanac = card(LOREBOOK, name="Harbor almanac", entries=[_entry(ALMANAC, "market")])
    land(store, encode(_scenario(lorebook=None)), lorebook=encode(almanac), target=STORM.source)
    assert _lore_of(store, started.chat, scene) == [ALMANAC]
    assert _lore_of(store, default.chat, scene) == [MARKET]  # r1 names the book it named


def test_a_chats_own_binding_of_the_scenarios_lorebook_is_kept(store, chats):
    land(store, encode(SCENARIO))
    started = chats.start_chat(Launch("scenario", STORM), attach=[(STORM_LORE.at("r1"), "lorebook")]).commit()
    assert _bindings(chats, started.chat) == [("scenario", STORM.at("r1")), ("lorebook", STORM_LORE.at("r1"))]
    assert _lore_of(store, started.chat, "The storm worsens.") == [STORMS]  # selected once


def _edit_scenario(store, lorebook: str) -> None:
    [path] = (store.root / "sources").rglob("scenario/storm-over-port-vell/r1.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    document["payload"]["lorebook"] = lorebook
    path.write_text(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def test_a_scenario_whose_lorebook_is_missing_still_launches_and_says_so(store, chats):
    land(store, encode(SCENARIO))
    land(store, encode(V3))
    gone = SourceRef(STORM.source, "lorebook", "gone", "r1")
    _edit_scenario(store, str(gone))
    started = chats.start_chat(Launch("scenario", STORM), attach=[(MARA, "character")]).commit()
    [pack] = _packs(store, started, "The storm worsens.").values()
    assert _lore(pack) == []
    assert {(p.code, p.source, p.detail) for p in pack.problems} == {("missing_source", gone, "missing_entity")}


def test_a_scenario_naming_something_else_as_its_lorebook_brings_no_lore_and_says_so(store, chats):
    land(store, encode(SCENARIO))
    land(store, encode(V3))
    _edit_scenario(store, "card:storm-over-port-vell/character/storm-over-port-vell@r1")
    started = chats.start_chat(Launch("scenario", STORM), attach=[(MARA, "character")]).commit()
    [mara] = started.actors
    built = actor_context(store.load(), started.chat, _ledger(store, started.chat), mara, as_of=0, budget=BUDGET,
                          scene_text="The storm worsens.")
    assert _lore(built.pack) == [] and built.pack.problems == ()
    assert built.problems == (f"invalid_scenario_lorebook:{STORM.at('r1')}",)
    director = chat_lore(store.load(), started.chat, table_text="The storm worsens.", budget=BUDGET)
    assert director.lore.items == () and director.problems == built.problems


def test_a_player_can_detach_a_scenarios_own_lorebook_and_have_it_back(store, chats):
    land(store, encode(SCENARIO))
    # head-following (the explicit opt-in), so the scenario update below reaches it
    started = chats.start_chat(Launch("scenario", STORM), follow=True).commit()
    scene = "The storm worsens."

    def change(method, *args):
        _, etag = chats.read_chat(started.chat)
        getattr(chats, method)(started.chat, *args, expected=etag).commit()

    change("detach_source", STORM_LORE, "lorebook")
    assert _lore_of(store, started.chat, scene) == []
    assert _bindings(chats, started.chat) == [("scenario", STORM)]
    # the opt-out sticks when the scenario and its book are updated
    longer = {**SCENARIO["lorebook"], "entries": [_entry(LONGER, "storm")]}
    land(store, encode(_scenario(lorebook=longer)), target=STORM.source)
    assert _lore_of(store, started.chat, scene) == []
    # attaching it again undoes the opt-out; detaching that binding opts out again
    change("attach_source", STORM_LORE, "lorebook")
    assert _lore_of(store, started.chat, scene) == [LONGER]
    change("detach_source", STORM_LORE, "lorebook")
    assert _lore_of(store, started.chat, scene) == []
    with pytest.raises(ChatModelError) as caught:  # a lorebook nothing implies is still not bound
        change("detach_source", GAZETTEER, "lorebook")
    assert caught.value.reason == "not_bound"
    # another chat of the same scenario still has its lore, and detaching the scenario removes it
    other = chats.start_chat(Launch("scenario", STORM)).commit()
    assert _lore_of(store, other.chat, scene) == [LONGER]
    _, etag = chats.read_chat(other.chat)
    chats.detach_source(other.chat, STORM, "scenario", expected=etag).commit()
    assert _lore_of(store, other.chat, scene) == []


# -- nothing leaks ------------------------------------------------------------------------------------

def test_an_unattached_lorebook_never_reaches_a_pack(store, chats):
    land(store, encode(V3))
    land(store, encode(_anya()))
    land(store, encode(LOREBOOK))
    land(store, encode(SCENARIO))
    assert not isinstance(store.check_definition(GAZETTEER), str)  # imported and loadable
    scene = "The lighthouse is dark, the fish market is shut and a storm is coming."
    bare = chats.start_chat(Launch("character", ANYA)).commit()
    [pack] = _packs(store, bare, scene).values()
    assert _lore(pack) == [] and pack.untriggered == () and pack.problems == ()
    assert _table(store, bare.chat, scene).items == ()
    # a scenario's chat selects its own lorebook and nothing else, and another chat's lorebook stays there
    stormy = chats.start_chat(Launch("scenario", STORM), attach=[(ANYA, "character")]).commit()
    chats.start_chat(Launch("lorebook", GAZETTEER)).commit()
    [pack] = _packs(store, stormy, scene).values()
    assert _lore(pack) == [STORMS]
    assert MARKET not in pack.render() and LIGHTHOUSE not in pack.render()
    [pack] = _packs(store, bare, scene).values()
    assert _lore(pack) == []
