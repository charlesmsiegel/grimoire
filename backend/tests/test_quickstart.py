"""052: Start Chat, from one selection to an open world-less chat, with no world and no wizard.

Synthetic cards (``card_fixtures``), imported through 037's real importer; every model call is a scripted
fake. Each test starts from a fresh copy of one built library.
"""

from __future__ import annotations

import shutil

import pytest

from card_fixtures import SCENARIO, V3, card, encode, land
from chat_fixtures import Clock
from scene_fixtures import (
    ANA, ANYA, ANYA_CARD, BOB, LIGHTHOUSE, MARA_LORE, MARA_VELL, STORM, TAMSIN, TAMSIN_CARD, VELL_EXAMPLE, VOICED,
    Table, Tagged, say, secure,
)
from store_helpers import OWNER

from grimoire.cardstore import CardImporter
from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.core.identity import SourceRef
from grimoire.quickstart import QuickStart, QuickStartRefused, Selection
from grimoire.scene import NARRATOR, SceneRefused
from grimoire.security import Grant, Permission, ResourcePath
from grimoire.store import LibraryStore

PREMISE = "A lighthouse keeper finds a letter addressed to {{user}}, dated tomorrow."


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    folder = tmp_path_factory.mktemp("built") / "cards"
    with LibraryStore.create(folder) as store:
        for document in (VOICED, ANYA_CARD, TAMSIN_CARD, SCENARIO):
            land(store, encode(document))
    secure(folder)
    return folder


@pytest.fixture
def folder(built, tmp_path):
    copy = tmp_path / "cards"
    shutil.copytree(built, copy)
    return copy


def quick(table: Table, tag: str = "qs") -> QuickStart:
    chats = ChatLibrary(table.store, table.owner, clock=Clock(), new_id=Tagged(tag), owner=OWNER)
    return QuickStart(chats, table.runner)


def reimport(table: Table, document: dict, **options):
    """A card import while the app is open: the owner may propose there too (037's plan asks to)."""
    for scope in ("sources", "assets"):
        table.authority.add_grant(Grant(f"owner-propose-{scope}", OWNER, frozenset({Permission.PROPOSE}),
                                        ResourcePath.parse(scope), OWNER))
    planned = CardImporter(table.store, table.owner, owner=OWNER, clock=Clock()).plan(encode(document), **options)
    planned.commit({})
    return planned


def chat_count(table: Table) -> int:
    return len(quick(table, "count").chats.navigation().all_chats())


# -- the proving scenario ----------------------------------------------------------------------------

def test_a_one_line_premise_starts_play_at_once_and_survives_a_restart(folder):
    with Table(folder, [say("The letter is in your own hand.")]) as table:
        ana = table.player()
        started = quick(table).start(Selection.from_premise(PREMISE), ana, persona=ANYA)
        assert table.harness.prompts() == []  # Start Chat runs no model: the opening is the premise itself
        assert started.unfinished is None and started.opening.open is False
        (line,) = started.opening.takes[0].lines
        assert line.speaker == NARRATOR and line.text == PREMISE.replace("{{user}}", "Anya Rook")
        chats = quick(table).chats
        record, _ = chats.read_chat(started.chat)
        view = chats.open_chat(started.chat)
        assert view.scope is not None and view.scope.world is None and view.scope.title is None  # no world, no setup
        assert record.persona is not None and record.persona.ref.pinned
        runner = table.runner
        runner.post(started.chat, ana, '"Who delivered it?"')
        take = runner.generate(started.chat, ana, director=True)
        assert take.lines[0].text == "The letter is in your own hand."
        runner.keep(started.chat, ana, take.take)

    with Table(folder, [say("Footprints lead to the sea.")]) as table:  # a restart
        ana, runner = table.player(), table.runner
        scene = runner.open(started.chat)
        assert [r.number for r in scene.records] == [1, 2] and not scene.problems
        runner.post(started.chat, ana, '"I follow them."')
        runner.generate(started.chat, ana, director=True)
        assert "Who delivered it?" in table.harness.prompts()[0]
        assert quick(table).chats.read_chat(started.chat)[0].launch.premise == PREMISE  # the prompt stays as written


def test_adding_a_card_later_keeps_the_premise_and_grants_no_old_lines(folder):
    with Table(folder, [say("Papers first.")]) as table:
        ana, qs, runner = table.player(), quick(table), table.runner
        started = qs.start(Selection.from_premise(PREMISE), ana, persona=ANYA)
        runner.post(started.chat, ana, '"I read the letter aloud."')
        runner.pass_turn(started.chat, ana)
        before = qs.chats.read_chat(started.chat)[0]
        qs.add_card(started.chat, TAMSIN, ana)
        after = qs.chats.read_chat(started.chat)[0]
        assert after.launch == before.launch and after.ref == before.ref  # the original prompt is untouched
        (binding,) = [b for b in after.active_bindings("character") if b.ref.entity_key == TAMSIN.entity_key]
        assert binding.ref.pinned and binding.actor in runner.open(started.chat).npcs  # seated with the director
        runner.post(started.chat, ana, '"Who keeps the customs house?"')
        runner.generate(started.chat, ana)
        (prompt,) = table.harness.prompts()
        assert "I read the letter aloud." not in prompt and "Who keeps the customs house?" in prompt
        assert not list(folder.glob(f"campaigns/{started.chat.campaign_id}/events*"))  # nothing became canon


# -- a character, a bundle, a scenario ------------------------------------------------------------------

def test_a_character_offers_its_greetings_and_opens_with_the_chosen_one(folder):
    with Table(folder) as table:
        ana, qs = table.player(), quick(table)
        options = qs.options(Selection.character(MARA_VELL))
        assert [(o.opening_id, o.group_only) for o in options.openings] == [
            ("first", False), ("alt-1", False), ("group-1", True)]
        assert options.default_opening == "first" and options.related == ()
        assert options.sources == (MARA_VELL.at("r1"),) and options.lorebook == MARA_LORE.at("r1")
        started = qs.start(Selection.character(MARA_VELL), ana, persona=ANYA, opening="alt-1")
        (line,) = started.opening.takes[0].lines
        assert line.text == "The tide is late tonight." and line.speaker == "Mara Vell"
        record = qs.chats.read_chat(started.chat)[0]
        assert [b.ref for b in record.active_bindings()] == [MARA_VELL.at("r1"), ANYA.at("r1")]  # pinned
        assert not record.active_bindings("lorebook")  # a standalone character brings no lore
        assert qs.options(Selection.character(MARA_VELL)).related == (started.chat,)
        assert table.harness.prompts() == []


def test_a_bundle_binds_the_cards_own_lorebook_and_shows_one_chat_under_both(folder):
    with Table(folder, [say("Dark, and the harbor pays for it.")]) as table:
        ana, qs = table.player(), quick(table)
        started = qs.start(Selection.bundle(MARA_VELL), ana, persona=ANYA)
        assert started.opening.takes[0].lines[0].text == "You came back. I didn't think you would."
        table.runner.post(started.chat, ana, '"Is the lighthouse still dark?"')
        table.runner.generate(started.chat, ana)
        (prompt,) = table.harness.prompts()
        assert LIGHTHOUSE in prompt and VELL_EXAMPLE in prompt
        nav = qs.chats.navigation()
        assert nav.by_source(MARA_VELL) == nav.by_source(MARA_LORE) == nav.by_source(ANYA) == (started.chat,)
        assert nav.all_chats() == (started.chat,)  # one canonical chat, never a copy per filter


def test_a_scenario_has_no_fabricated_npc_and_offers_its_openings(folder):
    with Table(folder) as table:
        ana, qs = table.player(), quick(table)
        options = qs.options(Selection.scenario(STORM))
        assert [o.opening_id for o in options.openings] == ["opening-1", "opening-2"] and options.lorebook is None
        started = qs.start(Selection.scenario(STORM), ana, persona=ANYA, opening="opening-2")
        line = started.opening.takes[0].lines[0]
        assert line.speaker == NARRATOR and line.text == "The lamps gutter; someone screams upstairs."
        scene = table.runner.open(started.chat)
        assert scene.npcs == () and scene.players == (scene.persona,)


def test_a_card_with_no_greeting_starts_empty_and_the_player_speaks_first(folder):
    with Table(folder) as table:
        ana, qs = table.player(), quick(table)
        started = qs.start(Selection.scenario(STORM), ana, persona=ANYA)  # baseline: opens
        assert started.opening is not None
        silent = card(V3, name="Quiet Ilse", first_mes=None, alternate_greetings=None, group_only_greetings=None,
                      character_book=None)
        reimport(table, silent)
        ilse = SourceRef("card:quiet-ilse", "character", "quiet-ilse")
        started = qs.start(Selection.character(ilse), ana, persona=ANYA)
        assert started.opening is None and started.unfinished is None
        assert table.runner.open(started.chat).records == ()
        table.runner.post(started.chat, ana, '"Hello?"')


# -- pin by default --------------------------------------------------------------------------------

def test_a_started_chat_stays_on_its_revision_when_the_card_is_imported_again(folder):
    with Table(folder) as table:
        ana, qs = table.player(), quick(table)
        first = qs.start(Selection.character(MARA_VELL), ana, persona=ANYA)
        update = card(VOICED, first_mes="Back again, are we?")
        planned = reimport(table, update, target="card:mara-vell")
        assert planned.report.status == "update"
        second = qs.start(Selection.character(MARA_VELL), ana, persona=ANYA)
        assert second.opening.takes[0].lines[0].text == "Back again, are we?"
        old = qs.chats.read_chat(first.chat)[0]
        assert old.launch.ref == MARA_VELL.at("r1") and old.active_bindings("character")[0].ref == MARA_VELL.at("r1")
        assert second.chat != first.chat and second.chat.campaign_id != first.chat.campaign_id
        assert table.runner.open(first.chat).records[0].takes[0].lines[0].text.startswith("You came back")


# -- refusals write nothing ----------------------------------------------------------------------------

def test_a_refused_start_writes_nothing(folder):
    with Table(folder) as table:
        ana, qs = table.player(), quick(table)
        with pytest.raises(QuickStartRefused) as refused:
            qs.start(Selection.character(MARA_VELL), ana, persona=ANYA, opening="alt-9")
        assert refused.value.reason == "unknown_opening"
        with pytest.raises(QuickStartRefused) as refused:
            qs.start(Selection.character(SourceRef("card:nobody", "character", "nobody")), ana, persona=ANYA)
        assert refused.value.reason == "missing_source"
        with pytest.raises(QuickStartRefused) as refused:
            qs.start(Selection.bundle(TAMSIN), ana, persona=ANYA)  # Tamsin's card embeds no lorebook
        assert refused.value.reason == "no_lorebook"
        with pytest.raises(QuickStartRefused) as refused:
            qs.start(Selection.from_premise(PREMISE), table.player(BOB))  # no persona: only the owner is at table
        assert refused.value.reason == "not_at_table"
        with pytest.raises(QuickStartRefused) as refused:
            qs.start(Selection.from_premise(PREMISE), table.authority.handle_for(ANA, trusted=False), persona=ANYA)
        assert refused.value.reason == "not_authenticated"
        assert chat_count(table) == 0


@pytest.mark.parametrize("bad", [
    lambda: Selection("lorebook", MARA_LORE), lambda: Selection.character(STORM),
    lambda: Selection.from_premise("   "), lambda: Selection("premise", MARA_VELL, premise="x"),
    lambda: Selection("character", MARA_VELL, MARA_LORE), lambda: Selection.bundle(MARA_VELL, ANYA),
])
def test_a_selection_is_exactly_one_launch(bad):
    with pytest.raises(QuickStartRefused) as refused:
        bad()
    assert refused.value.reason == "invalid_selection"


# -- an interrupted launch, Continue ---------------------------------------------------------------------

def test_an_interrupted_launch_is_finished_by_continue_never_started_twice(folder, monkeypatch):
    with Table(folder) as table:
        ana, qs = table.player(), quick(table)

        def lost(*_args, **_kwargs):
            raise SceneRefused("turn_changed", "lost a race")

        monkeypatch.setattr(table.runner, "begin", lost)
        started = qs.start(Selection.character(MARA_VELL), ana, persona=ANYA)
        assert started.unfinished == "turn_changed" and started.opening is None
        monkeypatch.undo()
        (related,) = qs.options(Selection.character(MARA_VELL)).related
        resumed = qs.continue_chat(related, ana)
        assert resumed.chat == started.chat and resumed.opening is not None and resumed.unfinished is None
        again = qs.continue_chat(started.chat, ana)  # a chat under way is left as it is
        assert again.opening == resumed.opening
        assert [r.number for r in table.runner.open(started.chat).records] == [1] and chat_count(table) == 1


def test_continue_from_another_player_is_refused_and_writes_nothing(folder):
    with Table(folder) as table:
        ana, qs = table.player(), quick(table)
        started = qs.start(Selection.character(MARA_VELL), ana, persona=ANYA)
        before = table.runner.open(started.chat).records
        resumed = qs.continue_chat(started.chat, table.player(BOB))
        assert resumed.opening == before[0]  # reading the opening needs no seat
        assert table.runner.open(started.chat).records == before


# -- a source gone ---------------------------------------------------------------------------------------

def test_a_missing_card_offers_detach_and_keeps_the_chat(folder):
    with Table(folder) as table:
        ana, qs = table.player(), quick(table)
        started = qs.start(Selection.bundle(MARA_VELL), ana, persona=ANYA)
        assert qs.repairs(started.chat) == ()
        story = table.runner.open(started.chat).records
    shutil.rmtree(folder / "sources" / "card" / "mara-vell")  # deleted while the app was closed
    with Table(folder) as table:
        ana, qs = table.player(), quick(table)
        repairs = qs.repairs(started.chat)
        assert {(r.field, r.ref, r.choices) for r in repairs} == {
            ("launch", str(MARA_VELL.at("r1")), ()),
            ("bindings.character", str(MARA_VELL.at("r1")), ("detach",)),
            ("bindings.lorebook", str(MARA_LORE.at("r1")), ("detach",)),
        }
        lore = next(r for r in repairs if r.role == "lorebook")
        qs.detach_broken(started.chat, lore)
        assert {r.field for r in qs.repairs(started.chat)} == {"launch", "bindings.character"}
        with pytest.raises(QuickStartRefused) as refused:
            qs.detach_broken(started.chat, lore)
        assert refused.value.reason == "not_broken"
        assert table.runner.open(started.chat).records == story  # the story stays
        table.runner.post(started.chat, ana, '"Mara?"')


# -- naming it a campaign --------------------------------------------------------------------------------

def test_naming_the_chat_a_campaign_changes_no_identity(folder):
    with Table(folder) as table:
        ana, qs = table.player(), quick(table)
        started = qs.start(Selection.bundle(MARA_VELL), ana, persona=ANYA)
        record = qs.chats.read_chat(started.chat)[0]
        qs.name_campaign(started.chat, "The Drowned Light")
        assert qs.chats.read_scope(started.chat.scope)[0].title == "The Drowned Light"
        after = qs.chats.read_chat(started.chat)[0]
        assert (after.ref, after.launch, after.roster, after.bindings) == (
            record.ref, record.launch, record.roster, record.bindings)
        assert qs.chats.navigation().by_scope(started.chat.campaign_id) == (started.chat,)


# -- 035's begin: choosing the opening ---------------------------------------------------------------

def test_begin_opens_with_a_chosen_greeting_and_refuses_one_the_chat_lacks(folder):
    with Table(folder) as table:
        ana, qs, runner = table.player(), quick(table), table.runner
        chats = qs.chats
        chat = chats.start_chat(Launch("character", MARA_VELL.at("r1")), persona=ANYA.at("r1")).commit().chat
        runner.seat(chat, ANA)
        for bad, reason in (("alt-9", "unknown_opening"), ("opening-1", "unknown_opening"), ("", "invalid_field")):
            with pytest.raises(SceneRefused) as refused:
                runner.begin(chat, ana, opening=bad)
            assert refused.value.reason == reason
        assert runner.open(chat).records == ()  # a refusal writes nothing
        record = runner.begin(chat, ana, opening="group-1")  # group-only is never a default, but may be chosen
        assert record.takes[0].lines[0].text == "Everyone, sit down and keep your hands visible."
        record = runner.begin(chat, ana)  # asked again with no choice: the first greeting, as before
        assert record.takes[-1].lines[0].text == "You came back. I didn't think you would."
