"""What a pin and an exclude actually do to the assembled prompt (#129).

The store tests next door prove the rules survive being written down; these
prove they override the four mechanisms that otherwise decide what the model
sees — the keyword rule, the owner gate, the cast loop, and the budget packer.
"""

import pytest

from grimoire.store import (appearances as ap, campaigns, characters, config, context,
                            entities, pcs, pins, playstate, scenes, worlds)
from grimoire.store.context import pack as context_pack


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "Scene")
    return wid, cid, sid


def _npc_card(name, **fields):
    card = characters.blank_card(name)
    card["data"].update(fields)
    return card


def _system(cid, sid):
    return context.build_messages(cid, sid)[0]["content"]


# --- activate(), the documented swap point ----------------------------------

def test_a_pin_beats_the_keyword_rule():
    entries = [{"name": "Oath", "body": "b", "keys": ["dragon"], "kind": "lore", "id": "oath"}]
    assert context.activate(entries, "nothing relevant") == []
    out = context.activate(entries, "nothing relevant", pinned_refs=frozenset({"lore:oath"}))
    assert [e["name"] for e in out] == ["Oath"]


def test_a_pin_beats_the_owner_gate():
    """Deliberate, and the sharpest edge of the feature: the owner gate keeps an
    absent character's lore out of the prompt, and a pin is the reader saying
    they want it anyway. See `activate`'s docstring."""
    entries = [{"name": "Secret", "body": "b", "keys": [], "owners": ["characters:mara"],
                "kind": "lore", "id": "secret"}]
    assert context.activate(entries, "x", present=frozenset()) == []
    out = context.activate(entries, "x", present=frozenset(),
                           pinned_refs=frozenset({"lore:secret"}))
    assert [e["name"] for e in out] == ["Secret"]


def test_an_exclude_beats_a_keyword_hit_and_an_always_on_entry():
    entries = [{"name": "Oath", "body": "b", "keys": ["pact"], "kind": "lore", "id": "oath"},
               {"name": "Standing", "body": "b", "keys": [], "kind": "lore", "id": "standing"}]
    out = context.activate(entries, "the pact holds",
                           excluded_refs=frozenset({"lore:oath", "lore:standing"}))
    assert out == []


def test_an_exclude_is_never_handed_to_semantic_recall():
    """The second stage only ever sees what the keyword rule REJECTED; an
    excluded entry must not reach it, or recall could put it back."""
    seen = []

    def recall(candidates, _text):
        seen.extend(e["id"] for e in candidates)
        return list(candidates)

    entries = [{"name": "Oath", "body": "b", "keys": ["dragon"], "kind": "lore", "id": "oath"}]
    out = context.activate(entries, "no match", recall=recall,
                           excluded_refs=frozenset({"lore:oath"}))
    assert out == [] and seen == []


# --- world info, end to end --------------------------------------------------

def test_a_pinned_lore_entry_reaches_the_prompt_unkeyed(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "Tide oath", "The tide keeps its promises.",
                           keys="dragon")
    scenes.append_message(cid, sid, "user", "hello")
    assert "tide keeps its promises" not in _system(cid, sid)

    pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid=sid)
    assert "tide keeps its promises" in _system(cid, sid)


def test_an_excluded_lore_entry_leaves_the_prompt(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Tide oath",
                           "The tide keeps its promises.")     # keyless -> always on
    scenes.append_message(cid, sid, "user", "hello")
    assert "tide keeps its promises" in _system(cid, sid)

    pins.set_rule(cid, "lore:tide-oath", pins.EXCLUDE, sid=sid)
    assert "tide keeps its promises" not in _system(cid, sid)


def test_a_pinned_keyless_location_surfaces_as_world_info(monkeypatch, tmp_path):
    """A keyless location is otherwise only ever the current setting."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    loc = entities.create_entity(campaigns.campaign_root(cid), "locations", "Saltmarch",
                                 "A drowned village.")
    scenes.append_message(cid, sid, "user", "hello")
    assert "A drowned village." not in _system(cid, sid)

    pins.set_rule(cid, f"locations:{loc}", pins.PIN, sid=sid)
    assert "A drowned village." in _system(cid, sid)


def test_excluding_the_current_location_drops_the_setting_block(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    loc = entities.create_entity(campaigns.campaign_root(cid), "locations", "Saltmarch",
                                 "A drowned village.")
    scenes.set_location(cid, sid, loc)
    scenes.append_message(cid, sid, "user", "hello")
    assert "A drowned village." in _system(cid, sid)

    pins.set_rule(cid, f"locations:{loc}", pins.EXCLUDE, sid=sid)
    assert "A drowned village." not in _system(cid, sid)


def test_a_campaign_rule_applies_to_a_scene_written_later(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Tide oath",
                           "The tide keeps its promises.")
    pins.set_rule(cid, "lore:tide-oath", pins.EXCLUDE, scope=pins.CAMPAIGN)
    later = scenes.create_scene(cid, "Second")
    scenes.append_message(cid, later, "user", "hello")
    assert "tide keeps its promises" not in _system(cid, later)


# --- the cast loop, which activate() never sees ------------------------------

def test_an_excluded_character_leaves_the_cast_sections(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Seraphine", "default",
                                _npc_card("Seraphine", description="A keeper of the road."))
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    scenes.append_message(cid, sid, "user", "hello")
    assert "A keeper of the road." in _system(cid, sid)

    pins.set_rule(cid, "characters:seraphine", pins.EXCLUDE, sid=sid)
    assert "A keeper of the road." not in _system(cid, sid)


def test_an_excluded_character_stops_unlocking_their_own_lore(monkeypatch, tmp_path):
    """Their ref leaves `present` with them, which is the point: excluding
    someone from the prompt cannot leave their private lore standing in it."""
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    characters.create_character(worlds.world_root(wid), "Seraphine", "default",
                                _npc_card("Seraphine", description="A keeper."))
    entities.create_entity(croot, "lore", "Her secret", "She was exiled.",
                           owners="characters:seraphine")
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    scenes.append_message(cid, sid, "user", "hello")
    assert "She was exiled." in _system(cid, sid)

    pins.set_rule(cid, "characters:seraphine", pins.EXCLUDE, sid=sid)
    assert "She was exiled." not in _system(cid, sid)


def test_an_excluded_pc_leaves_the_persona_section(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    pcs.create_pc(worlds.world_root(wid), "Winifred", [],
                  persona={"name": "Winifred", "pronouns": "they/them",
                           "summary": "scholar", "description": "A wanderer of the marsh."})
    ap.appear(cid, sid, "pcs", "winifred", "default", "player")
    scenes.append_message(cid, sid, "user", "hello")
    assert "A wanderer of the marsh." in _system(cid, sid)

    pins.set_rule(cid, "pcs:winifred", pins.EXCLUDE, sid=sid)
    assert "A wanderer of the marsh." not in _system(cid, sid)


def test_the_cast_record_is_untouched_by_an_exclude(monkeypatch, tmp_path):
    """An exclude is a context rule, not a departure: the actor is still cast,
    so lifting the rule puts them straight back."""
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Seraphine", "default",
                                _npc_card("Seraphine", description="A keeper of the road."))
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    scenes.append_message(cid, sid, "user", "hello")
    pins.set_rule(cid, "characters:seraphine", pins.EXCLUDE, sid=sid)

    assert [a["id"] for a in ap.scene_cast(cid, sid)] == ["seraphine"]
    pins.remove(cid, "characters:seraphine", sid=sid)
    assert "A keeper of the road." in _system(cid, sid)


# --- TTL, measured in posts --------------------------------------------------

def test_a_ttl_pin_stops_forcing_the_entry_once_its_window_is_spent(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Tide oath",
                           "The tide keeps its promises.", keys="dragon")
    scenes.append_message(cid, sid, "user", "hello")
    pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid=sid, ttl_posts=2,
                  posts=len(scenes.read_scene(cid, sid)["messages"]))

    assert "tide keeps its promises" in _system(cid, sid)
    scenes.append_message(cid, sid, "assistant", "The water rises.")
    assert "tide keeps its promises" in _system(cid, sid)      # second post of two
    scenes.append_message(cid, sid, "user", "And then?")
    assert "tide keeps its promises" not in _system(cid, sid)  # spent


# --- surviving the packer ----------------------------------------------------

def _crowded(cid, sid):
    """A scene big enough that a halved budget forces the packer to drop."""
    config.write_config(recap_depth="0")
    for n in range(8):
        scenes.append_message(cid, sid, "user", f"Turn {n} on the Saltmarch road. " * 20)


def test_a_pin_holds_up_a_section_the_packer_would_have_dropped(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Tide oath",
                           "The tide keeps its promises. " * 60)
    _crowded(cid, sid)

    sent = _system(cid, sid)
    config.write_config(context_budget=str(context.count_tokens(sent) // 2))
    secs = {s["label"]: s for s in context.context_sections(cid, sid)}
    assert secs["World info"]["dropped"] is True                # the baseline: it goes

    pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid=sid)
    secs = {s["label"]: s for s in context.context_sections(cid, sid)}
    assert secs["World info"]["dropped"] is False
    assert secs["World info"]["pinned"] is True
    assert "tide keeps its promises" in _system(cid, sid)


def test_a_pinned_section_is_exempt_at_every_tier_not_merely_last():
    """A pin is not a fifth tier — `pack` skips it wherever it sits, and the
    section keeps the tier it was declared with so the inspector still says
    what kind of content it is."""
    sections = [{"label": "recalled", "text": "r " * 40, "tier": context_pack.RECALLED,
                 "pinned": True},
                {"label": "spotlight", "text": "s " * 40, "tier": context_pack.SPOTLIGHT}]
    out = context_pack.pack(sections, [], budget=1)
    dropped = {s["label"]: s["dropped"] for s in out["sections"]}
    assert dropped == {"recalled": False, "spotlight": True}
    assert out["sections"][0]["tier"] == context_pack.RECALLED


def test_a_pinned_character_holds_up_their_state_section(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    characters.create_character(croot, "Seraphine", "default",
                                _npc_card("Seraphine", description="A keeper."))
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    playstate.write_state(croot, "seraphine", "Wounded and furious. " * 40)
    _crowded(cid, sid)

    sent = _system(cid, sid)
    config.write_config(context_budget=str(context.count_tokens(sent) // 2))
    assert {s["label"]: s["dropped"] for s in context.context_sections(cid, sid)}["Character state"]

    pins.set_rule(cid, "characters:seraphine", pins.PIN, sid=sid)
    secs = {s["label"]: s for s in context.context_sections(cid, sid)}
    assert secs["Character state"]["dropped"] is False


def test_an_unbounded_budget_still_reports_pins(monkeypatch, tmp_path):
    """The packer skips counting entirely when unbounded; the flag must survive
    that shortcut, or the inspector shows a pin only under pressure."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Tide oath",
                           "The tide keeps its promises.")
    scenes.append_message(cid, sid, "user", "hello")
    pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid=sid)
    secs = {s["label"]: s for s in context.context_sections(cid, sid)}
    assert secs["World info"]["pinned"] is True
    assert secs["Response format"]["pinned"] is False


def test_a_garbled_pins_file_does_not_take_the_turn_down(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Tide oath",
                           "The tide keeps its promises.")
    scenes.append_message(cid, sid, "user", "hello")
    (campaigns.campaign_root(cid) / "pins.json").write_text("{ not json", encoding="utf-8")
    assert "tide keeps its promises" in _system(cid, sid)


@pytest.mark.parametrize("mode", [pins.PIN, pins.EXCLUDE])
def test_a_rule_naming_something_the_campaign_lost_is_inert(monkeypatch, tmp_path, mode):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "hello")
    pins.set_rule(cid, "lore:never-existed", mode, sid=sid)
    assert context.build_messages(cid, sid)          # composes, selects nothing
