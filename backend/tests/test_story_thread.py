"""081's story explorer over a pure ledger: one early promise followed through ten later scenes to its outcome.

The proving scenario: in scene 1 Mara promises Seraphine to bring the brass key back before the thaw. Over the
next scenes a rumour reaches Winifred, Winifred sees Mara sell the key, canon rules the promise refuted on that
event, and Seraphine still believes it in scene 9. Unrelated threads (a storm, a secret) run alongside. The
thread finds the promise by its words, by its outcome's words and by a character, grades every entry
(confirmed, belief, proposal), names when and on what canon decided it, and names the contradictions, each with
the scene it is in. The table's thread holds only what the player's own character knows. Synthetic names only.
"""

from __future__ import annotations

import pytest

from grimoire.core import (Belief, ChatRef, ControlAssignment, Event, InstanceRef, Knowledge, Moment, Perception,
                           Proposition, Report, Ruling, Subject)
from grimoire.core.knowledge import KnowledgeError
from grimoire.core.perspective import player_knowledge
from grimoire.story import search, table_search, table_thread, thread

C = "saltmarch-run"
PLAYER = "human:owner"
MARA, SERAPHINE, WINIFRED = (InstanceRef(C, "character", name) for name in ("mara", "seraphine", "winifred"))
CONTROLS = (ControlAssignment(C, "seraphine", PLAYER), ControlAssignment(C, "mara", "director"),
            ControlAssignment(C, "winifred", "director"))
PROMISE = "Mara will bring the brass key back to the lighthouse before the thaw"
CHAT = ChatRef(C, "lighthouse")


def at(scene: int) -> Moment:
    """Scene ``n`` of the story, ten sequences apart, each with its own turn."""
    return Moment(scene * 10, f"scene-{scene:02d}", f"turn-{scene:02d}")


def ledger(*extra) -> Knowledge:
    records = [
        Proposition(C, "p-key", PROMISE, at(1), "director",
                    about=(Subject("instance", str(MARA)), Subject("instance", str(SERAPHINE)))),
        Report(C, "r-promise", SERAPHINE, "p-key", "told", at(1), "director", confirmed=True, sender=MARA,
               attributed_to=MARA, addressed=True, sent_at=at(1)),
        Belief(C, "b-trust", SERAPHINE, "p-key", "believed", at(1), PLAYER, ("r-promise",)),
        # a secret only Winifred hears of: never in the table's view, not even that it exists
        Proposition(C, "p-secret", "The harbourmaster keeps a second ledger", at(2), "director"),
        Report(C, "r-secret", WINIFRED, "p-secret", "overheard", at(2), "director", confirmed=True),
        Report(C, "r-rumour", WINIFRED, "p-key", "rumour", at(3), "director", confirmed=True),
        Belief(C, "b-doubt", WINIFRED, "p-key", "suspected", at(4), "director", ("r-rumour",)),
        # an unrelated thread Seraphine is part of
        Proposition(C, "p-storm", "A storm will close the harbour", at(5), "director"),
        Report(C, "r-storm", SERAPHINE, "p-storm", "told", at(5), "director", confirmed=True),
        Event(C, "e-sale", "Mara sells the brass key to the harbourmaster", at(6), "accepted", (MARA, WINIFRED)),
        Perception(C, "w-sale", WINIFRED, "e-sale", "seen", "Mara handing over a key for coin", at(6), "director",
                   confirmed=True),
        Belief(C, "b-saw", WINIFRED, "p-key", "rejected", at(6), "director", ("w-sale", "r-rumour"),
               supersedes="b-doubt"),
        Ruling(C, "ru-broken", "p-key", "refuted", at(7), "director", grounds=(Subject("event", "e-sale"),)),
        Event(C, "e-storm", "The storm closes the harbour", at(8), "accepted", (SERAPHINE,)),
        Ruling(C, "ru-storm", "p-storm", "established", at(8), "director", grounds=(Subject("event", "e-storm"),)),
        Belief(C, "b-still", SERAPHINE, "p-key", "believed", at(9), PLAYER, ("r-promise",), supersedes="b-trust"),
        Event(C, "e-thaw", "The thaw comes and the lighthouse stays locked", at(11), "accepted", ()),
        *extra,
    ]
    by_kind = {kind: [record for record in records if type(record) is kind]
               for kind in (Event, Proposition, Ruling, Belief)}
    evidence = [record for record in records if type(record) in (Report, Perception)]
    return Knowledge(C, events=by_kind[Event], propositions=by_kind[Proposition], rulings=by_kind[Ruling],
                     evidence=evidence, beliefs=by_kind[Belief])


NOW = 10_000


def kinds(found) -> list[tuple[str, str, str]]:
    return [(entry.kind, entry.record, entry.grade) for entry in found.entries]


def test_the_promise_is_followed_from_the_scene_it_was_made_to_its_outcome():
    found = thread(ledger(), "p-key", as_of=NOW, origins={"proposition:p-key": CHAT, "evidence:r-promise": CHAT})
    assert found.proposition.text == PROMISE
    assert kinds(found) == [
        ("stated", "proposition:p-key", "proposal"), ("report", "evidence:r-promise", "proposal"),
        ("belief", "belief:b-trust", "belief"),
        ("report", "evidence:r-rumour", "proposal"), ("belief", "belief:b-doubt", "belief"),
        ("event", "event:e-sale", "confirmed"), ("belief", "belief:b-saw", "belief"),
        ("ruling", "ruling:ru-broken", "confirmed"), ("belief", "belief:b-still", "belief")]
    # canon decided it in scene 7, on the sale in scene 6; the storm and the thaw are other threads
    assert found.outcome.standing == "refuted" and found.outcome.ruling == "ru-broken"
    assert found.outcome.moment == at(7) and found.outcome.grounds == ("event:e-sale",)
    sale = next(entry for entry in found.entries if entry.kind == "event")
    assert sale.text == "Mara sells the brass key to the harbourmaster" and sale.moment.scene_id == "scene-06"
    # each belief is its holder's, current or superseded, never the standing
    beliefs = {entry.record: (entry.holder, entry.stance, entry.current) for entry in found.entries
               if entry.kind == "belief"}
    assert beliefs == {"belief:b-trust": (SERAPHINE, "believed", False), "belief:b-doubt": (WINIFRED, "suspected", False),
                       "belief:b-saw": (WINIFRED, "rejected", True), "belief:b-still": (SERAPHINE, "believed", True)}
    promise = found.entries[1]
    assert (promise.holder, promise.sender, promise.attributed_to, promise.chat) == (SERAPHINE, MARA, MARA, CHAT)
    # Seraphine still believes what canon refuted: named with the ruling and the scene she believes it in
    (against,) = found.contradictions
    assert against.kind == "belief_against_canon" and against.records == ("ruling:ru-broken", "belief:b-still")
    assert against.moment.scene_id == "scene-09"


def test_every_scene_the_thread_passes_through_and_those_involving_one_character():
    found = thread(ledger(), "p-key", as_of=NOW, origins={"proposition:p-key": CHAT, "evidence:r-promise": CHAT})
    labels = [(scene.chat, scene.scene, scene.turns) for scene in found.scenes]
    # scene 1's records came from the lighthouse chat; the rest carry only their scene labels
    assert labels == [(CHAT, "scene-01", ("turn-01",)), (None, "scene-01", ("turn-01",)),
                      (None, "scene-03", ("turn-03",)), (None, "scene-04", ("turn-04",)),
                      (None, "scene-06", ("turn-06",)), (None, "scene-07", ("turn-07",)),
                      (None, "scene-09", ("turn-09",))]
    assert [scene.scene for scene in found.scenes_with(SERAPHINE)] == ["scene-01", "scene-01", "scene-09"]
    assert [scene.scene for scene in found.scenes_with(WINIFRED)] == ["scene-03", "scene-04", "scene-06"]
    assert found.actors == (MARA, SERAPHINE, WINIFRED)


def test_a_thread_read_as_of_an_earlier_scene_shows_only_the_story_so_far():
    found = thread(ledger(), "p-key", as_of=at(5).sequence)
    assert [entry.record for entry in found.entries] == ["proposition:p-key", "evidence:r-promise", "belief:b-trust",
                                                          "evidence:r-rumour", "belief:b-doubt"]
    assert found.outcome.standing == "reported" and found.outcome.ruling is None and found.outcome.moment is None
    assert found.contradictions == ()
    with pytest.raises(KnowledgeError) as caught:
        thread(ledger(), "p-storm", as_of=at(4).sequence)  # not yet in the story
    assert caught.value.reason == "unknown_proposition"
    with pytest.raises(KnowledgeError):
        thread(ledger(), "p-nothing", as_of=NOW)


def test_search_finds_the_promise_by_its_words_its_outcome_and_a_character_oldest_first():
    knowledge = ledger()

    def ids(**args) -> list[str]:
        hits, total = search(knowledge, as_of=NOW, **args)
        assert total == len(hits)
        return [hit.proposition.proposition_id for hit in hits]

    assert ids(query="brass KEY") == ["p-key"]
    assert ids(query="harbourmaster") == ["p-key", "p-secret"]  # p-key through its outcome's event
    assert ids(actor=SERAPHINE) == ["p-key", "p-storm"]
    assert ids(actor=WINIFRED, query="key") == ["p-key"]
    assert ids() == ["p-key", "p-secret", "p-storm"]
    assert ids(query="  ") == ids()
    (hit,), _ = search(knowledge, as_of=NOW, query="brass")
    assert (hit.first, hit.standing, hit.decided, hit.scenes, hit.contradictions) == (at(1), "refuted", at(7), 6, 1)
    hits, total = search(knowledge, as_of=NOW, limit=1)
    assert len(hits) == 1 and total == 3
    for bad in ({"limit": 0}, {"limit": 201}, {"query": 3}, {"query": "x" * 201}):
        with pytest.raises(ValueError):
            search(knowledge, as_of=NOW, **bad)


def test_a_ruling_reversed_later_and_a_ruling_on_a_revoked_event_are_contradictions():
    kept = ledger(Event(C, "e-return", "Mara buys the key back and returns it", at(12), "accepted", (MARA,)),
                  Ruling(C, "ru-kept", "p-key", "established", at(12), "director",
                         grounds=(Subject("event", "e-return"),)))
    found = thread(kept, "p-key", as_of=NOW, revoked={"e-return"})
    assert found.outcome.standing == "established" and found.outcome.revoked_grounds == ("e-return",)
    returned = next(entry for entry in found.entries if entry.record == "event:e-return")
    assert returned.grade == "revoked"  # the bridge outlives its effect's revocation: never shown as canon
    # Winifred's rejection (scene 6) is now against canon; Seraphine's belief no longer is
    assert [(item.kind, item.records) for item in found.contradictions] == [
        ("belief_against_canon", ("ruling:ru-kept", "belief:b-saw")),
        ("reversed_ruling", ("ruling:ru-broken", "ruling:ru-kept")),
        ("revoked_ground", ("ruling:ru-kept", "event:e-return"))]
    assert thread(kept, "p-key", as_of=at(11).sequence).outcome.standing == "refuted"


def test_a_report_sent_but_not_yet_received_is_shown_as_its_send():
    late = ledger(Report(C, "r-letter", SERAPHINE, "p-key", "letter", at(12), "director", confirmed=True,
                         sender=WINIFRED, addressed=True, sent_at=at(10)))
    found = thread(late, "p-key", as_of=at(11).sequence)
    (letter,) = [entry for entry in found.entries if entry.record == "evidence:r-letter"]
    assert letter.kind == "sent" and letter.moment == at(10)
    assert thread(late, "p-key", as_of=NOW).entries[-1].kind == "report"


def test_the_table_thread_holds_only_what_the_players_character_knows():
    knowledge = ledger()
    player = player_knowledge(knowledge, PLAYER, CONTROLS, as_of=NOW)
    found = table_thread(knowledge, player, "p-key", origins={"evidence:r-promise": CHAT, "ruling:ru-broken": CHAT})
    assert kinds(found) == [("report", "evidence:r-promise", "proposal"), ("belief", "belief:b-trust", "belief"),
                            ("belief", "belief:b-still", "belief")]
    assert found.outcome is None and found.contradictions == ()
    promise = found.entries[0]
    assert promise.sender is None and promise.attributed_to == MARA and promise.chat == CHAT
    assert [scene.scene for scene in found.scenes] == ["scene-01", "scene-01", "scene-09"]
    # the storm is hers too; the secret is not, and neither is the sale that ended the promise
    hits, total = table_search(knowledge, player)
    assert [hit.proposition.proposition_id for hit in hits] == ["p-key", "p-storm"] and total == 2
    assert table_search(knowledge, player, query="harbourmaster") == ((), 0)
    assert all(hit.standing is None and hit.decided is None for hit in hits)
    with pytest.raises(KnowledgeError):
        table_thread(knowledge, player, "p-secret")


def test_an_inherited_belief_is_marked_never_shown_as_the_players_own():
    # Seraphine's first belief was written by the director before the player took her over
    knowledge = ledger(Proposition(C, "p-map", "The map is a forgery", at(1), "director"),
                       Report(C, "r-map", SERAPHINE, "p-map", "told", at(1), "director", confirmed=True),
                       Belief(C, "b-map", SERAPHINE, "p-map", "suspected", at(1), "director", ("r-map",)))
    player = player_knowledge(knowledge, PLAYER, CONTROLS, as_of=NOW)
    (inherited,) = [entry for entry in table_thread(knowledge, player, "p-map").entries if entry.kind == "belief"]
    assert inherited.inherited is True and inherited.current is True
