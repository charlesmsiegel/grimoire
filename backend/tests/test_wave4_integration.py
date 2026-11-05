"""Seams between Wave 4 slices, on one synthetic library built once and reopened by every test.

The library holds both kinds of import at once: 026's reviewed V0.2.1 import (the Saltmarch world, the
Drowned Ledger campaign with Mara and Seraphine as NPCs and Winifred as the PC, and its scene as a chat)
and 037's card import (Mara Vell, started as a world-less 107 chat). Winifred's party (033) travels with
Mara and keeps Seraphine in reserve. A second, world-less Emberfall chat seats the 029 cast (Mara as
``human:ana``'s PC, Iris and Sola with light bending) beside Oda and Bellamy, whose voices are 014's.
029 opens the library's durable authority last, so from then on every slice writes with a handle that
authority issued: the tests open it and use its owner handle.

Each test crosses a seam the slices meet at, through their public services only:
- 026 x 037: both imports are playable sources after a reopen, with no load problems;
- 033 -> 032 -> 020 -> 031: the staged cast is the comms presence, so a reserved member hears nothing,
  and only the receiver's context pack holds the delivered line;
- 034 x 033 x 026 x 028: a gift between party members who are imported actors, and two identified
  devices that each forge a longsword offline keep two after sync;
- 032 -> 020 -> 022 -> 027: one scripted call per speaking NPC, each briefed from its own stored
  knowledge; 026's and 037's actors have no voice profile and are refused (a documented gap);
- 029 -> 021 -> 030: an adjudicated power is charged once through an acceptance that crashed after
  the commit point with its staging removed (docs/RECOVERY.md's proving scenario), then completed.

All content is synthetic; every model call is a scripted fake.
"""

from __future__ import annotations

import atexit
import contextlib
import functools
import shutil
import tempfile
from hashlib import sha256
from pathlib import Path

import pytest

from grimoire.adjudication import Decision
from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.comms import Message, Presence, transmit
from grimoire.context import Budget
from grimoire.contextstore import actor_context
from grimoire.core import DIRECTOR, Moment, snapshot_actor_view
from grimoire.core.ability_use import AbilityRuling
from grimoire.core.entity_codec import ENTITY_SCHEMA, definition_to_payload, instance_from_payload
from grimoire.core.identity import AcceptedRef, ChatRef, InstanceRef, SourceRef
from grimoire.effective import chat_snapshot
from grimoire.effects import ActedLine, EffectHoldings, EffectProposal
from grimoire.effectstore import EffectRecovery, TurnEffectsLibrary
from grimoire.evidencestore import EvidenceLibrary
from grimoire.generation import npc_brief, plan_generation_turn, resolve_snapshot_voice
from grimoire.holdings import Line, Operation
from grimoire.import_v1 import chat_id_of
from grimoire.ledgers import SovereigntyLedger
from grimoire.party import PartyRefused, presence
from grimoire.possessions import Step
from grimoire.security import Grant, Permission, ResourcePath
from grimoire.store import LibraryStore, ProcessLocker, RecordEnvelope, SourceManifest
from grimoire.sync import identify, sync
from grimoire.testing import Crash, RootedFileOps, SimulatedCrash, Target
from grimoire.voice import MissingVoice

from ability_fixtures import EMBER, person
from adjudication_fixtures import CHAT, IRIS_DEF, MARA_DEF, Q, SOLA, SOLA_DEF, Table, ability, moment, power_frame
from card_fixtures import PORTRAIT, V3, encode, land
from chat_fixtures import Clock, Ids
from generation_fixtures import Harness, say
from holdings_fixtures import holdings_library
from image_fixtures import handle as full_handle
from import_fixtures import ARCHIVE, CAMPAIGN_ID, Env
from party_fixtures import party_library
from possession_fixtures import LONGSWORD, make, op, possession_library, seed as seed_arms
from store_helpers import OWNER, owner_handle
from v1_fixture import CAMPAIGN
from voice_fixtures import SECRETS, emberfall

# -- the cast -------------------------------------------------------------------------------------

LIB, DESKTOP, PHONE = "lib", "desktop", "phone"
FULL = ("sources", "campaigns", "assets", "kept")
WINIFRED, MARA, SERAPHINE = (InstanceRef(CAMPAIGN_ID, "character", name) for name in ("winifred", "mara", "seraphine"))
SCENE = ChatRef(CAMPAIGN_ID, chat_id_of(f"v1:{ARCHIVE}:{CAMPAIGN}/campaign.md", "00000000000000000000000000000001"))
MARA_VELL = SourceRef("card:mara-vell", "character", "mara-vell")
CARD_SCOPE = "q-card-1"  # the world-less scope 107 allocates for the card's chat
ODA_DEF, BELLAMY_DEF = person("oda", "Oda"), person("bellamy", "Bellamy")  # 014's Emberfall voices fit these refs
ODA, BELLAMY = InstanceRef(Q, "character", "oda-1"), InstanceRef(Q, "character", "bellamy-1")
EMBER_MARA = InstanceRef(Q, "character", "mara-1")
SUNSTONE = "sunstone"
LEDGER_LINE = "The ledger is under the third floorboard."
WHISPER = "Bellamy cheats at cards."
ODA_LINE, BELLAMY_LINE = "Coin first. Then talk.", "Oh darling, how splendidly grim!"


class Tagged:
    """Deterministic ids that name their use (``q-card-1``), so no two builders draw the same one."""

    def __init__(self, tag: str) -> None:
        self.tag, self.count = tag, 0

    def __call__(self, prefix: str) -> str:
        self.count += 1
        return f"{prefix}-{self.tag}-{self.count}"


# -- the builder ----------------------------------------------------------------------------------

def _emberfall(store: LibraryStore) -> None:
    """029's cast plus Oda and Bellamy, in one world-less chat: q-1/t-1 with five character actors."""
    definitions = (MARA_DEF, IRIS_DEF, SOLA_DEF, ODA_DEF, BELLAMY_DEF)
    txn = store.transaction()
    txn.put(SourceManifest(EMBER, OWNER, "Emberfall", {f"character/{d.ref.entity_id}": "r1" for d in definitions}),
            None)
    for definition in definitions:
        txn.put(RecordEnvelope(definition.ref, definition_to_payload(definition), name=definition.name,
                               extra={"schema": ENTITY_SCHEMA}), None)
    txn.commit(owner_handle())
    started = ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER).start_chat(
        Launch("character", MARA_DEF.ref), attach=[(d.ref, "character") for d in definitions[1:]]).commit()
    assert started.chat == CHAT and {EMBER_MARA, SOLA, ODA, BELLAMY} <= set(started.actors)


def _build(root: Path) -> None:
    env = Env(root)  # an empty V2 library at root/v2, and the reviewed V1 library to import into it
    store = env.store
    env.plan().apply()  # 026
    land(store, encode(V3), portrait=PORTRAIT)  # 037: card:mara-vell
    started = ChatLibrary(store, full_handle(*FULL), clock=Clock(), new_id=Tagged("card"), owner=OWNER).start_chat(
        Launch("character", MARA_VELL)).commit()  # 107: a world-less chat from the card
    assert started.scope.campaign_id == CARD_SCOPE
    _emberfall(store)
    holdings_library(store).apply(Operation("stones", (Line.add(SOLA, SUNSTONE, 2),))).commit()  # 023
    seed_arms(store, parties=())  # 034's arms catalog: a longsword template
    party = party_library(store)  # 033: Winifred travels with Mara; Seraphine is boxed in her reserve
    party.join(WINIFRED, MARA, at=Moment(1), provenance="director").commit()
    party.join(WINIFRED, SERAPHINE, at=Moment(2), provenance="director").commit()
    party.store_in_reserve(SERAPHINE, at=Moment(3), provenance="director").commit()
    # Last: 029 opens the library's durable authority. Its security ledger means every later write, of
    # any slice, needs a handle that authority issued, so the owner is granted every synced scope there.
    table = Table(store).seat()  # Mara is Ana's PC; Iris and Sola are the director's (sovereignty ledger)
    table.control("oda-1", DIRECTOR)
    table.control("bellamy-1", DIRECTOR)
    for scope in FULL:
        table.authority.add_grant(Grant(f"owner-{scope}", OWNER, frozenset({Permission.APPLY}),
                                        ResourcePath.parse(scope), OWNER))
    table.close()
    store.close()


@functools.cache
def _template() -> Path:
    folder = Path(tempfile.mkdtemp(prefix="grimoire-wave4-"))
    atexit.register(shutil.rmtree, folder, True)
    _build(folder)
    return folder / "v2"


def _library(tmp_path: Path, locker: ProcessLocker | None = None) -> LibraryStore:
    """A reopened copy of the built library (copied on first use)."""
    if not (tmp_path / LIB).exists():
        shutil.copytree(_template(), tmp_path / LIB)
    return LibraryStore.open(tmp_path / LIB, locker=locker)


@contextlib.contextmanager
def _opened(tmp_path: Path, locker: ProcessLocker | None = None):
    """The reopened library with its durable authority open: ``table.owner`` is the owner's handle."""
    store = _library(tmp_path, locker)
    table = Table(store)
    try:
        yield store, table
    finally:
        table.close()
        store.close()


def _evidence(store: LibraryStore, handle) -> EvidenceLibrary:
    return EvidenceLibrary(store, handle, owner=OWNER)


def _clean(store: LibraryStore) -> None:
    view = store.load()
    assert (view.problems, view.broken, view.excluded, view.dropped) == ((), (), (), ())


def _card_chat(view) -> ChatRef:
    (found,) = [ref for ref in view.chats if ref.campaign_id == CARD_SCOPE]
    return found


# -- 026 x 037 ------------------------------------------------------------------------------------

def test_an_imported_world_and_an_imported_card_are_both_playable_after_a_reopen(tmp_path):
    with _library(tmp_path) as store:
        _clean(store)
        view = store.load()
        # the V1 scene is a chat on the imported world with its cast bound; the card's chat has no world
        scene = chat_snapshot(view, SCENE)
        assert scene.problems == () and scene.scope.world.source == "world:saltmarch"
        assert {b.actor for b in scene.chat.active_bindings()} == {MARA, SERAPHINE, WINIFRED}
        card = chat_snapshot(view, _card_chat(view))
        assert card.problems == () and card.scope.world is None
        (actor,) = {b.actor for b in card.chat.active_bindings()}
        assert card.snapshot.entity(actor).name == "Mara Vell"
        # three Maras in three sources: a name never joins them
        assert {view.index.resolve(ref).ref.source for ref in (
            MARA_VELL, SourceRef("world:saltmarch", "character", "mara.default"), MARA_DEF.ref.unpinned())} == {
            "card:mara-vell", "world:saltmarch", EMBER}


# -- 033 -> 032 -> 020 -> 031 ---------------------------------------------------------------------

def test_the_staged_cast_is_who_hears_and_only_the_hearer_packs_the_line(tmp_path):
    with _opened(tmp_path) as (store, table):
        party = party_library(store, table.owner)
        state = party.view(CAMPAIGN_ID, as_of=4).state
        assert state.party(WINIFRED) == ((MARA,), (SERAPHINE,))
        with pytest.raises(PartyRefused) as caught:
            party.stage(CAMPAIGN_ID, "tide", (WINIFRED, MARA, SERAPHINE), as_of=4)
        assert caught.value.reason == "in_reserve"
        cast = party.stage(CAMPAIGN_ID, "tide", (WINIFRED, MARA), as_of=4)
        assert presence(state, cast, SERAPHINE) == "reserved"
        # the runner's obligation (docs/COMMUNICATION.md): the comms presence is the staged cast, nothing more
        hall = Presence(CAMPAIGN_ID, cast.scene_id, cast.present)
        sent = transmit(Message("m-1", WINIFRED, "speech", LEDGER_LINE), hall, sequence=5, provenance="scene:tide")
        assert sent.receivers == (MARA,)
        _evidence(store, table.owner).append(CAMPAIGN_ID, sent.records(), chat=SCENE).commit()

    with _opened(tmp_path) as (store, table):
        _clean(store)
        evidence = _evidence(store, table.owner)
        ledger = evidence.read(CAMPAIGN_ID, requested_by=DIRECTOR)
        view = store.load()
        packs = {who: actor_context(view, SCENE, ledger, who, as_of=5, budget=Budget(600), present=cast.present)
                 for who in (MARA, SERAPHINE, WINIFRED)}
        assert all(context.problems == () for context in packs.values())
        assert f"You were told: {LEDGER_LINE}" in packs[MARA].pack.render()
        assert f"You told someone: {LEDGER_LINE}" in packs[WINIFRED].pack.render()  # the speaker recalls it
        assert packs[SERAPHINE].pack.render() == "You are Seraphine."  # boxed, so never told anything
        assert evidence.actor_knowledge(CAMPAIGN_ID, SERAPHINE, as_of=9).evidence == ()


# -- 034 x 033 x 026 x 028 ------------------------------------------------------------------------

def test_a_gift_between_party_members_and_two_devices_forging_offline_keep_two_longswords(tmp_path):
    with _opened(tmp_path) as (desktop, table), LibraryStore.create(tmp_path / PHONE) as phone:
        handles = {DESKTOP: table.owner, PHONE: full_handle(*FULL)}
        identify(desktop, DESKTOP)
        identify(phone, PHONE)
        # Both imports and the party arrive. Only 023's holdings stay behind until 028 has merge rules for
        # them (docs/DEVICE_SYNC.md, "Still owed by 028"): Sola's sunstones, in another campaign.
        held = {(f"campaigns/{Q}/holdings/character/sola-1.json", "held")}
        assert {(c.path, c.code) for c in sync(desktop, phone, handles).conflicts} == held

        # offline on the desktop: Mara forges a longsword and gives it to Winifred, who wears it
        items = possession_library(desktop, table.owner)
        change = items.create(make("forge-desk", name="Longsword", holder=MARA, owner=MARA, source=LONGSWORD))
        change.commit()
        sword = change.item
        items.apply(sword, op("gift", Step.move(WINIFRED), Step.own(WINIFRED)), expected_rev=1).commit()
        items.apply(sword, op("wear", Step.equip(WINIFRED, "main-hand")), expected_rev=2).commit()
        # offline on the phone: the same template, another longsword for Mara
        other = possession_library(phone).create(
            make("forge-phone", name="Longsword", holder=MARA, owner=MARA, source=LONGSWORD))
        other.commit()
        assert other.item != sword  # 028's device tags: never one id for two swords
        assert sword.instance_id.startswith(f"longsword-{sha256(DESKTOP.encode()).hexdigest()[:8]}-")

        assert {(c.path, c.code) for c in sync(desktop, phone, handles).conflicts} == held
        for store, handle in ((desktop, table.owner), (phone, full_handle(*FULL))):
            _clean(store)
            found = possession_library(store, handle).campaign_items(CAMPAIGN_ID)
            assert found.problems == () and found.complete
            assert [view.item for view in found.equipped_by(WINIFRED)["main-hand"]] == [sword]
            assert [view.item for view in found.held_by(MARA)] == [other.item]
            assert set(found.views) == {sword, other.item}
            assert [view.name for view in found.views.values()] == ["Longsword", "Longsword"]
            # the gift moved custody, never membership: both are still where 033 put them
            state = party_library(store, handle).view(CAMPAIGN_ID, as_of=9).state
            assert state.party(WINIFRED) == ((MARA,), (SERAPHINE,))


# -- 032 -> 020 -> 022 -> 027 ---------------------------------------------------------------------

def test_each_speaking_npc_is_one_scripted_call_briefed_from_its_own_stored_knowledge(tmp_path):
    with _opened(tmp_path) as (store, table):
        hall = Presence(Q, "guild-dinner", (EMBER_MARA, ODA, BELLAMY))
        sent = transmit(Message("w-1", EMBER_MARA, "whisper", WHISPER, (ODA,)), hall, sequence=11,
                        provenance="scene:guild-dinner")
        _evidence(store, table.owner).append(Q, sent.records(), chat=CHAT).commit()

    with _opened(tmp_path) as (store, table):
        before = dict(store.scan().files)
        view = store.load()
        captured = chat_snapshot(view, CHAT)
        catalog = emberfall()[1]
        controls = SovereigntyLedger(table.authority).load(Q).controls  # control as recorded, never passed in
        evidence = _evidence(store, table.owner)

        def brief(actor: InstanceRef):
            instance = instance_from_payload(actor, ENTITY_SCHEMA, view.instances[actor].envelope.payload)
            knowledge = evidence.actor_knowledge(Q, actor, as_of=11)
            return npc_brief(snapshot_actor_view(instance, captured.snapshot, knowledge),
                             resolve_snapshot_voice(actor, captured.snapshot, catalog), secrets=SECRETS)

        harness = Harness([say(ODA_LINE), say(BELLAMY_LINE)])
        plan = plan_generation_turn(Q, "t11", present=("mara-1", "oda-1", "bellamy-1"),
                                    speakers=("oda-1", "bellamy-1"), controls=controls)
        meter = harness.meter(plan)
        outcome = harness.runtime.run_turn(meter, [brief(ODA), brief(BELLAMY)], controls=controls)
        assert outcome.status == "complete"
        assert [(line.actor, line.text) for line in outcome.lines] == [(ODA, ODA_LINE), (BELLAMY, BELLAMY_LINE)]
        oda_prompt, bellamy_prompt = harness.prompts()
        assert WHISPER in oda_prompt and WHISPER not in bellamy_prompt  # the whisper reached Oda alone
        assert set(meter.summary().by_actor) == {"oda-1", "bellamy-1"}  # Mara is Ana's PC: never a call
        assert dict(store.scan().files) == before  # proposals only: nothing is committed

        # The documented gap: neither importer writes a voice profile, so 027 refuses their actors.
        card = chat_snapshot(view, _card_chat(view))
        (card_actor,) = {b.actor for b in card.chat.active_bindings()}
        for actor, snapshot in ((SERAPHINE, chat_snapshot(view, SCENE).snapshot), (card_actor, card.snapshot)):
            with pytest.raises(MissingVoice) as caught:
                resolve_snapshot_voice(actor, snapshot, catalog)
            assert caught.value.reason == "no_profile"


# -- 029 -> 021 -> 030 ----------------------------------------------------------------------------

SAIL = ability(SOLA, needs=("light", "force", "flight"), effect_id=None)  # flight by radiation pressure
EVENT = AcceptedRef(Q, "e-1")


def _effects(store: LibraryStore, table: Table) -> TurnEffectsLibrary:
    return TurnEffectsLibrary(store, table.owner, issued=table.authority.issued, clock=Clock(), new_id=Ids(),
                              owner=OWNER)


def _rise(used) -> EffectProposal:
    """Sola's adjudicated flight: the power's charges, and one sunstone burnt."""
    return EffectProposal(Q, CHAT, moment(), "Sola rises on a sail of sunlight.", (), "director", abilities=(used,),
                          attempt_id=SAIL.attempt_id,  # the use burns the stone it consumes: one attempt
                          holdings=EffectHoldings((ActedLine(Line.consume(SOLA, SUNSTONE, 1), SOLA),)))


def test_an_adjudicated_power_is_charged_once_through_an_acceptance_that_crashed_mid_publish(tmp_path):
    locker = ProcessLocker()
    with _opened(tmp_path, locker) as (store, table):
        library = table.library()
        library.frame(power_frame(SAIL, intent="Rise on a sail of sunlight."))
        library.rule(AbilityRuling("r1", SAIL, "extrapolation", "approved", "director"))
        library.decide(Q, SAIL.attempt_id, Decision("partial", "adjudicated", {"#ability", "#ability-uncertain"},
                                                     {"fall"}))
        used = library.ability_use(Q, SAIL.attempt_id)  # 029's hand-off: exactly what 021 may charge
        library.check_effect(_rise(used))

    # 021 accepts; the process dies after the commit point once the event file is published, and a
    # cleanup tool removes the rest of the staging before restart (docs/RECOVERY.md, shape A).
    crash = RootedFileOps(tmp_path, plan=[Crash(Target("replace", target=f"{LIB}/campaigns/{Q}/accepted/e-1.json"),
                                                 "after")], record=False)
    with pytest.raises(SimulatedCrash), LibraryStore.open(tmp_path / LIB, ops=crash, locker=locker) as store:
        crashing = Table(store)
        try:
            _effects(store, crashing).accept(_rise(used), controls=[]).commit()
        finally:
            crashing.close()
    staged = list((tmp_path / LIB / ".txn").glob("*.new"))
    assert staged  # the holder file never published
    for temp in staged:
        temp.unlink()

    with _opened(tmp_path, locker) as (store, table):
        assert store.last_recovery.incomplete
        effects = _effects(store, table)

        def stones() -> int:
            return holdings_library(store, table.owner).read(SOLA).counts().get(SUNSTONE, 0)

        def problems() -> list[str]:
            return [p.code for p in effects.continuity(Q, as_of=99, requested_by=DIRECTOR).problems]

        assert stones() == 2 and problems() == ["receipt_mismatch"]  # charged on the event; the stone not burnt
        for _ in range(2):  # the completion, then its retry: a no-op
            _, etag = effects.read(EVENT, requested_by=DIRECTOR)
            EffectRecovery(effects).complete(EVENT, expected=etag).commit()
            assert stones() == 1 and problems() == []
        assert effects.accept(_rise(used), controls=[]).commit().ref == EVENT  # a late retry is the duplicate
        accepted, _ = effects.read(EVENT, requested_by=DIRECTOR)
        assert [(charge.attempt.attempt_id, charge.charges) for charge in accepted.abilities] == [
            (SAIL.attempt_id, (("light_bending", "eye_strain"), ("light_bending", "migraine")))]
        assert table.library().ability_use(Q, SAIL.attempt_id) == used  # replayed, it still pins the charge
        assert stones() == 1
