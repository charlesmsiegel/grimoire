"""028: what holds a chat while sync leaves one of its turns in conflict, what ends that hold, and what a run in
flight does when sync lands a fork or a conflict meanwhile.

A turn conflict (019's keep-both) holds the chat on the device until it ends: a ``resolve``, a sync that finds
the devices agreeing, this device dismissing the peer's version (its local "keep mine"), or the owner ending
the pairing with a lost peer. It is found whatever the conflict's base (a device that lost its archive) and
whoever's keep first wrote the peer's bytes here (a third device). A run already in flight always lands its
take, with its calls' usage, over its own running take; a run not yet published never lands on a turn a fork
choice set aside. Over the synthetic card library; every model call is a scripted fake.
"""

from __future__ import annotations

from test_scene_turn_merge import (DESKTOP, PHONE, TABLET, built, devices, play, refused, synced, tag,  # noqa: F401
                                   turn_id)

from scene_fixtures import Table, say
from grimoire.peersync import unpair
from grimoire.scenestore.library import CONFLICT_RECORDS
from grimoire.store import etag
from grimoire.sync import dismiss, resolve, sync_until_quiet
from grimoire.sync.turns import open_conflicts


def _paused_and_struck(desk: Table, phone: Table, chat) -> tuple[str, str]:
    """Both devices hold the phone's turn 2; the desktop pauses it and the phone strikes its line, offline,
    then they sync: a keep-both conflict on turn 2. Its id and its path."""
    synced(desk, phone)
    play(phone, chat, "Second.")
    synced(desk, phone)
    two = turn_id(chat, 2, tag(PHONE))
    desk.runner.request(chat, desk.player(), "pause")
    phone.runner.strike(chat, phone.owner, f"{two}.1.1")
    path = f"campaigns/{chat.campaign_id}/turns/{two}.json"
    assert synced(desk, phone).conflict(path).code == "concurrent"
    return two, path


def _held(table: Table, chat, two: str) -> None:
    scene = table.runner.open(chat)
    assert scene.turns.conflicted == (two,) and scene.paused
    refused("turn_conflict", table.runner.post, chat, table.player(), '"Play on?"')
    refused("turn_conflict", table.runner.resume, chat, table.owner)


def test_a_run_in_flight_lands_its_take_and_usage_when_sync_brings_a_fork_mid_run(built, tmp_path):
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP, [say("Desk two.")]) as desk, Table(tmp_path / PHONE, [say("Phone two.")]) as phone:
        synced(desk, phone)
        play(phone, chat, "The phone's turn two.")
        desk.runner.post(chat, desk.player(), '"The desk asks."')
        real = desk.runner.runtime.run_turn

        def run_turn(*args, **kwargs):  # while the model call is out, a sync lands the phone's turn 2
            result = real(*args, **kwargs)
            synced(desk, phone)
            assert desk.runner.open(chat).turns.fork is not None
            return result
        desk.runner.runtime.run_turn = run_turn
        desk.runner.generate(chat, desk.player())
        mine = next(s for s in desk.runner.scenes.turns(chat).turns if s.record.turn_id == turn_id(chat, 2, tag(DESKTOP)))
        (take,) = mine.record.takes
        assert take.state == "proposed" and take.calls and all(call.attempts for call in take.calls)
        assert all(attempt.usage is not None for call in take.calls for attempt in call.attempts)


def test_a_run_in_flight_lands_its_take_when_sync_brings_a_conflict_on_its_turn_mid_run(built, tmp_path):
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP, [say("Desk two.")]) as desk, Table(tmp_path / PHONE) as phone:
        synced(desk, phone)
        desk.runner.post(chat, desk.player(), '"The desk asks."')
        synced(desk, phone)  # the phone holds the desktop's open turn 2
        real = desk.runner.runtime.run_turn

        def run_turn(*args, **kwargs):  # meanwhile the phone pauses that turn, and a sync brings the pause
            result = real(*args, **kwargs)
            phone.runner.request(chat, phone.player(), "pause")
            synced(desk, phone)
            assert desk.runner.open(chat).turns.conflicted == (turn_id(chat, 2, tag(DESKTOP)),)
            return result
        desk.runner.runtime.run_turn = run_turn
        desk.runner.generate(chat, desk.player())
        mine = desk.runner.scenes.turns(chat).last
        (take,) = mine.record.takes
        assert take.state == "proposed" and take.calls and all(call.attempts for call in take.calls)
        assert all(attempt.usage is not None for call in take.calls for attempt in call.attempts)


def test_a_run_never_starts_on_a_turn_a_fork_choice_set_aside_after_it_was_read(built, tmp_path):
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP, [say("Desk two."), say("Desk three.")]) as desk, \
            Table(tmp_path / PHONE, [say("Phone two.")]) as phone:
        synced(desk, phone)
        play(phone, chat, "The phone's turn two.")
        play(desk, chat, "The desk's turn two.")
        desk.runner.post(chat, desk.player(), '"The desk asks again."')  # the desktop's turn 3, open
        head, mine, theirs = (turn_id(chat, 2, tag(DESKTOP)), turn_id(chat, 3, tag(DESKTOP)),
                              turn_id(chat, 2, tag(PHONE)))
        before = desk.runner.scenes.turns(chat)
        real, prompts = desk.runner.scenes.write, len(desk.harness.prompts())

        def write(record, base, **kwargs):  # after generate read the open turn: a sync forks, the owner chooses
            if base is not None and record.turn_id == mine and record.running is not None:
                synced(desk, phone)
                desk.runner.choose(chat, desk.owner, theirs)  # the desktop's branch, turn 3 too, is set aside
            return real(record, base, **kwargs)
        desk.runner.scenes.write = write
        refused("turn_changed", desk.runner.generate, chat, desk.player())
        after = desk.runner.scenes.turns(chat)
        assert [s.record.turn_id for s in after.set_aside] == [head, mine]
        assert after.etags()[mine] == before.etags()[mine]  # nothing was played on the turn no one hears
        assert len(desk.harness.prompts()) == prompts


def test_dismissing_the_peers_version_ends_the_hold_on_this_device(built, tmp_path):
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP) as desk, Table(tmp_path / PHONE, [say("Two."), say("Three.")]) as phone:
        two, path = _paused_and_struck(desk, phone, chat)
        _held(desk, chat, two)
        found, complete = open_conflicts(desk.store, f"campaigns/{chat.campaign_id}/turns")
        assert complete and len(found[path]) == 1
        dismiss(desk.store, PHONE, path, etag(found[path][0]), desk.owner)  # the desktop keeps its own version
        scene = desk.runner.open(chat)
        assert scene.turns.conflicted == () and scene.paused  # its own pause still stands
        desk.runner.resume(chat, desk.owner)
        desk.runner.post(chat, desk.player(), '"The desktop goes on."')
        _held(phone, chat, two)  # the phone dismissed nothing: it still waits
        report = synced(desk, phone)
        assert report.conflict(path).code == "concurrent" and DESKTOP in report.conflict(path).dismissed
        assert desk.runner.open(chat).turns.conflicted == ()


def test_unpairing_a_lost_peer_ends_its_holds(built, tmp_path):
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP) as desk, Table(tmp_path / PHONE, [say("Two.")]) as phone:
        two, _ = _paused_and_struck(desk, phone, chat)
        _held(desk, chat, two)
        unpair(desk.store, desk.authority, PHONE)  # the phone is lost
        assert desk.runner.open(chat).turns.conflicted == ()
        desk.runner.resume(chat, desk.owner)
        desk.runner.post(chat, desk.player(), '"Without the phone."')


def test_a_damaged_kept_record_holds_only_chats_its_pair_synced_until_unpaired(built, tmp_path):
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP) as desk, Table(tmp_path / PHONE, [say("Two."), say("Three.")]) as phone:
        synced(desk, phone)
        peers = tmp_path / PHONE / ".sync" / "peers"
        (peers / TABLET).mkdir()  # a pair folder this version cannot read (a newer format, say), of no chat here
        (peers / TABLET / "keeps.json").write_text('{"format": "grimoire.sync.keeps/2"}\n')
        assert phone.runner.open(chat).turns.conflicted == ()
        play(phone, chat, "Not held by a stray record.")
        (peers / DESKTOP / "keeps.json").write_text('{"format": "grimoire.sync.keeps/2"}\n')  # the desktop's pair
        assert phone.runner.open(chat).turns.conflicted == (CONFLICT_RECORDS,)
        refused("turn_conflict", phone.runner.post, chat, phone.player(), '"Held?"')
        unpair(phone.store, phone.authority, DESKTOP)  # the owner's local way out
        assert phone.runner.open(chat).turns.conflicted == ()
        play(phone, chat, "Goes on.")


def test_a_conflict_whose_base_a_device_lost_still_holds_the_chat(built, tmp_path):
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP) as desk, Table(tmp_path / PHONE, [say("Two.")]) as phone:
        two, path = _paused_and_struck(desk, phone, chat)
        archive = tmp_path / PHONE / ".sync" / "peers" / DESKTOP / "archive.json"
        assert archive.exists()
        archive.unlink()  # the phone loses its archive: the conflict's base is no longer known
        report = synced(desk, phone)
        assert report.conflict(path).code == "missing_ancestor"
        for table in (desk, phone):
            _held(table, chat, two)
        assert phone.runner.open(chat).turns.kept_pauses == (desk.player().principal,)


def test_a_conflict_whose_peer_version_another_pair_kept_here_first_still_holds_the_chat(built, tmp_path):
    chat = devices(built, tmp_path, (DESKTOP, PHONE, TABLET))
    with Table(tmp_path / DESKTOP) as desk, Table(tmp_path / PHONE, [say("Two.")]) as phone, \
            Table(tmp_path / TABLET) as tablet:
        tables = {DESKTOP: desk, PHONE: phone, TABLET: tablet}

        def between(left: str, right: str):
            return synced(tables[left], tables[right], (left, right))
        play(phone, chat, "Second.")
        between(DESKTOP, PHONE)
        between(PHONE, TABLET)  # all three hold the phone's turn 2
        two = turn_id(chat, 2, tag(PHONE))
        path = f"campaigns/{chat.campaign_id}/turns/{two}.json"
        desk.runner.request(chat, desk.player(), "pause")
        phone.runner.strike(chat, phone.owner, f"{two}.1.1")
        between(PHONE, TABLET)  # the tablet takes the strike: no conflict there
        assert between(DESKTOP, TABLET).conflict(path) is not None  # the strike is kept on the desktop
        assert between(DESKTOP, PHONE).conflict(path).code == "concurrent"  # the same bytes, already kept
        mine = etag((tmp_path / DESKTOP / path).read_bytes())
        expected = {DESKTOP: mine, TABLET: etag((tmp_path / TABLET / path).read_bytes())}
        resolve(desk.store, tablet.store, path, mine, expected, {DESKTOP: desk.owner, TABLET: tablet.owner})
        _held(desk, chat, two)  # the phone's version still stands against it
        report = sync_until_quiet(desk.store, phone.store, {DESKTOP: desk.owner, PHONE: phone.owner})
        assert report.conflict(path).code == "concurrent"


def test_a_conflict_in_a_first_sync_holds_the_chat(built, tmp_path):
    """Two copies of one library that never synced: a pause on one and a strike on the other of turn 1."""
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP) as desk, Table(tmp_path / PHONE) as phone:
        one = turn_id(chat, 1)
        desk.runner.request(chat, desk.player(), "pause")
        phone.runner.strike(chat, phone.owner, f"{one}.1.1")
        assert synced(desk, phone).conflict(f"campaigns/{chat.campaign_id}/turns/{one}.json") is not None
        for table in (desk, phone):
            _held(table, chat, one)
