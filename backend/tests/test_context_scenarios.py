"""Context packs over the real native store, 107 chats and 020's ledger (Sprint 031 proving scenario).

Six NPCs share one world-less chat with a lorebook and a scenario; the antagonist is bound but
absent. Each NPC's pack is bounded and holds only its own card, its own knowledge and the common
lore its scene triggers. Attaching the world changes nothing (its bestiary is unbound); detaching
the lorebook removes its lore. 027's briefs render these packs, in a world-less chat and a
world-based one, and a scenario-only chat's director gets its lore. Every fixture is synthetic
under ``tmp_path``. No model is called.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

import context_fixtures as f
from chat_fixtures import Clock, Ids
from context_fixtures import BESTIARY, CAST, LORE, LORE_SCHEMA, NPCS, SCENARIO, SECRET, WORLD, card, definition
from portrayal_fixtures import derived, derives, envelope, manifest
from store_helpers import OWNER, owner_handle

from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.context import Budget, ContextError, director_lore, pack_actor_context
from grimoire.contextstore import ChatContext, actor_context, stored_record
from grimoire.core import DIRECTOR, ChatRef, ControlAssignment, InstanceRef, SourceRef, snapshot_actor_view
from grimoire.core.perspective import actor_knowledge
from grimoire.effective import chat_snapshot
from grimoire.evidencestore import EvidenceLibrary
from grimoire.generation import GenerationError, director_brief, npc_brief, resolve_snapshot_voice, screen_reply
from grimoire.store import LibraryStore, RecordEnvelope
from grimoire.testing.fileops import snapshot as files_of
from grimoire.voice import SecretFact, VoiceAnchor, VoiceCatalog, VoiceProfile

EVERYONE = (*NPCS, "duke")
HIDDEN = ("SECRET-TOKEN", "PLOT-TOKEN", "DIRECTOR-TOKEN", "UNCLASSIFIED-TOKEN", "SCENARIO-TOKEN",
          "UNCONFIRMED-TOKEN", "BESTIARY-TOKEN")
SCENE = "The ferry is late, and the lighthouse is dark again."
BUDGET = Budget(160, {"lore": 40})
DERIVED = SourceRef(WORLD, "character", "mara", "r1")


def lore_envelope(ref: SourceRef, name: str, entries: list[dict]) -> RecordEnvelope:
    return RecordEnvelope(ref, {"entries": entries}, name=name, extra={"schema": LORE_SCHEMA})


def seed(store: LibraryStore) -> None:
    txn = store.transaction()
    txn.put(manifest(CAST), None)
    for name in EVERYONE:
        txn.put(envelope(definition(name)), None)
    txn.put(lore_envelope(LORE, "Saltmarch lore", f.LORE_ENTRIES), None)
    txn.put(lore_envelope(SCENARIO, "Flood night", f.SCENARIO_ENTRIES), None)
    txn.put(manifest(WORLD, derives(DERIVED, card("mara"))), None)
    txn.put(lore_envelope(BESTIARY, "Bestiary", f.BESTIARY_ENTRIES), None)
    txn.put(envelope(derived(DERIVED, card("mara"), sections={"background": "Mara grew up aboard the ferry."},
                             clear_sections=["occupation"])), None)
    txn.commit(owner_handle())


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed(opened)
        yield opened


@pytest.fixture
def chats(store):
    return ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER)


def start(chats: ChatLibrary, mara: SourceRef = card("mara")):
    attach = [(card(n), "character") for n in EVERYONE if n != "mara"] + [(LORE, "lorebook"), (SCENARIO, "scenario")]
    started = chats.start_chat(Launch("character", mara), attach=attach).commit()
    cast = {ref.instance_id.rsplit("-", 1)[0]: ref for ref in started.actors}
    assert set(cast) == set(EVERYONE)
    return started, cast


def record(store: LibraryStore, started, cast) -> EvidenceLibrary:
    campaign = started.scope.campaign_id
    ledger = f.knowledge(campaign, cast.__getitem__)
    records = [*ledger.events, *ledger.propositions, *ledger.rulings, *ledger.evidence, *ledger.beliefs]
    evidence = EvidenceLibrary(store, owner_handle(), owner=OWNER)
    controls = [ControlAssignment(campaign, ref.instance_id, DIRECTOR) for ref in cast.values()]  # all NPCs
    evidence.append(campaign, records, claims=f.claim_shapes(campaign, cast.__getitem__), controls=controls,
                    chat=started.chat).commit()
    return evidence


def contexts(store, chat: ChatRef, evidence: EvidenceLibrary, cast, *, budget=BUDGET, **kwargs) -> dict[str, ChatContext]:
    view = store.load()  # one view and one director read for the whole turn
    ledger = evidence.read(chat.campaign_id, requested_by=DIRECTOR)
    options = {"scene_text": SCENE, "readers": f.READERS, **kwargs}
    return {name: actor_context(view, chat, ledger, cast[name], as_of=9, budget=budget,
                                present=[cast[n] for n in NPCS if n != name], **options) for name in NPCS}


# -- the proving scenario ------------------------------------------------------------------------------

def test_six_npcs_get_bounded_audience_safe_packs_in_a_world_less_chat(store, chats):
    started, cast = start(chats)
    evidence = record(store, started, cast)
    before = files_of(store.root)
    packs = contexts(store, started.chat, evidence, cast)
    assert files_of(store.root) == before  # nothing written, copied or rewritten
    for name, built in packs.items():
        text = built.pack.render()
        assert not any(token in text for token in HIDDEN), (name, text)
        assert built.pack.tokens <= BUDGET.max_tokens and built.pack.lines()[0] == f"You are {name.title()}."
        assert sum(item.tokens for item in built.pack.lane("lore")) <= 40
        assert (built.chat, built.problems, built.pack.problems) == (started.chat, (), ())
        assert built.chat_etag == chats.read_chat(started.chat)[1]
    assert "You suspect: the duke paid off the harbor watch" in packs["mara"].pack.lines()
    assert "You perceived (witnessed): the ferry pulling out after dark" in packs["anya"].pack.lines()
    assert "You told someone: the duke paid off the harbor watch" in packs["orrin"].pack.lines()
    [cousins] = [i for i in packs["mara"].pack.items if i.line == "You were told: Anya and Tavi are cousins"]
    assert {(c.predicate, c.node) for c in cousins.claims} == {("kin_of", cast["tavi"]), ("kin_of", cast["anya"])}
    assert all(not item.claims for name in NPCS if name != "mara" for item in packs[name].pack.items)
    lore = {name: [i.origin.name for i in built.pack.lane("lore")] for name, built in packs.items()}
    # the scene names the ferry and the lighthouse; the 40-token lore cap sheds the lighthouse, and
    # the guild, never mentioned, is only counted as untriggered
    assert all(lore[name] == ["tides", "ferry"] for name in NPCS), lore
    assert all([(o.origin.name, o.reason) for o in built.pack.omitted if o.lane == "lore"] == [("lighthouse", "lane_cap")]
               and built.pack.untriggered == ((LORE, 1),) for built in packs.values())


def test_the_store_pack_is_the_pure_pack_over_the_same_inputs(store, chats):
    started, cast = start(chats)
    evidence = record(store, started, cast)
    view = store.load()
    ledger = evidence.read(started.chat.campaign_id, requested_by=DIRECTOR)
    built = actor_context(view, started.chat, ledger, cast["anya"], as_of=9, budget=BUDGET, scene_text=SCENE,
                          readers=f.READERS)
    pure = pack_actor_context(chat_snapshot(view, started.chat).snapshot, ledger.knowledge, cast["anya"], as_of=9,
                              budget=BUDGET, scene_text=SCENE, claims=ledger.claims, readers=f.READERS,
                              unpack=stored_record)
    assert built.pack == pure


def test_only_the_absent_duke_reads_his_own_secret(store, chats):
    started, cast = start(chats)
    evidence = record(store, started, cast)
    view = store.load()
    ledger = evidence.read(started.chat.campaign_id, requested_by=DIRECTOR)
    duke = actor_context(view, started.chat, ledger, cast["duke"], as_of=9, budget=Budget(400), readers=f.READERS)
    assert SECRET in duke.pack.render() and "DIRECTOR-TOKEN" not in duke.pack.render()


def test_attaching_the_world_reads_nothing_of_its_unbound_bestiary(store, chats):
    started, cast = start(chats)
    evidence = record(store, started, cast)
    before = contexts(store, started.chat, evidence, cast)
    scope_etag = chats.read_scope(started.scope)[1]
    chats.attach_world(started.scope, WORLD, expected=scope_etag).commit()
    after = contexts(store, started.chat, evidence, cast, scene_text=SCENE + " A wyrm circles the ferry.")
    for name in NPCS:
        assert "BESTIARY-TOKEN" not in after[name].pack.render()
        assert {i.origin.source for i in after[name].pack.items} <= {card(name), LORE, None}
        assert after[name].scope_etag != before[name].scope_etag
    assert [i.line for i in after["mara"].pack.lane("memory")] == [i.line for i in before["mara"].pack.lane("memory")]


def test_a_detached_lorebook_contributes_nothing(store, chats):
    started, cast = start(chats)
    evidence = record(store, started, cast)
    before = contexts(store, started.chat, evidence, cast)
    chats.detach_source(started.chat, LORE, "lorebook", expected=chats.read_chat(started.chat)[1]).commit()
    after = contexts(store, started.chat, evidence, cast)
    for name in NPCS:
        assert before[name].pack.lane("lore") and after[name].pack.lane("lore") == ()
        assert after[name].chat_etag != before[name].chat_etag and after[name].pack.untriggered == ()


def test_a_derived_portrayal_shows_its_own_and_clears_inherited_sections(store, chats):
    started, cast = start(chats, mara=DERIVED)
    evidence = record(store, started, cast)
    mara = contexts(store, started.chat, evidence, cast)["mara"].pack
    sections = {item.origin.name: item.origin.source for item in mara.lane("portrayal") if item.origin.kind == "section"}
    assert sections == {"background": DERIVED}  # occupation cleared, voice evidence excluded
    assert "background: Mara grew up aboard the ferry." in mara.lines()


def test_a_store_without_a_reader_reports_each_lore_source(store, chats):
    started, cast = start(chats)
    evidence = record(store, started, cast)
    built = contexts(store, started.chat, evidence, cast, readers=None)["mara"]
    assert built.pack.lane("lore") == ()
    assert {(p.code, p.source, p.detail) for p in built.pack.problems} == {
        ("no_entry_reader", LORE, LORE_SCHEMA), ("no_entry_reader", SCENARIO, LORE_SCHEMA)}


def test_a_quarantined_ledger_is_noted(store, chats):
    started, cast = start(chats)
    evidence = record(store, started, cast)
    [belief] = list((store.root / "campaigns" / started.chat.campaign_id).rglob("b1.json"))
    belief.write_text("{not json", encoding="utf-8")
    built = contexts(store, started.chat, evidence, cast)["mara"]
    assert "knowledge_quarantined" in built.pack.notes
    assert not any(item.stance for item in built.pack.lane("memory"))  # her belief can only have gone


def test_the_adapter_refuses_mismatched_inputs(store, chats):
    started, cast = start(chats)
    evidence = record(store, started, cast)
    view = store.load()
    ledger = evidence.read(started.chat.campaign_id, requested_by=DIRECTOR)
    with pytest.raises(TypeError):
        actor_context(view, started.chat, ledger.knowledge, cast["mara"], as_of=9, budget=BUDGET)
    with pytest.raises(TypeError):
        actor_context(view, str(started.chat), ledger, cast["mara"], as_of=9, budget=BUDGET)
    other = chats.start_chat(Launch("character", card("anya"))).commit()
    with pytest.raises(ContextError) as caught:
        actor_context(view, other.chat, ledger, cast["mara"], as_of=9, budget=BUDGET)
    assert caught.value.reason == "campaign_mismatch"
    with pytest.raises(ContextError) as caught:
        actor_context(store.load(), started.chat, ledger, InstanceRef(started.chat.campaign_id, "character", "x-9"),
                      as_of=9, budget=BUDGET)
    assert caught.value.reason == "unresolved_audience"
    assert stored_record(definition("mara")) is None
    assert replace(ledger, quarantined=()).quarantined == ()


def test_a_head_following_lorebook_that_moves_reports_drift_and_still_packs(store, chats):
    started = chats.start_chat(Launch("character", card("mara")), attach=[(LORE.unpinned(), "lorebook")],
                               follow=True).commit()  # the explicit opt-in: the default pins (042 rebase)
    mara = started.actors[0]
    evidence = EvidenceLibrary(store, owner_handle(), owner=OWNER)
    first = actor_context(store.load(), started.chat, evidence.read(started.chat.campaign_id, requested_by=DIRECTOR),
                          mara, as_of=9, budget=BUDGET, scene_text=SCENE, readers=f.READERS)
    assert first.pack.problems == () and [i.origin.name for i in first.pack.lane("lore")] == ["tides", "ferry"]
    newer = [{**e, "text": "Tides turn three times now."} if e["id"] == "tides" else e for e in f.LORE_ENTRIES]
    put_head = store.transaction()
    put_head.put(lore_envelope(LORE.at("r2"), "Saltmarch lore", newer), None)
    put_head.put(manifest(CAST, heads={"lorebook/saltmarch-lore": "r2"}), store.read_source(CAST).etag)
    put_head.commit(owner_handle())
    later = actor_context(store.load(), started.chat, evidence.read(started.chat.campaign_id, requested_by=DIRECTOR),
                          mara, as_of=9, budget=BUDGET, scene_text=SCENE, readers=f.READERS)
    assert [(p.code, p.source, p.detail) for p in later.pack.problems] == [("binding_drift", LORE.unpinned(), "r1 -> r2")]
    assert "Tides turn three times now." in later.pack.lines()


# -- 027's briefs from these packs --------------------------------------------------------------------

#: A marker in the common ferry entry: no record teaches it, so only Mara's pack can expose it.
CROSSING = SecretFact("crossing", ("crosses the marsh at dusk",))
VOICES = VoiceCatalog([VoiceProfile(card("mara"), (VoiceAnchor("cadence", "cadence", "Mara speaks plainly."),))])


def mara_brief(store, started, cast, evidence, *, context_of=None, secrets=(CROSSING,)):
    """Mara's brief in ``started``'s chat, from her own pack there or from ``context_of``'s pack."""
    view = store.load()
    ledger = evidence.read(started.chat.campaign_id, requested_by=DIRECTOR)
    snap = chat_snapshot(view, started.chat).snapshot
    built = context_of or actor_context(view, started.chat, ledger, cast["mara"], as_of=9, budget=BUDGET,
                                        scene_text=SCENE, readers=f.READERS)
    actor_view = snapshot_actor_view(snap.instance(cast["mara"]), snap,
                                     actor_knowledge(ledger.knowledge, cast["mara"], as_of=9))
    return npc_brief(actor_view, resolve_snapshot_voice(cast["mara"], snap, VOICES), secrets=secrets,
                     context=built.pack, snapshot=snap)


def test_a_world_less_chats_npc_brief_renders_its_own_pack(store, chats):
    started, cast = start(chats)
    evidence = record(store, started, cast)
    brief = mara_brief(store, started, cast, evidence)
    assert "occupation: Mara works the Saltmarch quays." in brief.system
    assert "- The ferry crosses the marsh at dusk." in brief.system and "You suspect: the duke paid" in brief.system
    assert not any(token in brief.system for token in HIDDEN) and "the ferry pulling out" not in brief.system
    assert screen_reply(brief, "The ferry crosses the marsh at dusk, friend.") is None


def test_a_world_based_chats_derived_npc_renders_its_own_pack_and_refuses_another_chats(store, chats):
    plain, plain_cast = start(chats)
    plain_evidence = record(store, plain, plain_cast)
    started, cast = start(chats, mara=DERIVED)
    chats.attach_world(started.scope, WORLD, expected=chats.read_scope(started.scope)[1]).commit()
    evidence = record(store, started, cast)
    brief = mara_brief(store, started, cast, evidence)
    assert brief.voice.actor_revision == DERIVED and brief.voice.portrayal == card("mara")
    assert "background: Mara grew up aboard the ferry." in brief.system and "occupation" not in brief.system
    assert not any(token in brief.system for token in HIDDEN)
    other = contexts(store, plain.chat, plain_evidence, plain_cast)["mara"]
    with pytest.raises(GenerationError) as caught:
        mara_brief(store, started, cast, evidence, context_of=other)
    assert caught.value.reason == "foreign_context"  # the other chat's Mara is another actor


def test_a_continuation_chat_that_reuses_an_actor_refuses_her_pack_from_the_first_chat(store, chats):
    first, cast = start(chats)
    evidence = record(store, first, cast)
    # The campaign goes on in a second chat: the same Mara, the same card, but no lorebook bound.
    reused = {("character", card("mara").entity_key): cast["mara"]}
    later = chats.start_chat(Launch("character", card("mara")), scope=first.scope, reuse_actors=reused).commit()
    assert later.chat != first.chat and later.actors == ()  # Mara is reused, so no actor is new
    from_first = contexts(store, first.chat, evidence, cast)["mara"]
    assert "The ferry crosses the marsh at dusk." in from_first.pack.lines()
    with pytest.raises(GenerationError) as caught:
        mara_brief(store, later, cast, evidence, context_of=from_first)
    assert caught.value.reason == "context_mismatch"  # same actor, card and moment; another selection
    own = mara_brief(store, later, cast, evidence)
    assert "ferry crosses the marsh" not in own.system
    assert screen_reply(own, "The ferry crosses the marsh at dusk.") == "unlearned_secret"


def test_a_scenario_only_chat_gives_its_director_the_lore_the_table_triggered(store, chats):
    started = chats.start_chat(Launch("scenario", SCENARIO), attach=[(LORE, "lorebook")]).commit()
    assert started.actors == ()
    snap = chat_snapshot(store.load(), started.chat).snapshot
    lore = director_lore(snap, campaign_id=started.chat.campaign_id, table_text=SCENE, budget=Budget(60),
                         readers=f.READERS, unpack=stored_record)
    assert [(i.origin.name, i.audience) for i in lore.items] == [
        ("tides", "common"), ("opening", "director"), ("ferry", "common")]
    assert lore.tokens <= 60 and [o.origin.name for o in lore.omitted] == ["lighthouse"]
    narration = director_brief("A flooded quay at night.", campaign_id=started.chat.campaign_id, present=(),
                               controls=(), names={}, secrets=(CROSSING,), lore=lore, snapshot=snap)
    assert "- The ferry crosses the marsh at dusk." in narration.system and "SCENARIO-TOKEN" in narration.system
    assert screen_reply(narration, "The ferry crosses the marsh at dusk.") is None
    assert screen_reply(narration, "SCENARIO-TOKEN the flood comes tonight.") == "unlearned_secret"
    other = chats.start_chat(Launch("scenario", SCENARIO)).commit()  # another campaign, with no lorebook
    elsewhere = chat_snapshot(store.load(), other.chat).snapshot
    with pytest.raises(GenerationError) as caught:
        director_brief("A flooded quay at night.", campaign_id=other.chat.campaign_id, present=(), controls=(),
                       names={}, secrets=(CROSSING,), lore=lore, snapshot=elsewhere)
    assert caught.value.reason == "foreign_context"


def test_a_pack_from_before_a_hand_edit_of_the_bound_lorebook_is_refused(store, chats):
    # Same chat, same binding, same pinned revision r1: only the file's content changed, so neither the
    # selection nor a re-pin can tell. The ferry entry becomes the director's.
    started, cast = start(chats)
    evidence = record(store, started, cast)
    before = contexts(store, started.chat, evidence, cast)["mara"]
    assert "The ferry crosses the marsh at dusk." in before.pack.lines()
    table = director_lore(chat_snapshot(store.load(), started.chat).snapshot, campaign_id=started.chat.campaign_id,
                          table_text=SCENE, budget=Budget(400), readers=f.READERS, unpack=stored_record)
    [path] = (store.root / "sources").rglob("lorebook/saltmarch-lore/r1.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    for raw in document["payload"]["entries"]:
        if raw["id"] == "ferry":
            raw["audience"] = "director"
    path.write_text(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(GenerationError) as caught:
        mara_brief(store, started, cast, evidence, context_of=before)
    assert caught.value.reason == "context_mismatch"
    fresh = mara_brief(store, started, cast, evidence)
    assert "ferry crosses the marsh" not in fresh.system
    assert screen_reply(fresh, "The ferry crosses the marsh at dusk.") == "unlearned_secret"
    edited = chat_snapshot(store.load(), started.chat).snapshot
    with pytest.raises(GenerationError) as caught:
        director_brief("A flooded quay at night.", campaign_id=started.chat.campaign_id, present=(), controls=(),
                       names={}, secrets=(CROSSING,), lore=table, snapshot=edited)
    assert caught.value.reason == "context_mismatch"  # its ferry entry still reads as common


def test_a_pack_from_before_a_reload_quarantined_one_of_her_records_is_refused(store, chats):
    # Same chat, bindings and as_of: only the ledger read changed. Orrin's report to Mara is damaged on
    # disk, so the reload quarantines it (and her belief that cites it), and her projection shrinks.
    started, cast = start(chats)
    evidence = record(store, started, cast)
    bribe = (SecretFact("bribe", ("paid off the harbor watch",)),)
    before = contexts(store, started.chat, evidence, cast)["mara"]
    assert "You suspect: the duke paid off the harbor watch" in before.pack.lines()
    told = mara_brief(store, started, cast, evidence, context_of=before, secrets=bribe)
    assert screen_reply(told, "They say the duke paid off the harbor watch.") is None  # hers, until the reload
    [report] = list((store.root / "campaigns" / started.chat.campaign_id).rglob("evidence/v2.json"))
    report.write_text("{not json", encoding="utf-8")
    with pytest.raises(GenerationError) as caught:
        mara_brief(store, started, cast, evidence, context_of=before, secrets=bribe)
    assert caught.value.reason == "context_mismatch"
    after = contexts(store, started.chat, evidence, cast)["mara"]
    assert "knowledge_quarantined" in after.pack.notes and "harbor watch" not in after.pack.render()
    fresh = mara_brief(store, started, cast, evidence, context_of=after, secrets=bribe)
    assert "harbor watch" not in fresh.system
    assert screen_reply(fresh, "They say the duke paid off the harbor watch.") == "unlearned_secret"
