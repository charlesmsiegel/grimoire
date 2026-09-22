"""An actor-scoped compose never gathers what it would blank.

An assigned NPC (individual mode: every response of an automatic round) sees
no recap, archive, plot, commitments, group state, off-scene cast, art
catalogue or calendar -- `_assemble` blanks all of them, because none carries
actor attribution. It used to COMPUTE them first: the off-scene directory walks
the world's roster, `art.catalogue` walks asset sidecars and may make an
embeddings call, and the rest read the chronicle, two ledgers, group state and
the calendar, all on the path between one speaker finishing and the next
starting. `assemble._campaign_view` now skips them for an actor-scoped compose.

That is only an optimisation if it changes nothing, so the first test composes
the same NPC turn twice -- once as shipped and once with the skip switched off,
which is the old algorithm -- under one seed, and requires every profile
variant and every breakdown to match. The fixture puts content behind every
skipped producer, and the test proves it did, so the comparison is not two
empty prompts agreeing.

The last test is the same economy one layer down: macro expansion used to
re-read the scene file for `{{date}}` once per expanded string, which made a
compose O(messages x scene size) in IO.
"""

from __future__ import annotations

import builtins
import io
import random

import pytest

from grimoire.store import (
    appearances,
    campaigns,
    characters,
    chronicle,
    commitments,
    config,
    context,
    dice,
    dossiers,
    entities,
    groupstate,
    pcs,
    plot,
    relationships,
    scenes,
    taglines,
    worlds,
)
from grimoire.store.context import archive, art, assemble, story, world_state
from grimoire.store.context import cast as cast_data
from grimoire.store.scenes import paths as scene_paths

NPC = "characters:mara"


def _card(name: str, **fields) -> dict:
    card = characters.blank_card(name)
    card["data"].update(fields)
    return card


@pytest.fixture
def saltmarch(monkeypatch, tmp_path):
    """A scene with something behind every producer an NPC compose discards."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    for name in ("Mara", "Winifred", "Seraphine"):
        characters.create_character(wroot, name, "main", _card(
            name, description=f"{name} keeps the {{{{random:salt,tide,lantern}}}} ledger.",
            mes_example=f"<START>\n{name}: It is {{{{date}}}}, {{{{user}}}}."))
    # Off the roster entirely, with a tagline: tier 3 of the off-scene directory.
    characters.create_character(wroot, "Aese", "main", _card("Aese", description="a"))
    taglines.write(wroot, "aese", "A ferry pilot on the grey water.")
    pcs.create_pc(wroot, "Elara", [], persona={"name": "Elara", "pronouns": "",
                                               "summary": "", "description": "a scholar"})

    cid = campaigns.create_campaign("Saltmarch", wid)
    croot = campaigns.campaign_root(cid)
    config.write_config(recap_depth="1", speaker_turn_taking="on")

    # Seraphine is on the roster from another scene, with a dossier: tier 2.
    other = scenes.create_scene(cid, "Elsewhere")
    appearances.appear(cid, other, "characters", "seraphine", "main", "npc", narrate=False)
    dossiers.write(croot, "seraphine", "Seraphine counts debts on the far pier.")

    sid = scenes.create_scene(cid, "Tide")
    for kind, name, role in (("characters", "mara", "npc"), ("characters", "winifred", "npc"),
                             ("pcs", "elara", "player")):
        appearances.appear(cid, sid, kind, name, "main" if kind == "characters" else "default",
                           role, narrate=False)
    loc = entities.create_entity(croot, "locations", "The Docks", "Rotting piers and grey water.")
    scenes.set_location(cid, sid, loc)
    sid = scenes.set_datetime(cid, sid, "2026-12-25")["id"]  # the first date renames the scene

    plot.set_movement(cid, "the-map", "The map", "open", "A clue surfaces.", sid)
    commitments.set_movement(cid, "the-debt", "The debt", "promise", "open", None,
                             "Owed by the spring tide.", sid)
    # Two records ordering before this scene: the newer is the recap
    # (`recap_depth` 1), the older is only reachable by the archive's keyword.
    chronicle.absorb(cid, {"id": "000--old-pier", "one_line": "They first met.",
                           "summary": "The ledger changed hands at the old pier.",
                           "keywords": ["ledger"]})
    chronicle.absorb(cid, {"id": other, "one_line": "They fled the city.",
                           "summary": "The party escaped by river.", "keywords": []})
    union = entities.create_entity(croot, "groups", "Dock Union", "A public trade body.",
                                   keys="ledger")
    groupstate.write_state(croot, union, "## Goals\nRaise the pier tariff.")
    # Owned by Mara, or her compose would never activate it to have its state.
    office = entities.create_entity(croot, "groups", "Quiet Office", "A counting house.",
                                    keys="ledger", secrecy="secret", owners=NPC)
    groupstate.write_state(croot, office, "## Secrets\nThey hold the harbourmaster's debt.")
    relationships.set_feeling(cid, "characters:mara", "characters:winifred", 1, 0, 0,
                              "Trusts her with the ledger.")
    relationships.set_feeling(cid, "characters:winifred", "characters:mara", 0, 1, 0,
                              "Owes her a favour.")

    for n in range(6):
        scenes.append_message(cid, sid, "user", f"On {{{{date}}}} I open the ledger ({n}).")
        scenes.append_message(cid, sid, "assistant",
                              "Mara rolls {{roll:1d20}} and looks {{random:calm,tense}}.",
                              speaker="Mara")
    return cid, sid


class _Seeds:
    """A stand-in for the OS CSPRNG `dice.roll` seeds each roll from, counting
    up, so `{{roll}}` is as reproducible as `{{random}}` under `random.seed`."""

    def __init__(self) -> None:
        self.drawn = 0

    def randbits(self, _k: int) -> int:
        self.drawn += 1
        return self.drawn


def _npc_turn(monkeypatch, cid: str, sid: str) -> tuple[dict, dict]:
    # Expansion order is draw order, so equal output also means every macro
    # was expanded in the same sequence.
    random.seed(20260922)
    monkeypatch.setattr(dice, "secrets", _Seeds())
    prepared, detail = context.compose_turn(cid, sid, actor_ref=NPC, model="glm-5.3")
    assert detail is not None
    return prepared.snapshot(), detail


def test_skipping_changes_no_byte_of_any_variant(saltmarch, monkeypatch):
    cid, sid = saltmarch
    shipped = _npc_turn(monkeypatch, cid, sid)

    real = assemble._campaign_view
    gathered: list[dict] = []

    def unguarded(*args, actor_scoped):
        view = real(*args, actor_scoped=False)
        gathered.append(view)
        return view

    monkeypatch.setattr(assemble, "_campaign_view", unguarded)
    reference = _npc_turn(monkeypatch, cid, sid)

    assert shipped == reference
    # Not two empty prompts agreeing: with the skip off, every producer the
    # skip bypasses had something to say. Two exceptions, both structural: the
    # art catalogue (this fixture has no assets; the next test covers it), and
    # the relationship graph among those present, which for an actor-scoped
    # compose is one actor.
    assert len(gathered) == 1
    assert {k for k, v in gathered[0].items() if not v} == {"available_art",
                                                             "relationship_lines"}


def test_what_an_npc_compose_discards_is_never_computed(saltmarch, monkeypatch):
    cid, sid = saltmarch
    shipped = _npc_turn(monkeypatch, cid, sid)

    def forbidden(*args, **kwargs):
        raise AssertionError("an actor-scoped compose gathered campaign-wide data")

    for module, name in ((cast_data, "_cast_directory_data"), (art, "catalogue"),
                         (archive, "_archive_entries"), (story, "_story_entries"),
                         (story, "_relationship_lines"), (plot, "render_open"),
                         (commitments, "render_open"), (world_state, "_group_states"),
                         (world_state, "_today_data")):
        monkeypatch.setattr(module, name, forbidden)
    assert _npc_turn(monkeypatch, cid, sid) == shipped
    # ...and the narrator, which keeps the whole view, still asks for it.
    with pytest.raises(AssertionError, match="campaign-wide"):
        context.compose_turn(cid, sid)


def _scene_opens(monkeypatch, path) -> list[int]:
    """Count every open of `path`, however it is reached: `pathlib` goes
    through `io.open`, a bare `open` through `builtins`."""
    opened = [0]
    real = io.open

    def counting(file, *args, **kwargs):
        if str(file) == str(path):
            opened[0] += 1
        return real(file, *args, **kwargs)

    monkeypatch.setattr(io, "open", counting)
    monkeypatch.setattr(builtins, "open", counting)
    return opened


@pytest.mark.parametrize("actor", [None, NPC])
def test_a_compose_opens_the_scene_file_a_fixed_number_of_times(saltmarch, monkeypatch, actor):
    cid, sid = saltmarch
    path = scene_paths._scene_path(cid, sid)
    counts = []
    for _ in range(2):
        with monkeypatch.context() as m:
            opened = _scene_opens(m, path)
            context.compose_turn(cid, sid, actor_ref=actor)
            counts.append(opened[0])
        # Triple the transcript between the two composes -- alternating, since
        # `_project_history` merges a run of same-role posts into one message.
        for n in range(12):
            scenes.append_message(cid, sid, "user", f"On {{{{date}}}} the tide turns ({n}).")
            scenes.append_message(cid, sid, "assistant", f"Mara waits ({n}).", speaker="Mara")
    assert counts[0] == counts[1]
    assert counts[0] < 20
