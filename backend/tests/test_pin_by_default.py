"""Pin by default (Wave 5 audit 042 D2; owner's decision, Nov 6).

Both Wave 5 exit-gate chat kinds used to bind their sources unpinned, so a new card or world revision
changed a running chat's NPC with nobody reviewing it, and 042's reviewed rebase protected none of the
default chats. Now every product path pins the revision a source resolves to when it is bound:

- a card chat started as 035 starts it (``Launch("character", <unpinned card ref>)``), its lorebook,
  its persona and a second card attached mid-chat;
- 026's V0.2.1 scene chats (and their actors' instance files);
- 036's SillyTavern chats (through 107's writer).

A new revision reaches such a chat only through ``SourceRebase.review`` + ``apply`` with a ``take``.
``follow=True`` is the explicit opt-in to follow a head. Chats an earlier build stored unpinned keep
reading and keep following; a rerun of either importer over such a library rewrites none of them and
reports them instead.

Every fixture is synthetic under ``tmp_path``. No model is called.
"""

from __future__ import annotations

import json

import pytest

import grimoire.archaeologystore.library as st_library
import grimoire.import_v1._plan as v1_plan
from card_fixtures import V3, card, encode, land
from chat_fixtures import CARDS, Clock, chat_library, seed_sources
from import_fixtures import ARCHIVE, CAMPAIGN_ID, SALTMARCH, Env
from v1_fixture import CAMPAIGN, SID, STAMP, frontmatter
from scene_fixtures import V1_CHAT
from store_helpers import OWNER, owner_handle

from grimoire.archaeologystore import StImporter
from grimoire.changesets import ChangeSet
from grimoire.changesetstore import ChangeSetLibrary
from grimoire.chats import Launch, NewBinding
from grimoire.chatstore import ChatLibrary
from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.effective import chat_snapshot
from grimoire.generation import SnapshotVoices
from grimoire.import_v1 import Selection
from grimoire.rebase import RebaseError
from grimoire.rebasestore import SourceRebase
from grimoire.store import LibraryStore, RecordEnvelope
from grimoire.testing.fileops import snapshot

MARA = SourceRef("card:mara-vell", "character", "mara-vell")
LORE = SourceRef("card:mara-vell", "lorebook", "mara-vell-lore")
ANYA = SourceRef("card:anya-rook", "character", "anya-rook")
TAMSIN = SourceRef("card:tamsin-hale", "character", "tamsin-hale")
R1_LINE, R2_LINE = "We sail when the lamps go out.", "Lanterns out. Now."
R1_LORE, R2_LORE = V3["data"]["character_book"]["entries"][0]["content"], "The lighthouse burns again."
VOICED = card(V3, mes_example="<START>\n{{char}}: " + R1_LINE)
UPDATED = card(V3, personality="Sharper now; trusts no one.", mes_example="<START>\n{{char}}: " + R2_LINE,
               character_book={**V3["data"]["character_book"], "entries": [
                   {**V3["data"]["character_book"]["entries"][0], "content": R2_LORE},
                   V3["data"]["character_book"]["entries"][1]]})
ANYA_CARD = card(V3, name="Anya Rook", description="A harbor pilot.", character_book=None,
                 mes_example="<START>\n{{char}}: Mind the shoals.")
TAMSIN_CARD = card(V3, name="Tamsin Hale", description="A customs clerk.", character_book=None,
                   mes_example="<START>\n{{char}}: Every crate has a stamp.")
MARA_V1 = InstanceRef(CAMPAIGN_ID, "character", "mara")


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        for document in (VOICED, ANYA_CARD, TAMSIN_CARD):
            land(opened, encode(document))
        yield opened


def rebase(store) -> SourceRebase:
    return SourceRebase(store, owner_handle(), clock=Clock())


def take_all(store, actor: InstanceRef):
    """Review ``actor`` against its source's head and take every change, as a human would."""
    review = rebase(store).review(actor)
    rebase(store).apply(review, {name: "take" for name in review.decisions_needed}).commit()
    return review


def card_chat(store, chats: ChatLibrary):
    """Started as 035's fixtures start it: unpinned card and lorebook refs, then an unpinned persona."""
    started = chats.start_chat(Launch("character", MARA), attach=[(LORE, "lorebook")]).commit()
    _, etag = chats.read_chat(started.chat)
    chats.set_persona(started.chat, ANYA, expected=etag).commit()
    return started


def voice_line(store, chat, actor) -> str:
    snap = chat_snapshot(store.load(), chat).snapshot
    return SnapshotVoices(snap).resolve(actor).examples[0].text


def lore_text(store, chat) -> list[str]:
    snap = chat_snapshot(store.load(), chat).snapshot
    raw = snap.resolve(LORE.unpinned(), "lorebook")
    return [entry["content"] for entry in raw.envelope.payload["entries"]]


def pinned_at(bindings, revision: str) -> bool:
    return all(b.ref.pinned and b.ref.revision == b.resolved_revision == revision for b in bindings)


# -- exit-gate kind 1: a card chat ------------------------------------------------------------------

def test_a_card_chat_pins_card_lorebook_and_persona_and_holds_r1_until_a_reviewed_take(store):
    chats = chat_library(store, owner_handle())
    started = card_chat(store, chats)
    [actor] = started.actors
    record, _ = chats.read_chat(started.chat)
    assert record.launch.ref == MARA.at("r1")  # the launch names what its binding pins
    assert pinned_at(record.active_bindings(), "r1") and len(record.active_bindings()) == 3
    stored = store.read(actor).envelope
    assert (stored.payload["source"], stored.derived_from, stored.base) == (str(MARA.at("r1")),) * 3
    other = chats.start_chat(Launch("character", MARA)).commit()  # another campaign of the same card
    [other_actor] = other.actors

    land(store, encode(UPDATED), target=MARA.source)  # the card's update: r2 of the character and its lore
    assert store.check_definition(MARA).ref == MARA.at("r2") and store.check_definition(LORE).ref == LORE.at("r2")
    snap = chat_snapshot(store.load(), started.chat).snapshot
    assert snap.effective(MARA).ref == MARA.at("r1")
    assert snap.effective(MARA).section("personality") == V3["data"]["personality"]
    assert [n for r in snap.sources() for n in r.notices] == []  # no drift: nothing reached the chat
    assert voice_line(store, started.chat, actor) == R1_LINE and lore_text(store, started.chat)[0] == R1_LORE

    review = take_all(store, actor)
    assert review.repinned == (started.chat,) and review.following == ()
    rows = {row.field: row for row in review.rows}
    assert [(row.field, row.status) for row in review.rows] == [("section:personality", "upstream"),
                                                                ("extra:card", "upstream")]
    assert "card.texts.example-messages" in rows["extra:card"].changed  # the voice's text, a take-only row
    record, _ = chats.read_chat(started.chat)
    [character] = record.active_bindings("character")
    assert character.ref == MARA.at("r2") and character.actor == actor  # re-pinned, same actor
    snap = chat_snapshot(store.load(), started.chat).snapshot
    assert snap.effective(MARA).section("personality") == "Sharper now; trusts no one."
    assert voice_line(store, started.chat, actor) == R2_LINE
    # only the actor was rebased: the lorebook stays pinned until it is re-attached (no actor to rebase)
    assert lore_text(store, started.chat)[0] == R1_LORE
    # a campaign that did not rebase keeps r1
    assert voice_line(store, other.chat, other_actor) == R1_LINE
    assert rebase(store).review(other_actor).repinned == (other.chat,)


def test_a_card_chat_is_up_to_date_until_its_card_changes(store):
    chats = chat_library(store, owner_handle())
    started = card_chat(store, chats)
    with pytest.raises(RebaseError) as caught:
        rebase(store).review(started.actors[0])
    assert caught.value.reason == "up_to_date"


def test_a_second_card_attached_mid_chat_is_pinned(store):
    chats = chat_library(store, owner_handle())
    started = card_chat(store, chats)
    _, etag = chats.read_chat(started.chat)
    joined = chats.attach_source(started.chat, TAMSIN, "character", expected=etag).commit()
    [tamsin] = joined.actors
    record, _ = chats.read_chat(started.chat)
    [late] = [b for b in record.active_bindings("character") if b.actor == tamsin]
    assert late.ref == TAMSIN.at("r1") and late.resolved_revision == "r1" and late.introduced_seq > 0
    assert record.roster_entry(tamsin).source == TAMSIN.at("r1")

    land(store, encode(card(TAMSIN_CARD, mes_example="<START>\n{{char}}: Stamp it twice.")), target=TAMSIN.source)
    assert voice_line(store, started.chat, tamsin) == "Every crate has a stamp."  # still r1
    take_all(store, tamsin)
    assert voice_line(store, started.chat, tamsin) == "Stamp it twice."


def test_a_replaced_persona_keeps_its_body_and_the_revision_it_plays(store):
    chats = chat_library(store, owner_handle())
    started = card_chat(store, chats)
    record, etag = chats.read_chat(started.chat)
    body = record.persona.actor
    land(store, encode(card(ANYA_CARD, description="A harbor pilot, retired.")), target=ANYA.source)
    # the same persona chosen again, unpinned: the body keeps the revision it plays, never the unreviewed r2
    etag = chats.set_persona(started.chat, ANYA, expected=etag).commit().etags[_chat_path(started.chat)]
    record, _ = chats.read_chat(started.chat)
    assert record.persona.actor == body and pinned_at([record.persona], "r1")
    # asking for r2 by name is the player's explicit choice (107's rebind; the file keeps r1: actor_rebound)
    chats.set_persona(started.chat, ANYA.at("r2"), expected=etag).commit()
    record, _ = chats.read_chat(started.chat)
    assert record.persona.actor == body and pinned_at([record.persona], "r2")


def test_a_reused_actor_is_pinned_to_the_revision_it_plays_after_a_rebase(store):
    chats = chat_library(store, owner_handle())
    started = chats.start_chat(Launch("character", MARA)).commit()
    [actor] = started.actors
    land(store, encode(UPDATED), target=MARA.source)  # r2
    take_all(store, actor)  # the campaign reviewed and took r2
    land(store, encode(card(UPDATED, personality="Unreviewed.")), target=MARA.source)  # r3, nobody reviewed it
    _, etag = chats.read_chat(started.chat)
    etag = chats.detach_source(started.chat, MARA, "character", expected=etag).commit().etags[
        _chat_path(started.chat)]
    chats.attach_source(started.chat, MARA, "character", expected=etag, actor=actor).commit()  # unpinned, reused
    record, _ = chats.read_chat(started.chat)
    [again] = record.active_bindings("character")
    assert again.actor == actor and again.ref == MARA.at("r2")  # what the actor plays, not the head r3
    # and a continued scope reusing it does the same
    key = ("character", MARA.entity_key)
    later = chats.start_chat(Launch("character", MARA), scope=started.scope, reuse_actors={key: actor}).commit()
    assert chats.read_chat(later.chat)[0].launch.ref == MARA.at("r2")
    assert rebase(store).review(actor).ancestor.ref == MARA.at("r2")  # one revision: r3 is still reviewable


def _chat_path(chat) -> str:
    return f"campaigns/{chat.campaign_id}/chats/{chat.chat_id}.json"


def rebind(chats, chat, ref, role, actor) -> str:
    """107's rebind: detach ``ref``'s binding and bind ``actor`` again at ``ref``; the chat's new etag."""
    _, etag = chats.read_chat(chat)
    etag = chats.detach_source(chat, ref.unpinned(), role, expected=etag).commit().etags[_chat_path(chat)]
    return chats.attach_source(chat, ref, role, expected=etag, actor=actor).commit().etags[_chat_path(chat)]


def test_a_reused_actor_is_pinned_to_the_revision_an_explicit_rebind_plays_it_at(store):
    chats = chat_library(store, owner_handle())
    first = chats.start_chat(Launch("character", MARA)).commit()
    [actor] = first.actors
    land(store, encode(UPDATED), target=MARA.source)  # r2
    rebind(chats, first.chat, MARA.at("r2"), "character", actor)  # the player's explicit choice; the file keeps r1
    assert store.read(actor).envelope.payload["source"] == str(MARA.at("r1"))
    land(store, encode(card(UPDATED, personality="Unreviewed.")), target=MARA.source)  # r3
    key = ("character", MARA.entity_key)
    later = chats.start_chat(Launch("character", MARA), scope=first.scope, reuse_actors={key: actor}).commit()
    record, _ = chats.read_chat(later.chat)
    assert record.launch.ref == MARA.at("r2") and [b.ref for b in record.active_bindings()] == [MARA.at("r2")]
    review = rebase(store).review(actor)  # one revision across the campaign, never binding_disagrees
    assert review.ancestor.ref == MARA.at("r2") and review.upstream.ref == MARA.at("r3")


def test_a_reused_actor_whose_chats_disagree_is_pinned_to_its_own_revision_not_to_the_head(store):
    chats = chat_library(store, owner_handle())
    first = chats.start_chat(Launch("character", MARA)).commit()
    [actor] = first.actors
    key = ("character", MARA.entity_key)
    second = chats.start_chat(Launch("character", MARA), scope=first.scope, reuse_actors={key: actor}).commit()
    land(store, encode(UPDATED), target=MARA.source)  # r2
    rebind(chats, second.chat, MARA.at("r2"), "character", actor)  # r1 in one chat, r2 in the other
    land(store, encode(card(UPDATED, personality="Later.")), target=MARA.source)  # r3
    _, etag = chats.read_chat(first.chat)
    etag = chats.detach_source(first.chat, MARA, "character", expected=etag).commit().etags[_chat_path(first.chat)]
    chats.attach_source(first.chat, MARA, "character", expected=etag, actor=actor).commit()
    # the remaining chat pins r2 and this one is unbound: one revision agreed, so it is that one
    assert chats.read_chat(first.chat)[0].active_bindings("character")[0].ref == MARA.at("r2")
    third = chats.start_chat(Launch("character", MARA.at("r1")), scope=first.scope,
                             reuse_actors={key: actor}).commit()  # r1 and r2 now: they disagree
    fourth = chats.start_chat(Launch("character", MARA), scope=first.scope, reuse_actors={key: actor}).commit()
    # no silent choice of one chat, and no unreviewed head: the instance's own revision (its file says r1)
    assert store.read(actor).envelope.payload["source"] == str(MARA.at("r1"))
    assert chats.read_chat(fourth.chat)[0].launch.ref == MARA.at("r1")
    assert chats.read_chat(third.chat)[0].launch.ref == MARA.at("r1")
    with pytest.raises(RebaseError) as caught:
        rebase(store).review(actor)
    assert caught.value.reason == "binding_disagrees"


def test_the_same_persona_chosen_again_keeps_the_revision_an_explicit_rebind_chose(store):
    chats = chat_library(store, owner_handle())
    started = card_chat(store, chats)
    record, etag = chats.read_chat(started.chat)
    body = record.persona.actor
    land(store, encode(card(ANYA_CARD, description="A harbor pilot, retired.")), target=ANYA.source)  # r2
    etag = chats.set_persona(started.chat, ANYA.at("r2"), expected=etag).commit().etags[_chat_path(started.chat)]
    land(store, encode(card(ANYA_CARD, description="Unreviewed.")), target=ANYA.source)  # r3
    chats.set_persona(started.chat, ANYA, expected=etag).commit()  # the same persona, named without a revision
    record, _ = chats.read_chat(started.chat)
    assert record.persona.actor == body and pinned_at([record.persona], "r2")


def test_a_following_chat_repinned_by_detach_and_attach_stays_on_the_head_it_played(store):
    chats = chat_library(store, owner_handle())
    following = chats.start_chat(Launch("character", MARA), follow=True).commit()  # as an earlier build stored it
    [actor] = following.actors
    land(store, encode(UPDATED), target=MARA.source)  # r2: the following chat plays it already
    assert chat_snapshot(store.load(), following.chat).snapshot.entity(actor).resolved_source == MARA.at("r2")
    rebind(chats, following.chat, MARA, "character", actor)  # the documented re-pin, named without a revision
    snap = chat_snapshot(store.load(), following.chat).snapshot
    assert snap.entity(actor).resolved_source == MARA.at("r2")  # what it played, never back to the base r1
    assert chats.read_chat(following.chat)[0].active_bindings("character")[0].ref == MARA.at("r2")


def test_a_lorebook_attached_by_itself_is_pinned(store):
    chats = chat_library(store, owner_handle())
    started = chats.start_chat(Launch("lorebook", LORE)).commit()
    record, _ = chats.read_chat(started.chat)
    assert record.launch.ref == LORE.at("r1") and pinned_at(record.active_bindings(), "r1")
    land(store, encode(UPDATED), target=MARA.source)
    assert lore_text(store, started.chat)[0] == R1_LORE


def test_following_a_head_is_an_explicit_opt_in(store):
    chats = chat_library(store, owner_handle())
    following = chats.start_chat(Launch("character", MARA), attach=[(LORE, "lorebook")], follow=True).commit()
    [actor] = following.actors
    record, _ = chats.read_chat(following.chat)
    assert record.launch.ref == MARA and all(not b.ref.pinned for b in record.active_bindings())
    assert store.read(actor).envelope.payload["source"] == str(MARA)
    land(store, encode(UPDATED), target=MARA.source)
    snap = chat_snapshot(store.load(), following.chat).snapshot
    assert snap.effective(MARA).ref == MARA.at("r2")
    assert {(n.code, n.detail) for r in snap.sources() for n in r.notices} == {("binding_drift", "r1 -> r2")}
    review = rebase(store).review(actor)
    assert review.following == (following.chat,) and review.repinned == ()
    # an explicitly pinned ref is bound as given, follow or not
    pinned = chats.start_chat(Launch("character", MARA.at("r1")), follow=True).commit()
    assert chats.read_chat(pinned.chat)[0].launch.ref == MARA.at("r1")


# -- exit-gate kind 2: a migrated V0.2.1 scene chat ---------------------------------------------------

def revise_mara(store, description: str):
    """A 043 change set giving the world's Mara a new revision (the head moves)."""
    sets = ChangeSetLibrary(store, owner_handle())
    _, head, _ = sets.head(SALTMARCH, "character", "mara.default")
    change = sets.revise(SALTMARCH, "character", "mara.default", {**head.payload, "description": description},
                         name=head.name, evidence=("session 3",))
    review = sets.review(ChangeSet("cs-mara", SALTMARCH, "Mara keeps two ledgers", OWNER, (change,)))
    assert review.applicable
    return sets.apply(review).revisions[0]


def test_a_migrated_v1_chat_holds_its_revision_through_an_applied_change_set_until_rebased(tmp_path):
    env = Env(tmp_path)
    env.plan().apply()
    store = env.store
    record = chat_snapshot(store.load(), V1_CHAT).chat
    imported = {b.actor: b.ref for b in record.active_bindings()}
    assert len(imported) == 3 and all(b.ref.pinned and b.ref.revision == b.resolved_revision
                                      for b in record.active_bindings())
    assert record.launch.ref == imported[MARA_V1]
    assert store.read(MARA_V1).envelope.payload["source"] == str(imported[MARA_V1])

    r2 = revise_mara(store, "A harbor clerk who keeps two ledgers.")
    assert store.check_definition(r2.unpinned()).ref == r2
    snap = chat_snapshot(store.load(), V1_CHAT).snapshot
    assert snap.entity(MARA_V1).resolved_source == imported[MARA_V1]  # the chat stays on its revision
    assert snap.entity(MARA_V1).description != "A harbor clerk who keeps two ledgers."
    assert [n for r in snap.sources() for n in r.notices] == []

    review = take_all(store, MARA_V1)
    assert review.ancestor.ref == imported[MARA_V1] and review.upstream.ref == r2
    assert review.repinned == (V1_CHAT,) and [row.field for row in review.rows] == ["description"]
    snap = chat_snapshot(store.load(), V1_CHAT).snapshot
    assert snap.entity(MARA_V1).resolved_source == r2
    assert snap.entity(MARA_V1).description == "A harbor clerk who keeps two ledgers."
    assert [b.ref for b in snap.selection.sources if b.actor == MARA_V1] == [r2]


# -- back-compat: libraries an earlier build wrote ---------------------------------------------------

def _unpinned(ref):
    return ref.unpinned() if isinstance(ref, SourceRef) else ref


@pytest.fixture
def earlier_build(monkeypatch):
    """The importers as an earlier build ran them (``as_earlier_build``); ``undo()`` restores this build."""
    return as_earlier_build(monkeypatch)


def as_earlier_build(monkeypatch):
    """Run the importers as the build before pin-by-default did: every binding, launch and actor source
    as the unpinned (head-following) ref. The campaign files it writes are byte for byte what that
    build wrote (checked against it once, when this test was written)."""

    def binding(ref, *args, **kwargs):
        return NewBinding(_unpinned(ref), *args, **kwargs)

    def launch(kind, ref=None, premise=None):
        return Launch(kind, _unpinned(ref), premise)

    def envelope(address, payload, **kwargs):
        if isinstance(address, InstanceRef):
            payload = {**payload, "source": str(SourceRef.parse(payload["source"]).unpinned())}
            kwargs["derived_from"] = str(SourceRef.parse(kwargs["derived_from"]).unpinned())
        return RecordEnvelope(address, payload, **kwargs)

    class Following(ChatLibrary):
        def start_chat(self, launch, **kwargs):  # type: ignore[override]
            return super().start_chat(launch, follow=True, **kwargs)

    monkeypatch.setattr(v1_plan, "NewBinding", binding)
    monkeypatch.setattr(v1_plan, "Launch", launch)
    monkeypatch.setattr(v1_plan, "RecordEnvelope", envelope)
    monkeypatch.setattr(st_library, "ChatLibrary", Following)
    return monkeypatch


def campaign_bytes(store) -> dict[str, bytes]:
    return {path: data for path, data in snapshot(store.root).items() if path.startswith("campaigns/")}


def test_a_v1_rerun_over_a_library_an_earlier_build_imported_rewrites_nothing_and_reports_it(tmp_path, earlier_build):
    env = Env(tmp_path)
    env.plan().apply()
    earlier_build.undo()
    store = env.store
    record = chat_snapshot(store.load(), V1_CHAT).chat
    assert all(not b.ref.pinned for b in record.active_bindings())  # what the earlier build stored
    before = campaign_bytes(store)

    plan = env.plan()
    review = plan.review()
    assert review["writes"] == [] and plan.preview() == {}
    follows = sorted(row["detail"] for row in review["unresolved"] if row["reason"] == "binding_follows_head")
    assert follows == sorted(f"{V1_CHAT} {b.ref}" for b in record.active_bindings())
    # nothing else: an actor the earlier build wrote unpinned is the same V1 content, never ``v1_changed``
    assert {row["reason"] for row in review["unresolved"]} <= {"dangling_object", "binding_follows_head"}
    plan.apply()
    assert campaign_bytes(store) == before  # never rewritten: no duplicate chat, no re-pinned binding

    # it still reads and still follows: a world revision reaches it, flagged as drift
    r2 = revise_mara(store, "A harbor clerk who keeps two ledgers.")
    snap = chat_snapshot(store.load(), V1_CHAT).snapshot
    assert snap.entity(MARA_V1).resolved_source == r2
    assert ("binding_drift", f"{record.active_bindings()[0].resolved_revision} -> {r2.revision}") in {
        (n.code, n.detail) for r in snap.sources() for n in r.notices}
    review = rebase(store).review(MARA_V1)  # the reviewed rebase still sees it, as following
    assert review.following == (V1_CHAT,) and review.repinned == ()

    # a scene first imported now binds such an actor pinned at the head it follows, never back at its base
    known = set(store.campaign_chats(CAMPAIGN_ID).records)
    add_v1_scene(env)
    env.plan().apply()
    [new] = set(store.campaign_chats(CAMPAIGN_ID).records) - known
    record = chat_snapshot(store.load(), new).chat
    assert [b.ref for b in record.active_bindings() if b.actor == MARA_V1] == [r2] and record.launch.ref == r2


def test_a_scene_first_imported_after_a_rebase_pins_the_revision_the_campaign_plays(tmp_path):
    env = Env(tmp_path)
    scene = f"v1:{ARCHIVE}:{CAMPAIGN}/scenes/{SID}.md"
    env.plan(Selection({"saltmarch": SALTMARCH}, {"the-drowned-ledger": CAMPAIGN_ID}, frozenset({scene}))).apply()
    store = env.store
    imported = SourceRef.parse(store.read(MARA_V1).envelope.payload["source"])
    r2 = revise_mara(store, "A harbor clerk who keeps two ledgers.")
    take_all(store, MARA_V1)  # the campaign reviewed and took r2 (no chat yet)
    assert store.read(MARA_V1).envelope.payload["source"] == str(r2)

    plan = env.plan(Selection({"saltmarch": SALTMARCH}, {"the-drowned-ledger": CAMPAIGN_ID}))
    assert any("/chats/" in path for path in plan.review()["writes"])
    plan.apply()
    captured = chat_snapshot(store.load(), V1_CHAT)
    [mara] = [b for b in captured.chat.active_bindings() if b.actor == MARA_V1]
    assert mara.ref == r2 != imported and captured.chat.launch.ref == r2  # never pinned back to the old revision
    assert captured.snapshot.entity(MARA_V1).description == "A harbor clerk who keeps two ledgers."
    others = [b for b in captured.chat.active_bindings() if b.actor != MARA_V1]
    assert all(b.ref.pinned and b.ref.revision.startswith("v1-") for b in others)  # untouched actors: imported
    with pytest.raises(RebaseError) as caught:  # one revision across the campaign: nothing to review now
        rebase(store).review(MARA_V1)
    assert caught.value.reason == "up_to_date"


SID2 = "002--the-second-ledger"


def add_v1_scene(env) -> None:
    """A second V1 scene of the campaign with Winifred and Mara in it, then a fresh review of the library."""
    campaign = env.v1 / CAMPAIGN
    (campaign / "scenes" / f"{SID2}.md").write_text(frontmatter(
        [("title", "The second ledger"), ("model", "''"), ("created", STAMP), ("updated", STAMP),
         ("identity", "0" * 31 + "2")],
        '**Winifred:** <!-- grimoire-response {"post_id": "00000000000000000000000000000021"} -->\n'
        "I ask for the other book.\n\n"
        '**Mara:** <!-- grimoire-response {"post_id": "00000000000000000000000000000022"} -->\n'
        "There is no other book.\n"), encoding="utf-8")
    path = campaign / "appearances.json"
    appearances = json.loads(path.read_text(encoding="utf-8"))
    for key in ("characters/mara", "pcs/winifred"):
        appearances[key]["presence"][SID2] = [{"end": None, "start": 0}]
        appearances[key]["scenes"].append(SID2)
    path.write_text(json.dumps(appearances, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    env.rescan()


def test_a_new_v1_scene_after_a_rebind_in_a_kept_chat_pins_the_revision_the_rebind_chose(tmp_path):
    env = Env(tmp_path)
    env.plan().apply()
    store = env.store
    imported = SourceRef.parse(store.read(MARA_V1).envelope.payload["source"])
    r2 = revise_mara(store, "A harbor clerk who keeps two ledgers.")
    chats = chat_library(store, owner_handle())
    rebind(chats, V1_CHAT, r2, "character", MARA_V1)  # in V2, in the chat the import kept; the file keeps r1
    assert store.read(MARA_V1).envelope.payload["source"] == str(imported)
    before = {chat for chat in store.campaign_chats(CAMPAIGN_ID).records}

    add_v1_scene(env)
    plan = env.plan()
    assert not {row["reason"] for row in plan.review()["unresolved"]} & {"v1_changed", "binding_follows_head"}
    plan.apply()
    [new] = set(store.campaign_chats(CAMPAIGN_ID).records) - before
    record = chat_snapshot(store.load(), new).chat
    [mara] = [b for b in record.active_bindings() if b.actor == MARA_V1]
    assert mara.ref == r2 and record.launch.ref == r2  # the revision the campaign plays, not the file's r1
    kept = chat_snapshot(store.load(), V1_CHAT).chat
    assert [b.ref for b in kept.active_bindings() if b.actor == MARA_V1] == [r2]  # the rerun left the rebind
    with pytest.raises(RebaseError) as caught:  # one revision across the campaign, and it is the head
        rebase(store).review(MARA_V1)
    assert caught.value.reason == "up_to_date"


def test_a_sillytavern_rerun_over_an_earlier_builds_import_rewrites_nothing_and_reports_it(tmp_path, earlier_build):
    data = (json.dumps({"name": "Mara", "is_user": False, "mes": "Welcome aboard.", "original_avatar": "m.png"})
            + "\n").encode()
    with LibraryStore.create(tmp_path / "lib") as store:
        seed_sources(store)
        mara = SourceRef(CARDS, "character", "mara")  # unpinned, as a caller maps it
        importer = StImporter(store, owner_handle(), clock=Clock(), owner=OWNER)
        [speaker] = importer.scan(data).chat.speakers
        first = importer.plan(data, speakers={speaker.id: mara})
        first.commit()
        earlier_build.undo()
        before = campaign_bytes(store)
        again = importer.plan(data, speakers={speaker.id: mara})
        assert again.status == "already_imported" and again.change is None and again.chat == first.chat
        assert again.following == (mara,)  # reported, never rewritten
        assert campaign_bytes(store) == before


def test_a_sillytavern_import_now_pins_and_its_rerun_reports_nothing_following(tmp_path):
    data = (json.dumps({"name": "Mara", "is_user": False, "mes": "Welcome aboard.", "original_avatar": "m.png"})
            + "\n").encode()
    with LibraryStore.create(tmp_path / "lib") as store:
        seed_sources(store)
        mara = SourceRef(CARDS, "character", "mara")
        importer = StImporter(store, owner_handle(), clock=Clock(), owner=OWNER)
        [speaker] = importer.scan(data).chat.speakers
        first = importer.plan(data, speakers={speaker.id: mara})
        first.commit()
        record = chat_library(store).read_chat(first.chat)[0]
        assert record.launch.ref == mara.at("r1") and pinned_at(record.active_bindings(), "r1")
        again = importer.plan(data, speakers={speaker.id: mara})
        assert again.status == "already_imported" and again.following == ()


def test_a_damaged_chat_elsewhere_in_the_campaign_never_lets_a_reuse_take_the_unreviewed_head(store):
    chats = chat_library(store, owner_handle())
    first = chats.start_chat(Launch("character", MARA)).commit()  # r1
    [actor] = first.actors
    land(store, encode(UPDATED), target=MARA.source)  # r2, nobody reviewed it
    other = chats.start_chat(Launch("character", ANYA), scope=first.scope).commit()
    (store.root / _chat_path(other.chat)).write_text("{ not json", encoding="utf-8")  # hand-damaged
    _, etag = chats.read_chat(first.chat)
    etag = chats.detach_source(first.chat, MARA, "character", expected=etag).commit().etags[_chat_path(first.chat)]
    chats.attach_source(first.chat, MARA, "character", expected=etag, actor=actor).commit()
    # its bindings cannot all be read, so the instance's own revision, never r2
    assert [b.ref for b in chats.read_chat(first.chat)[0].active_bindings("character")] == [MARA.at("r1")]
    with pytest.raises(RebaseError) as caught:
        rebase(store).review(actor)
    assert caught.value.reason == "chats_unreadable"


def _drop_revision(store, ref: SourceRef) -> None:
    """Delete one revision file by hand, leaving the source's later revisions: a broken pinned reference."""
    source = ref.source.split(":", 1)[1]
    (store.root / "sources" / "card" / source / "_source" / "records" / ref.kind / ref.entity_id
     / f"{ref.revision}.json").unlink()


def test_the_same_persona_chosen_again_repairs_a_pinned_revision_that_no_longer_resolves(store):
    chats = chat_library(store, owner_handle())
    started = chats.start_chat(Launch("character", MARA)).commit()
    _, etag = chats.read_chat(started.chat)
    etag = chats.set_persona(started.chat, ANYA, expected=etag).commit().etags[_chat_path(started.chat)]  # r1
    body = chats.read_chat(started.chat)[0].persona.actor
    land(store, encode(card(ANYA_CARD, description="A harbor pilot, retired.")), target=ANYA.source)  # r2
    _drop_revision(store, ANYA.at("r1"))
    assert [b.reason for b in chats.open_chat(started.chat).broken] == ["missing_revision"]
    chats.set_persona(started.chat, ANYA, expected=etag).commit()  # the documented repair
    record, _ = chats.read_chat(started.chat)
    assert record.persona.actor == body and record.persona.ref == ANYA.at("r2")
    assert not chats.open_chat(started.chat).broken


def test_detach_and_attach_of_the_same_actor_repairs_a_pinned_revision_that_no_longer_resolves(store):
    chats = chat_library(store, owner_handle())
    first = chats.start_chat(Launch("character", MARA)).commit()  # r1
    [actor] = first.actors
    land(store, encode(UPDATED), target=MARA.source)  # r2
    _drop_revision(store, MARA.at("r1"))
    _, etag = chats.read_chat(first.chat)
    etag = chats.detach_source(first.chat, MARA.at("r1"), "character", expected=etag).commit().etags[
        _chat_path(first.chat)]
    chats.attach_source(first.chat, MARA, "character", expected=etag, actor=actor).commit()
    assert [b.ref for b in chats.read_chat(first.chat)[0].active_bindings("character")] == [MARA.at("r2")]
