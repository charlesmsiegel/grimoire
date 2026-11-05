"""Seams between Wave 3 slices, on one synthetic library: built, reopened, then synced to a fresh device.

One campaign holds every Wave 3 family at once: a world-less 107 chat with two actors, an individualized
024 creature, a secret 017 membership, a private 020 perception, an accepted 021 gift, one 018 image in
two placements, 023 holdings and a 025 assessment on the same actors. The library is built once per
session (``_template``), closed, and each test reopens a copy. The second library starts empty, as a
fresh device pairing (019): what has merge rules arrives and resolves identically; what does not
(021's accepted events, 023's holdings and the 017 relations of a campaign with an accepted event) is
reported ``held`` (docs/SYNC.md). All content is synthetic; nothing calls a model.
"""

from __future__ import annotations

import atexit
import functools
import shutil
import tempfile
from hashlib import sha256
from pathlib import Path

from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary, campaign_taken_ids
from grimoire.core import (
    DIRECTOR, CampaignRoster, DefinitionIndex, Event, InstanceRef, Moment, Perception, Proposition,
    SourceDescriptor, Subject, assess_attempt, effective_entity,
)
from grimoire.core.authority import PlayerContribution
from grimoire.core.entity import EntityInstance
from grimoire.core.entity_codec import (
    ENTITY_SCHEMA, definition_from_payload, definition_to_payload, instance_from_payload, instance_to_payload,
)
from grimoire.core.identity import ChatRef, ScopeRef
from grimoire.core.individual import IndividualizationDecision, individualize, is_generic
from grimoire.core.model import ControlAssignment
from grimoire.effects import EffectProposal, Op
from grimoire.evidencestore import EvidenceLibrary
from grimoire.graph import ClaimSet, ClaimShape, claim_matches, claims_about
from grimoire.graphstore import RelationLibrary
from grimoire.holdings import Line, Operation
from grimoire.images import Crop, Placement, Shown
from grimoire.imagestore import ImageLibrary
from grimoire.store import LibraryStore, RecordEnvelope, SourceManifest, etag
from grimoire.store.layout import classify
from grimoire.sync import device_of, identify, sync

from ability_fixtures import attempt, light_bending, person, thruster_pack
from chat_fixtures import Clock, Ids
from effects_fixtures import ANA, Handles, effects_library
from entity_fixtures import EMBER, goblin
from graph_fixtures import instance_record
from holdings_fixtures import holdings_library
from image_fixtures import PNG, handle as full_handle
from knowledge_fixtures import leaks
from store_helpers import OWNER, owner_handle
from sync_helpers import DESKTOP, PHONE, kept

# -- the cast -------------------------------------------------------------------------------------

MARA_DEF = person("mara", "Mara", light_bending())  # world:emberfall/character/mara@r1
ANYA_DEF = person("anya", "Anya", thruster_pack())
SPECIES = goblin()
Q = "q-1"  # the world-less scope 107 allocates for the first chat
CHAT = ChatRef(Q, "t-1")
MARA, ANYA = InstanceRef(Q, "character", "mara-1"), InstanceRef(Q, "character", "anya-1")
SNIK = InstanceRef(Q, "creature", "goblin-1")  # the individualized creature (024)
GUILD, KEY = InstanceRef(Q, "organization", "guild"), InstanceRef(Q, "item", "key")
CONTROLS = (ControlAssignment(Q, "anya-1", ANA), ControlAssignment(Q, "mara-1", DIRECTOR))
FULL = ("sources", "campaigns", "assets", "kept")
HANDLES = {DESKTOP: full_handle(*FULL), PHONE: full_handle(*FULL)}
PERCEPT, SUMMARY = "PRIVATE-PERCEPT a ledger page torn out", "SECRET-SUMMARY the vault is opened"
MEMBERSHIP = "Mara belongs to the guild"
AVATAR_CROP, PORTRAIT_CROP = Crop(0, 0, 5000, 5000), Crop(2500, 2500, 7500, 7500)
#: the families ``load()`` holds, mapped to the view attribute holding them
LOADED = {"manifest": "sources", "instance": "instances", "scope": "scopes", "chat": "chats",
          "relation": "relations"}


class Tagged:
    """Deterministic ids that never collide across libraries: ``r-seed-1``, ``t-phone-1`` ..."""

    def __init__(self, tag: str) -> None:
        self.tag, self.count = tag, 0

    def __call__(self, prefix: str) -> str:
        self.count += 1
        return f"{prefix}-{self.tag}-{self.count}"


def _gift(summary: str = "Anya hands Mara the iron key.") -> EffectProposal:
    return EffectProposal(Q, CHAT, Moment(7, "s1", "t7"), summary,
                          (Op("end", "carries", ANYA, KEY, ANYA, "c7"), Op("begin", "carries", MARA, KEY, ANYA, "c7")),
                          "narration")


def _contribution() -> PlayerContribution:
    return PlayerContribution(Q, "c7", "s1", "t7", "anya-1", ANA, "action", "I give Mara the iron key.")


def _claims(store: LibraryStore) -> ClaimSet:
    knowledge = _evidence(store).read(Q, requested_by=DIRECTOR).knowledge
    return ClaimSet(knowledge, [ClaimShape(Q, "p-guild", "member_of", MARA, GUILD)])


def _evidence(store: LibraryStore) -> EvidenceLibrary:
    return EvidenceLibrary(store, owner_handle(), owner=OWNER)


def _images(store: LibraryStore) -> ImageLibrary:
    return ImageLibrary(store, full_handle(*FULL), owner=OWNER)


def _relations(store: LibraryStore, tag: str = "x") -> RelationLibrary:
    return RelationLibrary(store, owner_handle(), clock=Clock(), new_id=Tagged(tag), owner=OWNER)


def _chats(store: LibraryStore, tag: str) -> ChatLibrary:
    return ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Tagged(tag), owner=OWNER)


def _index(view) -> DefinitionIndex:
    """Every head revision of every loaded source, decoded into entity definitions (as test_individual_store)."""
    descriptors, definitions, heads = [], [], {}
    for source_id, stored in view.sources.items():
        descriptors.append(SourceDescriptor(source_id, owner=stored.manifest.owner))
        for ref, revision in stored.manifest.head_refs().items():
            record = view.index.resolve(ref.at(revision))
            definitions.append(definition_from_payload(record.ref, ENTITY_SCHEMA, record.envelope.payload))
            heads[ref] = revision
    return DefinitionIndex(descriptors, definitions, heads)


def _instance(view, ref: InstanceRef) -> EntityInstance:
    return instance_from_payload(ref, ENTITY_SCHEMA, view.instances[ref].envelope.payload)


# -- the builder ----------------------------------------------------------------------------------

def _build(root: Path) -> None:
    with LibraryStore.create(root) as store:
        txn = store.transaction()
        txn.put(SourceManifest(EMBER, OWNER, "Emberfall",
                               {"character/mara": "r1", "character/anya": "r1", "species/goblin": "r1"}), None)
        for definition in (MARA_DEF, ANYA_DEF, SPECIES):
            txn.put(RecordEnvelope(definition.ref, definition_to_payload(definition), name=definition.name), None)
        txn.commit(owner_handle())

        # 107: a world-less chat with two character actors. 024's individualized creature cannot be a
        # chat actor yet (actor reuse requires kind "character"; owned by sprint 033), so it is an
        # instance of the same campaign alongside them.
        started = ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER).start_chat(
            Launch("character", MARA_DEF.ref), attach=[(ANYA_DEF.ref, "character")]).commit()
        assert (started.chat, started.actors) == (CHAT, (MARA, ANYA))
        txn = store.transaction()
        txn.put(instance_record(GUILD), None)
        txn.put(instance_record(KEY), None)
        txn.put(RecordEnvelope(SNIK, instance_to_payload(EntityInstance(SNIK, SPECIES.ref.unpinned())),
                               derived_from=str(SPECIES.ref.unpinned())), None)
        txn.commit(owner_handle())

        # 024: the goblin becomes Snik, in place.
        view = store.load()
        roster = CampaignRoster(Q, tuple(_instance(view, ref) for ref in view.instances if ref.campaign_id == Q))
        decision = IndividualizationDecision(Q, "d-snik", SNIK, "Snik", DIRECTOR, "s1", ("e-vault",))
        snik = individualize(roster, decision, _index(view), ()).instance
        txn = store.transaction()
        txn.put(RecordEnvelope(SNIK, instance_to_payload(snik), derived_from=str(SPECIES.ref.unpinned())),
                view.instances[SNIK].etag)
        txn.commit(owner_handle())

        # 017: a secret membership, and the key Anya carries.
        relations = _relations(store, "seed")
        relations.add(Q, "member_of", MARA, GUILD, since=Moment(1), provenance="director").commit()
        relations.add(Q, "carries", ANYA, KEY, since=Moment(1), provenance="director").commit()

        # 020: Mara alone witnesses the vault; the membership is a proposition nobody holds.
        _evidence(store).append(Q, [
            Event(Q, "e-vault", SUMMARY, Moment(1), "director"),
            Perception(Q, "v-mara", MARA, "e-vault", "witnessed", PERCEPT, Moment(1), "director", confirmed=True),
            Proposition(Q, "p-guild", MEMBERSHIP, Moment(1), "director",
                        about=(Subject("instance", str(MARA)), Subject("instance", str(GUILD)))),
        ], chat=CHAT).commit()

        # 021: Anya (a PC) gives Mara the key.
        keys = Handles()
        proposal = _gift()
        effects_library(store, keys, new_id=Tagged("fx")).accept(
            proposal, controls=CONTROLS, contributions=[_contribution()],
            endorsements=[keys.endorse(proposal)]).commit()

        # 018: one picture, two placements with distinct crops (an actor and the creature).
        batch = _images(store).change()
        face = batch.add_image(f"campaign:{Q}", "face", PNG, {"origin": "synthetic"})
        batch.place(MARA, "avatar", Placement("set", "avatar", face, AVATAR_CROP), None)
        batch.place(SNIK, "portrait", Placement("set", "portrait", face, PORTRAIT_CROP), None)
        batch.commit()

        # 023: counted holdings on both chat actors.
        holdings = holdings_library(store)
        holdings.apply(Operation("h-1", (Line.add(MARA, "coin", 5), Line.add(ANYA, "coin", 3)))).commit()
        holdings.apply(Operation("h-2", (Line.move(MARA, ANYA, "coin", 2),))).commit()


@functools.cache
def _template() -> Path:
    folder = Path(tempfile.mkdtemp(prefix="grimoire-wave3-"))
    atexit.register(shutil.rmtree, folder, True)
    _build(folder / DESKTOP)
    return folder


def _desktop(tmp_path: Path) -> LibraryStore:
    """A reopened copy of the built library."""
    shutil.copytree(_template() / DESKTOP, tmp_path / DESKTOP)
    return LibraryStore.open(tmp_path / DESKTOP)


def _paired(tmp_path: Path):
    """The reopened library and a fresh, empty device, identified and synced once."""
    desktop = _desktop(tmp_path)
    phone = LibraryStore.create(tmp_path / PHONE)
    identify(desktop, DESKTOP)
    identify(phone, PHONE)
    return desktop, phone, sync(desktop, phone, HANDLES)


# -- shared checks ----------------------------------------------------------------------------------

def _agree(store: LibraryStore) -> dict[str, set]:
    """``load()`` and ``replica.scan`` (``store.scan()``) see the same records, with no problems either side.

    Returns the scanned refs by family, so a caller can check which families are present.
    """
    view, found = store.load(), store.scan()
    assert (view.problems, view.broken, view.excluded, view.dropped) == ((), (), (), ())
    assert (dict(found.blocked), dict(found.pairs)) == ({}, {})
    scanned: dict[str, set] = {}
    for path, data in found.files.items():
        kind, ref = classify(tuple(path.split("/")))
        scanned.setdefault(kind, set()).add(ref)
        if kind == "definition":
            assert view.index.resolve(ref).etag == etag(data), path
        elif kind in LOADED:
            assert getattr(view, LOADED[kind])[ref].etag == etag(data), path
    for kind, attribute in LOADED.items():
        assert set(getattr(view, attribute)) == scanned.get(kind, set()), kind
    return scanned


def _assessments(store: LibraryStore):
    view = store.load()
    index = _index(view)
    veil = attempt("mara", effect_id="veil", campaign=Q)
    hop = attempt("anya", "thruster_pack", ("thrust", "flight"), effect_id="lift", conditions=("fuel",), campaign=Q)
    return (assess_attempt(veil, effective_entity(_instance(view, MARA), index)),
            assess_attempt(hop, effective_entity(_instance(view, ANYA), index)))


def _resolved(store: LibraryStore) -> dict[str, object]:
    """What each family with merge rules resolves to on one library: compared across devices."""
    view = store.load()
    chats = _chats(store, "read")
    evidence = _evidence(store)
    images = _images(store)
    index = _index(view)
    snik = _instance(view, SNIK)
    return {
        "chat": chats.open_chat(CHAT),
        "navigation": chats.navigation(),
        "instances": {ref: stored.etag for ref, stored in view.instances.items()},
        "snik": (effective_entity(snik, index).name, snik.individuality, is_generic(snik, index)),
        "knowledge": evidence.read(Q, requested_by=DIRECTOR),
        "mara knows": evidence.actor_knowledge(Q, MARA, as_of=9),
        "anya knows": evidence.actor_knowledge(Q, ANYA, as_of=9),
        "mara art": images.effective_placements(MARA),
        "snik art": images.effective_placements(SNIK),
        "face": images.read_image_bytes(images.effective_placements(MARA).slot("avatar").image),
        "assessments": _assessments(store),
    }


# -- one library, reopened --------------------------------------------------------------------------

def test_one_library_holds_every_wave3_family_and_survives_a_reopen(tmp_path):
    with _desktop(tmp_path) as store:
        scanned = _agree(store)
        assert {"accepted", "holdings", "image", "placement", "knowledge", "relation", "chat", "scope"} <= set(scanned)
        assert len(scanned["placement"]) == 2 and len(scanned["image"]) == 1 and len(store.scan().blobs) == 1

        # 107 x 024: a world-less chat with two character actors; Snik is an individual of the same campaign.
        chat = _chats(store, "read").open_chat(CHAT)
        assert chat.broken == () and chat.problems == () and chat.scope.world is None
        assert {binding.actor for binding in chat.record.bindings} == {MARA, ANYA}
        view = store.load()
        snik = _instance(view, SNIK)
        assert effective_entity(snik, _index(view)).name == "Snik" and snik.individuality.template == SPECIES.ref
        assert not is_generic(snik, _index(view))

        # 017: the director sees the secret membership; Anya, uninformed, holds no claim about it.
        index = _relations(store).graph(Q, requested_by=DIRECTOR).index
        assert index.recorded("member_of", MARA, GUILD, as_of=9, requested_by=DIRECTOR)
        claims = _claims(store)
        assert claim_matches(index, claims, "p-guild", as_of=9, requested_by=DIRECTOR) != ()
        evidence = _evidence(store)
        anya = evidence.actor_knowledge(Q, ANYA, as_of=9)
        assert claims_about(anya, claims, MARA).items == () and claims_about(anya, claims, GUILD).items == ()
        about = evidence.knowledge_about(Q, ANYA, Subject("instance", str(GUILD)), as_of=9)
        assert (about.evidence, about.sent, about.current, about.history) == ((), (), (), ())

        # 020: the perception is Mara's alone.
        mara = evidence.actor_knowledge(Q, MARA, as_of=9)
        assert [item.evidence_id for item in mara.evidence] == ["v-mara"] and leaks(mara, PERCEPT) == [PERCEPT]
        assert anya.evidence == () and leaks(anya, PERCEPT, SUMMARY, MEMBERSHIP, "v-mara", "e-vault") == []

        # 021: the gift is in force once; a reroll (reworded) and a plain retry are no-ops, even with new keys.
        effects = effects_library(store, Handles(), new_id=Tagged("retry"))
        continuity = effects.continuity(Q, as_of=8, requested_by=DIRECTOR)
        assert len(continuity.events) == 1 and continuity.problems == ()
        graph = continuity.graph.index
        assert graph.recorded("carries", MARA, KEY, as_of=8, requested_by=DIRECTOR)
        assert not graph.recorded("carries", ANYA, KEY, as_of=8, requested_by=DIRECTOR)
        before = dict(store.scan().files)
        for proposal in (_gift("Anya, reluctantly, gives up the key."), _gift()):
            change = effects.accept(proposal, controls=CONTROLS, contributions=[_contribution()])
            assert change.duplicate_of == continuity.events[0].ref and change.preview() == {}
            assert change.commit().ref == continuity.events[0].ref
        assert dict(store.scan().files) == before

        # 018: one picture, two crops.
        images = _images(store)
        avatar, portrait = images.effective_placements(MARA).slot("avatar"), \
            images.effective_placements(SNIK).slot("portrait")
        assert isinstance(avatar, Shown) and isinstance(portrait, Shown)
        assert avatar.image == portrait.image and (avatar.crop, portrait.crop) == (AVATAR_CROP, PORTRAIT_CROP)
        assert images.read_image_bytes(avatar.image) == PNG

        # 023 x 025 on the same actors.
        holdings = holdings_library(store)
        assert (holdings.read(MARA).counts(), holdings.read(ANYA).counts()) == ({"coin": 3}, {"coin": 5})
        veil, hop = _assessments(store)
        assert (veil.attempt.actor, veil.classification, veil.permitted) == (MARA, "known_capability", True)
        assert (hop.attempt.actor, hop.classification, hop.permitted) == (ANYA, "known_capability", True)
        assert [cost.id for cost in veil.costs] == ["eye_strain"]


# -- synced to a fresh device ---------------------------------------------------------------------

def test_a_fresh_device_receives_what_has_merge_rules_and_reports_the_rest_held(tmp_path):
    desktop, phone, report = _paired(tmp_path)
    with desktop, phone:
        origin = desktop.scan()
        held = {path for path in origin.files
                if classify(tuple(path.split("/")))[0] in ("accepted", "holdings", "relation")}
        assert {classify(tuple(path.split("/")))[0] for path in held} == {"accepted", "holdings", "relation"}
        assert {(conflict.path, conflict.code) for conflict in report.conflicts} == {(path, "held") for path in held}
        assert held <= set(report.skipped[DESKTOP])
        assert {path for path, device in report.copied if device == PHONE} == \
            (set(origin.files) - held) | set(origin.blobs)
        assert not [path for path, device in report.copied if device == DESKTOP]
        assert desktop.conflicts() == () and phone.conflicts() == ()  # a held path keeps nothing
        again = sync(phone, desktop, HANDLES)
        assert not again.wrote and {conflict.code for conflict in again.conflicts} == {"held"}

        mine, theirs = _agree(desktop), _agree(phone)
        assert {kind for kind in theirs} == {kind for kind in mine} - {"accepted", "holdings", "relation"}
        arrived = phone.scan()
        assert {path: data for path, data in origin.files.items() if path not in held} == dict(arrived.files)
        assert dict(arrived.blobs) == dict(origin.blobs)
        assert _resolved(phone) == _resolved(desktop)  # 107, instances, 020 and art resolve identically

        # held back: the phone has no accepted gift, no holdings and no relations.
        assert effects_library(phone, Handles()).continuity(Q, as_of=8, requested_by=DIRECTOR).events == ()
        assert holdings_library(phone).read(MARA).counts() == {}
        assert _relations(phone).graph(Q, requested_by=DIRECTOR).index.relations(
            MARA, as_of=9, requested_by=DIRECTOR) == ()


def test_a_concurrent_chat_edit_is_kept_as_a_conflict_on_both_devices(tmp_path):
    desktop, phone, _ = _paired(tmp_path)
    with desktop, phone:
        path = f"campaigns/{Q}/chats/{CHAT.chat_id}.json"
        for store, label in ((desktop, "At the desk"), (phone, "On the road")):
            chats = _chats(store, store.root.name)
            chats.set_label(CHAT, label, expected=chats.open_chat(CHAT).etag).commit()
        ours, theirs = (desktop.root / path).read_bytes(), (phone.root / path).read_bytes()
        report = sync(desktop, phone, HANDLES)
        found = report.conflict(path)
        assert found.code == "concurrent" and dict(found.kept) == {DESKTOP: True, PHONE: True}
        assert ((desktop.root / path).read_bytes(), (phone.root / path).read_bytes()) == (ours, theirs)
        assert kept(desktop) == [theirs] and kept(phone) == [ours]
        assert sync(phone, desktop, HANDLES).conflict(path).code == "concurrent"
        assert kept(desktop) == [theirs] and kept(phone) == [ours]  # never duplicated
        for store, label in ((desktop, "At the desk"), (phone, "On the road")):
            assert _chats(store, "read").navigation().all_chats() == (CHAT,)  # one chat, never two
            assert _chats(store, "read").open_chat(CHAT).record.label == label
            _agree(store)


def test_an_actor_added_after_sync_gets_a_fresh_id_and_knows_nothing(tmp_path):
    desktop, phone, _ = _paired(tmp_path)
    with desktop, phone:
        taken = campaign_taken_ids(phone, Q)
        assert {"mara-1", "anya-1", "goblin-1", "guild", "key"} <= taken
        started = _chats(phone, "phone").start_chat(Launch("character", MARA_DEF.ref), scope=ScopeRef(Q)).commit()
        (actor,) = started.actors
        tag = sha256(device_of(phone).encode()).hexdigest()[:8]  # 028: numbered for the phone, never mara-2
        assert actor.instance_id not in taken and actor == InstanceRef(Q, "character", f"mara-{tag}-1")
        known = _evidence(phone).actor_knowledge(Q, actor, as_of=99)
        assert (known.evidence, known.sent, known.current, known.history) == ((), (), (), ())
        assert leaks(known, PERCEPT, SUMMARY, MEMBERSHIP) == []
        assert not isinstance(_images(phone).effective_placements(actor).slot("avatar"), Shown)  # not Mara's face
        assert _evidence(phone).actor_knowledge(Q, MARA, as_of=99).evidence != ()  # the old actor still knows
        _agree(phone)
