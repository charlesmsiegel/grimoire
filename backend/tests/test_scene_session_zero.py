"""035 milestone 2: executing 016's session zero in play.

A chat's configuration is stored in its play scope (``grimoire.session-zero/1``), changed only through the
runner by an authenticated table member, and read from the store by every call that shows narration: a limit in
force for the table holds narration (016 holds an unlabelled segment while any limit applies, and nothing labels
themes yet), a configuration that cannot be read fails closed, and a chat without one plays exactly as before.

Over the synthetic card library (``scene_fixtures``): Mara Vell's card chat with Anya Rook as the persona
(``human:ana`` plays her). Every model call is a scripted fake; every name is a placeholder.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading

import pytest

from chat_fixtures import Clock
from generation_fixtures import say
from scene_fixtures import ANA, BOB, LABEL_POLICY, Table, Tagged, attach_tamsin, card_library
from store_helpers import OWNER

from grimoire.chatstore import ChatLibrary
from grimoire.core import DIRECTOR
from grimoire.core.boundaries import Boundary, BoundaryChange, SessionZero
from grimoire.core.boundaries_codec import SESSION_ZERO_SCHEMA, session_zero_to_payload
from grimoire.ledgers.codec import ControlChange
from grimoire.play import boundaries as bounding
from grimoire.play import new_boundary_id
from grimoire.scene import SceneRefused
from grimoire.store.layout import address_parts

REPLY = json.dumps({"factors": [{"fact": "f1", "bearing": "favours"}], "stakes": ["She falls to the deck"]})


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return card_library(tmp_path_factory.mktemp("built"))


@pytest.fixture
def cards(built, tmp_path):
    folder, card_chat, _ = built
    copy = tmp_path / "cards"
    shutil.copytree(folder, copy)
    return copy, card_chat


def refused(reason: str, call, *args, **kwargs) -> SceneRefused:
    with pytest.raises(SceneRefused) as error:
        call(*args, **kwargs)
    assert error.value.reason == reason, error.value
    return error.value


def files(folder) -> dict[str, bytes]:
    return {str(path.relative_to(folder)): path.read_bytes() for path in sorted(folder.rglob("*.json"))}


def scope_file(folder, chat):
    return folder.joinpath(*address_parts(chat.scope))


def stored(folder, chat):
    return json.loads(scope_file(folder, chat).read_text(encoding="utf-8"))["payload"].get("session_zero")


def opaque(label: str) -> str:
    """A deterministic id of the opaque form the runner accepts, named by ``label`` in the test only."""
    return "b-" + hashlib.sha256(label.encode()).hexdigest()[:32]


def setting(principal, label, theme, treatment, scope="player", scope_id=None, **kw) -> BoundaryChange:
    return BoundaryChange("set", principal, Boundary(opaque(label), scope, scope_id or principal, theme, treatment,
                                                     principal, **kw))


def withdrawing(principal, label) -> BoundaryChange:
    return BoundaryChange("withdraw", principal, boundary_id=opaque(label))


def write_raw(table: Table, chat, value) -> None:
    """Store ``value`` as the scope's session zero directly (a hand edit, or another build), bypassing the runner."""
    chats = ChatLibrary(table.store, table.owner, clock=Clock(), new_id=Tagged("raw"), owner=OWNER)
    _, etag = chats.read_scope(chat.scope)
    chats.set_session_zero(chat.scope, value, expected=etag).commit()


def hand(table: Table, chat, actor, player) -> None:
    table.runner.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, actor.instance_id, player),
                                    table.owner)


def asking(table: Table, chat):
    table.runner.seat(chat, ANA)
    return table.runner.post(chat, table.player(), '"Well?"')


# -- a chat without one: exactly as before ----------------------------------------------------------------

def test_a_chat_without_a_session_zero_plays_as_before(cards):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide.")]) as table:
        asking(table, chat)
        view = table.runner.boundaries(chat, table.player())
        assert (view.revision, view.visible, view.own, view.held) == (0, (), (), False)
        take = table.runner.generate(chat, table.player())
        assert [line.text for line in take.lines] == ["Safe as the tide."]
        table.runner.keep(chat, table.player(), take.take)
        assert stored(folder, chat) is None  # reading it wrote nothing
        assert not [p for p in table.runner.open(chat).problems if p[0] == "session_zero"]


# -- stored, read back and honoured -------------------------------------------------------------------------

def test_a_stored_configuration_is_versioned_and_an_allow_only_one_generates(cards):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide.")]) as table:
        asking(table, chat)
        view = table.runner.change_boundaries(chat, table.player(), setting(
            ANA, "b-sea", "sea_peril", "allow", scope="campaign", scope_id=chat.campaign_id))
        assert view.revision == 1 and not view.held and [b.boundary_id for b in view.own] == [opaque("b-sea")]
        data = stored(folder, chat)
        assert data["schema"] == SESSION_ZERO_SCHEMA and data["revision"] == 1
        assert [b["treatment"] for b in data["boundaries"]] == ["allow"]
        take = table.runner.generate(chat, table.player())  # a stored configuration no longer refuses generation
        assert [line.text for line in take.lines] == ["Safe as the tide."]
        assert table.runner.keep(chat, table.player(), take.take).kept.take == take.take
    with Table(folder) as table:  # reopened: the same configuration, read from the scope file
        view = table.runner.boundaries(chat, table.player())
        assert view.revision == 1 and [(v.theme, v.treatment) for v in view.visible] == [("sea_peril", "allow")]


def test_a_hard_limit_holds_narration_before_any_call_and_withdrawing_it_lets_play_go_on(cards):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide.")]) as table:
        table.runner.seat(chat, ANA)
        table.runner.change_boundaries(chat, table.player(), setting(ANA, "b-gore", "graphic_violence", "avoid",
                                                                     private_note="never again"))
        assert table.runner.boundaries(chat, table.player()).held
        before = files(folder)
        refused("held_by_boundary", table.runner.begin, chat, table.player())  # the opening is narration too
        assert files(folder) == before
        table.runner.post(chat, table.player(), '"Well?"')
        before = files(folder)
        refused("held_by_boundary", table.runner.generate, chat, table.player())
        assert files(folder) == before and not table.harness.transport.sends  # nothing written, nothing billed
        view = table.runner.change_boundaries(chat, table.player(), withdrawing(ANA, "b-gore"))
        assert (view.revision, view.own, view.held) == (2, (), False)
        assert stored(folder, chat)["boundaries"] == []  # an empty configuration, still versioned
        assert table.runner.generate(chat, table.player()).lines


@pytest.mark.parametrize("treatment", ["check_in", "fade_to_black", "skip"])
def test_every_limit_stricter_than_allow_holds_unlabelled_narration(cards, treatment):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide.")]) as table:
        asking(table, chat)
        table.runner.change_boundaries(chat, table.owner, setting(OWNER, "b-1", "storms", treatment,
                                                                  scope="campaign", scope_id=chat.campaign_id))
        refused("held_by_boundary", table.runner.generate, chat, table.player())


def test_a_players_limit_applies_only_while_that_player_is_at_the_table(cards):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide."), say("Calm seas.")]) as table:
        tamsin = attach_tamsin(table.store, table.owner, chat)
        hand(table, chat, tamsin, BOB)  # Bob plays Tamsin
        asking(table, chat)
        table.runner.change_boundaries(chat, table.player(BOB), setting(BOB, "b-bob", "spiders", "avoid"))
        refused("held_by_boundary", table.runner.generate, chat, table.player())  # Ana's call: Bob is at the table
        view = table.runner.boundaries(chat, table.player())
        assert view.own == () and [(v.theme, v.set_by, v.boundary_id) for v in view.visible] == [
            ("spiders", None, None)]  # Ana sees the effect, never whose it is
        hand(table, chat, tamsin, DIRECTOR)  # Bob leaves: his limit drops out of presentation (016)
        assert table.runner.generate(chat, table.player()).lines


def test_the_owners_own_limit_applies_to_what_the_owner_asks_for(cards):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide.")]) as table:
        asking(table, chat)
        table.runner.change_boundaries(chat, table.owner, setting(OWNER, "b-owner", "spiders", "avoid"))
        refused("held_by_boundary", table.runner.generate, chat, table.owner)  # the owner is the requester
        assert table.runner.generate(chat, table.player()).lines  # Ana's table: the owner is not in its audience


def test_labelling_is_held_like_generation(cards):
    folder, chat = cards
    with Table(folder, policy=LABEL_POLICY, labels=[say(REPLY)]) as table:
        table.runner.seat(chat, ANA)
        table.runner.post(chat, table.player(), "*I climb the rigging*")
        table.runner.change_boundaries(chat, table.player(), setting(ANA, "b-fall", "falling", "avoid"))
        refused("held_by_boundary", table.runner.label, chat, table.player())
        assert not table.labels.prompts() and not table.runner.open(chat).pending.labels


# -- a configuration that cannot be read fails closed ------------------------------------------------------

@pytest.mark.parametrize("value", [
    {"lines": []},  # no codec reads it (a build before this one stored it verbatim)
    {"schema": "grimoire.session-zero/2", "revision": 1, "boundaries": []},  # a newer one
    {**session_zero_to_payload(SessionZero(), 1), "mood": "calm"},  # an unknown key
])
def test_an_unreadable_configuration_refuses_and_writes_nothing(cards, value):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide.")]) as table:
        asking(table, chat)
        write_raw(table, chat, value)
        before = files(folder)
        refused("session_zero_unreadable", table.runner.generate, chat, table.player())
        refused("session_zero_unreadable", table.runner.boundaries, chat, table.player())
        refused("session_zero_unreadable", table.runner.change_boundaries, chat, table.player(),
                setting(ANA, "b-1", "spiders", "avoid"))
        assert files(folder) == before and not table.harness.transport.sends
        assert ("session_zero", "session_zero_unreadable") in table.runner.open(chat).problems


def test_a_hand_edit_that_loosens_or_plants_a_limit_is_refused_as_tampered(cards):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide.")]) as table:
        asking(table, chat)
        table.runner.change_boundaries(chat, table.player(), setting(
            ANA, "b-sea", "sea_peril", "fade_to_black", scope="campaign", scope_id=chat.campaign_id))
        data = stored(folder, chat)
        data["boundaries"][0]["agreed_by"] = []  # the setter's own agreement removed by hand
        write_raw(table, chat, data)
        refused("session_zero_unreadable", table.runner.generate, chat, table.player())
        data["boundaries"][0].update(agreed_by=[ANA], scope="player", scope_id=BOB)  # planted for another player
        write_raw(table, chat, data)
        refused("session_zero_unreadable", table.runner.generate, chat, table.player())


def test_an_undecodable_scope_holds_narration_but_a_chat_with_no_scope_plays_as_before(cards):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide."), say("Calm seas.")]) as table:
        asking(table, chat)
        path = scope_file(folder, chat)
        original = path.read_bytes()
        document = json.loads(original)
        document["payload"]["title"] = 7  # a scope this version cannot decode may hold anything
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        refused("session_zero_unreadable", table.runner.generate, chat, table.player())
        path.unlink()  # no scope record at all (a chat from before 107): no configuration, as before
        assert table.runner.generate(chat, table.player()).lines
        refused("no_scope", table.runner.change_boundaries, chat, table.player(), setting(ANA, "b", "x", "avoid"))


def test_a_world_boundary_a_chat_is_not_in_fails_closed(cards):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide.")]) as table:
        asking(table, chat)
        refused("invalid_field", table.runner.change_boundaries, chat, table.player(),  # a world-less chat
                setting(ANA, "b-w", "storms", "allow", scope="world", scope_id="w1"))
        write_raw(table, chat, session_zero_to_payload(SessionZero((
            Boundary("b-w", "world", "w1", "storms", "allow", ANA),)), 1))  # one stored while a world was attached
        refused("session_zero_unreadable", table.runner.generate, chat, table.player())


# -- who may change it --------------------------------------------------------------------------------------

def test_only_the_authenticated_speaker_changes_only_their_own_boundaries(cards):
    folder, chat = cards
    with Table(folder) as table:
        table.runner.seat(chat, ANA)
        table.runner.change_boundaries(chat, table.owner, setting(OWNER, "b-owner", "storms", "avoid",
                                                                  scope="campaign", scope_id=chat.campaign_id))
        before = files(folder)
        refused("not_your_boundary", table.runner.change_boundaries, chat, table.player(),
                setting(OWNER, "b-x", "storms", "allow"))  # Ana speaking as the owner
        refused("not_your_boundary", table.runner.change_boundaries, chat, table.player(),
                withdrawing(ANA, "b-owner"))  # another's limit
        refused("not_your_boundary", table.runner.change_boundaries, chat, table.player(),
                withdrawing(ANA, "no-such-id"))  # the same answer: a refusal confirms no hidden id
        refused("not_at_table", table.runner.change_boundaries, chat, table.player(BOB),
                setting(BOB, "b", "x", "avoid"))
        refused("not_authenticated", table.runner.change_boundaries, chat,
                table.authority.handle_for(ANA, trusted=False), setting(ANA, "b", "x", "avoid"))
        refused("invalid_field", table.runner.change_boundaries, chat, table.player(), "set b avoid")
        refused("invalid_field", table.runner.change_boundaries, chat, table.player(),
                setting(ANA, "b-n", "storms", "avoid", private_note="x" * 2001))  # past the codec's bound
        refused("invalid_field", table.runner.change_boundaries, chat, table.player(),  # 016: ids are opaque
                BoundaryChange("set", ANA, Boundary("ana-spiders", "player", ANA, "spiders", "avoid", ANA)))
        assert files(folder) == before
        assert bounding.OPAQUE_ID.fullmatch(new_boundary_id()) and new_boundary_id() != new_boundary_id()
        view = table.runner.change_boundaries(chat, table.player(), BoundaryChange("agree", ANA,
                                                                                     boundary_id=opaque("b-owner")))
        assert view.revision == 2 and view.own == ()  # Ana agreed; only the setter's view shows who agreed


def test_a_limit_can_be_set_while_play_is_paused(cards):
    folder, chat = cards
    with Table(folder) as table:
        asking(table, chat)
        table.runner.request(chat, table.player(), "pause")
        assert table.runner.change_boundaries(chat, table.player(), setting(ANA, "b", "storms", "avoid")).held


# -- mid-session: requests, and changes racing a turn ---------------------------------------------------------

def test_a_limit_set_mid_session_holds_the_next_keep_and_the_tables_requests_still_run(cards):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide."), say("Calm seas."), say("Fair winds.")]) as table:
        asking(table, chat)
        first = table.runner.generate(chat, table.player())
        second = table.runner.generate(chat, table.player())
        table.runner.change_boundaries(chat, table.player(), setting(ANA, "b-storm", "storms", "avoid"))
        before = files(folder)
        refused("held_by_boundary", table.runner.keep, chat, table.player(), first.take)  # nothing caches it
        assert files(folder) == before
        turn = table.runner.request(chat, table.player(), "redirect", [second.lines[0].line_id], direction="calmer")
        assert {t.take: t.state for t in turn.takes}[second.take] == "withdrawn"  # a request is never held
        refused("held_by_boundary", table.runner.generate, chat, table.player())  # its reroll is
        write_raw(table, chat, {"lines": []})  # even a damaged configuration never blocks a pause or a skip
        table.runner.request(chat, table.player(), "skip", [first.lines[0].line_id])
        assert table.runner.request(chat, table.player(), "pause").paused
        table.runner.resume(chat, table.player())
        refused("session_zero_unreadable", table.runner.generate, chat, table.player())


def test_a_limit_set_while_a_run_is_in_flight_lands_its_usage_without_its_lines(cards, monkeypatch):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide.")]) as table:
        asking(table, chat)
        run_turn = table.harness.runtime.run_turn

        def then_limited(*args, **kwargs):
            outcome = run_turn(*args, **kwargs)
            table.runner.change_boundaries(chat, table.player(), setting(ANA, "b-late", "storms", "avoid"))
            return outcome

        monkeypatch.setattr(table.harness.runtime, "run_turn", then_limited)
        take = table.runner.generate(chat, table.player())
        assert (take.state, take.lines, take.outcome) == ("proposed", (), "halted")
        assert [(c.status, c.reason) for c in take.calls] == [("rejected", "held_by_boundary")]
        assert take.calls[0].attempts  # its usage is on disk
        landed = table.runner.open(chat).pending.takes[0]
        assert landed.lines == () and landed.calls[0].attempts
        refused("empty_take", table.runner.keep, chat, table.player(), take.take)


def test_a_limit_set_between_a_keeps_read_and_its_commit_holds_it(cards, monkeypatch):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide.")]) as table:
        asking(table, chat)
        take = table.runner.generate(chat, table.player())
        read = table.runner._context

        def then_limited(chat_ref):  # another table member's change lands after the keep read the chat
            found = read(chat_ref)
            write_raw(table, chat, session_zero_to_payload(SessionZero((
                Boundary("b-race", "player", ANA, "storms", "avoid", ANA),)), 1))
            return found

        monkeypatch.setattr(table.runner, "_context", then_limited)
        refused("held_by_boundary", table.runner.keep, chat, table.player(), take.take)
        monkeypatch.undo()
        assert table.runner.open(chat).pending.takes[0].state == "proposed"


def test_concurrent_changes_serialise_and_none_is_lost(cards):
    folder, chat = cards
    with Table(folder) as table:
        tamsin = attach_tamsin(table.store, table.owner, chat)
        hand(table, chat, tamsin, BOB)
        table.runner.seat(chat, ANA)
        errors = []

        def changing(principal, handle):
            try:
                for n in range(8):
                    table.runner.change_boundaries(chat, handle, setting(principal, f"b-{principal}-{n}", "x", "allow"))
            except Exception as error:  # pragma: no cover - reported below
                errors.append(error)

        threads = [threading.Thread(target=changing, args=(who, table.player(who))) for who in (ANA, BOB)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        assert not errors
        data = stored(folder, chat)
        assert data["revision"] == 16 and len(data["boundaries"]) == 16


# -- sync: another device's version of the scope ---------------------------------------------------------------

def test_another_devices_stricter_version_kept_in_a_sync_conflict_holds_here_too(cards, monkeypatch):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide.")]) as table:
        asking(table, chat)
        path = "/".join(address_parts(chat.scope))
        mine = scope_file(folder, chat).read_bytes()
        document = json.loads(mine)
        document["payload"]["session_zero"] = session_zero_to_payload(SessionZero((
            Boundary("b-peer", "player", ANA, "storms", "avoid", ANA),)), 1)
        theirs = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")

        monkeypatch.setattr(bounding, "open_conflicts", lambda *a, **k: ({path: (theirs,)}, True))
        refused("held_by_boundary", table.runner.generate, chat, table.player())
        monkeypatch.setattr(bounding, "open_conflicts", lambda *a, **k: ({path: (b"{not json",)}, True))
        refused("session_zero_unreadable", table.runner.generate, chat, table.player())
        monkeypatch.setattr(bounding, "open_conflicts", lambda *a, **k: ({}, False))  # records cannot say
        refused("session_zero_unreadable", table.runner.generate, chat, table.player())
        monkeypatch.setattr(bounding, "open_conflicts", lambda *a, **k: ({path: (mine,)}, True))  # no session zero
        assert table.runner.generate(chat, table.player()).lines


def test_concurrent_offline_changes_on_two_devices_hold_play_on_both_until_one_is_kept(cards, tmp_path):
    from test_scene_turn_merge import DESKTOP, PHONE, devices, synced
    from grimoire.store import etag
    from grimoire.sync import dismiss
    from grimoire.sync.turns import open_conflicts
    folder, chat = cards
    with Table(folder) as table:
        table.runner.seat(chat, ANA)
    chat = devices((folder, chat), tmp_path)
    with Table(tmp_path / DESKTOP, [say("Desk.")]) as desk, Table(tmp_path / PHONE, [say("Phone.")]) as phone:
        synced(desk, phone)
        desk.runner.change_boundaries(chat, desk.player(), setting(ANA, "b-desk", "storms", "avoid"))  # offline
        phone.runner.change_boundaries(chat, phone.player(), setting(
            ANA, "b-phone", "sea_peril", "allow", scope="campaign", scope_id=chat.campaign_id))
        path = "/".join(address_parts(chat.scope))
        assert synced(desk, phone).conflict(path).code == "concurrent"  # keep-both: each reads its own scope
        phone.runner.post(chat, phone.player(), '"Well?"')
        assert stored(tmp_path / PHONE, chat)["boundaries"][0]["treatment"] == "allow"  # its own: no limit
        refused("held_by_boundary", phone.runner.generate, chat, phone.player())  # the desk's limit holds here too
        assert phone.runner.boundaries(chat, phone.player()).held
        found, _ = open_conflicts(phone.store, path, [path])  # the scope file alone
        dismiss(phone.store, DESKTOP, path, etag(found[path][0]), phone.owner)  # the phone keeps its own version
        assert phone.runner.generate(chat, phone.player()).lines
        refused("held_by_boundary", desk.runner.begin, chat, desk.player())  # the desk keeps its own limit


def test_a_keep_is_judged_for_the_table_as_it_is_kept_not_as_the_take_was_written(cards):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide.")]) as table:
        tamsin = attach_tamsin(table.store, table.owner, chat)
        asking(table, chat)  # Tamsin is seated with the director: an NPC
        mara = next(actor for actor in table.runner.open(chat).npcs if actor != tamsin)
        write_raw(table, chat, session_zero_to_payload(SessionZero((
            Boundary(opaque("b-bob"), "player", BOB, "spiders", "avoid", BOB),)), 1))  # Bob's, from an earlier seat
        take = table.runner.generate(chat, table.player(), speakers=[mara])  # Bob is not at the table: presented
        assert take.lines
        hand(table, chat, tamsin, BOB)  # Bob sits down before the take is kept
        refused("held_by_boundary", table.runner.keep, chat, table.player(), take.take)


# -- the table as it stands when narration is shown, read again under the lock -------------------------------

def test_a_player_seated_while_a_run_is_in_flight_is_judged_as_its_take_lands(cards, monkeypatch):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide.")]) as table:
        tamsin = attach_tamsin(table.store, table.owner, chat)
        asking(table, chat)
        mara = next(actor for actor in table.runner.open(chat).npcs if actor != tamsin)
        write_raw(table, chat, session_zero_to_payload(SessionZero((
            Boundary(opaque("b-bob"), "player", BOB, "spiders", "avoid", BOB),)), 1))  # Bob's, from an earlier seat
        run_turn = table.harness.runtime.run_turn

        def then_seated(*args, **kwargs):
            outcome = run_turn(*args, **kwargs)
            hand(table, chat, tamsin, BOB)  # Bob sits down while the call is in flight
            return outcome

        monkeypatch.setattr(table.harness.runtime, "run_turn", then_seated)
        take = table.runner.generate(chat, table.player(), speakers=[mara])
        monkeypatch.undo()
        assert (take.lines, take.outcome) == ((), "halted")
        assert [(c.status, c.reason) for c in take.calls] == [("rejected", "held_by_boundary")]
        assert table.runner.open(chat).pending.takes[0].lines == ()


def test_a_player_seated_while_a_labelling_is_in_flight_is_judged_as_it_lands(cards, monkeypatch):
    folder, chat = cards
    with Table(folder, policy=LABEL_POLICY, labels=[say(REPLY)]) as table:
        tamsin = attach_tamsin(table.store, table.owner, chat)
        table.runner.seat(chat, ANA)
        table.runner.post(chat, table.player(), "*I climb the rigging*")
        write_raw(table, chat, session_zero_to_payload(SessionZero((
            Boundary(opaque("b-bob"), "player", BOB, "falling", "avoid", BOB),)), 1))
        original = table.runner.labeller.label

        def then_seated(*args, **kwargs):
            reply = original(*args, **kwargs)
            hand(table, chat, tamsin, BOB)  # Bob sits down while the labelling call is in flight
            return reply

        monkeypatch.setattr(table.runner.labeller, "label", then_seated)
        label = table.runner.label(chat, table.player())
        monkeypatch.undo()
        assert (label.state, label.reason, label.stakes) == ("refused", "held_by_boundary", ())
        assert label.calls[0].attempts  # its usage is on disk
        assert table.runner.open(chat).pending.labels[-1].state == "refused"


def test_a_player_seated_between_a_keeps_read_and_its_commit_holds_it(cards, monkeypatch):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide.")]) as table:
        tamsin = attach_tamsin(table.store, table.owner, chat)
        asking(table, chat)
        mara = next(actor for actor in table.runner.open(chat).npcs if actor != tamsin)
        write_raw(table, chat, session_zero_to_payload(SessionZero((
            Boundary(opaque("b-bob"), "player", BOB, "spiders", "avoid", BOB),)), 1))
        take = table.runner.generate(chat, table.player(), speakers=[mara])
        assert take.lines
        read, seated = table.runner._context, []

        def then_seated(chat_ref):  # Bob sits down after the keep read the chat, before it commits
            found = read(chat_ref)
            if not seated:
                seated.append(hand(table, chat, tamsin, BOB))
            return found

        monkeypatch.setattr(table.runner, "_context", then_seated)
        refused("held_by_boundary", table.runner.keep, chat, table.player(), take.take)
        monkeypatch.undo()
        assert table.runner.open(chat).pending.takes[0].state == "proposed"


# -- a world or setting boundary with no context to resolve it ---------------------------------------------

def test_open_reports_a_world_boundary_the_chat_is_not_in(cards):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide.")]) as table:
        asking(table, chat)
        write_raw(table, chat, session_zero_to_payload(SessionZero((
            Boundary(opaque("b-w"), "world", "w1", "storms", "allow", ANA),)), 1))
        refused("session_zero_unreadable", table.runner.generate, chat, table.player())
        refused("session_zero_unreadable", table.runner.boundaries, chat, table.player())
        assert ("session_zero", "session_zero_unreadable") in table.runner.open(chat).problems


def test_a_setting_boundary_is_refused_at_write_and_unreadable_when_stored(cards):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide.")]) as table:
        asking(table, chat)
        before = files(folder)
        refused("invalid_field", table.runner.change_boundaries, chat, table.player(),
                setting(ANA, "b-s", "storms", "allow", scope="setting", scope_id="s1"))
        assert files(folder) == before
        data = session_zero_to_payload(SessionZero((
            Boundary(opaque("b-s"), "player", ANA, "storms", "allow", ANA),)), 1)
        data["boundaries"][0].update(scope="setting", scope_id="s1")  # stored by hand, or by another build
        write_raw(table, chat, data)
        refused("session_zero_unreadable", table.runner.generate, chat, table.player())
        assert ("session_zero", "session_zero_unreadable") in table.runner.open(chat).problems


# -- a group limit whose setter has left the table ----------------------------------------------------------

def test_the_owner_withdraws_a_group_limit_whose_setter_has_left_the_table(cards):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide.")]) as table:
        tamsin = attach_tamsin(table.store, table.owner, chat)
        hand(table, chat, tamsin, BOB)
        asking(table, chat)
        table.runner.change_boundaries(chat, table.player(BOB), setting(
            BOB, "b-camp", "storms", "check_in", scope="campaign", scope_id=chat.campaign_id))
        table.runner.change_boundaries(chat, table.player(BOB), setting(BOB, "b-own", "spiders", "avoid"))
        # while its setter is at the table it is theirs alone
        refused("not_your_boundary", table.runner.change_boundaries, chat, table.owner, withdrawing(OWNER, "b-camp"))
        hand(table, chat, tamsin, DIRECTOR)  # Bob leaves the table
        refused("held_by_boundary", table.runner.generate, chat, table.player())
        refused("not_your_boundary", table.runner.change_boundaries, chat, table.player(), withdrawing(ANA, "b-camp"))
        # a player's own boundary stays theirs, wherever they are
        refused("not_your_boundary", table.runner.change_boundaries, chat, table.owner, withdrawing(OWNER, "b-own"))
        view = table.runner.change_boundaries(chat, table.owner, withdrawing(OWNER, "b-camp"))
        assert view.revision == 3 and not view.held
        assert [b["boundary"] for b in stored(folder, chat)["boundaries"]] == [opaque("b-own")]
        assert table.runner.generate(chat, table.player()).lines


def test_the_owners_withdrawal_is_judged_for_the_table_as_it_is_written(cards, monkeypatch):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide.")]) as table:
        tamsin = attach_tamsin(table.store, table.owner, chat)
        hand(table, chat, tamsin, BOB)
        asking(table, chat)
        table.runner.change_boundaries(chat, table.player(BOB), setting(
            BOB, "b-camp", "storms", "check_in", scope="campaign", scope_id=chat.campaign_id))
        hand(table, chat, tamsin, DIRECTOR)
        read, seated = table.runner._context, []

        def then_seated(chat_ref):  # Bob sits down again after the owner's change read the chat
            found = read(chat_ref)
            if not seated:
                seated.append(hand(table, chat, tamsin, BOB))
            return found

        monkeypatch.setattr(table.runner, "_context", then_seated)
        refused("not_your_boundary", table.runner.change_boundaries, chat, table.owner, withdrawing(OWNER, "b-camp"))
        monkeypatch.undo()
        assert [b["boundary"] for b in stored(folder, chat)["boundaries"]] == [opaque("b-camp")]


# -- who is at the table cannot be read: never judged against the requester alone ----------------------------

def membership(table: Table, chat):
    """A party relation of the chat's two characters, so membership is read from a relation file."""
    from chat_fixtures import Ids
    from grimoire.core import Moment
    from grimoire.partystore import PartyLibrary
    scene = table.runner.open(chat)
    (mara,) = [actor for actor in scene.cast if actor != scene.persona and actor.instance_id.startswith("mara")]
    PartyLibrary(table.store, table.owner, clock=Clock(), new_id=Ids(), owner=OWNER).join(
        scene.persona, mara, at=Moment(1), provenance="director").commit()


def damage_membership(folder, chat) -> None:
    """A relation file whose store envelope reads but whose payload 033 cannot decode: no ``LoadProblem``,
    yet membership is unknown, so no cast is staged (``cast_unstaged``)."""
    (relation,) = folder.joinpath("campaigns", chat.campaign_id, "relations").glob("*.json")
    document = json.loads(relation.read_text(encoding="utf-8"))
    document["payload"]["since"] = {"sequence": "soon"}
    relation.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_unreadable_membership_never_lets_the_owner_withdraw_a_seated_players_group_limit(cards):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide.")]) as table:
        tamsin = attach_tamsin(table.store, table.owner, chat)
        hand(table, chat, tamsin, BOB)
        asking(table, chat)
        membership(table, chat)
        table.runner.change_boundaries(chat, table.player(BOB), setting(
            BOB, "b-camp", "storms", "check_in", scope="campaign", scope_id=chat.campaign_id))
        damage_membership(folder, chat)
        scene = table.runner.open(chat)
        assert scene.staged is None and ("cast_unstaged", "relations_unreadable") in scene.problems
        assert not [code for code, _ in scene.problems if code == "party"]  # no LoadProblem says so
        refused("not_your_boundary", table.runner.change_boundaries, chat, table.owner, withdrawing(OWNER, "b-camp"))
        assert [b["boundary"] for b in stored(folder, chat)["boundaries"]] == [opaque("b-camp")]
        assert table.runner.boundaries(chat, table.owner).held


def test_a_cast_that_cannot_be_read_as_a_take_lands_holds_any_players_limit(cards, monkeypatch):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide.")]) as table:
        tamsin = attach_tamsin(table.store, table.owner, chat)
        asking(table, chat)
        mara = next(actor for actor in table.runner.open(chat).npcs if actor != tamsin)
        membership(table, chat)
        write_raw(table, chat, session_zero_to_payload(SessionZero((
            Boundary(opaque("b-bob"), "player", BOB, "spiders", "avoid", BOB),)), 1))  # Bob is not seated
        run_turn = table.harness.runtime.run_turn

        def then_damaged(*args, **kwargs):
            outcome = run_turn(*args, **kwargs)
            damage_membership(folder, chat)  # who is present can no longer be told: Bob may be at the table
            return outcome

        monkeypatch.setattr(table.harness.runtime, "run_turn", then_damaged)
        take = table.runner.generate(chat, table.player(), speakers=[mara])
        monkeypatch.undo()
        assert (take.lines, take.outcome) == ((), "halted")
        assert [(c.status, c.reason) for c in take.calls] == [("rejected", "held_by_boundary")]


def test_a_cast_that_cannot_be_read_holds_nothing_without_a_limit(cards, monkeypatch):
    folder, chat = cards
    with Table(folder, [say("Safe as the tide.")]) as table:
        asking(table, chat)
        membership(table, chat)
        run_turn = table.harness.runtime.run_turn

        def then_damaged(*args, **kwargs):
            outcome = run_turn(*args, **kwargs)
            damage_membership(folder, chat)
            return outcome

        monkeypatch.setattr(table.harness.runtime, "run_turn", then_damaged)
        take = table.runner.generate(chat, table.player())
        monkeypatch.undo()
        assert take.lines  # no configuration: an unknown table changes nothing session zero decides


def test_a_character_with_two_recorded_controllers_leaves_the_table_unknown():
    from types import SimpleNamespace
    from grimoire.core.identity import InstanceRef
    from grimoire.play.scene import _table_known
    actor = InstanceRef("c-1", "character", "pc-1")
    control = [SimpleNamespace(campaign_id="c-1", actor_id="pc-1", controller_id=who) for who in (ANA, BOB)]
    scene = SimpleNamespace(staged=object(), problems=(), cast=(actor,), controls=tuple(control[:1]))
    assert _table_known(scene)
    assert not _table_known(SimpleNamespace(**{**vars(scene), "controls": tuple(control)}))  # neither is named
    assert not _table_known(SimpleNamespace(**{**vars(scene), "staged": None}))
    assert not _table_known(SimpleNamespace(**{**vars(scene), "problems": (("party_uncertain", str(actor)),)}))
    assert _table_known(SimpleNamespace(**{**vars(scene), "problems": (("missing_actor", str(actor)),)}))


# -- a standing scope conflict whose kept version is lost -----------------------------------------------------

@pytest.mark.parametrize("damage", ["lost", "corrupted"])
def test_a_standing_scope_conflict_whose_kept_version_is_gone_fails_closed(cards, tmp_path, damage):
    from test_scene_turn_merge import DESKTOP, PHONE, devices, synced
    folder, chat = cards
    with Table(folder) as table:
        table.runner.seat(chat, ANA)
    chat = devices((folder, chat), tmp_path)
    with Table(tmp_path / DESKTOP, [say("Desk.")]) as desk, Table(tmp_path / PHONE, [say("Phone.")]) as phone:
        synced(desk, phone)
        desk.runner.change_boundaries(chat, desk.player(), setting(ANA, "b-desk", "storms", "avoid"))
        phone.runner.change_boundaries(chat, phone.player(), setting(
            ANA, "b-phone", "sea_peril", "allow", scope="campaign", scope_id=chat.campaign_id))
        path = "/".join(address_parts(chat.scope))
        assert synced(desk, phone).conflict(path).code == "concurrent"
        phone.runner.post(chat, phone.player(), '"Well?"')
        refused("held_by_boundary", phone.runner.generate, chat, phone.player())
        (slot,) = [slot for slot in phone.store.conflicts() if path.rsplit("/", 1)[-1].removesuffix(".json") in slot]
        kept = (tmp_path / PHONE).joinpath(*slot.split("/"))
        if damage == "lost":
            kept.unlink()
        else:
            kept.write_bytes(kept.read_bytes().replace(b"avoid", b"allow"))
        # the conflict still stands, unresolved and undismissed: the desk's limit may be the one in force
        refused("session_zero_unreadable", phone.runner.generate, chat, phone.player())
        assert phone.runner.boundaries(chat, phone.player()).held
        assert ("session_zero", "session_zero_unreadable") in phone.runner.open(chat).problems
        assert not phone.harness.transport.sends
