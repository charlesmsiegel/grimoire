"""108: ``shell.search_lines``, the command palette's search over the lines that were said.

Synthetic cards imported through 037's real importer; every model call is a scripted fake. What can be found is
what opening the chat shows of its played turns, and nothing else.
"""

from __future__ import annotations

import shutil
from dataclasses import replace

import pytest
from card_fixtures import SCENARIO, encode, land
from scene_fixtures import ANYA, ANYA_CARD, MARA_VELL, TAMSIN_CARD, VOICED, say, secure
from textapi_fixtures import Device as TextDevice
from textapi_fixtures import desktop_library

from grimoire.app.adapter import AdapterContext
from grimoire.app.adapters.shell import line_index
from grimoire.app.adapters.shell import lines as linesearch
from grimoire.app.adapters.shell.lines import (
    MIN_BUILT,
    PER_CHAT,
    LineIndex,
    excerpt,
    fold,
    words_of,
)
from grimoire.app.appapi import AppApi
from grimoire.core.identity import ChatRef
from grimoire.store import LibraryStore
from grimoire.textapi import ApiRefused

GREETING = "You came back. I didn't think you would."


class Device(TextDevice):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.app = AppApi(self.api)

    def __call__(self, op: str, **args):
        return self.app.call(op, args)

    @property
    def index(self) -> LineIndex:
        return line_index(AdapterContext(self.api))

    def find(self, query: str, **args) -> list[tuple[str | None, str, int]]:
        return [(line["speaker"], line["text"], line["turn"]) for line in self("shell.search_lines", query=query, **args)["lines"]]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    folder = tmp_path_factory.mktemp("built") / "cards"
    with LibraryStore.create(folder) as store:
        for document in (VOICED, ANYA_CARD, TAMSIN_CARD, SCENARIO):
            land(store, encode(document))
    secure(folder)
    return folder


@pytest.fixture
def folder(built, tmp_path):
    copy = tmp_path / "cards"
    shutil.copytree(built, copy)
    return copy


def start(device: Device, **args) -> str:
    return device("play.start", kind="character", ref=str(MARA_VELL), persona=str(ANYA), **args)["chat"]


def play(device: Device, chat: str, text: str) -> dict:
    """Post, ask and keep the answer: one whole turn."""
    device("post", chat=chat, text=text)
    scene = device("generate", chat=chat)
    return device("keep", chat=chat, turn=scene["state"]["open_turn"], take=max(scene["state"]["keepable"]))


def shown(scene: dict) -> list[tuple[int, str]]:
    """Every line the chat page draws for the scene's played turns: (turn number, text)."""
    found = []
    for turn in scene["turns"]:
        if turn["post"]:
            found.append((turn["number"], turn["post"]["text"]))
        if turn["kept"]:
            found += [(turn["number"], line["text"]) for line in turn["kept"]["lines"]]
    return found


def refused(device: Device, **args) -> ApiRefused:
    with pytest.raises(ApiRefused) as caught:
        device("shell.search_lines", **args)
    return caught.value


# -- what is found ---------------------------------------------------------------------------------------------

def test_a_kept_line_and_a_post_are_found_with_their_chat_turn_and_speaker(folder):
    with Device(folder, [say("Safe as the tide, and twice as cold.")]) as device:
        chat = start(device, label="Harbor night")
        play(device, chat, '"Is the cargo safe?"')
        answer = device("shell.search_lines", query="tide")
        assert answer["complete"] is True and answer["stopped"] is None and answer["more"] is False
        assert (answer["chats"], answer["searched"], answer["unread"]) == (1, 1, 0)
        assert answer["lines"] == [{"chat": chat, "title": "Harbor night", "turn": 2, "turn_id": answer["lines"][0]["turn_id"],
                                    "kind": "line", "speaker": "Mara Vell", "text": "Safe as the tide, and twice as cold."}]
        assert ChatRef.parse(chat).chat_id in answer["lines"][0]["turn_id"]
        # the person's own post, said by the character they play
        (post,) = device("shell.search_lines", query="cargo")["lines"]
        assert (post["kind"], post["speaker"], post["turn"], post["text"]) == ("post", "Anya Rook", 2, "Is the cargo safe?")
        # the card's greeting was kept as turn 1
        assert device.find("came back") == [("Mara Vell", GREETING, 1)]


def test_a_chat_with_no_label_is_titled_by_what_it_was_started_from(folder):
    with Device(folder) as device:
        start(device)
        assert device("shell.search_lines", query="came back")["lines"][0]["title"] == "Mara Vell"


def test_case_and_accents_do_not_count_and_every_word_must_be_in_the_line(folder):
    with Device(folder, [say("The café by the Quay is CLOSED, señor.")]) as device:
        chat = start(device)
        play(device, chat, "I knock at the door of the Élan.")
        assert device.find("cafe QUAY") == [("Mara Vell", "The café by the Quay is CLOSED, señor.", 2)]
        assert device.find("SEÑOR closed") == device.find("senor  closed") == device.find("cafe QUAY")
        assert device.find("elan") == [("Anya Rook", "I knock at the door of the Élan.", 2)]
        assert device.find("afé") == device.find("cafe QUAY")  # part of a word matches, as in the palette
        assert device.find("cafe door") == []  # both words are in the chat, never in one line
        assert device.find("cafe zebra") == []


def test_fold_is_the_palettes_folding():
    assert fold("SÉRAPHINE") == "seraphine" and fold("ﬁne Ångström") == "fine angstrom"
    assert words_of("  Tide  SERA tide ") == ("tide", "sera")


def test_a_long_line_is_answered_as_the_words_around_the_match():
    text = "Rain. " * 40 + "The lantern-keeper waved from the pier. " + "Fog. " * 60
    cut = excerpt(text, ("lantern",))
    assert "lantern-keeper waved" in cut and cut.startswith("…") and cut.endswith("…") and len(cut) <= 162
    assert excerpt("Short  and\nplain.", ("plain",)) == "Short and plain."
    assert excerpt("É" * 400, ("zzz",)).endswith("…") and len(excerpt("É" * 400, ("zzz",))) <= 161
    assert excerpt("word " * 100 + "End", ("end",)).endswith("End")


def test_results_are_newest_first_and_capped_per_chat_and_by_the_limit(folder):
    with Device(folder, [say(f"The bell rings {n}.") for n in range(5)]) as device:
        old = start(device, label="Old")
        new = start(device, label="New")
        for n in range(4):
            play(device, new, f"I ring the bell again, {n}.")
        play(device, old, "I ring the bell too.")
        answer = device("shell.search_lines", query="bell")
        # chats in the chat list's own order; in a chat the latest turn first, and at most PER_CHAT of its lines
        expected = {"Old": [("Old", 2), ("Old", 2)], "New": [("New", 5), ("New", 5), ("New", 4)]}
        recent = [entry["label"] for entry in device("chats")["chats"]]
        assert [(line["title"], line["turn"]) for line in answer["lines"]] == expected[recent[0]] + expected[recent[1]]
        assert len(expected["New"]) == PER_CHAT
        assert answer["more"] is True and answer["complete"] is True  # more lines, but every chat was looked at
        capped = device("shell.search_lines", query="bell", limit=1)
        assert len(capped["lines"]) == 1 and capped["more"] is True


# -- what is never found ---------------------------------------------------------------------------------------

def test_a_take_that_was_not_kept_is_not_found_until_it_is(folder):
    with Device(folder, [say("Smugglers took the lantern."), say("Nobody took anything.")]) as device:
        chat = start(device)
        device("post", chat=chat, text='"What happened?"')
        scene = device("generate", chat=chat)
        assert scene["turns"][-1]["offered"][0]["lines"][0]["text"] == "Smugglers took the lantern."
        assert device.find("smugglers") == []  # on offer, not part of the story
        assert device.find("happened") == [("Anya Rook", "What happened?", 2)]  # the post already is
        scene = device("generate", chat=chat)  # a second take; the person keeps this one
        device("keep", chat=chat, turn=scene["state"]["open_turn"], take=max(scene["state"]["keepable"]))
        assert device.find("smugglers") == [] and device.find("nobody took") == [("Mara Vell", "Nobody took anything.", 2)]


def test_a_withdrawn_line_and_the_direction_given_for_it_are_not_found(folder):
    with Device(folder, [say("Safe as the tide."), say("Gone with the tide.")]) as device:
        chat = start(device)
        device("post", chat=chat, text='"Is the cargo safe?"')
        scene = device("generate", chat=chat)
        line = scene["turns"][-1]["offered"][0]["lines"][0]["line"]
        device("play.withdraw", chat=chat, kind="redirect", lines=[line], direction="make her kinder, mention gulls")
        scene = device("generate", chat=chat)
        device("keep", chat=chat, turn=scene["state"]["open_turn"], take=max(scene["state"]["keepable"]))
        assert device.find("safe as") == [] and device.find("kinder") == [] and device.find("gulls") == []
        assert device.find("gone with") == [("Mara Vell", "Gone with the tide.", 2)]


def test_a_struck_line_stops_being_found_and_only_the_changed_chat_is_read_again(folder):
    with Device(folder, [say("The vault code is seven seven three.")]) as device:
        other = start(device, label="Other")
        chat = start(device, label="Vault")
        scene = play(device, chat, '"What is the code?"')
        assert device.find("vault code") == [("Mara Vell", "The vault code is seven seven three.", 2)]
        assert device.index.built == 2  # both chats were read
        assert device.find("vault code") and device.index.built == 0  # and are held
        line = scene["turns"][-1]["kept"]["lines"][0]["line"]
        device("play.strike", chat=chat, line=line)
        assert device.find("vault code") == [] and device.index.built == 1  # only the chat that changed
        assert device.find("what is the code") == [("Anya Rook", "What is the code?", 2)]
        assert device.find("came back") == [("Mara Vell", GREETING, 1)] * 2 and other != chat


def test_a_change_the_stamp_missed_is_caught_before_a_line_is_answered(folder):
    with Device(folder, [say("The vault code is seven seven three.")]) as device:
        chat = start(device)
        scene = play(device, chat, '"What is the code?"')
        assert device.find("vault code")
        ref = ChatRef.parse(chat)
        device("play.strike", chat=chat, line=scene["turns"][-1]["kept"]["lines"][0]["line"])
        # as if the file's size, time and inode had not moved: the index still holds the old lines under the new stamp
        index = device.index
        index._entries[ref] = replace(index._entries[ref], stamp=linesearch._stamps(index.store.root, ref.campaign_id)(ref))
        assert device.find("vault code") == [] and index.built == 1  # each result is checked against its file's bytes
        assert device.find("what is the code") == [("Anya Rook", "What is the code?", 2)]


def test_a_turn_file_that_is_gone_takes_its_lines_with_it(folder):
    with Device(folder, [say("Safe as the tide.")]) as device:
        chat = start(device)
        play(device, chat, '"Is the cargo safe?"')
        (line,) = device("shell.search_lines", query="safe as")["lines"]
        ref = ChatRef.parse(chat)
        (device.api.store.root / "campaigns" / ref.campaign_id / "turns" / (line["turn_id"] + ".json")).unlink()
        assert device.find("safe as") == [] and device.find("cargo") == []
        assert device.find("came back") == [("Mara Vell", GREETING, 1)]


def test_the_search_shows_exactly_what_opening_each_chat_shows(tmp_path):
    """The imported V1 chat (with imported history and no played turn) and a played card chat: the index holds the
    played turns' lines as ``open`` gives them, line for line, and none of the imported history."""
    desk, card_chat = desktop_library(tmp_path / "desk")
    with Device(desk, [say("Heard you.")]) as device:
        device("continue", chat=str(card_chat))
        play(device, str(card_chat), '"Anyone there?"')
        chats = [entry["chat"] for entry in device("chats")["chats"]]
        assert len(chats) == 2
        device("shell.search_lines", query="anything at all")
        index = device.index
        history = []
        for chat in chats:
            scene = device("open", chat=chat)
            held = index._entries[ChatRef.parse(chat)]
            assert [(line.number, line.text) for line in held.lines] == shown(scene)
            history += [item["text"] for item in scene["history"] or []]
        assert history, "the fixture has imported history"
        word = max(history[0].split(), key=len)
        assert all(word.lower() not in line["text"].lower() for line in device("shell.search_lines", query=word)["lines"])
        assert device.find("heard you") == [("Mara Vell", "Heard you.", 2)]


# -- the index is a derivative, and its caps ---------------------------------------------------------------------

def test_forgetting_the_index_loses_nothing(folder):
    with Device(folder) as device:
        start(device)
        assert device.find("came back") and device.index.built == 1
        device.index.forget()
        assert device.find("came back") == [("Mara Vell", GREETING, 1)] and device.index.built == 1
        assert not any(path.name.startswith("lines") for path in folder.rglob("*")), "nothing is written to the library"


def test_a_search_that_runs_out_of_time_says_so_and_the_next_one_gets_further(folder):
    with Device(folder) as device:
        chats = [start(device, label=f"Chat {n}") for n in range(MIN_BUILT + 3)]
        ticks = iter(range(0, 10_000, 10))
        index = LineIndex(device.api.store, device.api.owner_handle, owner=device.api.owner, clock=lambda: next(ticks), budget=5)
        first = index.search("came back", 50)
        assert first["complete"] is False and first["stopped"] == "time"
        assert (first["searched"], first["unread"], first["chats"]) == (MIN_BUILT, 3, len(chats))
        assert len(first["lines"]) == MIN_BUILT  # what it reached, newest first
        recent = [entry["label"] for entry in device("chats")["chats"]]  # the chat list's own order
        assert [line["title"] for line in first["lines"]] == recent[:MIN_BUILT]
        second = index.search("came back", 50)
        assert second["complete"] is True and second["searched"] == len(chats) and index.built == 3
        assert len(second["lines"]) == len(chats)


def test_only_the_most_recent_chats_are_looked_at_past_the_cap(folder):
    with Device(folder) as device:
        for n in range(3):
            start(device, label=f"Chat {n}")
        index = LineIndex(device.api.store, device.api.owner_handle, owner=device.api.owner, max_chats=2)
        answer = index.search("came back", 50)
        assert answer["complete"] is False and answer["stopped"] == "chats"
        recent = [entry["label"] for entry in device("chats")["chats"]]
        assert [line["title"] for line in answer["lines"]] == recent[:2] and answer["chats"] == 3


def test_past_the_memory_cap_a_chat_is_searched_and_not_kept(folder):
    with Device(folder) as device:
        start(device)
        index = LineIndex(device.api.store, device.api.owner_handle, owner=device.api.owner, max_chars=10)
        for _ in range(2):
            answer = index.search("came back", 5)
            assert answer["complete"] is True and len(answer["lines"]) == 1 and index.built == 1
        assert not index._entries


def test_a_chat_removed_from_the_library_leaves_the_index(folder):
    with Device(folder) as device:
        chat = ChatRef.parse(start(device))
        assert device.find("came back")
        shutil.rmtree(device.api.store.root / "campaigns" / chat.campaign_id)
        answer = device("shell.search_lines", query="came back")
        assert answer["lines"] == [] and answer["chats"] == 0 and not device.index._entries


# -- refusals ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("args", [
    {}, {"query": 7}, {"query": None}, {"query": "a"}, {"query": " a  "}, {"query": "́́́"}, {"query": "x" * 201},
    {"query": "a b c d e f g h i"}, {"query": "tide", "limit": 0}, {"query": "tide", "limit": 51},
    {"query": "tide", "limit": True}, {"query": "tide", "limit": "5"}, {"query": "tide", "limit": 2.0},
])
def test_bad_arguments_are_refused(folder, args):
    with Device(folder) as device:
        assert refused(device, **args).reason == "invalid_field"


def test_the_app_lists_the_op_and_an_empty_library_answers_nothing(folder):
    with Device(folder) as device:
        assert "shell.search_lines" in device("library")["ops"]
        assert device("shell.search_lines", query="tide") == {
            "query": "tide", "lines": [], "complete": True, "stopped": None, "more": False, "chats": 0, "searched": 0,
            "unread": 0}
