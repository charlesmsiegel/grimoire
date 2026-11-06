"""035: a scene's present cast is 033's staged ``SceneCast``, and every member ``stage`` refuses is reported.

The cast is the chat's bound actors, less reserved members (stored away, never on stage) and members whose
membership is uncertain, staged by 033's ``stage``: a member with no instance file (``missing_actor``) or whose
id another kind also uses (``instance_id_conflict``) is left out and reported, and while membership cannot be
read no cast is staged at all. Over the synthetic card library; every model call is a scripted fake.
"""

from __future__ import annotations

import shutil

import pytest

from chat_fixtures import Clock, Ids
from scene_fixtures import ANA, Table, card_library, say
from store_helpers import OWNER

from grimoire.core import Moment
from grimoire.core.identity import InstanceRef
from grimoire.party import SceneCast, presence
from grimoire.partystore import PartyLibrary
from grimoire.play.runner import NOW
from grimoire.scene import SceneRefused


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return card_library(tmp_path_factory.mktemp("built"))


@pytest.fixture
def cards(built, tmp_path):
    folder, card_chat, storm = built
    copy = tmp_path / "cards"
    shutil.copytree(folder, copy)
    return copy, card_chat, storm


def refused(reason: str, call, *args, **kwargs) -> None:
    with pytest.raises(SceneRefused) as error:
        call(*args, **kwargs)
    assert error.value.reason == reason, error.value


def actors(table: Table, chat) -> tuple[InstanceRef, InstanceRef]:
    scene = table.runner.open(chat)
    (mara,) = [actor for actor in scene.cast if actor != scene.persona]
    return scene.persona, mara


def party(table: Table) -> PartyLibrary:
    return PartyLibrary(table.store, table.owner, clock=Clock(), new_id=Ids(), owner=OWNER)


def test_the_cast_is_a_staged_scene_cast(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        anya, mara = actors(table, chat)
        scene = table.runner.open(chat)
        assert type(scene.staged) is SceneCast
        assert (scene.staged.campaign_id, scene.staged.scene_id) == (chat.campaign_id, chat.chat_id)
        assert scene.staged.present == scene.cast == (anya, mara)
        state = party(table).view(chat.campaign_id, as_of=NOW).state
        assert all(presence(state, scene.staged, actor) == "present" for actor in scene.cast)


def test_a_reserved_member_is_not_on_stage_and_never_speaks(cards):
    folder, chat, _ = cards
    with Table(folder, [say("Unreachable.")]) as table:
        anya, mara = actors(table, chat)
        table.runner.seat(chat, ANA)
        library = party(table)
        library.join(anya, mara, at=Moment(1), provenance="director").commit()
        library.store_in_reserve(mara, at=Moment(2), provenance="director").commit()
        scene = table.runner.open(chat)
        assert scene.cast == scene.staged.present == (anya,)
        assert not [code for code, _ in scene.problems if code != "party"]  # stored away is not a problem
        table.runner.post(table.runner.open(chat).chat, table.player(), '"Anyone here?"')
        refused("nothing_to_generate", table.runner.generate, chat, table.player())
        assert table.harness.prompts() == []


def test_a_member_without_an_instance_file_is_left_out_and_reported(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        anya, mara = actors(table, chat)
    (folder / "campaigns" / chat.campaign_id / "instances" / "character" / f"{mara.instance_id}.json").unlink()
    with Table(folder) as table:
        scene = table.runner.open(chat)
        assert ("missing_actor", str(mara)) in scene.problems
        assert scene.cast == scene.staged.present == (anya,)


def test_a_member_whose_id_another_kind_uses_is_left_out_and_reported(cards):
    folder, chat, _ = cards
    with Table(folder) as table:
        anya, mara = actors(table, chat)
    # a hand edit (or two devices) left the same id under another kind; the store never writes one itself
    instances = folder / "campaigns" / chat.campaign_id / "instances"
    (instances / "creature").mkdir()
    original = (instances / "character" / f"{mara.instance_id}.json").read_text(encoding="utf-8")
    (instances / "creature" / f"{mara.instance_id}.json").write_text(
        original.replace(f"/character/{mara.instance_id}", f"/creature/{mara.instance_id}"), encoding="utf-8")
    with Table(folder) as table:
        scene = table.runner.open(chat)
        assert ("instance_id_conflict", str(mara)) in scene.problems
        assert scene.cast == scene.staged.present == (anya,)


def test_while_membership_cannot_be_read_no_cast_is_staged_and_play_waits(cards):
    folder, chat, _ = cards
    with Table(folder, [say("Dark, and the harbor pays for it.")]) as table:
        table.runner.seat(chat, ANA)
        table.runner.begin(chat, table.player())
        table.runner.keep(chat, table.player(), 1)
    relations = folder / "campaigns" / chat.campaign_id / "relations"
    relations.mkdir(exist_ok=True)
    (relations / "r-broken.json").write_bytes(b"{not json")
    with Table(folder, [say("Unreachable.")]) as table:
        ana = table.player()
        scene = table.runner.open(chat)
        assert scene.staged is None and scene.cast == ()  # fail closed: a reserve may hide in that file
        assert ("cast_unstaged", "relations_unreadable") in scene.problems
        assert len(scene.records) == 1  # the story is still there
        refused("party_unreadable", table.runner.post, chat, table.owner, '"Hello?"')
        refused("party_unreadable", table.runner.generate, chat, table.owner)
        assert table.harness.prompts() == []
        assert len(table.runner.open(chat).records) == 1
        refused("not_at_table", table.runner.post, chat, ana, '"Hello?"')  # nobody plays a present character
