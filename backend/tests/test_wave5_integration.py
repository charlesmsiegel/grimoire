"""Seams between Wave 5 slices, each on a small synthetic library built through the slices' public services.

Wave 5's first playable runner (035) meets what the other Wave 5 slices write or decide. Each test builds
its own library under ``tmp_path``, plays or decides through the public services only, and where a seam
crosses an app restart it closes the store and opens it again:
- 036 -> 035: a SillyTavern chat imported by 036 and continued by the runner. What was never part of the
  conversation (an unchosen swipe, an edited line's pre-edit text, hidden and narrator ``is_system`` lines,
  the header's author's note) never reaches an NPC prompt. The conversation itself (the greeting, a
  whispered secret, a chosen swipe, an edited line) does not reach one either, but only until 035's
  milestone 2 reads ``transcripts/``: that is milestone 1's pinned limit (fail closed). Play writes no
  imported text outside the transcript, and never rewrites the transcript;
- 037/038 -> 035: a card imported with its art and lorebook plays in a world-less chat. The art placements
  the chat's actor inherits survive play and a restart; card lore reaches an NPC only through its own
  context pack, when its own window holds a key (a key whispered to one NPC triggers that NPC's lore
  alone), a disabled entry never, and no lore is written anywhere;
- 042 -> 035: a reviewed rebase re-pins one chat's actor to the card's newer revision, and its next turn
  briefs the NPC from that revision's description, in its unchanged voice, while still hearing its earlier
  turns; a chat
  pinned to the old revision in another campaign is untouched and keeps it;
- 043 -> 035: a World Change Set changes what a world-based chat's NPC is briefed with only once applied:
  never from a rejected set, nor from one aborted because the world changed under its review;
- 020 -> 039 -> 027: an NPC's intention, chosen from its stored knowledge, rides in its one ordinary
  generation call; the turn plans exactly one call per speaker, and another NPC's call carries none of it
  (at the library level, since 035's runner does not call 039 yet);
- 040 x 029 x 025: an unsuggested approach that is a fictional power is framed against an obstacle. A
  failure charges nothing and escalates its stake into a fact the next attempt must carry; one success
  overcomes the obstacle with its costs due and the power charged once, and both ledgers replay alike;
- 033 -> 041 -> 032 -> 020 -> 031: the stage, not the staged cast alone, is who hears. A whisper through a
  shut door is refused, although 033's cast would deliver it; once the door opens, speech is overheard in
  the hall, and each actor's context pack holds exactly what it heard, and how.

All content is synthetic; every model call is a scripted fake.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from grimoire.adjudication import ActionFrame, AdjudicationError, Decision
from grimoire.archaeologystore import StImporter
from grimoire.changesets import ChangeSet
from grimoire.changesetstore import ChangeSetConflict, ChangeSetLibrary
from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.comms import Message, Presence, TransmissionError, transmit
from grimoire.context import Budget
from grimoire.contextstore import actor_context
from grimoire.core import DIRECTOR, Belief, Moment, Proposition, Report, Subject
from grimoire.core.entity import EntityDefinition
from grimoire.core.facets import Agent, FacetSet, Physical
from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.core.inference import InferenceStage
from grimoire.core.model import ActorView
from grimoire.effective import chat_snapshot
from grimoire.evidencestore import EvidenceLibrary
from grimoire.generation import npc_brief
from grimoire.goals import choose
from grimoire.images import Shown
from grimoire.imagestore import ImageLibrary
from grimoire.obstacles import Approach, Cost, ObstacleError
from grimoire.rebasestore import SourceRebase
from grimoire.staging import Area, Placement, Stage, Way, apply, check_cast, deliver, open_way
from grimoire.store import LibraryStore, ProcessLocker, SourceManifest
from grimoire.testing.fileops import snapshot
from grimoire.voice import VoiceAnchor, VoiceCatalog, VoiceExample, VoiceProfile

from adjudication_fixtures import CHAT, IRIS, MARA, Q, SOLA, ability, build_library, moment
from card_fixtures import PORTRAIT, card, encode, land
from chat_fixtures import Clock
from generation_fixtures import BELLAMY, ODA, QUILL, Harness, say
from goals_fixtures import AGENDA, COLLECT_AIM, PRESENT, PRESS_ACTION, RULE, SELL, SELL_ACTION, TALES, TALES_TEXT
from graph_fixtures import instance_record
from obstacle_fixtures import ALARM, ArchiveTable, archive
from party_fixtures import party_library
from rebase_fixtures import VOICE as SPEAKS, WORLD, WREN, envelope, put, wren
from scene_fixtures import (
    ANA, ANYA, ANYA_CARD, MARA_LORE, MARA_VELL, TAMSIN, TAMSIN_CARD, VELL_EXAMPLE, VOICED, LIGHTHOUSE, Table, Tagged,
    secure,
)
from store_helpers import OWNER, owner_handle
from voice_fixtures import CAMPAIGN, SECRETS, emberfall

TAMSIN_EXAMPLE = "Every crate has a story, and a stamp."  # Tamsin Hale's card's own example line
DISABLED_LORE = next(entry["content"] for entry in VOICED["data"]["character_book"]["entries"]
                     if not entry["enabled"])  # Mara Vell's card's disabled lore entry


def the(prompts: list[str], marker: str) -> str:
    """The one prompt that carries ``marker`` (a speaker's own voice example)."""
    (found,) = [prompt for prompt in prompts if marker in prompt]
    return found


def _files(root: Path, *prefixes: str) -> dict[str, bytes | None]:
    return {path: data for path, data in snapshot(root).items() if path.startswith(prefixes)}


def _changed(before: dict, after: dict) -> set[str]:
    """Files added, removed or rewritten (directories are not state)."""
    return {path for path in before.keys() | after.keys()
            if before.get(path) != after.get(path) and (before.get(path) or after.get(path)) is not None}


# -- 036 -> 035 -----------------------------------------------------------------------------------

#: Every imported text is a distinct marker, so a prompt that held any of them is caught.
#: What was never in the conversation: no NPC prompt may ever hold it, before or after 035's milestone 2. The
#: author's note is in the page header, which docs/TRANSCRIPTS.md keeps verbatim and never interprets.
NEVER = {
    "unchosen": "An unchosen swipe about the tide tables.",
    "pre_edit": "A line as first written, before the edit.",
    "hidden": "A hidden aside the author kept from the model.",
    "narrator": "Rain hammers the harbor shutters.",
    "note": "Author's note: keep the smuggler evasive.",
}
#: The conversation itself: 035's milestone 2 will read it as history and decide who heard it.
UNTIL_M2 = {
    "greeting": "The lamps are low tonight, friend.",
    "secret": "The key is under the blue boat.",
    "chosen": "Noted, and never repeated.",
    "edited": "A line as the player edited it afterwards.",
}
IMPORTED = {**NEVER, **UNTIL_M2}
ST_ROWS = [
    {"user_name": "Anya Rook", "character_name": "Mara Vell", "chat_metadata": {"note_prompt": IMPORTED["note"]}},
    {"name": "Mara Vell", "is_user": False, "mes": IMPORTED["greeting"]},
    {"name": "Anya Rook", "is_user": True, "mes": f"*whispers* {IMPORTED['secret']}"},
    {"name": "Mara Vell", "is_user": False, "mes": IMPORTED["chosen"],
     "swipes": [IMPORTED["chosen"], IMPORTED["unchosen"]], "swipe_id": 0},
    {"name": "Mara Vell", "is_user": False, "mes": IMPORTED["edited"], "swipes": [IMPORTED["pre_edit"]], "swipe_id": 0},
    {"name": "Mara Vell", "is_user": False, "is_system": True, "mes": IMPORTED["hidden"]},
    {"name": "Narrator", "is_user": False, "is_system": True, "mes": IMPORTED["narrator"],
     "extra": {"type": "narrator"}},
]


def _imported_st_chat(root: Path):
    """037's Mara Vell and Anya Rook cards, and a SillyTavern chat between them imported by 036; secured."""
    data = ("\n".join(json.dumps(row) for row in ST_ROWS) + "\n").encode("utf-8")
    with LibraryStore.create(root) as store:
        for document in (VOICED, ANYA_CARD):
            land(store, encode(document))
        importer = StImporter(store, owner_handle(), clock=Clock(), owner=OWNER)
        roles = {"character": MARA_VELL, "user": ANYA}  # the narrator stays a label
        plan = importer.plan(data, speakers={s.id: roles[s.role] for s in importer.scan(data).speakers
                                             if s.role in roles})
        plan.commit()
    secure(root)
    return plan.chat, plan.actors


def test_an_imported_chat_continued_by_the_runner_never_shows_an_npc_its_unspoken_lines_nor_yet_its_history(
        tmp_path):
    root = tmp_path / "lib"
    chat, actors = _imported_st_chat(root)
    pages = f"campaigns/{chat.campaign_id}/transcripts/"
    transcript = _files(root, pages)
    assert any(path.endswith(".json") for path in transcript)  # 036 wrote the history it keeps
    asked, later = '"Where did I say the key was?"', '"And the tide tables?"'

    with Table(root, [say("I keep no such ledger.")]) as table:
        runner, ana = table.runner, table.player()
        runner.seat(chat, ANA)
        scene = runner.open(chat)
        # The actors 036 bound. Scene.problems is not pinned here: 035's ``imported_history_unread`` there is
        # the flag milestone 2 reads, and whether it is raised yet is no part of this seam.
        assert set(scene.cast) == set(actors.values())
        (mara,) = scene.npcs
        assert scene.persona in scene.players and scene.records == ()  # no imported line is a turn
        runner.post(chat, ana, asked)
        take = runner.generate(chat, ana)
        assert [line.actor for line in take.lines] == [mara]
        (prompt,) = table.harness.prompts()
        assert VELL_EXAMPLE in prompt and "Where did I say the key was?" in prompt  # her voice, the live post
        assert [name for name, text in NEVER.items() if text in prompt] == []
        assert [name for name, text in UNTIL_M2.items() if text in prompt] == []  # 035 M1's pinned limit
        runner.keep(chat, ana, 1)

    with Table(root, [say("Charts drown, like ledgers.")]) as table:  # a restart
        runner, ana = table.runner, table.player()
        runner.post(chat, ana, later)
        runner.generate(chat, ana)
        (prompt,) = table.harness.prompts()
        assert "I keep no such ledger." in prompt  # what the runner's own turns said is heard
        assert [name for name, text in NEVER.items() if text in prompt] == []
        assert [name for name, text in UNTIL_M2.items() if text in prompt] == []  # 035 M1's pinned limit
    assert _files(root, pages) == transcript  # play never rewrites the imported history
    # and no imported text was copied anywhere else in the campaign: not into a turn, evidence or other record
    played = {path: data for path, data in _files(root, f"campaigns/{chat.campaign_id}/").items()
              if not path.startswith(pages)}
    assert [(name, path) for name, text in IMPORTED.items() for path, data in played.items()
            if text.encode("utf-8") in (data or b"")] == []


# -- 037/038 -> 035 -------------------------------------------------------------------------------

SMILE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR synthetic smile"
DOCKS_JPEG = b"\xff\xd8\xff\xe0" + b"synthetic docks"
SMILE = "expr.default.smile"


def _data_uri(data: bytes, media: str) -> str:
    return f"data:{media};base64,{base64.b64encode(data).decode('ascii')}"


ART = [{"type": "icon", "uri": "ccdefault:", "name": "main", "ext": "png"},
       {"type": "emotion", "uri": _data_uri(SMILE_PNG, "image/png"), "name": "smile", "ext": "png"},
       {"type": "background", "uri": _data_uri(DOCKS_JPEG, "image/jpeg"), "name": "docks", "ext": "jpg"}]


def _art(store: LibraryStore, actor: InstanceRef):
    images = ImageLibrary(store, owner_handle("sources", "campaigns", "assets"))
    shown = images.effective_placements(actor)
    return {slot.slot: (slot.layer, images.read_image_bytes(slot.image)) for slot in shown.slots
            if isinstance(slot, Shown)}


def test_a_card_with_art_and_lore_plays_world_less_its_art_survives_and_lore_reaches_only_the_pack(tmp_path):
    root = tmp_path / "lib"
    with LibraryStore.create(root) as store:
        planned = land(store, encode(card(VOICED, assets=ART)), portrait=PORTRAIT)  # 037 with 038's art
        assert {(entry.slot, entry.status) for entry in planned.report.art} == {
            ("avatar", "placed"), (SMILE, "placed"), ("background", "placed")}
        for document in (ANYA_CARD, TAMSIN_CARD):
            land(store, encode(document))
        started = ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Tagged("c"), owner=OWNER).start_chat(
            Launch("character", MARA_VELL), attach=[(MARA_LORE, "lorebook"), (TAMSIN, "character")],
            persona=ANYA).commit()
        chat, (mara, tamsin, anya) = started.chat, started.actors
        assert chat_snapshot(store.load(), chat).scope.world is None
        art = _art(store, mara)
        # the chat's actor inherits the card's pictures as source placements
        assert art == {"avatar": ("source", PORTRAIT), SMILE: ("source", SMILE_PNG),
                       "background": ("source", DOCKS_JPEG)}
    secure(root)
    before = _files(root, "sources/", "assets/", "campaigns/")

    script = [say("Late, and short of coin."), say("Stamped and filed."), say("Not tonight."), say("Dark a year now.")]
    with Table(root, script) as table:
        runner, ana = table.runner, table.player()
        runner.seat(chat, ANA)
        assert set(runner.open(chat).npcs) == {mara, tamsin}
        runner.post(chat, ana, '"The guild is late."')  # a key of a disabled entry, said aloud
        runner.generate(chat, ana)
        prompts = table.harness.prompts()
        assert all(DISABLED_LORE not in prompt and LIGHTHOUSE not in prompt for prompt in prompts)
        runner.keep(chat, ana, 1)
        whisper = runner.post(chat, ana, 'I whisper to Tamsin Hale, "Is the lighthouse lit?"')
        assert set(whisper.post.audience) == {anya, tamsin}
        runner.generate(chat, ana)
        prompts = table.harness.prompts()[2:]
        # common lore triggers on what each NPC itself heard: Tamsin heard the key, Mara did not
        assert LIGHTHOUSE in the(prompts, TAMSIN_EXAMPLE) and LIGHTHOUSE not in the(prompts, VELL_EXAMPLE)
        runner.keep(chat, ana, 1)

    with LibraryStore.open(root, writable=False) as store:  # a restart
        assert _art(store, mara) == art  # play and the restart left every placement as imported
        after = _files(root, "sources/", "assets/", "campaigns/")
        turns = f"campaigns/{chat.campaign_id}/turns/"
        changed = _changed(before, after)
        assert changed and all(path.startswith(turns) for path in changed)  # play wrote turns, and nothing else
        # lore lives in the packs alone: no turn file, evidence or other record holds its text
        assert all(LIGHTHOUSE.encode() not in (after[path] or b"") for path in changed)


# -- 042 -> 035 -----------------------------------------------------------------------------------

OLD_DESCRIPTION = VOICED["data"]["description"]
NEW_DESCRIPTION = "A retired smuggler who now keeps the harbor's lamps."


def test_a_reviewed_rebase_briefs_the_npc_from_the_new_revision_and_leaves_another_chat_on_the_old(tmp_path):
    root = tmp_path / "lib"
    with LibraryStore.create(root) as store:
        for document in (VOICED, ANYA_CARD):
            land(store, encode(document))
        chats = ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Tagged("c"), owner=OWNER)
        moving, staying = (chats.start_chat(Launch("character", MARA_VELL.at("r1")), persona=ANYA).commit()
                           for _ in range(2))  # two world-less campaigns, both pinned to r1
        update = land(store, encode(card(VOICED, description=NEW_DESCRIPTION)), target="card:mara-vell")
        assert update.report.status == "update"  # r2 is the head; pinned chats are never rewritten by it
    secure(root)
    a, b = moving.chat, staying.chat
    actor = moving.actors[0]

    with Table(root, [say("First words at r1."), say("Other words at r1."), say("Words at r2."),
                      say("Still at r1.")]) as table:
        runner, ana = table.runner, table.player()
        for chat in (a, b):
            runner.seat(chat, ANA)
            runner.post(chat, ana, '"Who are you now?"')
            runner.generate(chat, ana)
            runner.keep(chat, ana, 1)
        assert all(OLD_DESCRIPTION in p and VELL_EXAMPLE in p and NEW_DESCRIPTION not in p
                   for p in table.harness.prompts())

        b_before = _files(root, f"campaigns/{b.campaign_id}/")
        a_turns = _files(root, f"campaigns/{a.campaign_id}/turns/")
        rebase = SourceRebase(table.store, table.owner, clock=Clock())
        review = rebase.review(actor)
        assert [(row.field, row.status) for row in review.rows] == [("description", "upstream")]
        assert review.repinned == (a,) and review.history.chats == (a,)
        rebase.apply(review, {"description": "take"}).commit()
        assert _files(root, f"campaigns/{b.campaign_id}/") == b_before  # another campaign is not written
        assert _files(root, f"campaigns/{a.campaign_id}/turns/") == a_turns  # history is kept as played

        for chat in (a, b):
            runner.post(chat, ana, '"And now?"')
            runner.generate(chat, ana)
        rebased, untouched_b = table.harness.prompts()[2:]
        assert NEW_DESCRIPTION in rebased and OLD_DESCRIPTION not in rebased  # r2's description
        assert VELL_EXAMPLE in rebased  # in the voice both revisions share
        assert "First words at r1." in rebased  # it still heard its earlier turn
        assert OLD_DESCRIPTION in untouched_b and VELL_EXAMPLE in untouched_b and NEW_DESCRIPTION not in untouched_b
        assert runner.open(a).problems == () and runner.open(b).problems == ()


# -- 043 -> 035 -----------------------------------------------------------------------------------

TOMA = SourceRef(WORLD, "character", "toma")
SAILOR = "Tide's turning; mind the rope."
#: Wren's authored voice, for every revision a change set may write (a profile names one revision).
WREN_VOICES = VoiceCatalog([VoiceProfile(WREN.at(revision), (VoiceAnchor("salt", "cadence", "Short, salty lines."),),
                                         (VoiceExample("rope", SAILOR),)) for revision in ("r1", "r2")])
WREN_BEFORE = "A harbor pilot."  # rebase_fixtures' Wren
WREN_AFTER = "A harbor pilot who lost an eye to the storm."


def _world_chat(root: Path):
    """The harbor world with Wren and Toma (the persona), and a chat of its scope attached to that world."""
    with LibraryStore.create(root) as store:
        toma = EntityDefinition(TOMA.at("r1"), "Toma", "individual", FacetSet([Physical(), Agent(), SPEAKS]),
                                "A deckhand.", {"sections": {}})
        put(store, SourceManifest(WORLD, OWNER, "Harbor", {"character/wren": "r1", "character/toma": "r1"}),
            envelope(wren()), envelope(toma))
        chats = ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Tagged("w"), owner=OWNER)
        started = chats.start_chat(Launch("character", WREN), persona=TOMA).commit()  # Wren follows the head
        _, etag = chats.read_scope(started.scope)
        chats.attach_world(started.scope, WORLD, expected=etag).commit()
        assert chat_snapshot(store.load(), started.chat).scope.world.source == WORLD
    secure(root)
    return started.chat


def _revise_wren(lib: ChangeSetLibrary, changeset_id: str) -> ChangeSet:
    _, head, _ = lib.head(WORLD, "character", "wren")
    change = lib.revise(WORLD, "character", "wren", {**head.payload, "description": WREN_AFTER},
                        evidence=("session 3: the storm",))
    return ChangeSet(changeset_id, WORLD, "Wren's eye", "human:owner", (change,))


def test_a_world_change_set_reaches_a_world_based_chats_npc_only_once_applied(tmp_path):
    root = tmp_path / "lib"
    chat = _world_chat(root)
    world = f"sources/{WORLD.replace(':', '/')}/"
    with Table(root, [say(f"Turn {n}.") for n in range(1, 5)], voices=WREN_VOICES) as table:
        runner, ana = table.runner, table.player()
        runner.seat(chat, ANA)

        def turn(text: str) -> str:
            runner.post(chat, ana, text)
            runner.generate(chat, ana)
            runner.keep(chat, ana, 1)
            prompt = table.harness.prompts()[-1]
            assert SAILOR in prompt  # Wren speaks, in Wren's voice
            return prompt

        lib = ChangeSetLibrary(table.store, table.owner)
        before = _files(root, world)
        rejected = lib.review(_revise_wren(lib, "cs-rejected"))
        assert rejected.applicable
        lib.reject(rejected)
        prompt = turn('"Wren?"')
        assert WREN_BEFORE in prompt and WREN_AFTER not in prompt  # a rejected set changes nothing
        assert _files(root, world) == before

        stale = lib.review(_revise_wren(lib, "cs-stale"))
        docks = lib.create(WORLD, "location", "docks", {"traits": {"spatial": {}}}, name="Docks", evidence=("s3",))
        lib.apply(lib.review(ChangeSet("cs-docks", WORLD, "The docks", "human:owner", (docks,))))  # the world moves on
        moved = _files(root, world)
        with pytest.raises(ChangeSetConflict) as caught:
            lib.apply(stale)
        assert [conflict.reason for conflict in caught.value.conflicts] == ["manifest_changed"]
        assert _files(root, world) == moved
        prompt = turn('"Wren, are you hurt?"')
        assert WREN_BEFORE in prompt and WREN_AFTER not in prompt  # an aborted set changes nothing either

        lib.apply(lib.review(_revise_wren(lib, "cs-applied")))
        prompt = turn('"Wren, your eye!"')
        assert WREN_AFTER in prompt and WREN_BEFORE not in prompt  # the next turn is briefed from the new head
        assert "Turn 2." in prompt  # and the chat's history stands as played


# -- 020 -> 039 -> 027 ----------------------------------------------------------------------------

CONTROLS, VOICES = emberfall()[3], emberfall()[4]


def _goals_library(root: Path) -> None:
    """The guild dinner's campaign with Quill's warning to Oda, and Oda's belief in it, stored by 020."""
    with LibraryStore.create(root) as store:
        txn = store.transaction()
        for ref in PRESENT:
            txn.put(instance_record(ref), None)
        txn.commit(owner_handle())
        tales = Proposition(CAMPAIGN, TALES, TALES_TEXT, Moment(1), "director",
                            about=(Subject("instance", str(BELLAMY)),))
        warned = Report(CAMPAIGN, "quill-warns-oda", ODA, TALES, "told", Moment(2), "scene:dinner/t1", confirmed=True,
                        sender=QUILL, attributed_to=QUILL)
        believed = Belief(CAMPAIGN, "oda-on-bellamy", ODA, TALES, "believed", Moment(2), DIRECTOR, ("quill-warns-oda",))
        EvidenceLibrary(store, owner_handle(), owner=OWNER).append(CAMPAIGN, (tales, warned, believed),
                                                                    controls=CONTROLS).commit()


@pytest.mark.parametrize("as_of, approach", [(1, "sell-ledger"), (2, "press-wren")])
def test_an_intention_chosen_from_stored_knowledge_rides_in_the_npcs_one_call(tmp_path, as_of, approach):
    root = tmp_path / "lib"
    _goals_library(root)
    with LibraryStore.open(root, writable=False) as store:  # a reopen: the choice reads only what 020 stored
        evidence = EvidenceLibrary(store)
        oda, bellamy = (evidence.actor_knowledge(CAMPAIGN, who, as_of=as_of) for who in (ODA, BELLAMY))
        choice = choose(AGENDA, oda, present=PRESENT, controls=CONTROLS, desired=SELL)
        assert choice.approach.approach_id == approach  # before the warning Oda sells; after it, Oda refuses
        assert choice.desire.followed is (approach == "sell-ledger")

        harness = Harness([say("Wren. Your tally. Now."), say("Oh darling, how grim.")])
        plan = harness.plan(CONTROLS, present=tuple(ref.instance_id for ref in PRESENT),
                            speakers=(ODA.instance_id, BELLAMY.instance_id))
        meter = harness.meter(plan)
        briefs = [npc_brief(ActorView(CAMPAIGN, ODA.instance_id, "Oda", "The counting-house keeper.", knowledge=oda),
                            VOICES[ODA.instance_id], secrets=SECRETS, intention=choice),
                  npc_brief(ActorView(CAMPAIGN, BELLAMY.instance_id, "Bellamy", "A guest.", knowledge=bellamy),
                            VOICES[BELLAMY.instance_id], secrets=SECRETS)]
        outcome = harness.runtime.run_turn(meter, briefs, controls=CONTROLS)
        assert outcome.status == "complete" and [line.actor for line in outcome.lines] == [ODA, BELLAMY]
        oda_prompt, bellamy_prompt = harness.prompts()
        chosen = SELL_ACTION if approach == "sell-ledger" else PRESS_ACTION
        assert COLLECT_AIM in oda_prompt and chosen in oda_prompt
        assert (RULE in oda_prompt) is (approach == "press-wren")  # a refusal is shown only while it refuses
        assert not any(text in bellamy_prompt for text in (COLLECT_AIM, SELL_ACTION, PRESS_ACTION, RULE, TALES_TEXT))
        summary = meter.summary()  # one ordinary call per speaker: the goal added none
        assert summary.baseline.attempts == 2 and summary.optional.attempts == 0
        assert set(summary.by_stage) == {InferenceStage.NPC_GENERATION}
        assert set(summary.by_actor) == {ODA.instance_id, BELLAMY.instance_id}


# -- 040 x 029 x 025 ------------------------------------------------------------------------------

#: Not one of the archive's suggestions: Sola, a director NPC, hides the door with Sola's own light bending.
VEIL = Approach("veil", "Sola bends the lantern light until the door stands in shadow.", frozenset({"alarm"}),
                (Cost("glare", "The lantern burns strangely bright for the rest of the night."),))


def _veil(library, attempt_id: str, sequence: int, factors=None) -> ActionFrame:
    power = ability(SOLA, ("light", "concealment"), attempt_id=attempt_id)
    found = library.required(Q, "archive", VEIL, sequence) if factors is None else factors
    return ActionFrame(attempt_id, SOLA, CHAT, moment(sequence, f"t{sequence}"), "Hide the door in shadow.", found,
                       (ALARM.stake,), "labeller:test", power)


def test_a_power_used_as_a_novel_approach_escalates_on_failure_and_is_charged_once_on_success(tmp_path):
    locker = ProcessLocker()
    store = LibraryStore.create(tmp_path / "lib", locker=locker)
    build_library(store)
    table = ArchiveTable(store).seat()
    try:
        library = table.obstacles()
        library.record(archive())
        first = _veil(library, "director:veil-1", 10)
        assert [factor.factor_id for factor in first.factors] == ["watch"]  # the blanket fact binds a novel approach
        library.frame("archive", first, approach=VEIL)
        assert library.adjudication.assess(Q, "director:veil-1").classification == "uncertain"
        library.adjudication.decide(Q, "director:veil-1", Decision("failure", "adjudicated", {"watch"}, {"alarm"}))
        with pytest.raises(AdjudicationError) as caught:
            library.adjudication.ability_use(Q, "director:veil-1")
        assert caught.value.reason == "action_failed"  # a failed power charges nothing
        assert library.state(Q, "archive").status == "open"  # the obstacle stays open

        # The stake escalated: the next attempt meets the alarm, and cannot leave it out.
        with pytest.raises(ObstacleError) as missing:
            library.frame("archive", _veil(library, "director:veil-2", 11, factors=first.factors), approach=VEIL)
        assert missing.value.reason == "missing_fact"
        second = _veil(library, "director:veil-2", 11)
        assert [factor.factor_id for factor in second.factors] == ["alarm", "watch"]
        library.frame("archive", second, approach=VEIL)
        library.adjudication.decide(Q, "director:veil-2", Decision("success", "adjudicated", {"#ability"}))
        state = library.state(Q, "archive")
        assert (state.status, [a.status for a in state.attempts]) == ("overcome", ["failed", "overcame"])
        assert [(c.kind, c.consequence_id) for c in state.due()] == [("stake", "alarm"), ("cost", "glare")]
        used = library.adjudication.ability_use(Q, "director:veil-2")
        assert [cost.id for _, cost in used.charges] == ["eye_strain"]  # the power's own price, once
    finally:
        table.close()
        store.close()

    reopened = ArchiveTable(LibraryStore.open(tmp_path / "lib", locker=locker))  # both ledgers replay alike
    try:
        assert reopened.obstacles().state(Q, "archive") == state
        assert reopened.library().ability_use(Q, "director:veil-2") == used
    finally:
        reopened.close()
        reopened.store.close()


# -- 033 -> 041 -> 032 -> 020 -> 031 --------------------------------------------------------------

P = "scene:the-study"
SECRET_WHISPER, CALL, LATE_CALL = "The key is in my boot.", "Bar the door.", "Too late, it is open."


def test_the_stage_not_the_cast_decides_who_hears_and_each_pack_holds_what_it_heard(tmp_path):
    root = tmp_path / "lib"
    with LibraryStore.create(root) as store:
        build_library(store)  # the q-1 chat: Mara, Iris and Sola
        cast = party_library(store).stage(Q, "the-study", (MARA, IRIS, SOLA), as_of=1)  # 033: all three present
        stage = Stage(Q, "the-study", 1, (Area("study"), Area("hall")),
                      (Way("study-door", ("study", "hall"), "closed"),), (MARA, IRIS, SOLA),
                      (Placement(MARA, area="study"), Placement(IRIS, area="study"), Placement(SOLA, area="hall")), ())
        check_cast(stage, cast)  # 041's stage is of exactly 033's cast
        stage = stage.at(2)
        whisper = Message("m-1", MARA, "whisper", SECRET_WHISPER, (SOLA,))
        # 033's cast alone would carry the whisper to Sola; the stage knows a shut door is between them
        assert transmit(whisper, Presence(Q, "the-study", cast.present), sequence=2, provenance=P).receivers == (SOLA,)
        with pytest.raises(TransmissionError) as caught:
            deliver(stage, whisper, sequence=2, provenance=P)
        assert caught.value.reason == "addressee_absent"
        shut = deliver(stage, Message("m-2", MARA, "speech", CALL), sequence=2, provenance=P)
        stage = apply(stage, open_way(IRIS, "study-door"), sequence=3, provenance=P)
        opened = deliver(stage.at(4), Message("m-3", MARA, "speech", LATE_CALL), sequence=4, provenance=P)
        assert [(r.holder, r.channel) for r in shut.reports] == [(IRIS, "told")]
        assert {(r.holder, r.channel) for r in opened.reports} == {(IRIS, "told"), (SOLA, "overheard")}
        evidence = EvidenceLibrary(store, owner_handle(), owner=OWNER)
        for sent in (shut, opened):
            evidence.append(Q, sent.records(), chat=CHAT).commit()

    with LibraryStore.open(root, writable=False) as store:  # a reopen
        ledger = EvidenceLibrary(store).read(Q, requested_by=DIRECTOR)
        view = store.load()
        packs = {who: actor_context(view, CHAT, ledger, who, as_of=4, budget=Budget(600), present=cast.present)
                 for who in (MARA, IRIS, SOLA)}
        assert all(context.problems == () for context in packs.values())
        rendered = {who.instance_id: context.pack.render().splitlines() for who, context in packs.items()}
        assert rendered == {
            "mara-1": ["You are Mara.", f"You told someone: {LATE_CALL}", f"You told someone: {CALL}"],
            "iris-1": ["You are Iris.", f"You were told: {LATE_CALL}", f"You were told: {CALL}"],
            "sola-1": ["You are Sola.", f"You heard (overheard): {LATE_CALL}"],  # never the whisper, nor the call
        }
