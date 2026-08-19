"""What a pin and an exclude actually do to the assembled prompt (#129).

The store tests next door prove the rules survive being written down; these
prove they override the four mechanisms that otherwise decide what the model
sees — the keyword rule, the owner gate, the cast loop, and the budget packer.
"""

import pytest
from grimoire.store import appearances as ap
from grimoire.store import (
    campaigns,
    characters,
    config,
    context,
    dossiers,
    entities,
    groupstate,
    pcs,
    pins,
    playstate,
    scenes,
    worlds,
)
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
    """Byte-identical, not merely non-crashing: a rule for a record that is not
    there must not perturb the prompt in either direction — no empty section, no
    section quietly held up by a pin that selected nothing."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Tide oath",
                           "The tide keeps its promises.")
    scenes.append_message(cid, sid, "user", "hello")
    before = context.context_sections(cid, sid)

    pins.set_rule(cid, "lore:never-existed", mode, sid=sid)
    assert context.context_sections(cid, sid) == before


# --- the seams a later refactor could quietly break -------------------------

def test_every_protected_id_is_a_section_that_exists():
    """`_pinned_sections` names sections by `Section.id`, and a pin protects
    nothing at all if that id stops matching. Two of the three mappings
    (transient_state, group_state) are cheap to break and expensive to notice,
    since a pin that protects nothing looks exactly like a pin whose content did
    not activate.

    Ids rather than labels since #29 made the label the reader's to edit: two
    sections may legitimately carry the same label, so matching on it could hold
    up the wrong one. Against the CATALOG rather than a rendered layout: a
    reader may have dropped a section from their own order, and that is them
    choosing to lose it, not this mapping being wrong."""
    from grimoire.store.context import assemble
    ids = {s.id for s in assemble.SECTIONS}
    protected = {*assemble._CAST_SECTIONS, assemble._WORLD_INFO_SECTION,
                 assemble._GROUP_STATE_SECTION, assemble._SETTING_SECTION}
    assert protected <= ids


def test_a_pinned_group_holds_up_its_state_block_too(monkeypatch, tmp_path):
    """Activating a group pulls its campaign state into a section of its own, so
    a pin that saved the group's body and lost its state would have kept half
    of what the reader asked for."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    gid = entities.create_entity(croot, "groups", "Salt Circle", "A quiet cabal. " * 40)
    groupstate.write_state(croot, gid, groupstate.compose_body(
        {"goals": "Hold the gate. " * 40, "resources": "", "focus": "",
         "public_perception": "", "secrets": ""}))
    _crowded(cid, sid)

    sent = _system(cid, sid)
    config.write_config(context_budget=str(context.count_tokens(sent) // 2))
    assert {s["label"]: s["dropped"] for s in context.context_sections(cid, sid)}["Group state"]

    pins.set_rule(cid, f"groups:{gid}", pins.PIN, sid=sid)
    secs = {s["label"]: s for s in context.context_sections(cid, sid)}
    assert secs["World info"]["dropped"] is False
    assert secs["Group state"]["dropped"] is False


def test_an_excluded_character_does_not_come_back_as_off_scene_cast(monkeypatch, tmp_path):
    """The Off-scene cast section renders the roster MINUS whoever is in the
    scene, and it reads the cast record itself rather than the filtered list --
    so an excluded actor is absent from both halves. That holds by construction
    today and would invert the moment someone passes `_assemble`'s filtered cast
    into `_cast_directory_data`: exclude a character and their dossier would
    reappear one section down, which is the exact opposite of what was asked."""
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    characters.create_character(worlds.world_root(wid), "Seraphine", "default",
                                _npc_card("Seraphine", description="A keeper."))
    dossiers.write(croot, "seraphine", "She has been counting the tide-tolls.")
    scenes.append_message(cid, sid, "user", "hello")

    # First prove the section renders her at all -- cast elsewhere, so she is in
    # the campaign roster and off THIS scene. Without this half the assertion
    # below passes whether or not the exclusion works, since a character nobody
    # has ever cast is absent from the prompt either way.
    elsewhere = scenes.create_scene(cid, "Another")
    ap.appear(cid, elsewhere, "characters", "seraphine", "default", "npc")
    assert "counting the tide-tolls" in _system(cid, sid)

    # Now bring her on stage here and exclude her: she must leave by the front
    # door, not reappear through the off-scene one.
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    pins.set_rule(cid, "characters:seraphine", pins.EXCLUDE, sid=sid)
    text = _system(cid, sid)
    assert "A keeper." not in text
    assert "counting the tide-tolls" not in text


def test_a_shrinking_transcript_never_hands_back_more_posts_than_the_window(monkeypatch, tmp_path):
    """A retry pops the last reply, so the post count goes DOWN. The countdown
    restarts from the full window rather than exceeding it."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    for n in range(4):
        scenes.append_message(cid, sid, "user", f"post {n}")
    pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid=sid, ttl_posts=2, posts=4)
    assert pins.records(cid, sid, 4)[0]["remaining"] == 2
    assert pins.records(cid, sid, 1)[0]["remaining"] == 2      # not 5


# --- where a pin stops: gm-only secrecy (#49) --------------------------------

def test_a_pin_cannot_resurrect_a_gm_only_entry():
    """The one gate a pin does not open, and the distinction is the point: the
    owner gate and the keyword rule are CONDITIONS a pin answers, while gm-only
    is the entry saying it is not for the model at all. A pin that overrode it
    would turn pinning into a way to leak the GM's own notes."""
    entries = [{"name": "GM note", "body": "b", "keys": [], "kind": "lore",
                "id": "gm-note", "secrecy": "gm-only"}]
    assert context.activate(entries, "x", pinned_refs=frozenset({"lore:gm-note"})) == []


def test_a_pinned_gm_only_entry_never_reaches_the_prompt(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    entities.create_entity(campaigns.campaign_root(cid), "lore", "GM note",
                           "The tide oath was forged.", secrecy="gm-only")
    scenes.append_message(cid, sid, "user", "hello")
    pins.set_rule(cid, "lore:gm-note", pins.PIN, sid=sid)
    assert "tide oath was forged" not in _system(cid, sid)


def test_a_pinned_secret_entry_is_still_pinned(monkeypatch, tmp_path):
    """`secret` is not `gm-only`: it reaches the prompt, in its own block of the
    World info section, so a pin on one protects that section like any other."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Tide oath",
                           "The tide keeps its promises. " * 60, keys="dragon",
                           secrecy="secret")
    _crowded(cid, sid)
    pins.set_rule(cid, "lore:tide-oath", pins.PIN, sid=sid)

    sent = _system(cid, sid)
    assert "tide keeps its promises" in sent          # pinned past its unmatched key
    config.write_config(context_budget=str(context.count_tokens(sent) // 2))
    secs = {s["label"]: s for s in context.context_sections(cid, sid)}
    assert secs["World info"]["dropped"] is False
    assert secs["World info"]["pinned"] is True
