"""028's turn merge end to end: two devices play one chat offline, sync, and the chat goes on from one branch.

A desktop and a phone each play turn 2 of one card chat offline. Sync carries both turn files, so each library
then holds both: the chat is forked, ``open`` names the branches, and every write waits. The library's owner
chooses one branch (a player may not); the other is set aside from turn 2 on, kept but heard by no one, and play
goes on. Two devices choosing differently offline end in a conflict both see, never a silent flip; a turn that
was played after a set-aside turn on a third device and arrives later is set aside too. Over the synthetic card
library; every model call is a scripted fake.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from hashlib import sha256

import pytest

from scene_fixtures import ANA, ANYA, MARA_VELL, Table, card_library, say
from store_helpers import OWNER

from chat_fixtures import Clock
from grimoire.archaeologystore import StImporter
from grimoire.core.identity import TurnRef
from grimoire.scene import TURN_SCHEMA, SceneRefused, turn_id, turn_to_payload
from grimoire.scene.branches import replay_tag
from grimoire.store import ScopeRecord, etag
from grimoire.store.envelope import scope_record_document
from grimoire.store.jsonfmt import render
from grimoire.store.replica import CanonicalScan
from grimoire.sync import identify, resolve, sync_until_quiet
from grimoire.sync.turns import turn_named

DESKTOP, PHONE, TABLET = "desktop", "phone", "tablet"


def tag(device: str) -> str:
    return sha256(device.encode()).hexdigest()[:8]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    folder, chat, _ = card_library(tmp_path_factory.mktemp("built"))
    with Table(folder, [say("Dark, and the harbor pays for it.")]) as table:  # turn 1, before any copy
        table.runner.seat(chat, ANA)
        table.runner.begin(chat, table.player())
        table.runner.keep(chat, table.player(), 1)
    return folder, chat


def devices(built, tmp_path, names=(DESKTOP, PHONE)):
    folder, chat = built
    for name in names:
        shutil.copytree(folder, tmp_path / name)
        with Table(tmp_path / name) as table:
            identify(table.store, name)
    return chat


def refused(reason: str, call, *args, **kwargs) -> None:
    with pytest.raises(SceneRefused) as error:
        call(*args, **kwargs)
    assert error.value.reason == reason, error.value


def synced(left: Table, right: Table, names=(DESKTOP, PHONE)):
    return sync_until_quiet(left.store, right.store, dict(zip(names, (left.owner, right.owner))))


def play(table: Table, chat, line: str) -> None:
    """One whole turn: the player's line, a reply, kept."""
    runner, ana = table.runner, table.player()
    runner.post(chat, ana, f'"{line}"')
    runner.generate(chat, ana)
    runner.keep(chat, ana, 1)


def turn_files(folder, chat) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted((folder / "campaigns" / chat.campaign_id / "turns").glob("*.json"))}


def test_two_offline_devices_sync_fork_the_owner_chooses_and_play_goes_on_hearing_one_branch(built, tmp_path):
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP, [say("Answered on the desktop."), say("Mind the tide.")]) as desk, \
            Table(tmp_path / PHONE, [say("Answered on the phone."), say("The phone goes on.")]) as phone:
        assert synced(desk, phone).conflicts == ()
        play(desk, chat, "Asked on the desktop.")
        play(phone, chat, "Asked on the phone.")
        report = synced(desk, phone)
        assert report.conflicts == ()
        ours, theirs = turn_id(chat, 2, tag(DESKTOP)), turn_id(chat, 2, tag(PHONE))
        assert {f"campaigns/{chat.campaign_id}/turns/{t}.json" for t in (ours, theirs)} <= {p for p, _ in report.copied}
        for table, mine in ((desk, ours), (phone, theirs)):
            scene = table.runner.open(chat)
            assert ("turns", f"{chat}:turn_fork") in scene.problems
            (detail,) = [text for code, text in scene.problems if code == "turn_fork"]
            assert detail.startswith("turn 2:") and ours in detail and theirs in detail
            assert f"{mine} ({mine.rsplit('-', 1)[1]}, from 2: 1 turn, this device)" in detail
            fork = scene.turns.fork
            assert [(b.head, b.device, b.set_aside) for b in fork.branches] == sorted(
                [(ours, tag(DESKTOP), False), (theirs, tag(PHONE), False)], key=lambda b: b[1])
            refused("turn_fork", table.runner.post, chat, table.player(), '"Hello?"')
        # nothing is chosen automatically, and only the owner chooses, in a chat this library can read
        chat_file = tmp_path / DESKTOP / "campaigns" / chat.campaign_id / "chats" / f"{chat.chat_id}.json"
        saved, before = chat_file.read_bytes(), turn_files(tmp_path / DESKTOP, chat)
        chat_file.write_bytes(b"{not json")
        with pytest.raises(SceneRefused) as unread:
            desk.runner.choose(chat, desk.owner, ours)
        assert unread.value.reason == "no_chat"
        assert turn_files(tmp_path / DESKTOP, chat) == before  # refused before anything is written
        chat_file.write_bytes(saved)
        refused("not_owner", desk.runner.choose, chat, desk.player(), ours)
        refused("not_a_branch", desk.runner.choose, chat, desk.owner, turn_id(chat, 1))
        scene = desk.runner.choose(chat, desk.owner, ours)
        assert scene.turns.fork is None and scene.turns.complete
        assert [r.turn_id for r in scene.records] == [turn_id(chat, 1), ours]
        assert [s.record.turn_id for s in scene.turns.set_aside] == [theirs]
        refused("not_forked", desk.runner.choose, chat, desk.owner, theirs)
        # play goes on; the set-aside branch is heard by no one and is in nobody's view
        desk.runner.post(chat, desk.player(), '"And the debts?"')
        desk.runner.generate(chat, desk.player())
        prompt = desk.harness.prompts()[-1]
        assert "Asked on the desktop." in prompt and "Answered on the desktop." in prompt
        assert "Asked on the phone." not in prompt and "Answered on the phone." not in prompt
        refused("unknown_line", desk.runner.strike, chat, desk.owner, f"{theirs}.1.1")
        desk.runner.keep(chat, desk.player(), 1)
        # the choice and the new turn reach the phone, which goes on from the same line with its own tag
        assert synced(desk, phone).conflicts == ()
        scene = phone.runner.open(chat)
        assert scene.turns.fork is None and [r.turn_id for r in scene.records] == [
            turn_id(chat, 1), ours, turn_id(chat, 3, tag(DESKTOP))]
        phone.runner.post(chat, phone.player(), '"Where now?"')
        phone.runner.generate(chat, phone.player())
        prompt = phone.harness.prompts()[-1]
        assert "And the debts?" in prompt and "Asked on the phone." not in prompt
        assert phone.runner.open(chat).last.follows == turn_id(chat, 3, tag(DESKTOP))
        assert phone.runner.open(chat).last.turn_id == turn_id(chat, 4, tag(PHONE))
        assert synced(desk, phone).conflicts == ()
    assert turn_files(tmp_path / DESKTOP, chat) == turn_files(tmp_path / PHONE, chat)  # converged, nothing dropped


def test_a_device_that_chose_the_other_branch_plays_its_number_again_under_a_fresh_tag(built, tmp_path):
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP, [say("Desk two."), say("Desk three."), say("Desk again.")]) as desk, \
            Table(tmp_path / PHONE, [say("Phone two.")]) as phone:
        synced(desk, phone)
        play(desk, chat, "Second on the desktop.")
        play(desk, chat, "Third on the desktop.")
        play(phone, chat, "Second on the phone.")
        synced(desk, phone)
        theirs = turn_id(chat, 2, tag(PHONE))
        scene = desk.runner.choose(chat, desk.owner, theirs)
        assert [s.record.turn_id for s in scene.turns.set_aside] == [
            turn_id(chat, 2, tag(DESKTOP)), turn_id(chat, 3, tag(DESKTOP))]  # the whole branch, from turn 2
        desk.runner.post(chat, desk.player(), '"Third, again."')  # its own turn 3 is taken (set aside)
        again = desk.runner.open(chat).last
        assert again.turn_id == turn_id(chat, 3, replay_tag(tag(DESKTOP), 1)) and again.follows == theirs
        assert [code for code, _ in desk.runner.open(chat).problems if code.startswith("turn")] == []
        assert synced(desk, phone).conflicts == ()
        assert [r.turn_id for r in phone.runner.open(chat).records] == [turn_id(chat, 1), theirs, again.turn_id]


def test_the_same_choice_on_both_devices_offline_converges_byte_for_byte(built, tmp_path):
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP, [say("Desk.")]) as desk, Table(tmp_path / PHONE, [say("Phone.")]) as phone:
        synced(desk, phone)
        play(desk, chat, "On the desktop.")
        play(phone, chat, "On the phone.")
        synced(desk, phone)
        ours = turn_id(chat, 2, tag(DESKTOP))
        desk.runner.choose(chat, desk.owner, ours)
        phone.runner.choose(chat, phone.owner, ours)
        report = synced(desk, phone)
        assert report.conflicts == () and not [p for p, _ in report.copied if "/turns/" in p]
    assert turn_files(tmp_path / DESKTOP, chat) == turn_files(tmp_path / PHONE, chat)


def test_different_offline_choices_are_a_conflict_on_both_devices_until_the_owner_chooses_again(built, tmp_path):
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP, [say("Desk.")]) as desk, \
            Table(tmp_path / PHONE, [say("Phone."), say("Phone again.")]) as phone:
        synced(desk, phone)
        play(desk, chat, "On the desktop.")
        play(phone, chat, "On the phone.")
        synced(desk, phone)
        ours, theirs = turn_id(chat, 2, tag(DESKTOP)), turn_id(chat, 2, tag(PHONE))
        desk.runner.choose(chat, desk.owner, ours)
        phone.runner.choose(chat, phone.owner, theirs)
        assert synced(desk, phone).conflicts == ()  # each marked the other's branch: two files, both copied
        for table in (desk, phone):  # never a flip to one device's choice: the same conflict on both
            scene = table.runner.open(chat)
            assert scene.turns.fork.state == "conflict" and scene.turns.set_aside == ()
            assert any(code == "turn_fork" and "choices conflict" in text for code, text in scene.problems)
            assert {r.turn_id for r in scene.records} == {turn_id(chat, 1), ours, theirs}
            refused("turn_fork", table.runner.generate, chat, table.player())
        phone.runner.choose(chat, phone.owner, ours)  # the owner chooses again, on either device
        assert synced(desk, phone).conflicts == ()
        for table in (desk, phone):
            scene = table.runner.open(chat)
            assert scene.turns.fork is None and [r.turn_id for r in scene.records] == [turn_id(chat, 1), ours]
        play(phone, chat, "The phone goes on.")


def test_a_turn_played_after_a_set_aside_one_on_a_third_device_is_set_aside_when_it_arrives(built, tmp_path):
    names = (DESKTOP, PHONE, TABLET)
    chat = devices(built, tmp_path, names)
    with Table(tmp_path / DESKTOP, [say("Desk."), say("Desk again.")]) as desk, \
            Table(tmp_path / PHONE, [say("Phone.")]) as phone, \
            Table(tmp_path / TABLET, [say("Tablet.")]) as tablet:
        tables = {DESKTOP: desk, PHONE: phone, TABLET: tablet}

        def between(a: str, b: str):
            return synced(tables[a], tables[b], (a, b))
        for a, b in ((DESKTOP, PHONE), (DESKTOP, TABLET), (PHONE, TABLET)):
            between(a, b)
        play(phone, chat, "On the phone.")
        between(PHONE, TABLET)  # the tablet plays on after the phone's turn 2
        tablet.runner.post(chat, tablet.player(), '"On the tablet, after the phone."')
        late = turn_id(chat, 3, tag(TABLET))
        play(desk, chat, "On the desktop.")
        between(DESKTOP, PHONE)
        ours, theirs = turn_id(chat, 2, tag(DESKTOP)), turn_id(chat, 2, tag(PHONE))
        desk.runner.choose(chat, desk.owner, ours)
        report = between(DESKTOP, TABLET)
        scene = desk.runner.open(chat)
        assert scene.turns.fork is None and [r.turn_id for r in scene.records] == [turn_id(chat, 1), ours]
        assert [s.record.turn_id for s in scene.turns.set_aside] == [theirs, late]
        # the desktop and the tablet each received the phone's turn 2 from another device, so they share no base
        # for it: its mark is an ordinary keep-both conflict there. The tablet stays forked, and neither plays on
        # until it is resolved, since each reads its own version of that turn
        path = f"campaigns/{chat.campaign_id}/turns/{theirs}.json"
        conflict = report.conflict(path)
        assert conflict is not None and conflict.code == "missing_ancestor"
        assert tablet.runner.open(chat).turns.fork.state == "open"
        for table in (desk, tablet):
            assert table.runner.open(chat).turns.conflicted == (theirs,)
        refused("turn_conflict", play, desk, chat, "Too soon.")
        expected = {name: etag((tmp_path / name / path).read_bytes()) for name in (DESKTOP, TABLET)}
        resolve(desk.store, tablet.store, path, expected[DESKTOP], expected,
                {DESKTOP: desk.owner, TABLET: tablet.owner})
        assert desk.runner.open(chat).turns.conflicted == ()
        play(desk, chat, "The desktop goes on.")  # turn 3 of the line; the tablet's turn 3 takes no number
        assert between(DESKTOP, TABLET).conflicts == ()
        scene = tablet.runner.open(chat)
        assert scene.turns.fork is None and late in [s.record.turn_id for s in scene.turns.set_aside]
        assert [r.turn_id for r in scene.records] == [r.turn_id for r in desk.runner.open(chat).records]


def test_a_pause_on_a_branch_set_aside_still_holds_the_chosen_line(built, tmp_path):
    """016's pause is a safety tool: a choice never lifts it. The phone's player paused after its turn 2; the
    owner chooses the desktop's branch, and the chat stays paused until that player (or the owner) resumes."""
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP, [say("Desk."), say("Desk again.")]) as desk, \
            Table(tmp_path / PHONE, [say("Phone.")]) as phone:
        synced(desk, phone)
        play(desk, chat, "On the desktop.")
        play(phone, chat, "On the phone.")
        phone.runner.request(chat, phone.player(), "pause")
        synced(desk, phone)
        ours = turn_id(chat, 2, tag(DESKTOP))
        scene = desk.runner.choose(chat, desk.owner, ours)
        assert scene.turns.fork is None and scene.paused and scene.last.turn_id == ours
        refused("paused", desk.runner.post, chat, desk.player(), '"Onward?"')
        assert [s.record.paused for s in scene.turns.set_aside] == [True]  # the pause stays where it was made
        desk.runner.resume(chat, desk.player())  # the player who paused, on either device
        assert not desk.runner.open(chat).paused
        play(desk, chat, "Onward.")
        assert synced(desk, phone).conflicts == () and not phone.runner.open(chat).paused


def test_different_choices_with_pauses_stay_a_conflict_on_both_and_each_pause_stays_its_makers(built, tmp_path):
    """A choice writes only marks, so two different offline choices touch two files and conflict on both devices
    even with pauses about; each pause stays until its maker or the owner lifts it."""
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP, [say("Desk."), say("Desk again.")]) as desk, \
            Table(tmp_path / PHONE, [say("Phone.")]) as phone:
        synced(desk, phone)
        play(desk, chat, "On the desktop.")
        play(phone, chat, "On the phone.")
        phone.runner.request(chat, phone.player(), "pause")  # the player, on the phone's turn 2
        desk.runner.request(chat, desk.owner, "pause")  # the owner, on the desktop's
        synced(desk, phone)
        ours, theirs = turn_id(chat, 2, tag(DESKTOP)), turn_id(chat, 2, tag(PHONE))
        desk.runner.choose(chat, desk.owner, ours)
        phone.runner.choose(chat, phone.owner, theirs)
        assert synced(desk, phone).conflicts == ()  # two marks on two files, both copied
        for table in (desk, phone):
            scene = table.runner.open(chat)
            assert scene.turns.fork.state == "conflict" and scene.paused
            refused("turn_fork", table.runner.resume, chat, table.owner)
            refused("turn_fork", table.runner.post, chat, table.player(), '"Onward?"')
        scene = desk.runner.choose(chat, desk.owner, ours)
        assert scene.turns.fork is None and len(scene.turns.pauses) == 2
        desk.runner.resume(chat, desk.player())  # lifts the player's own pause only
        assert [s.record.paused_by for s in desk.runner.open(chat).turns.pauses] == [OWNER]
        refused("paused", desk.runner.post, chat, desk.player(), '"Onward?"')
        desk.runner.resume(chat, desk.owner)
        play(desk, chat, "Onward.")
        assert synced(desk, phone).conflicts == ()
        lines = [[r.turn_id for r in table.runner.open(chat).records] for table in (desk, phone)]
        assert lines[0] == lines[1]


def test_a_pause_on_a_branch_a_choice_from_elsewhere_sets_aside_is_never_lifted_by_sync(built, tmp_path):
    names = (DESKTOP, PHONE, TABLET)
    chat = devices(built, tmp_path, names)
    with Table(tmp_path / DESKTOP, [say("Desk.")]) as desk, Table(tmp_path / PHONE, [say("Phone.")]) as phone, \
            Table(tmp_path / TABLET, [say("Tablet."), say("Tablet again.")]) as tablet:
        tables = {DESKTOP: desk, PHONE: phone, TABLET: tablet}

        def between(a: str, b: str):
            return synced(tables[a], tables[b], (a, b))
        for a, b in ((DESKTOP, PHONE), (DESKTOP, TABLET), (PHONE, TABLET)):
            between(a, b)
        play(phone, chat, "Phone two.")
        between(PHONE, TABLET)
        play(tablet, chat, "Tablet three, after the phone.")
        tablet.runner.request(chat, tablet.player(), "pause")  # the tablet's player stops play
        play(desk, chat, "Desk two.")
        between(DESKTOP, PHONE)
        desk.runner.choose(chat, desk.owner, turn_id(chat, 2, tag(DESKTOP)))
        between(DESKTOP, PHONE)
        between(PHONE, TABLET)
        scene = tablet.runner.open(chat)
        assert scene.turns.fork is None and scene.paused
        assert turn_id(chat, 3, tag(TABLET)) in [s.record.turn_id for s in scene.turns.pauses]
        refused("paused", tablet.runner.post, chat, tablet.player(), '"Play goes on?"')
        tablet.runner.resume(chat, tablet.player())
        play(tablet, chat, "Now it goes on.")


def test_a_pause_landing_between_choose_reading_the_chat_and_committing_refuses_the_choice(built, tmp_path):
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP, [say("Desk.")]) as desk, \
            Table(tmp_path / PHONE, [say("P2."), say("P3.")]) as phone:
        synced(desk, phone)
        play(desk, chat, "On the desktop.")
        play(phone, chat, "Phone two.")
        play(phone, chat, "Phone three.")
        synced(desk, phone)
        library = desk.runner.scenes
        stale = library.turns(chat)
        three = next(s for s in stale.turns if s.record.turn_id == turn_id(chat, 3, tag(PHONE)))
        txn = desk.store.transaction()  # a pause deep in the phone's branch lands (a peer's transfer, say)
        txn.put(ScopeRecord(three.record.ref, TURN_SCHEMA,
                            turn_to_payload(replace(three.record, paused=True, paused_by=OWNER)),
                            owner=three.owner, extra=three.extra), three.etag)
        txn.commit(desk.owner)
        refused("turn_changed", library.choose, stale, turn_id(chat, 2, tag(DESKTOP)), OWNER)
        scene = desk.runner.choose(chat, desk.owner, turn_id(chat, 2, tag(DESKTOP)))
        assert scene.turns.fork is None and scene.paused


def test_a_turn_never_lands_without_its_chat_or_the_turn_it_follows(built, tmp_path):
    chat = devices(built, tmp_path)
    turns = f"campaigns/{chat.campaign_id}/turns"
    with Table(tmp_path / DESKTOP) as desk, Table(tmp_path / PHONE, [say("Two."), say("Three.")]) as phone:
        synced(desk, phone)
        play(phone, chat, "Second.")
        play(phone, chat, "Third.")
        two, three = (f"{turns}/{turn_id(chat, n, tag(PHONE))}.json" for n in (2, 3))
        chat_file = tmp_path / DESKTOP / "campaigns" / chat.campaign_id / "chats" / f"{chat.chat_id}.json"
        saved = chat_file.read_bytes()
        chat_file.write_bytes(b"{not json")  # the desktop's chat record cannot be read: unknown, never absent
        report = synced(desk, phone)
        for path in (two, three):
            assert report.conflict(path).code == "head_dependency"
            assert not (tmp_path / DESKTOP / path).exists()
        chat_file.write_bytes(saved)
        phone_two = tmp_path / PHONE / two
        played = phone_two.read_bytes()
        phone_two.write_bytes(b"{not json")  # the turn the phone's turn 3 follows cannot be read there
        report = synced(desk, phone)
        assert report.conflict(three).code == "head_dependency" and two in report.conflict(three).detail
        assert not (tmp_path / DESKTOP / three).exists()
        phone_two.write_bytes(played)
        assert synced(desk, phone).conflicts == ()
        assert [r.turn_id for r in desk.runner.open(chat).records][-1] == turn_id(chat, 3, tag(PHONE))


def test_a_turn_written_before_follows_never_lands_without_the_turn_its_device_played_before(built, tmp_path):
    """A turn with no ``follows`` (written before 028) is placed after its own device's turn before it. The
    phone has a turn 2 of its own, so the desktop's turn 3 landing alone would read as played after the phone's
    turn 2: it waits until the desktop's own turn 2 can land with it."""
    chat = devices(built, tmp_path)
    turns = f"campaigns/{chat.campaign_id}/turns"
    with Table(tmp_path / DESKTOP, [say("Desk two."), say("Desk three.")]) as desk, \
            Table(tmp_path / PHONE, [say("Phone two.")]) as phone:
        play(desk, chat, "Second, on the desktop.")
        play(desk, chat, "Third, on the desktop.")
        stored = desk.runner.scenes.turns(chat).last
        payload = {key: value for key, value in turn_to_payload(stored.record).items()
                   if key not in ("follows", "set_aside")}  # as a turn written before 028
        txn = desk.store.transaction()
        txn.put(ScopeRecord(stored.record.ref, TURN_SCHEMA, payload, owner=stored.owner, extra=stored.extra),
                stored.etag)
        txn.commit(desk.owner)
        play(phone, chat, "Second, on the phone.")
        two, three = (f"{turns}/{turn_id(chat, n, tag(DESKTOP))}.json" for n in (2, 3))
        desk_two = tmp_path / DESKTOP / two
        played = desk_two.read_bytes()
        desk_two.write_bytes(b"{not json")  # the desktop's own turn 2 cannot be read there, so it cannot travel
        report = synced(desk, phone)
        assert report.conflict(three).code == "head_dependency" and two in report.conflict(three).detail
        assert [r.turn_id for r in phone.runner.open(chat).records] == [turn_id(chat, 1), turn_id(chat, 2, tag(PHONE))]
        desk_two.write_bytes(played)
        assert synced(desk, phone).conflicts == ()
        fork = phone.runner.open(chat).turns.fork
        assert {b.head: b.turns for b in fork.branches}[turn_id(chat, 2, tag(DESKTOP))] == (
            turn_id(chat, 2, tag(DESKTOP)), turn_id(chat, 3, tag(DESKTOP)))


@pytest.mark.parametrize("origin, needed", [
    (["c.000002-aaaaaaaa", "c.000002", "c.000002-bbbbbbbb"], ["c.000002-aaaaaaaa"]),  # its own device's
    (["c.000002", "c.000002-bbbbbbbb"], ["c.000002"]),  # else the untagged one
    (["c.000002-bbbbbbbb"], ["c.000002-bbbbbbbb"]),  # else the only one
    (["c.000002-bbbbbbbb", "c.000002-dddddddd"], ["c.000002-bbbbbbbb", "c.000002-dddddddd"]),  # a gap on both
])
def test_a_turn_written_before_follows_needs_what_its_origin_places_it_after(origin, needed):
    """Where the origin places such a turn, the receiver places it the same way: what that takes comes too, and
    an unreadable turn there (the first of each list) counts as present, so the turn waits, never as absent."""
    folder = "campaigns/q/turns"
    path = f"{folder}/c.000003-aaaaaaaa.json"
    ref = TurnRef("q", "c.000003-aaaaaaaa")
    data = render(scope_record_document(ScopeRecord(ref, TURN_SCHEMA, {"chat": "c", "number": 3}, owner=OWNER), None))
    paths = [f"{folder}/{name}.json" for name in origin]
    scan = CanonicalScan({other: b"{}" for other in paths[1:]} | {"campaigns/r/turns/c.000002.json": b"{}"},
                         {paths[0]: "unreadable"})
    assert turn_named(path, data, scan) == [f"{folder}/{name}.json" for name in needed]
    with_follows = ScopeRecord(ref, TURN_SCHEMA, {"chat": "c", "number": 3, "follows": "c.000002"}, owner=OWNER)
    assert turn_named(path, render(scope_record_document(with_follows, None)), scan) == []


def test_a_forked_imported_chat_hears_its_history_then_the_chosen_branch_only(built, tmp_path):
    """035 M2's imported history comes before turn 1 on every branch; a choice sets aside only played turns."""
    folder, _ = built
    shutil.copytree(folder, tmp_path / "base")
    rows = [{"user_name": "Anya Rook", "character_name": "Mara Vell", "create_date": "2026-01-02@10h00m00s"},
            {"name": "Mara Vell", "is_user": False, "mes": "The lamps are out tonight.", "original_avatar": "m.png"},
            {"name": "Anya Rook", "is_user": True, "mes": "Then we wait for the tide.",
             "force_avatar": "User Avatars/a.png"}]
    data = ("\n".join(json.dumps(row) for row in rows) + "\n").encode("utf-8")
    with Table(tmp_path / "base") as base:
        importer = StImporter(base.store, base.owner, clock=Clock(), owner=OWNER)
        avatars = {speaker.avatar: speaker.id for speaker in importer.scan(data).speakers}
        plan = importer.plan(data, speakers={avatars["m.png"]: MARA_VELL, avatars["User Avatars/a.png"]: ANYA},
                             name="harbor.jsonl")
        plan.commit()
        chat = plan.chat
        base.runner.seat(chat, ANA)
    for name in (DESKTOP, PHONE):
        shutil.copytree(tmp_path / "base", tmp_path / name)
        with Table(tmp_path / name) as table:
            identify(table.store, name)
    with Table(tmp_path / DESKTOP, [say("Desk reply.")]) as desk, \
            Table(tmp_path / PHONE, [say("Phone reply."), say("Phone goes on.")]) as phone:
        play(desk, chat, "Asked on the desktop.")
        play(phone, chat, "Asked on the phone.")
        assert synced(desk, phone).conflicts == ()
        fork = phone.runner.open(chat).turns.fork
        assert fork.number == 1 and fork.after is None
        phone.runner.choose(chat, phone.owner, turn_id(chat, 1, tag(DESKTOP)))
        play(phone, chat, "And now?")
        prompt = phone.harness.prompts()[-1]
        assert prompt.index("The lamps are out tonight.") < prompt.index("Asked on the desktop.")
        assert "Then we wait for the tide." in prompt and "Asked on the phone." not in prompt
        assert phone.runner.open(chat).imported is not None


def test_a_pause_in_a_turn_the_other_device_edited_holds_on_both_until_resolved(built, tmp_path):
    """A pause lives in its turn's file, so the other device's offline edit of that turn (a strike) makes a
    keep-both conflict and each device reads its own version. Both still read the chat as paused (one from
    the version kept beside its own), and nothing is played on either until the conflict is resolved."""
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP, [say("Desk three.")]) as desk, \
            Table(tmp_path / PHONE, [say("Phone two."), say("Phone three.")]) as phone:
        synced(desk, phone)
        play(phone, chat, "Second.")
        synced(desk, phone)  # both hold turn 2, the chat's last turn
        two = turn_id(chat, 2, tag(PHONE))
        desk.runner.request(chat, desk.player(), "pause")  # the desktop's player stops play
        phone.runner.strike(chat, phone.owner, f"{two}.1.1")  # offline, the phone edits the same turn
        report = synced(desk, phone)
        path = f"campaigns/{chat.campaign_id}/turns/{two}.json"
        assert report.conflict(path).code == "concurrent"
        for table in (desk, phone):
            scene = table.runner.open(chat)
            assert scene.paused and scene.turns.conflicted == (two,)
            assert ("turn_conflict", two) in scene.problems
            refused("turn_conflict", table.runner.post, chat, table.player(), '"Play on?"')
            refused("turn_conflict", table.runner.resume, chat, table.owner)
        assert phone.runner.open(chat).turns.kept_pauses == (desk.player().principal,)  # who paused, seen there
        expected = {name: etag((tmp_path / name / path).read_bytes()) for name in (DESKTOP, PHONE)}
        resolve(desk.store, phone.store, path, expected[DESKTOP], expected,
                {DESKTOP: desk.owner, PHONE: phone.owner})
        for table in (desk, phone):
            scene = table.runner.open(chat)
            assert scene.turns.conflicted == () and scene.paused  # the version kept is the paused one
        phone.runner.resume(chat, phone.player())
        play(phone, chat, "Third, once resolved.")
        assert synced(desk, phone).conflicts == () and not desk.runner.open(chat).paused
