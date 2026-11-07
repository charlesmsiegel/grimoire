"""028's turn merge, pure: how one chat's turns from several devices hang together.

Every turn names the turn it was played after, so a chat's turns are a tree. Its line goes on while one turn
follows the last; two or more make a fork, which waits for a choice. A choice is a set-aside mark on every other
branch head naming the chosen one, and sets aside that head and everything played after it, a turn arriving
later included. Marks that choose nothing are a conflict, never a guess. Synthetic turns only.
"""

from __future__ import annotations

import pytest

from grimoire.core.identity import ChatRef
from grimoire.scene import SceneRefused, SetAside, TurnRecord, arrange, turn_from_payload, turn_id, turn_to_payload
from grimoire.scene.branches import is_mine, replay_tag, tag_for

CHAT = ChatRef("c1", "chat-a")
DESK, PHONE, TABLET = "d0d0d0d0", "f1f1f1f1", "a2a2a2a2"


def turn(number: int, device: str | None, follows: TurnRecord | None = None, *, aside: TurnRecord | None = None,
         legacy: bool = False) -> TurnRecord:
    mark = None if aside is None else SetAside(aside.turn_id, "human:owner")
    return TurnRecord(CHAT, number, "prompt", device=device,
                      follows=None if legacy or follows is None else follows.turn_id, set_aside=mark)


def ids(records) -> list[str]:
    return [record.turn_id for record in records]


def test_a_line_with_no_fork_is_every_turn_in_order():
    one = turn(1, None)
    two = turn(2, DESK, one)
    three = turn(3, DESK, two)
    found = arrange([three, one, two])
    assert ids(found.line) == ids([one, two, three]) and found.fork is None and found.set_aside == ()


def test_two_devices_turn_n_is_an_open_fork_showing_both_branches_and_choosing_nothing():
    one = turn(1, None)
    desk, phone = turn(2, DESK, one), turn(2, PHONE, one)
    phone3 = turn(3, PHONE, phone)
    found = arrange([phone3, phone, desk, one])
    assert found.fork.state == "open" and found.fork.number == 2 and found.fork.after == one.turn_id
    assert [(b.head, b.device, b.turns, b.set_aside) for b in found.fork.branches] == [
        (desk.turn_id, DESK, (desk.turn_id,), False), (phone.turn_id, PHONE, (phone.turn_id, phone3.turn_id), False)]
    assert ids(found.line) == ids([one, desk, phone, phone3])  # every turn, in one order, as before a choice
    assert "turn 2:" in found.fork.describe() and "2 turns" in found.fork.describe()


def test_a_choice_sets_aside_the_other_branch_and_everything_played_after_it_even_later():
    one = turn(1, None)
    desk = turn(2, DESK, one)
    phone = turn(2, PHONE, one, aside=desk)
    found = arrange([one, desk, phone])
    assert found.fork is None and ids(found.line) == ids([one, desk]) and ids(found.set_aside) == ids([phone])
    late = turn(3, TABLET, phone)  # played after the phone's turn 2 on a third device, arriving later
    desk3 = turn(3, DESK, desk)
    found = arrange([one, desk, phone, late, desk3])
    assert ids(found.line) == ids([one, desk, desk3]) and ids(found.set_aside) == ids([phone, late])


def test_a_new_branch_arriving_after_a_choice_is_a_fork_again_with_the_set_aside_one_kept_aside():
    one = turn(1, None)
    desk, tablet = turn(2, DESK, one), turn(2, TABLET, one)
    phone = turn(2, PHONE, one, aside=desk)
    found = arrange([one, desk, phone, tablet])
    assert found.fork.state == "open" and ids(found.set_aside) == ids([phone])
    assert [b.set_aside for b in found.fork.branches] == [False, False, True]  # chat order: a2.., d0.., f1..
    assert phone.turn_id not in ids(found.line)


def test_a_later_fork_of_the_chosen_branch_is_a_fork_again():
    one = turn(1, None)
    desk = turn(2, DESK, one)
    phone = turn(2, PHONE, one, aside=desk)
    a, b = turn(3, DESK, desk), turn(3, PHONE, desk)
    found = arrange([one, desk, phone, a, b])
    assert found.fork.number == 3 and found.fork.after == desk.turn_id and found.fork.state == "open"


@pytest.mark.parametrize("marks", ["each_other", "both_for_a_turn_set_aside", "for_an_absent_turn"])
def test_marks_that_choose_nothing_are_a_conflict_showing_every_branch(marks):
    one = turn(1, None)
    if marks == "each_other":  # two devices chose differently offline: each marked the other's branch
        desk = TurnRecord(CHAT, 2, "prompt", device=DESK, follows=one.turn_id,
                          set_aside=SetAside(turn_id(CHAT, 2, PHONE), "human:owner"))
        phone = turn(2, PHONE, one, aside=desk)
        level = [desk, phone]
    elif marks == "both_for_a_turn_set_aside":
        desk = turn(2, DESK, one)
        tablet = turn(2, TABLET, one, aside=desk)
        desk = turn(2, DESK, one, aside=tablet)
        level = [desk, tablet, turn(2, PHONE, one)]
    else:
        desk = TurnRecord(CHAT, 2, "prompt", device=DESK, follows=one.turn_id,
                          set_aside=SetAside(turn_id(CHAT, 2, TABLET), "human:owner"))
        level = [desk, turn(2, PHONE, one)]
    found = arrange([one, *level])
    assert found.fork.state == "conflict" and found.set_aside == ()
    assert set(ids(found.line)) == set(ids([one, *level])) and not any(b.set_aside for b in found.fork.branches)
    assert "choices conflict" in found.fork.describe()


def test_turns_written_before_follows_existed_are_placed_by_device_then_untagged_and_never_guessed():
    one, two = turn(1, None), turn(2, None, legacy=True)
    desk3 = turn(3, DESK, legacy=True)  # played on from the untagged turn 2 after identify
    assert ids(arrange([one, two, desk3]).line) == ids([one, two, desk3])
    desk2, phone2 = turn(2, DESK, legacy=True), turn(2, PHONE, legacy=True)
    found = arrange([one, desk2, phone2, turn(3, PHONE, legacy=True)])
    assert [b.turns for b in found.fork.branches][1][-1] == turn_id(CHAT, 3, PHONE)  # its own device's turn 2
    orphan = turn(3, TABLET, legacy=True)  # no turn 2 of its own and two to choose from: never guessed
    assert ids(arrange([one, desk2, phone2, orphan]).orphans) == ids([orphan])
    assert ids(arrange([one, turn(3, None, legacy=True)]).orphans) == [turn_id(CHAT, 3)]  # a gap


def test_follows_and_marks_round_trip_and_are_checked():
    one = turn(1, None)
    phone = turn(2, PHONE, one, aside=turn(2, DESK, one))
    payload = turn_to_payload(phone)
    assert payload["follows"] == one.turn_id and payload["set_aside"] == {"chosen": turn_id(CHAT, 2, DESK),
                                                                          "by": "human:owner"}
    assert turn_from_payload(phone.ref, "grimoire.scene-turn/1", payload) == phone
    for bad in ({"follows": turn_id(CHAT, 3)}, {"set_aside": {"chosen": phone.turn_id, "by": "human:owner"}},
                {"set_aside": {"chosen": turn_id(CHAT, 3, DESK), "by": "human:owner"}},
                {"set_aside": {"chosen": turn_id(CHAT, 2, DESK)}}, {"follows": 7}):
        with pytest.raises(SceneRefused) as error:
            turn_from_payload(phone.ref, "grimoire.scene-turn/1", {**payload, **bad})
        assert error.value.reason == "invalid_field"
    old = {key: value for key, value in payload.items() if key not in ("follows", "set_aside")}
    assert turn_from_payload(phone.ref, "grimoire.scene-turn/1", old).follows is None  # written before 028


def test_a_device_replaying_a_number_it_set_aside_takes_a_fresh_documented_tag():
    def spell(tag):
        return turn_id(CHAT, 3, tag)
    assert tag_for(DESK, [spell(PHONE)], spell) == DESK
    first = tag_for(DESK, [spell(DESK)], spell)
    assert first == replay_tag(DESK, 1) != DESK and len(first) == 8
    assert tag_for(DESK, [spell(DESK), spell(first)], spell) == replay_tag(DESK, 2)
    assert is_mine(DESK, first, 1) and not is_mine(PHONE, first, 5) and is_mine(None, None, 0)
    assert tag_for(None, [], lambda tag: turn_id(CHAT, 3, tag)) is None  # an unidentified library stays untagged
