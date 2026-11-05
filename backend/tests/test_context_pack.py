"""The pure context packer (Sprint 031): budgets, triggers, audience rules and the six-NPC cast.

Every fixture is synthetic (``context_fixtures``). No store, no model.
"""

from __future__ import annotations

import pickle

import pytest

import context_fixtures as f
from context_fixtures import (
    BESTIARY, LORE, LORE_SCHEMA, NPCS, PLOT, SCENARIO, SECRET, actor, card, entry, instance,
    knowledge,
)
from grimoire.context import (
    FORMAT, LANES, MAX_ENTRIES, Budget, ClaimReading, ContextError, ContextPack, LoreEntry, Origin, SceneWords,
    estimate_tokens, pack_actor_context, words,
)
from grimoire.context.pack import Candidate, pack
from grimoire.core import InstanceRef, SourceRef
from grimoire.core.snapshot import EffectiveSnapshot, SelectedSource, SourceSelection
from grimoire.graph import ClaimSet

EVERYONE = (*NPCS, "duke")
#: Text no NPC but the duke may ever see, and text nobody may.
HIDDEN = ("SECRET-TOKEN", "PLOT-TOKEN", "DIRECTOR-TOKEN", "UNCLASSIFIED-TOKEN", "SCENARIO-TOKEN",
          "UNCONFIRMED-TOKEN", "BESTIARY-TOKEN")
SCENE = "The ferry is late, and the lighthouse is dark again."


def reason(call) -> str:
    with pytest.raises(ContextError) as caught:
        call()
    return caught.value.reason


def selection(*extra: SelectedSource, cast=EVERYONE, world=None, lore=True) -> SourceSelection:
    sources = [SelectedSource(card(n), "character", actor=actor(n)) for n in cast]
    if lore:
        sources += [SelectedSource(LORE, "lorebook"), SelectedSource(SCENARIO, "scenario")]
    return SourceSelection(world, (*sources, *extra))


def snapshot(*extra: SelectedSource, index=None, cast=EVERYONE, world=None, lore=True) -> EffectiveSnapshot:
    return EffectiveSnapshot(index or f.index(), selection(*extra, cast=cast, world=world, lore=lore),
                             instances=[(instance(n), None) for n in cast])


def build(who: str = "mara", *, snap=None, ledger=None, budget=None, **kwargs) -> ContextPack:
    options = {"as_of": 9, "scene_text": SCENE, "readers": f.READERS, "unpack": f.unpack, **kwargs}
    return pack_actor_context(snap or snapshot(), ledger or knowledge(), actor(who), budget=budget or Budget(2000),
                              **options)


# -- budgets, entries and token estimates -----------------------------------------------------------

@pytest.mark.parametrize("make", [
    lambda: Budget(0), lambda: Budget(True), lambda: Budget(1_000_001), lambda: Budget(10, {"gossip": 1}),
    lambda: Budget(10, {"lore": 11}), lambda: Budget(10, {"lore": -1}), lambda: Budget(10, {"lore": True}),
    lambda: Budget(10, item_overhead=0), lambda: Budget(10, item_overhead=1001), lambda: Budget(10, "lore"),
    lambda: Budget(10, (("lore", 1), ("lore", 2))),
])
def test_an_invalid_budget_is_refused(make):
    assert reason(make) == "invalid_budget"


def test_a_budget_normalizes_its_caps_in_lane_order_and_is_hashable():
    by_mapping = Budget(100, {"lore": 10, "memory": 20})
    assert by_mapping == Budget(100, (("memory", 20), ("lore", 10)))
    assert by_mapping.lane_caps == (("memory", 20), ("lore", 10)) and hash(by_mapping) == hash(Budget(100, by_mapping.lane_caps))
    assert (by_mapping.cap("lore"), by_mapping.cap("portrayal")) == (10, 100)


@pytest.mark.parametrize("make", [
    lambda: LoreEntry("bad id!", "text"), lambda: LoreEntry("e", "x" * 8001), lambda: LoreEntry("e", "a\x00b"),
    lambda: LoreEntry("e", "t", "ferry"), lambda: LoreEntry("e", "t", ("ferry", "ferry")),
    lambda: LoreEntry("e", "t", ("ferry\nboat",)), lambda: LoreEntry("e", "t", ("...",)),
    lambda: LoreEntry("e", "t", ("",)), lambda: LoreEntry("e", "t", tuple(f"k{n}" for n in range(65))),
    lambda: LoreEntry("e", "t", ("k" * 101,)), lambda: LoreEntry("e", "t", constant=1),
    lambda: LoreEntry("e", "t", audience="players"), lambda: LoreEntry("e", "t", order=True),
    lambda: LoreEntry("e", "t", order=1_000_001), lambda: LoreEntry("e", "t", (3,)),
])
def test_an_invalid_lore_entry_is_refused(make):
    assert reason(make) == "invalid_entry"


def test_a_lore_entry_is_director_only_until_classified():
    assert LoreEntry("e", "t").audience == "director"
    assert LoreEntry("e", "t", ["Ferry", "salt pans"]).keys == ("Ferry", "salt pans")


def test_the_token_estimate_counts_utf8_bytes_by_thirds():
    assert [estimate_tokens(t) for t in ("", "a", "abc", "abcd", "é", "日本")] == [0, 1, 1, 2, 1, 2]


def test_a_context_error_pickles_with_its_reason():
    error = pickle.loads(pickle.dumps(ContextError("m", "invalid_budget", "detail")))
    assert (error.reason, error.detail, str(error)) == ("invalid_budget", "detail", "m")


# -- deterministic triggering -----------------------------------------------------------------------

def test_keys_match_whole_normalized_words():
    scene = SceneWords(["Mara's Black-Eyed Ferry crossed the ASHEN marsh.", "salt"])
    assert scene.contains("mara") and scene.contains("black eyed") and scene.contains("FERRY")
    assert scene.contains("Ashen marsh") and not scene.contains("ash") and not scene.contains("eyed ferry crossed x")
    assert words("Café") == words("Café")  # NFC
    assert not scene.contains("marsh salt")  # a key never spans two texts
    assert scene.hits(["ferry", "Ferry", "marsh", "lighthouse"]) == 2


# -- the greedy packer ------------------------------------------------------------------------------

def candidate(lane, rank, line):
    return Candidate(lane, (rank,), line, Origin("lore", name=f"x{rank}"))


def test_packing_is_first_fit_by_lane_then_rank_and_never_exceeds_the_budget():
    found = [candidate("lore", 2, "b" * 3), candidate("lore", 0, "a" * 30), candidate("memory", 0, "m" * 3),
             candidate("lore", 1, "c" * 300)]
    items, omitted = pack(found, Budget(30, {"lore": 20}, item_overhead=2), estimate_tokens)
    assert [(i.lane, i.line[0], i.tokens) for i in items] == [("memory", "m", 3), ("lore", "a", 12), ("lore", "b", 3)]
    assert [(o.origin.name, o.reason, o.tokens) for o in omitted] == [("x1", "over_budget", 102)]
    items, omitted = pack(found, Budget(200, {"lore": 20}, item_overhead=2), estimate_tokens)
    assert [(o.origin.name, o.reason) for o in omitted] == [("x1", "lane_cap")]
    assert sum(i.tokens for i in items) <= 200


@pytest.mark.parametrize("count", [lambda line: -1, lambda line: True, lambda line: 1.5, lambda line: "2"])
def test_a_bad_token_counter_is_refused(count):
    assert reason(lambda: pack([candidate("lore", 0, "x")], Budget(10), count)) == "invalid_count"


# -- the six-NPC cast: audience safety --------------------------------------------------------------

@pytest.mark.parametrize("who", NPCS)
def test_no_npc_sees_another_card_the_absent_duke_secret_or_any_director_text(who):
    built = build(who, present=[actor(n) for n in NPCS if n != who])
    text = built.render()
    assert not any(token in text for token in HIDDEN), text
    assert built.lines()[0] == f"{who.title()}: A Saltmarch local called {who.title()}."
    others = [n.title() for n in EVERYONE if n != who]
    assert not any(line.startswith(f"{other}:") for line in built.lines() for other in others)
    assert "speaks in short sentences" not in text  # voice evidence is 014/027's, excluded by default
    assert {item.origin.source for item in built.items if item.origin.source is not None} <= {card(who), LORE}
    assert all(item.origin.actor in (None, actor(who)) for item in built.items)
    assert built.tokens <= 2000 and built.problems == ()


def test_the_duke_sees_his_own_secret_and_plot_but_no_director_lore():
    text = build("duke").render()
    assert SECRET in text and f"You were told: {PLOT}" in text
    assert "DIRECTOR-TOKEN" not in text and "UNCLASSIFIED-TOKEN" not in text and "SCENARIO-TOKEN" not in text


def test_a_refuted_rumor_is_held_only_as_her_stance_and_its_true_sender_is_unnamed():
    built = build("mara")
    [item] = [i for i in built.items if i.origin.record == "p2"]
    assert (item.line, item.stance, item.origin.belief, item.origin.evidence) == (
        "You suspect: the duke paid off the harbor watch", "suspected", "b1", ("v2",))
    assert "refuted" not in built.render().casefold() and "Orrin" not in built.render()
    told = build("orrin")
    [sent] = [i for i in told.items if i.origin.record == "p2"]
    assert sent.line == "You told someone: the duke paid off the harbor watch" and "Mara" not in told.render()


def test_memory_is_as_of_the_moment():
    lines = lambda as_of: build("mara", as_of=as_of, scene_text="").lane("memory")
    assert [i.line for i in lines(0)] == []
    assert [i.line for i in lines(2)] == ["You were told: the duke paid off the harbor watch",
                                         "You were told: Anya and Tavi are cousins"]
    assert [i.stance for i in lines(3)] == ["suspected", None, None]  # p2 then p3 (both moment 3), then p5


def test_unconfirmed_evidence_and_events_never_enter_memory():
    assert build("orrin").lane("memory") == build("orrin", as_of=9).lane("memory")
    assert [i.origin.record for i in build("orrin").lane("memory")] == ["p2"]
    assert [i.line for i in build("anya").lane("memory")] == ["You perceived (witnessed): the ferry pulling out after dark"]


def test_present_cast_ranks_memory_about_them_first():
    alone = [i.origin.record for i in build("mara").lane("memory")]
    with_anya = [i.origin.record for i in build("mara", present=[actor("anya")]).lane("memory")]
    assert alone == ["p2", "p3", "p5"] and with_anya == ["p3", "p5", "p2"]  # p5 is about Anya too, but older
    assert [i.origin.record for i in build("mara", present=[actor("tavi")]).lane("memory")] == ["p5", "p2", "p3"]


def test_typed_claims_annotate_held_items_only():
    ledger = knowledge()
    claims = ClaimSet(ledger, f.claim_shapes())
    built = build("mara", ledger=ledger, claims=claims, present=[actor("tavi")])
    [item] = [i for i in built.items if i.origin.record == "p5"]
    assert item.claims == (ClaimReading("kin_of", "either", actor("tavi")),)
    assert all(i.claims == () for i in build("orrin", ledger=ledger, claims=claims, present=[actor("tavi")]).items)
    assert reason(lambda: build("mara", claims=ClaimSet(knowledge("c2", lambda n: InstanceRef("c2", "character", f"{n}-1")),
                                                        f.claim_shapes("c2", lambda n: InstanceRef("c2", "character", f"{n}-1"))))
                  ) == "campaign_mismatch"
    assert reason(lambda: build("mara", claims=dict(claims.shapes))) == "invalid_claims"


# -- lore ---------------------------------------------------------------------------------------------

def test_lore_is_common_constant_or_triggered_and_ranked_by_hits():
    built = build("orrin", scene_text="Ashen guild boats at the salt pans; the ferry waits.")
    assert [i.origin.name for i in built.lane("lore")] == ["tides", "guild", "ferry"]
    assert built.untriggered == ((LORE, 1),)  # lighthouse; director entries are never counted
    assert all(o.origin.name not in ("duke-plan", "unclassified", "opening") for o in built.omitted)


def test_lore_is_triggered_by_the_actors_own_memory_not_by_others():
    assert [i.origin.name for i in build("anya", scene_text="").lane("lore")] == ["tides", "ferry"]
    assert [i.origin.name for i in build("orrin", scene_text="").lane("lore")] == ["tides"]


def test_an_unbound_world_lorebook_is_never_read_even_with_the_world_selected():
    read = []

    def spy(payload):
        read.append(len(payload["entries"]))
        return f.read_lore(payload)

    built = build("mara", snap=snapshot(world="world:saltmarch"), readers={LORE_SCHEMA: spy})
    assert read == [6, 1] and "BESTIARY-TOKEN" not in built.render()
    bound = build("mara", snap=snapshot(SelectedSource(BESTIARY, "lorebook")),
                  readers={LORE_SCHEMA: lambda p: [LoreEntry(e["id"], e["text"], tuple(e["keys"]), audience="common")
                                                   for e in p["entries"]] if len(p["entries"]) == 40 else f.read_lore(p)},
                  budget=Budget(2000, {"lore": 120}))
    beasts = [i for i in bound.lane("lore") if i.origin.source == BESTIARY]
    assert beasts and sum(i.tokens for i in bound.lane("lore")) <= 120
    assert {o.reason for o in bound.omitted if o.origin.source == BESTIARY} == {"lane_cap"}


def test_a_character_card_is_never_read_for_lore():
    planted = [entry("card-lore", "CARD-TOKEN", constant=True)]
    built = build("mara", unpack=lambda d: (LORE_SCHEMA, {"entries": planted}))
    assert [i.line for i in built.lane("lore")].count("CARD-TOKEN") == 2  # the lorebook and scenario only
    assert reason(lambda: build("mara", readers={"grimoire.entity/0": f.read_lore})) == "invalid_readers"


def test_a_reader_cannot_change_the_view_it_reads():
    def vandal(payload):
        payload["entries"].clear()
        return []

    snap = snapshot()
    build("mara", snap=snap, readers={LORE_SCHEMA: vandal})
    assert [i.origin.name for i in build("mara", snap=snap).lane("lore")] == ["tides", "ferry", "lighthouse"]


@pytest.mark.parametrize("reader, code, detail", [
    (lambda p: p["missing"], "unreadable_entries", "KeyError"),
    (lambda p: [LoreEntry("bad id!", "t")], "unreadable_entries", "invalid_entry"),
    (lambda p: ["a string"], "unreadable_entries", "not_lore_entry"),
    (lambda p: [LoreEntry("a", "t"), LoreEntry("a", "u")], "unreadable_entries", "duplicate_entry"),
    (lambda p: [LoreEntry(f"e{n}", "t") for n in range(MAX_ENTRIES + 1)], "too_many_entries", str(MAX_ENTRIES + 1)),
])
def test_an_unreadable_source_contributes_nothing_and_is_reported(reader, code, detail):
    built = build("mara", readers={LORE_SCHEMA: reader})
    assert built.lane("lore") == ()
    assert {(p.code, p.source, p.detail) for p in built.problems} == {(code, LORE, detail), (code, SCENARIO, detail)}


def test_a_reader_bug_propagates():
    def broken(payload):
        raise RuntimeError("bug")

    with pytest.raises(RuntimeError):
        build("mara", readers={LORE_SCHEMA: broken})


def test_a_source_without_a_reader_or_record_is_reported_not_fatal():
    built = build("mara", readers={})
    assert built.lane("lore") == () and {(p.code, p.detail) for p in built.problems} == {("no_entry_reader", LORE_SCHEMA)}
    assert {p.code for p in build("mara", unpack=None).problems} == {"no_entry_reader"}
    ghost = SourceRef("catalog:saltmarch-cast", "lorebook", "ghost", "r1")
    missing = build("mara", snap=snapshot(SelectedSource(ghost, "lorebook")))
    assert [(p.code, p.source) for p in missing.problems] == [("missing_source", ghost)]


# -- shape, budget and determinism --------------------------------------------------------------------

def test_a_tight_budget_sheds_lore_and_memory_first_and_keeps_the_identity():
    built = build("mara", budget=Budget(20))
    assert built.lines() == ("Mara: A Saltmarch local called Mara.",) and built.tokens <= 20
    assert {o.reason for o in built.omitted} == {"over_budget"}
    assert reason(lambda: build("mara", budget=Budget(5))) == "budget_too_small"


def test_the_rendered_pack_never_estimates_above_its_total():
    for who in EVERYONE:
        built = build(who)
        assert estimate_tokens(built.render()) <= built.tokens
    assert reason(lambda: build("mara").lane("gossip")) == "invalid_lane"


def test_packing_is_deterministic_and_carries_its_format():
    assert build("mara") == build("mara") and FORMAT == "grimoire.context-pack/0" and LANES[0] == "portrayal"
    reordered = EffectiveSnapshot(f.index(), SourceSelection(None, tuple(reversed(selection().sources))),
                                  instances=[(instance(n), None) for n in EVERYONE])
    assert build("mara", snap=reordered).items == build("mara").items


def test_a_custom_counter_and_measurement_are_recorded():
    built = build("mara", count=lambda line: len(line.split()), measurement="counted")
    assert built.measurement == "counted" and built.items[0].tokens == 6 + 4


def test_excluded_sections_can_be_named():
    assert "voice-evidence: Mara speaks in short sentences." in build("mara", exclude_sections=()).lines()
    assert not any(line.startswith("occupation") for line in build("mara", exclude_sections={"occupation"}).lines())


# -- refused requests ---------------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs, code", [
    ({"as_of": -1}, "invalid_as_of"), ({"as_of": True}, "invalid_as_of"),
    ({"present": "anya"}, "invalid_present"), ({"present": [actor("anya"), actor("anya")]}, "invalid_present"),
    ({"present": ["anya"]}, "invalid_present"),
    ({"present": [InstanceRef("c2", "character", "anya-1")]}, "campaign_mismatch"),
    ({"scene_text": None}, "invalid_scene_text"), ({"scene_text": "x" * 100_001}, "invalid_scene_text"),
    ({"readers": {LORE_SCHEMA: "not callable"}}, "invalid_readers"), ({"readers": [LORE_SCHEMA]}, "invalid_readers"),
    ({"measurement": "exact"}, "invalid_measurement"), ({"exclude_sections": "voice"}, "invalid_sections"),
    ({"exclude_sections": [1]}, "invalid_sections"), ({"budget": 100}, "invalid_budget"),
])
def test_an_invalid_request_is_refused(kwargs, code):
    assert reason(lambda: build("mara", **kwargs)) == code


def test_the_audience_must_resolve_in_this_campaign_and_snapshot():
    snap = snapshot()
    assert reason(lambda: pack_actor_context(snap, knowledge(), "mara", as_of=1, budget=Budget(99))) == "invalid_audience"
    assert reason(lambda: pack_actor_context(snap, knowledge(), InstanceRef("c2", "character", "mara-1"), as_of=1,
                                             budget=Budget(99))) == "campaign_mismatch"
    assert reason(lambda: pack_actor_context(snap, knowledge(), actor("nobody"), as_of=1, budget=Budget(99))
                  ) == "unresolved_audience"
    with pytest.raises(TypeError):
        pack_actor_context(snap, "ledger", actor("mara"), as_of=1, budget=Budget(99))
    with pytest.raises(TypeError):
        pack_actor_context("snap", knowledge(), actor("mara"), as_of=1, budget=Budget(99))

