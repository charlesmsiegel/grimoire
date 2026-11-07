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

import json
import shutil
from dataclasses import replace

import pytest

from test_scene_turn_merge import (DESKTOP, PHONE, TABLET, built, devices, play, refused, synced, tag,  # noqa: F401
                                   turn_id)

from scene_fixtures import LABEL_POLICY, Table, say
from grimoire.peersync import unpair
from grimoire.scenestore.library import CONFLICT_RECORDS
from grimoire.store import etag
from grimoire.sync import ResolveRefused, dismiss, identify, resolve, sync_until_quiet
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


def test_a_run_in_flight_keeps_its_usage_when_its_turn_is_resolved_to_the_peers_version_mid_run(built, tmp_path):
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP, [say("Desk two.")]) as desk, Table(tmp_path / PHONE) as phone:
        synced(desk, phone)
        desk.runner.post(chat, desk.player(), '"The desk asks."')
        synced(desk, phone)  # the phone holds the desktop's open turn 2
        two = turn_id(chat, 2, tag(DESKTOP))
        path = f"campaigns/{chat.campaign_id}/turns/{two}.json"
        real = desk.runner.runtime.run_turn

        def run_turn(*args, **kwargs):  # meanwhile the phone pauses that turn, and the owner keeps the phone's
            result = real(*args, **kwargs)
            phone.runner.request(chat, phone.player(), "pause")
            assert synced(desk, phone).conflict(path).code == "concurrent"
            theirs = etag((tmp_path / PHONE / path).read_bytes())
            resolve(desk.store, phone.store, path, theirs, {DESKTOP: etag((tmp_path / DESKTOP / path).read_bytes()),
                                                            PHONE: theirs}, {DESKTOP: desk.owner, PHONE: phone.owner})
            return result
        desk.runner.runtime.run_turn = run_turn
        take = desk.runner.generate(chat, desk.player())
        chosen = phone.runner.scenes.turns(chat).last.record
        assert take.state == "stale" and take.lines == () and take.attempts()
        assert all(attempt.usage is not None for attempt in take.attempts())
        mine = desk.runner.scenes.turns(chat).last.record
        assert mine.takes == (take,) and mine.paused  # the phone's version, as the owner chose it, plus the usage
        assert chosen.takes == () and replace(mine, takes=()) == chosen
        assert synced(desk, phone).conflicts == ()  # an ordinary change of the agreed version: the phone takes it
        assert phone.runner.scenes.turns(chat).last.record == mine


def test_a_run_in_flight_keeps_its_intentions_when_its_turn_is_resolved_to_the_peers_version_mid_run(built,
                                                                                                    tmp_path):
    from grimoire.goals import Agenda, Approach, Goal

    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP, runtime=False) as desk:
        (npc,) = desk.runner.open(chat).npcs
    plan = Agenda(chat.campaign_id, npc, goals=(Goal("cargo", "Get the cargo past the harbour", 50, (
        Approach("wait", "Tell the pilot the cargo can wait"),)),))
    with Table(tmp_path / DESKTOP, [say("Desk two.")], agendas=[plan]) as desk, Table(tmp_path / PHONE) as phone:
        synced(desk, phone)
        desk.runner.post(chat, desk.player(), '"The desk asks."')
        synced(desk, phone)
        two = turn_id(chat, 2, tag(DESKTOP))
        path = f"campaigns/{chat.campaign_id}/turns/{two}.json"
        real = desk.runner.runtime.run_turn

        def run_turn(*args, **kwargs):  # meanwhile the owner resolves the turn to the phone's paused version
            result = real(*args, **kwargs)
            phone.runner.request(chat, phone.player(), "pause")
            assert synced(desk, phone).conflict(path).code == "concurrent"
            theirs = etag((tmp_path / PHONE / path).read_bytes())
            resolve(desk.store, phone.store, path, theirs, {DESKTOP: etag((tmp_path / DESKTOP / path).read_bytes()),
                                                            PHONE: theirs}, {DESKTOP: desk.owner, PHONE: phone.owner})
            return result
        desk.runner.runtime.run_turn = run_turn
        take = desk.runner.generate(chat, desk.player())
        chosen = phone.runner.scenes.turns(chat).last.record
        mine = desk.runner.scenes.turns(chat).last.record
        (intention,) = mine.intentions_of(take.take)
        assert take.state == "stale" and mine.takes == (take,) and take.attempts()
        assert (intention.actor, intention.approach_id) == (npc, "wait") and chosen.intentions == ()
        assert replace(mine, takes=(), intentions=()) == chosen  # the phone's version, plus this run's record


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


LABELS = json.dumps({"factors": [{"fact": "f1", "bearing": "favours"}], "stakes": ["She falls to the deck"]})


def test_a_standing_turn_conflict_holds_labelling_before_any_call(built, tmp_path):
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP, [say("Desk two.")], policy=LABEL_POLICY, labels=[say(LABELS)]) as desk, \
            Table(tmp_path / PHONE) as phone:
        synced(desk, phone)
        desk.runner.post(chat, desk.player(), "*I climb the rigging*")
        synced(desk, phone)
        phone.runner.request(chat, phone.player(), "pause")  # offline, the desktop runs a take on the same turn
        desk.runner.generate(chat, desk.player())
        two = turn_id(chat, 2, tag(DESKTOP))
        assert synced(desk, phone).conflict(f"campaigns/{chat.campaign_id}/turns/{two}.json").code == "concurrent"
        refused("turn_conflict", desk.runner.label, chat, desk.player())
        assert desk.labels.prompts() == [] and desk.runner.open(chat).pending.labels == ()


def test_a_labelling_in_flight_lands_with_its_usage_when_sync_brings_a_conflict_on_its_turn(built, tmp_path):
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP, policy=LABEL_POLICY, labels=[say(LABELS)]) as desk, \
            Table(tmp_path / PHONE) as phone:
        synced(desk, phone)
        desk.runner.post(chat, desk.player(), "*I climb the rigging*")
        synced(desk, phone)  # the phone holds the desktop's open action turn
        real = desk.runner.labeller.label

        def label(*args, **kwargs):  # while the call is out, the phone pauses that turn and a sync brings it
            reply = real(*args, **kwargs)
            phone.runner.request(chat, phone.player(), "pause")
            synced(desk, phone)
            assert desk.runner.open(chat).turns.conflicted == (turn_id(chat, 2, tag(DESKTOP)),)
            return reply
        desk.runner.labeller.label = label
        landed = desk.runner.label(chat, desk.player())
        assert landed.state == "proposed" and landed.factors
        (written,) = desk.runner.scenes.turns(chat).last.record.labels
        assert written == landed
        assert all(attempt.usage is not None for attempt in written.attempts()) and written.attempts()


def test_a_labelling_in_flight_keeps_its_usage_when_its_turn_is_resolved_to_the_peers_version(built, tmp_path):
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP, policy=LABEL_POLICY, labels=[say(LABELS)]) as desk, \
            Table(tmp_path / PHONE) as phone:
        synced(desk, phone)
        desk.runner.post(chat, desk.player(), "*I climb the rigging*")
        synced(desk, phone)
        two = turn_id(chat, 2, tag(DESKTOP))
        path = f"campaigns/{chat.campaign_id}/turns/{two}.json"
        real = desk.runner.labeller.label

        def label(*args, **kwargs):  # meanwhile the phone pauses that turn, and the owner keeps the phone's
            reply = real(*args, **kwargs)
            phone.runner.request(chat, phone.player(), "pause")
            assert synced(desk, phone).conflict(path).code == "concurrent"
            theirs = etag((tmp_path / PHONE / path).read_bytes())
            resolve(desk.store, phone.store, path, theirs, {DESKTOP: etag((tmp_path / DESKTOP / path).read_bytes()),
                                                            PHONE: theirs}, {DESKTOP: desk.owner, PHONE: phone.owner})
            return reply
        desk.runner.labeller.label = label
        landed = desk.runner.label(chat, desk.player())
        chosen = phone.runner.scenes.turns(chat).last.record
        assert landed.state == "superseded" and (landed.factors, landed.stakes) == ((), ()) and landed.attempts()
        assert all(attempt.usage is not None for attempt in landed.attempts())
        mine = desk.runner.scenes.turns(chat).last.record
        assert mine.labels == (landed,) and mine.paused and replace(mine, labels=()) == chosen
        assert synced(desk, phone).conflicts == ()
        assert phone.runner.scenes.turns(chat).last.record == mine


def test_a_labelling_never_starts_on_a_turn_a_fork_choice_set_aside_after_it_was_read(built, tmp_path):
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP, [say("Desk two.")], policy=LABEL_POLICY, labels=[say(LABELS)]) as desk, \
            Table(tmp_path / PHONE, [say("Phone two.")]) as phone:
        synced(desk, phone)
        play(phone, chat, "The phone's turn two.")
        play(desk, chat, "The desk's turn two.")
        desk.runner.post(chat, desk.player(), "*I climb the rigging*")  # the desktop's turn 3, open
        mine, theirs = turn_id(chat, 3, tag(DESKTOP)), turn_id(chat, 2, tag(PHONE))
        before = desk.runner.scenes.turns(chat)
        real = desk.runner.scenes.write

        def write(record, base, **kwargs):  # after label read the open turn: a sync forks, the owner chooses
            if record.turn_id == mine and record.labels and record.labels[-1].state == "running":
                synced(desk, phone)
                desk.runner.choose(chat, desk.owner, theirs)  # the desktop's branch, turn 3 too, is set aside
            return real(record, base, **kwargs)
        desk.runner.scenes.write = write
        refused("turn_changed", desk.runner.label, chat, desk.player())
        assert desk.runner.scenes.turns(chat).etags()[mine] == before.etags()[mine]  # nothing labelled unheard
        assert desk.labels.prompts() == []


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


def test_a_turn_the_peer_deleted_holds_the_chat_until_resolved(built, tmp_path):
    """Sync never propagates a turn's deletion (turns take no part in it): a turn one device lost is a
    `deleted` conflict, and the device that still holds it has nothing of the peer's to keep. It holds the
    chat there too, until `resolve` gives the turn back to the other device."""
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP, [say("Two.")]) as desk, Table(tmp_path / PHONE) as phone:
        synced(desk, phone)
        one = turn_id(chat, 1)
        path = f"campaigns/{chat.campaign_id}/turns/{one}.json"
        (tmp_path / PHONE / path).unlink()
        assert synced(desk, phone).conflict(path).code == "deleted"
        scene = desk.runner.open(chat)
        assert scene.turns.conflicted == (one,) and ("turn_conflict", one) in scene.problems
        refused("turn_conflict", desk.runner.post, chat, desk.player(), '"Play on?"')
        mine = etag((tmp_path / DESKTOP / path).read_bytes())
        resolve(desk.store, phone.store, path, mine, {DESKTOP: mine, PHONE: None},
                {DESKTOP: desk.owner, PHONE: phone.owner})
        assert desk.runner.open(chat).turns.conflicted == () and (tmp_path / PHONE / path).exists()
        play(desk, chat, "Second, once resolved.")


def test_a_damaged_identity_with_sync_records_fails_closed(built, tmp_path):
    """A library whose device identity is damaged but that holds pair records may have turns in conflict:
    the kept pause still holds play there, and nothing is played until it is identified again."""
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP) as desk, Table(tmp_path / PHONE, [say("Two."), say("Three.")]) as phone:
        two, _ = _paused_and_struck(desk, phone, chat)
        identity = tmp_path / PHONE / ".sync" / "device.json"
        saved = identity.read_bytes()
        identity.write_text("{not json")
        scene = phone.runner.open(chat)
        assert scene.turns.conflicted == (CONFLICT_RECORDS,)
        refused("turn_conflict", phone.runner.post, chat, phone.player(), '"Play on?"')
        identity.write_bytes(saved)  # identified again (as itself): the conflict reads as before
        _held(phone, chat, two)


def test_a_library_that_never_synced_holds_nothing(built, tmp_path):
    folder, chat = built
    shutil.copytree(folder, tmp_path / "solo")
    with Table(tmp_path / "solo") as table:  # no identity, no pair records
        assert table.runner.open(chat).turns.conflicted == ()


# -- the conflict guard never vanishes with its metadata ------------------------------------------------------
# Each piece of sync metadata the guard reads (or that stands beside it), missing or damaged while a turn
# conflict stands and neither version changed: the chat still waits, as the conflict itself (the turn's id)
# or, where the record that names it cannot be honoured, as ``<sync records unreadable>``.

def _lose(root, kind: str, how: str, path: str) -> None:
    pair = root / ".sync" / "peers" / DESKTOP
    if kind == "pair":  # the record and the archive both: only the kept version is left to tell
        for name in ("keeps.json", "archive.json"):
            (pair / name).unlink()
        return
    target = {"keeps": pair / "keeps.json", "archive": pair / "archive.json", "pending": pair / "pending.json",
              "identity": root / ".sync" / "device.json", "pairing": root / "secrets" / "peers" / f"{DESKTOP}.json",
              "kept": None}[kind]
    if kind == "kept":  # every kept version of the turn under .conflicts
        (slot,) = [p for p in (root / ".conflicts").rglob("*") if p.is_file() and p.name.startswith(path.rsplit("/")[-1])]
        target = slot
    if how == "missing":
        target.unlink(missing_ok=True)
    elif how == "damaged":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{not json")
    else:  # unreadable: a folder where the file belongs
        target.unlink(missing_ok=True)
        target.mkdir(parents=True)


@pytest.mark.parametrize("kind,how,reads", [
    ("keeps", "missing", "records"), ("keeps", "damaged", "records"), ("keeps", "unreadable", "records"),
    ("pair", "missing", "records"),
    ("identity", "missing", "records"), ("identity", "damaged", "records"),
    ("archive", "missing", "turn"), ("archive", "damaged", "turn"),
    ("pending", "missing", "turn"), ("pending", "damaged", "turn"),
    ("kept", "missing", "turn"), ("kept", "unreadable", "records"),
    ("pairing", "missing", "turn"), ("pairing", "damaged", "turn"),
])
def test_a_standing_conflict_survives_its_metadata_lost_or_damaged(built, tmp_path, kind, how, reads):
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP) as desk, Table(tmp_path / PHONE, [say("Two."), say("Three.")]) as phone:
        two, path = _paused_and_struck(desk, phone, chat)
        _lose(tmp_path / PHONE, kind, how, path)
        scene = phone.runner.open(chat)
        if reads == "turn":
            assert scene.turns.conflicted == (two,)
        else:  # the turn itself too, where a valid record still names it
            assert CONFLICT_RECORDS in scene.turns.conflicted
        refused("turn_conflict", phone.runner.post, chat, phone.player(), '"Play on?"')


def test_a_resolve_its_grant_refuses_leaves_the_conflict_standing(built, tmp_path):
    """The deliveries are retired before the data commit; the conflict itself ends only once that lands."""
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP) as desk, Table(tmp_path / PHONE, [say("Two."), say("Three.")]) as phone:
        two, path = _paused_and_struck(desk, phone, chat)
        expected = {name: etag((tmp_path / name / path).read_bytes()) for name in (DESKTOP, PHONE)}
        with pytest.raises(ResolveRefused):  # the phone receives, under a handle that may not write the turn
            resolve(desk.store, phone.store, path, expected[DESKTOP], expected,
                    {DESKTOP: desk.owner, PHONE: phone.player()})
        assert {name: etag((tmp_path / name / path).read_bytes()) for name in (DESKTOP, PHONE)} == expected
        for table in (desk, phone):
            _held(table, chat, two)
        assert phone.runner.open(chat).turns.kept_pauses == (desk.player().principal,)


def test_a_new_identity_keeps_a_standing_conflict(built, tmp_path):
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP) as desk, Table(tmp_path / PHONE, [say("Two."), say("Three.")]) as phone:
        two, _ = _paused_and_struck(desk, phone, chat)
        identify(phone.store, "phone-two", replace=True)
        _held(phone, chat, two)


def test_a_pair_folder_that_never_synced_holds_nothing(built, tmp_path):
    chat = devices(built, tmp_path)
    with Table(tmp_path / DESKTOP) as desk, Table(tmp_path / PHONE, [say("Two.")]) as phone:
        synced(desk, phone)
        (tmp_path / PHONE / ".sync" / "peers" / TABLET).mkdir()  # no archive, no record: nothing to hold
        assert phone.runner.open(chat).turns.conflicted == ()
        play(phone, chat, "Goes on.")
