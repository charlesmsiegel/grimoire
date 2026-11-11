"""Keeping chosen lines of several takes as one kept result (035's ``keep_lines``, the app's ``play.keep_lines``).

Two NPCs answer each ask, so a take holds two lines. Synthetic cards; every model call is a scripted fake.
"""

from __future__ import annotations

import shutil

import pytest
from card_fixtures import V3, card, encode, land
from scene_fixtures import ANYA, ANYA_CARD, MARA_VELL, TAMSIN, TAMSIN_CARD, VOICED, say, secure
from test_app_play import Device, actor_named, refused

from grimoire.core.identity import ChatRef, SourceRef
from grimoire.play.mixed import MAX_PICKED, kept_order, line_place
from grimoire.scene import MAX_TAKES, SceneLine, SceneRefused, Take
from grimoire.store import LibraryStore

CORIN = SourceRef("card:corin-vale", "character", "corin-vale")
CORIN_CARD = card(V3, name="Corin Vale", description="A lamplighter who keeps late hours.", character_book=None,
                  first_mes="Evening.", mes_example="<START>\n{{char}}: The wick wants trimming.")
M1, T1, M2, T2 = "Mara, first.", "Tamsin, first.", "Mara, second.", "Tamsin, second."


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    folder = tmp_path_factory.mktemp("built") / "cards"
    with LibraryStore.create(folder) as store:
        for document in (VOICED, ANYA_CARD, TAMSIN_CARD, CORIN_CARD):
            land(store, encode(document))
    secure(folder)
    return folder


@pytest.fixture
def folder(built, tmp_path):
    copy = tmp_path / "cards"
    shutil.copytree(built, copy)
    return copy


def table(device: Device, takes: int = 2) -> dict:
    """Mara and Tamsin answer one post ``takes`` times: the scene, with that many two-line takes on offer."""
    scene = device("play.start", kind="character", ref=str(MARA_VELL), persona=str(ANYA))
    chat = scene["chat"]
    scene = device("play.add_card", chat=chat, ref=str(TAMSIN))
    speakers = [actor_named(scene, "Mara Vell"), actor_named(scene, "Tamsin Hale")]
    device("play.post", chat=chat, text='"Who goes first?"', mode="speech")
    for _ in range(takes):
        scene = device("generate", chat=chat, speakers=speakers)
    return scene


def offered(scene: dict) -> dict[str, str]:
    """Each line on offer, by its text."""
    return {line["text"]: line["line"] for take in scene["turns"][-1]["offered"] for line in take["lines"]}


def kept(scene: dict) -> list[tuple[str, str]]:
    return [(line["speaker"], line["text"]) for line in scene["turns"][-1]["kept"]["lines"]]


def record_of(device: Device, scene: dict):
    return device.api.runner.open(ChatRef.parse(scene["chat"])).turns.last.record


def turn_files(folder) -> dict:
    return {path.name: path.read_bytes() for path in (folder / "campaigns").glob("*/turns/*.json")}


# -- the order, as a pure rule ---------------------------------------------------------------------------------

def test_lines_are_kept_in_the_order_they_were_spoken_then_by_take():
    def pair(take: int, place: int):
        return Take(take, "proposed", "generated", ()), SceneLine(f"c.000002.{take}.{place}", None, "N", "x", ())
    assert line_place("chat.one.000002.3.12") == 12
    picked = [pair(2, 2), pair(3, 1), pair(1, 2), pair(1, 1)]
    assert [(take.take, line_place(line.line_id)) for take, line in kept_order(picked)] == [(1, 1), (3, 1), (1, 2), (2, 2)]


# -- one line from each take -----------------------------------------------------------------------------------

def test_one_line_from_each_take_is_kept_as_the_turns_one_kept_take(folder):
    with Device(folder, [say(text) for text in (M1, T1, M2, T2)]) as device:
        scene = table(device)
        chat, turn, lines = scene["chat"], scene["state"]["open_turn"], offered(scene)
        assert scene["state"]["keepable"] == [1, 2]
        # named out of order: the order kept is the order spoken, shown before keeping and the same here
        scene = device("play.keep_lines", chat=chat, turn=turn, lines=[lines[T2], lines[M1]])
        assert kept(scene) == [("Mara Vell", M1), ("Tamsin Hale", T2)]
        assert scene["state"]["open_turn"] is None and scene["turns"][-1]["offered"] == []
        assert scene["unfinished"] is None

        record = record_of(device, scene)
        assert [take.state for take in record.takes] == ["proposed", "proposed", "kept"]
        composed = record.kept
        assert (composed.take, composed.origin, composed.calls, composed.outcome) == (3, "generated", (), "complete")
        assert [line.line_id for line in composed.lines] == [f"{turn}.3.1", f"{turn}.3.2"]
        # every call stays on the take it was billed under, and each source take still holds its own lines
        assert [len(take.calls) for take in record.takes] == [2, 2, 0]
        assert [[line.text for line in take.lines] for take in record.takes[:2]] == [[M1, T1], [M2, T2]]
        by_text = {line.text: line for take in record.takes[:2] for line in take.lines}
        for line in composed.lines:  # a copy of the line as it was spoken: who said it, to whom, in which voice
            source = by_text[line.text]
            assert (line.actor, line.speaker, line.audience, line.actor_revision, line.voice_profile, line.state) == (
                source.actor, source.speaker, source.audience, source.actor_revision, source.voice_profile, "shown")
        assert refused(device, "play.keep_lines", chat=chat, turn=turn, lines=[lines[M2]]).reason == "no_open_turn"


def test_a_mixed_keep_means_what_a_normal_keep_of_the_same_lines_means(built, tmp_path):
    """One library keeps Mara's line of take 1 with Tamsin's of take 2; another, where one take held exactly
    those two lines, keeps that take. What everyone heard, the recap and every later prompt are the same."""
    results = {}
    for name, script, mixed in (("mixed", (M1, T1, M2, T2), True), ("plain", (M1, T2), False)):
        copy = tmp_path / name / "cards"  # the same folder name: the same ids are drawn in both
        shutil.copytree(built, copy)
        with Device(copy, [say(text) for text in (*script, "Mara answers.", "Tamsin answers.")]) as device:
            scene = table(device, takes=2 if mixed else 1)
            chat, turn, lines = scene["chat"], scene["state"]["open_turn"], offered(scene)
            if mixed:
                scene = device("play.keep_lines", chat=chat, turn=turn, lines=[lines[M1], lines[T2]])
            else:
                scene = device("keep", chat=chat, turn=turn, take=1)
            record = record_of(device, scene)
            cast = [member["actor"] for member in scene["cast"]]
            heard = {str(actor): [line.text for line in record.visible(actor)] for actor in record.kept.cast}
            shown = [(str(line.actor), line.speaker, line.text, tuple(map(str, line.audience)), str(line.actor_revision),
                      str(line.voice_profile), line.state) for line in record.kept.shown()]
            view = [(line["speaker"], line["actor"], line["text"]) for line in scene["turns"][-1]["kept"]["lines"]]
            recap = [(line["speaker"], line["text"]) for line in device("play.story", chat=chat, since=turn)["recap"]["lines"]]
            before = len(device.prompts())
            device("play.post", chat=chat, text='"And then?"', mode="speech")
            device("generate", chat=chat, speakers=[actor_named(scene, "Mara Vell"), actor_named(scene, "Tamsin Hale")])
            results[name] = {"cast": cast, "heard": heard, "shown": shown, "view": view, "recap": recap,
                             "prompts": device.prompts()[before:], "state": scene["state"]}
    assert results["mixed"] == results["plain"]
    assert len(results["mixed"]["prompts"]) == 2
    for prompt in results["mixed"]["prompts"]:  # what was not kept left no trace: nobody heard it
        assert M1 in prompt and T2 in prompt
        assert T1 not in prompt and M2 not in prompt


def test_a_part_of_one_take_is_kept_and_its_other_line_is_heard_by_no_one(folder):
    with Device(folder, [say(text) for text in (M1, T1, M2, T2, "Mara again.")]) as device:
        scene = table(device)
        chat, turn, lines = scene["chat"], scene["state"]["open_turn"], offered(scene)
        scene = device("play.keep_lines", chat=chat, turn=turn, lines=[lines[T1]])
        assert kept(scene) == [("Tamsin Hale", T1)]
        assert record_of(device, scene).kept.take == 3
        device("play.post", chat=chat, text='"Go on."', mode="speech")
        device("generate", chat=chat, speakers=[actor_named(scene, "Mara Vell")])
        prompt = device.prompts()[-1]
        assert T1 in prompt and M1 not in prompt and M2 not in prompt and T2 not in prompt


def test_every_line_of_one_take_is_that_take_kept_as_keep_keeps_it(folder):
    with Device(folder, [say(text) for text in (M1, T1, M2, T2)]) as device:
        scene = table(device)
        lines = offered(scene)
        scene = device("play.keep_lines", chat=scene["chat"], turn=scene["state"]["open_turn"], lines=[lines[T2], lines[M2]])
        assert kept(scene) == [("Mara Vell", M2), ("Tamsin Hale", T2)]
        record = record_of(device, scene)
        assert [take.state for take in record.takes] == ["proposed", "kept"] and len(record.kept.calls) == 2


# -- refusals: nothing is half kept ------------------------------------------------------------------------------

def test_two_lines_of_one_speaker_and_lines_not_on_offer_are_refused_and_nothing_is_written(folder):
    with Device(folder, [say(text) for text in (M1, T1, M2, T2, "Mara, third.", "Tamsin, third.")]) as device:
        scene = table(device)
        chat, turn, lines = scene["chat"], scene["state"]["open_turn"], offered(scene)
        before = turn_files(folder)
        error = refused(device, "play.keep_lines", chat=chat, turn=turn, lines=[lines[M1], lines[M2]])
        assert (error.reason, error.status) == ("one_line_per_speaker", 409)
        assert refused(device, "play.keep_lines", chat=chat, turn=turn, lines=[lines[M1], lines[T1], lines[T2]]).reason == "one_line_per_speaker"
        assert refused(device, "play.keep_lines", chat=chat, turn=turn, lines=[lines[M1], turn + ".9.1"]).reason == "not_proposed"
        greeting = scene["turns"][0]["kept"]["lines"][0]["line"]  # a kept line of an earlier turn
        assert refused(device, "play.keep_lines", chat=chat, turn=turn, lines=[lines[M1], greeting]).reason == "not_proposed"
        assert refused(device, "play.keep_lines", chat=chat, turn="other.000001", lines=[lines[M1], lines[T2]]).reason == "turn_changed"
        for bad in (None, [], "text", [lines[M1], lines[M1]], [lines[M1], 7], [""], [f"x.{n}" for n in range(MAX_PICKED + 1)]):
            error = refused(device, "play.keep_lines", chat=chat, turn=turn, lines=bad)
            assert (error.reason, error.status) == ("invalid_field", 400), bad
        assert refused(device, "play.keep_lines", chat=chat, lines=[lines[M1]]).reason == "invalid_field"  # no turn named
        assert turn_files(folder) == before  # none of that wrote anything

        # a skipped line is on offer no longer, and a paused table keeps nothing
        device("play.withdraw", chat=chat, kind="skip", lines=[lines[T2]])
        assert refused(device, "play.keep_lines", chat=chat, turn=turn, lines=[lines[M1], lines[T2]]).reason == "not_proposed"
        device("pause", chat=chat)
        assert refused(device, "play.keep_lines", chat=chat, turn=turn, lines=[lines[M2], lines[T1]]).reason == "paused"
        device("resume", chat=chat)
        scene = device("play.keep_lines", chat=chat, turn=turn, lines=[lines[M2], lines[T1]])
        assert kept(scene) == [("Mara Vell", M2), ("Tamsin Hale", T1)]
        assert "play.keep_lines" in device("library")["ops"]


def test_a_turn_that_changed_since_it_was_read_is_refused_and_nothing_is_half_kept(folder):
    with Device(folder, [say(text) for text in (M1, T1, M2, T2)]) as device:
        scene = table(device)
        chat, turn, lines = scene["chat"], scene["state"]["open_turn"], offered(scene)
        runner = device.api.runner
        write, raced = runner._write, []

        def racing(*args, **kwargs):  # another request lands between this keep's read and its write
            if not raced:
                raced.append(True)
                runner.request(ChatRef.parse(chat), device.api.owner_handle, "skip", (lines[T1],))
            return write(*args, **kwargs)

        runner._write = racing
        error = refused(device, "play.keep_lines", chat=chat, turn=turn, lines=[lines[M1], lines[T2]])
        runner._write = write
        assert (error.reason, error.status) == ("turn_changed", 409)
        scene = device("open", chat=chat)
        assert scene["state"]["open_turn"] == turn and scene["turns"][-1]["kept"] is None
        assert [take.state for take in record_of(device, scene).takes] == ["proposed", "proposed"]
        assert T1 not in offered(scene)  # the other request stands
        scene = device("play.keep_lines", chat=chat, turn=turn, lines=[lines[M1], lines[T2]])  # read again: kept
        assert kept(scene) == [("Mara Vell", M1), ("Tamsin Hale", T2)]


def test_lines_written_for_another_cast_are_not_kept(folder):
    with Device(folder, [say(text) for text in (M1, T1, M2, T2)]) as device:
        scene = table(device)
        chat, turn, lines = scene["chat"], scene["state"]["open_turn"], offered(scene)
        device("play.add_card", chat=chat, ref=str(CORIN))  # someone arrived since the takes were written
        assert refused(device, "play.keep_lines", chat=chat, turn=turn, lines=[lines[M1], lines[T2]]).reason == "cast_changed"
        assert refused(device, "keep", chat=chat, turn=turn, take=1).reason == "cast_changed"  # as a normal keep is
        assert device("open", chat=chat)["turns"][-1]["kept"] is None


def test_a_turn_with_no_room_for_another_take_keeps_one_take_whole_instead(folder):
    replies = [say(f"{who}, take {n}.") for n in range(1, MAX_TAKES + 1) for who in ("Mara", "Tamsin")]
    with Device(folder, replies) as device:
        scene = table(device, takes=MAX_TAKES)
        chat, turn, lines = scene["chat"], scene["state"]["open_turn"], offered(scene)
        error = refused(device, "play.keep_lines", chat=chat, turn=turn, lines=[lines["Mara, take 1."], lines["Tamsin, take 2."]])
        assert error.reason == "too_many_takes"
        assert device("open", chat=chat)["turns"][-1]["kept"] is None
        scene = device("play.keep_lines", chat=chat, turn=turn, lines=[lines["Mara, take 2."], lines["Tamsin, take 2."]])
        assert kept(scene) == [("Mara Vell", "Mara, take 2."), ("Tamsin Hale", "Tamsin, take 2.")]


def test_the_runner_refuses_a_selection_that_is_not_a_list_of_ids(folder):
    with Device(folder, [say(text) for text in (M1, T1, M2, T2)]) as device:
        scene = table(device)
        runner, handle, chat = device.api.runner, device.api.owner_handle, ChatRef.parse(scene["chat"])
        for bad in ("a.b", [7], ["x"] * 2):
            with pytest.raises(SceneRefused) as caught:
                runner.keep_lines(chat, handle, bad)
            assert caught.value.reason == "invalid_field"
        with pytest.raises(SceneRefused) as caught:
            runner.keep_lines(chat, handle, [])
        assert caught.value.reason == "no_targets"
