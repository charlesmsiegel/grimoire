from grimoire.store import context


def test_activate_keyword_and_always_on():
    entries = [
        {"name": "Salt Pact", "body": "the pact", "keys": ["pact", "salt"]},
        {"name": "Constant", "body": "always", "keys": []},
        {"name": "Hidden", "body": "secret", "keys": ["dragon"]},
    ]
    out = context.activate(entries, "We spoke of the Pact at dawn.")
    names = [e["name"] for e in out]
    assert "Salt Pact" in names      # 'Pact' whole-word, case-insensitive
    assert "Constant" in names       # keyless -> always-on
    assert "Hidden" not in names     # 'dragon' absent


def test_activate_whole_word_only():
    entries = [{"name": "Pac", "body": "x", "keys": ["pac"]}]
    # 'pact' must NOT trigger key 'pac' (whole-word match)
    assert context.activate(entries, "the pact") == []


def test_activate_owned_silent_when_owner_absent():
    entries = [{"name": "Backstory", "body": "b", "keys": [], "owners": ["characters:tanaka"]}]
    # keyless but owned -> NOT always-on; silent because owner not present
    assert context.activate(entries, "anything", present=frozenset()) == []


def test_activate_owned_on_when_owner_present_keyless():
    entries = [{"name": "Backstory", "body": "b", "keys": [], "owners": ["characters:tanaka"]}]
    out = context.activate(entries, "", present=frozenset({"characters:tanaka"}))
    assert [e["name"] for e in out] == ["Backstory"]


def test_activate_owned_present_still_needs_keyword():
    entries = [{"name": "Secret", "body": "s", "keys": ["duel"], "owners": ["characters:tanaka"]}]
    present = frozenset({"characters:tanaka"})
    assert context.activate(entries, "they talked", present=present) == []          # present, no keyword
    out = context.activate(entries, "the duel ended", present=present)              # present + keyword
    assert [e["name"] for e in out] == ["Secret"]


def test_activate_multi_owner_any_present():
    entries = [{"name": "Feud", "body": "f", "keys": [], "owners": ["characters:a", "characters:b"]}]
    out = context.activate(entries, "", present=frozenset({"characters:b"}))
    assert [e["name"] for e in out] == ["Feud"]


def test_activate_unowned_unchanged():
    entries = [{"name": "World", "body": "w", "keys": []}]  # no owners key at all
    assert [e["name"] for e in context.activate(entries, "x")] == ["World"]


import pytest  # noqa: E402

from grimoire.store import appearances as ap  # noqa: E402
from grimoire.store import campaigns, characters, chronicle, entities, plot, pcs, scenes, worlds  # noqa: E402


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    return wid, cid, sid


def _npc_card(name, **fields):
    card = characters.blank_card(name)
    card["data"].update(fields)
    return card


def test_owned_lore_only_shows_when_owner_in_scene(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    croot = campaigns.campaign_root(cid)
    characters.create_character(wroot, "Tanaka", "default", _npc_card("Tanaka", description="sensei"))
    # owned, keyless lore for the character (id is the slug "tanaka")
    entities.create_entity(croot, "lore", "Tanaka secret", "He was exiled.",
                           owners="characters:tanaka")
    scenes.append_message(cid, sid, "user", "hello")

    # owner NOT in scene -> lore absent
    assert "He was exiled." not in context.build_messages(cid, sid)[0]["content"]

    # bring the owner into the scene -> lore present
    ap.appear(cid, sid, "characters", "tanaka", "default", "npc")
    assert "He was exiled." in context.build_messages(cid, sid)[0]["content"]


def test_location_owned_lore_only_shows_at_that_location(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    loc = entities.create_entity(croot, "locations", "Old Dojo", "A worn training hall.")
    entities.create_entity(croot, "lore", "Dojo secret", "A blade is hidden under the floor.",
                           owners=f"locations:{loc}")
    scenes.append_message(cid, sid, "user", "look around")

    # not at the location -> owned lore absent
    assert "hidden under the floor" not in context.build_messages(cid, sid)[0]["content"]

    # set the current location to the owner -> owned lore present
    scenes.set_location(cid, sid, loc)
    assert "hidden under the floor" in context.build_messages(cid, sid)[0]["content"]


def test_pc_owned_lore_activates_when_pc_in_scene(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    pid, _vid = pcs.create_pc(croot, "Hero", [], persona=pcs.blank_persona("Hero"))
    entities.create_entity(croot, "lore", "Hero secret", "She carries a hidden key.",
                           owners=f"pcs:{pid}")
    scenes.append_message(cid, sid, "user", "hello")

    # PC not in scene -> owned lore absent
    assert "hidden key" not in context.build_messages(cid, sid)[0]["content"]

    # bring the PC into the scene as a player -> owned lore present
    ap.appear(cid, sid, "pcs", pid, "default", "player")
    assert "hidden key" in context.build_messages(cid, sid)[0]["content"]


def test_new_kinds_activate_like_lore(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "groups", "Salt Circle", "A quiet cabal.")          # keyless -> always-on
    entities.create_entity(croot, "creatures", "Marsh Wyrm", "Sleeps in brine.", keys="wyrm")
    entities.create_entity(croot, "items", "Salt Knife", "Cuts anything.", keys="knife")
    scenes.append_message(cid, sid, "user", "The wyrm stirs.")
    text = context.build_messages(cid, sid)[0]["content"]
    assert "A quiet cabal." in text          # keyless group always-on
    assert "Sleeps in brine." in text        # 'wyrm' key matched
    assert "Cuts anything." not in text      # 'knife' key absent


def test_single_npc_block_order(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Seraphine", "default",
                                _npc_card("Seraphine", description="keeper", personality="cold", scenario="docks"))
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    scenes.append_message(cid, sid, "user", "hello")
    msgs = context.build_messages(cid, sid)
    assert msgs[0]["role"] == "system"
    sys = msgs[0]["content"]
    assert sys.index("keeper") < sys.index("cold") < sys.index("docks")
    assert msgs[-1] == {"role": "user", "content": "hello"}


def test_multi_npc_scene_leaves_char_token_literal(monkeypatch, tmp_path):
    # #137: {{char}} is never resolved to "the present NPC cast" -- with more
    # than one NPC present that would be ambiguous (which one?). It's baked to
    # a card's own name at creation instead (see test_bake_char_name_* below);
    # a {{char}} typed into a chat message has no "self" to bake against, so
    # it stays literal, same as an unresolved {{user}}.
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Seraphine", "default", _npc_card("Seraphine", description="A"))
    characters.create_character(wroot, "Drowned King", "default", _npc_card("Drowned King", description="B"))
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    ap.appear(cid, sid, "characters", "drowned-king", "default", "npc")
    scenes.append_message(cid, sid, "user", "{{char}} arrives")
    msgs = context.build_messages(cid, sid)
    assert "A" in msgs[0]["content"] and "B" in msgs[0]["content"]
    assert msgs[-1]["content"] == "{{char}} arrives"


def test_player_persona_and_user_token(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    pcs.create_pc(worlds.world_root(wid), "Elara", [],
                  persona={"name": "Elara", "pronouns": "she/her", "summary": "scholar", "description": "A wanderer."})
    ap.appear(cid, sid, "pcs", "elara", "default", "player")
    scenes.append_message(cid, sid, "user", "I am {{user}}")
    msgs = context.build_messages(cid, sid)
    assert "A wanderer." in msgs[0]["content"]
    assert msgs[-1]["content"] == "I am Elara"


def test_post_history_is_last(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Seraphine", "default",
                                _npc_card("Seraphine", description="d", post_history_instructions="STAY IN CHARACTER"))
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    scenes.append_message(cid, sid, "user", "hi")
    msgs = context.build_messages(cid, sid)
    assert msgs[-1] == {"role": "system", "content": "STAY IN CHARACTER"}
    assert msgs[0]["role"] == "system" and "d" in msgs[0]["content"]


def test_worldinfo_keyword_depth(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    entities.create_entity(campaigns.campaign_root(cid), "lore", "Salt Pact", "the pact lore", keys="pact")
    for i in range(10):
        scenes.append_message(cid, sid, "user", "we mentioned the pact" if i == 0 else f"filler {i}")
    from grimoire.store import config
    config.write_config(context_scan_depth="3")
    # the only 'pact' is message 0; with depth 3 the scan sees only the last 3 fillers.
    # no cast, no always-on entry, key outside depth -> only the Response format section.
    sys_msgs = [m for m in context.build_messages(cid, sid) if m["role"] == "system"]
    assert len(sys_msgs) == 1 and "the pact lore" not in sys_msgs[0]["content"]


def test_worldinfo_always_on_and_in_depth(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "Const", "always lore", keys="")
    entities.create_entity(croot, "lore", "Salt", "pact lore", keys="pact")
    scenes.append_message(cid, sid, "user", "the pact matters")
    sys = context.build_messages(cid, sid)[0]["content"]
    assert "always lore" in sys and "pact lore" in sys


def test_empty_context_is_raw_history(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "plain message")
    msgs = context.build_messages(cid, sid)
    # an empty store still gets the Response format section; the history stays raw
    assert msgs[0]["role"] == "system" and "Write your reply as a script" in msgs[0]["content"]
    assert msgs[1:] == [{"role": "user", "content": "plain message"}]


def test_character_cast_as_player_uses_persona_not_char(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Desmond", "default",
                                _npc_card("Desmond", description="a tall man", personality="gruff"))
    ap.appear(cid, sid, "characters", "desmond", "default", "player")
    scenes.append_message(cid, sid, "user", "I am {{user}}, not {{char}}")
    msgs = context.build_messages(cid, sid)
    assert "a tall man" in msgs[0]["content"]          # injected as persona, not an NPC block
    # {{user}} resolves to the player; {{char}} stays literal (no NPCs in scene)
    assert msgs[-1]["content"] == "I am Desmond, not {{char}}"


def test_substitution_in_card_and_worldinfo(monkeypatch, tmp_path):
    # #137: a card's own {{char}} is baked to its own name at creation
    # (self-reference, unambiguous); world-info has no single "self" to bake
    # against, so its {{char}} is never resolved and stays literal.
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    characters.create_character(worlds.world_root(wid), "Seraphine", "default",
                                _npc_card("Seraphine", description="{{char}} greets {{user}}"))
    pcs.create_pc(worlds.world_root(wid), "Elara", [],
                  persona={"name": "Elara", "pronouns": "", "summary": "", "description": "hero"})
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    ap.appear(cid, sid, "pcs", "elara", "default", "player")
    entities.create_entity(croot, "lore", "Note", "{{char}} knows {{user}}", keys="")
    scenes.append_message(cid, sid, "user", "hi")
    sys = context.build_messages(cid, sid)[0]["content"]
    assert "Seraphine greets Elara" in sys    # baked at card creation, then {{user}} substituted
    assert "{{char}} knows Elara" in sys      # world-info {{char}} stays literal, {{user}} substituted


def test_mes_example_and_system_prompt_placement(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Seraphine", "default",
                                _npc_card("Seraphine", system_prompt="SYS", description="desc", mes_example="EXAMPLE"))
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    scenes.append_message(cid, sid, "user", "hi")
    sys = context.build_messages(cid, sid)[0]["content"]
    assert sys.index("SYS") < sys.index("desc") < sys.index("EXAMPLE")


def test_substitute_handles_backslash_name(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    # a name with regex-replacement metachars must not crash or corrupt
    pcs.create_pc(worlds.world_root(wid), r"A\1B", [])
    ap.appear(cid, sid, "pcs", pcs.pc_refs(worlds.world_root(wid))[0], "default", "player")
    scenes.append_message(cid, sid, "user", "hi {{user}}")
    assert context.build_messages(cid, sid)[-1]["content"] == r"hi A\1B"


def test_build_opener_messages(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    pcs.create_pc(worlds.world_root(wid), "Elara", [],
                  persona={"name": "Elara", "pronouns": "", "summary": "", "description": "a scholar"})
    ap.appear(cid, sid, "pcs", "elara", "default", "player")
    # an in-scene NPC — the opener now sees the cast, not just the players
    characters.create_character(worlds.world_root(wid), "Seraphine", "default",
                                _npc_card("Seraphine", description="a harbor keeper"))
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    entities.create_entity(croot, "lore", "Always", "ambient lore", keys="")
    entities.create_entity(croot, "lore", "Salt", "salt lore", keys="salt")
    loc = entities.create_entity(croot, "locations", "The Docks", "Rotting piers and grey water.")
    scenes.set_location(cid, sid, loc)
    sid = scenes.set_datetime(cid, sid, "2026-12-25")["id"]  # first date set renames the scene
    plot.set_movement(cid, "the-map", "The map", "open", "A clue surfaces.", sid)
    chronicle.absorb(cid, {"id": "s00", "one_line": "They fled the city.",
                           "summary": "The party escaped the burning capital by river."})

    msgs = context.build_opener_messages(cid, sid, "A storm over the salt marshes for {{user}}.")
    assert msgs[0]["role"] == "system"
    sys = msgs[0]["content"]
    assert "a scholar" in sys                       # player persona present
    assert "a harbor keeper" in sys                 # in-scene NPC description present
    assert "ambient lore" in sys                    # always-on lore present
    assert "salt lore" in sys                        # 'salt' activated by the prompt text
    assert "Rotting piers and grey water." in sys   # current setting (location)
    assert "It is 25 December 2026 (Friday)." in sys # date
    assert "The map (open): A clue surfaces." in sys  # plot thread
    assert "The party escaped the burning capital by river." in sys  # full recap summary...
    assert "They fled the city." not in sys          # ...not the compact one-liner
    assert "{{user}}" not in sys                     # substituted
    user_msg = next(m for m in msgs if m["role"] == "user")
    assert user_msg["content"] == "A storm over the salt marshes for Elara."


def test_opener_shape_is_the_last_message_and_names_the_cast(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Seraphine", "default",
                                _npc_card("Seraphine", description="a harbor keeper"))
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    msgs = context.build_opener_messages(cid, sid, "A storm.")
    last = msgs[-1]
    assert last["role"] == "system" and msgs[-2]["role"] == "user"  # rides last, after the prompt
    assert "at most five short paragraphs" in last["content"]
    assert "**Seraphine:**" in last["content"]              # names the present cast's markers
    assert "never under **Grimoire:**" in last["content"]   # actions belong to the character


def test_opener_shape_without_npcs_uses_generic_marker(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    last = context.build_opener_messages(cid, sid, "A storm.")[-1]
    assert last["role"] == "system"
    assert "**<Name>:**" in last["content"]
    assert "at most five short paragraphs" in last["content"]


def test_offscreen_opener_keeps_shape_last(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    sid2 = scenes.create_scene(cid, "Off", pcless=True)
    msgs = context.build_opener_messages(cid, sid2, "A storm.")
    assert "offscreen" in msgs[0]["content"].split("\n\n")[0]
    assert "at most five short paragraphs" in msgs[-1]["content"]


def test_depth_zero_and_unparseable_fallback(monkeypatch, tmp_path):
    from grimoire.store import config
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "Salt", "pact lore", keys="pact")
    scenes.append_message(cid, sid, "user", "the pact matters")
    config.write_config(context_scan_depth="0")  # no scan window -> keyword entry not activated
    sys_msgs = [m for m in context.build_messages(cid, sid) if m["role"] == "system"]
    assert len(sys_msgs) == 1 and "pact lore" not in sys_msgs[0]["content"]
    config.write_config(context_scan_depth="abc")  # unparseable -> fallback 8 -> 'pact' activates
    assert "pact lore" in context.build_messages(cid, sid)[0]["content"]


def test_cast_directory_tiers(monkeypatch, tmp_path):
    from grimoire.store import taglines, dossiers
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)

    # present in this scene (full card)
    characters.create_character(wroot, "Aese", "main", _npc_card("Aese", description="present-desc"))
    # appeared elsewhere in the campaign -> tier 2 paragraph from the campaign dossier
    characters.create_character(wroot, "Myval", "main", _npc_card("Myval", description="m"))
    # off-roster with a tagline and two versions -> tier 3 sentence + version list
    characters.create_character(wroot, "Akane", "main", _npc_card("Akane", description="a"))
    characters.create_version(wroot, "akane", "futa", _npc_card("Akane", description="a"))
    taglines.write(wroot, "akane", "An eager doggirl.")
    # off-roster WITHOUT a tagline (must be skipped)
    characters.create_character(wroot, "Ghost", "main", _npc_card("Ghost", description="g"))

    # seed BEFORE the fork: the campaign copy carries cards + taglines
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    croot = campaigns.campaign_root(cid)

    # Myval appears in a different scene -> roster, not in this scene's cast
    other = scenes.create_scene(cid, "Other")
    ap.appear(cid, other, "characters", "myval", "main", "npc")
    # Aese appears in our scene
    ap.appear(cid, sid, "characters", "aese", "main", "npc")
    dossiers.write(croot, "myval", "Myval prowls the dusk road.")
    scenes.append_message(cid, sid, "user", "hi")

    sys = context.build_messages(cid, sid)[0]["content"]
    assert "present-desc" in sys                                         # tier 1 full card
    assert "Myval: Myval prowls the dusk road." in sys                   # tier 2 dossier
    assert "Akane: An eager doggirl. (available as: futa, main)" in sys  # tier 3 tagline + versions
    assert "Ghost" not in sys                                            # no tagline -> skipped
    assert "Myval" not in sys.split("## Known to exist")[1]              # roster char not in tier 3


def test_cast_directory_absent_when_no_artifacts(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Aese", "main", _npc_card("Aese", description="d"))
    ap.appear(cid, sid, "characters", "aese", "main", "npc")
    scenes.append_message(cid, sid, "user", "hi")
    sys = context.build_messages(cid, sid)[0]["content"]
    assert "Other characters in this world" not in sys


def test_current_setting_injected_once(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    loc = entities.create_entity(croot, "locations", "Salt Cathedral", "A drowned basilica of black salt.")
    scenes.set_location(cid, sid, loc)
    scenes.append_message(cid, sid, "user", "look around")
    sys = context.build_messages(cid, sid)[0]["content"]
    assert "# Current setting" in sys
    # the current location is shown once as the setting; exclude keeps a keyed current
    # location from also re-injecting via world-info
    assert sys.count("A drowned basilica of black salt.") == 1


def test_keyless_noncurrent_location_stays_silent(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    here = entities.create_entity(croot, "locations", "Cohen House", "A quiet suburban home.")
    entities.create_entity(croot, "locations", "The Ascend Institute",
                           "A gleaming laboratory of demi-human genesis.")  # keyless, not current
    scenes.set_location(cid, sid, here)
    scenes.append_message(cid, sid, "user", "look around")
    sys = context.build_messages(cid, sid)[0]["content"]
    # a keyless location that isn't the current setting must not leak into the scene
    assert "gleaming laboratory of demi-human genesis" not in sys


def test_keyed_location_activates_on_keyword_when_not_current(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    here = entities.create_entity(croot, "locations", "Cohen House", "A quiet suburban home.")
    entities.create_entity(croot, "locations", "The Ascend Institute",
                           "A gleaming laboratory of demi-human genesis.",
                           keys="Ascend Institute")
    scenes.set_location(cid, sid, here)

    # keyword absent -> silent
    scenes.append_message(cid, sid, "user", "look around")
    assert "gleaming laboratory" not in context.build_messages(cid, sid)[0]["content"]

    # keyword present in recent chat -> the keyed location activates even though it isn't current
    scenes.append_message(cid, sid, "user", "tell me about the Ascend Institute")
    assert "gleaming laboratory" in context.build_messages(cid, sid)[0]["content"]


def test_no_setting_block_when_unset(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "hi")
    msgs = context.build_messages(cid, sid)
    sys = msgs[0]["content"] if msgs and msgs[0]["role"] == "system" else ""
    assert "# Current setting" not in sys


def test_natural_prose_section_in_system_prompt(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "hi")
    msgs = context.build_messages(cid, sid)
    assert msgs[0]["role"] == "system"
    assert "# Natural prose" in msgs[0]["content"]


def test_natural_prose_section_in_opener_prompt(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    msgs = context.build_opener_messages(cid, sid, "A storm rolls in.")
    assert msgs[0]["role"] == "system"
    assert "# Natural prose" in msgs[0]["content"]


def test_context_sections_labels_and_global_prompt(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import config
    config.write_config(system_prompt="Never speak for the PC.")
    characters.create_character(worlds.world_root(wid), "Seraphine", "default",
                                _npc_card("Seraphine", description="keeper"))
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    scenes.append_message(cid, sid, "user", "hello")
    secs = context.context_sections(cid, sid)
    labels = [s["label"] for s in secs]
    assert labels[0] == "Global system prompt"
    assert secs[0]["text"] == "Never speak for the PC."
    assert "Character descriptions" in labels
    assert "Conversation history" in labels
    assert all(s["text"].strip() for s in secs)  # no empty sections


def test_count_tokens_positive_and_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assert context.count_tokens("") == 0
    assert context.count_tokens("the drowned king waits") > 0


def test_today_block_present_when_dated(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)  # default region US
    sid = scenes.set_datetime(cid, sid, "2026-12-25")["id"]  # first date set renames the scene
    sections = context.context_sections(cid, sid)
    assert "Today" in [s["label"] for s in sections]
    today = next(s["text"] for s in sections if s["label"] == "Today")
    assert "It is 25 December 2026 (Friday)." in today
    assert "Christmas Day" in today


def test_no_today_block_when_undated(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    assert "Today" not in [s["label"] for s in context.context_sections(cid, sid)]


def test_today_block_includes_present_cast_age(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    characters.create_character(croot, "Seraphine", "default", characters.blank_card("Seraphine"))
    characters.set_birthdate(croot, "seraphine", "1990-12-25")
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    sid = scenes.set_datetime(cid, sid, "2026-12-25")["id"]  # first date set renames the scene
    today = next(s["text"] for s in context.context_sections(cid, sid) if s["label"] == "Today")
    assert "Seraphine" in today and "36" in today and "birthday" in today.lower()


def test_story_so_far_section_is_injected(monkeypatch, tmp_path):
    from grimoire.store import campaigns, chronicle, context, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "Now")
    chronicle.absorb(cid, {"id": "2026-01-01-past", "one_line": "They first met.",
                           "summary": "A met B.", "keywords": []})
    sections = {s["label"]: s["text"] for s in context.context_sections(cid, sid)}
    assert "Story so far" in sections
    text = sections["Story so far"]
    assert "They first met." in text and text.startswith("# Story so far")


def test_story_so_far_absent_when_empty(monkeypatch, tmp_path):
    from grimoire.store import campaigns, context, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "Now")
    assert "Story so far" not in {s["label"] for s in context.context_sections(cid, sid)}


def test_story_so_far_tolerates_garbled_chronicle(monkeypatch, tmp_path):
    from grimoire.store import campaigns, context, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "Now")
    (campaigns.campaign_root(cid) / "chronicle.json").write_text("{ not valid json", encoding="utf-8")
    labels = {s["label"] for s in context.context_sections(cid, sid)}  # must not raise
    assert "Story so far" not in labels
    context.build_messages(cid, sid)  # the real consumer must not crash either


def test_character_state_section_injected(monkeypatch, tmp_path):
    from grimoire.store import (appearances, campaigns, characters, context,
                                playstate, scenes, worlds)
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    croot = campaigns.campaign_root(cid)
    ch = characters.create_character(croot, "Seraphine", "main", characters.blank_card("Seraphine"))[0]
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    playstate.write_state(croot, ch, "Wounded; travels with the party.")
    system = {s["label"]: s["text"] for s in context.context_sections(cid, sid)}
    assert "Character state" in system
    assert "Seraphine: Wounded; travels with the party." in system["Character state"]


def test_character_state_renders_knowledge(monkeypatch, tmp_path):
    from grimoire.store import (appearances, campaigns, characters, context,
                                playstate, scenes, worlds)
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    croot = campaigns.campaign_root(cid)
    ch = characters.create_character(croot, "Seraphine", "main", characters.blank_card("Seraphine"))[0]
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    playstate.write_state(croot, ch, playstate.compose_body("Hurt.", "map is fake", "elara lies"))
    section = {s["label"]: s["text"] for s in context.context_sections(cid, sid)}["Character state"]
    assert "Seraphine: Hurt." in section
    assert "Knows: map is fake" in section
    assert "Suspects: elara lies" in section


def test_character_state_no_dangling_name_when_current_state_empty(monkeypatch, tmp_path):
    from grimoire.store import (appearances, campaigns, characters, context,
                                playstate, scenes, worlds)
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    croot = campaigns.campaign_root(cid)
    ch = characters.create_character(croot, "Alice", "main", characters.blank_card("Alice"))[0]
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    playstate.write_state(croot, ch, playstate.compose_body("", "the password", ""))
    section = {s["label"]: s["text"] for s in context.context_sections(cid, sid)}["Character state"]
    assert "Alice:\n" not in section and not section.endswith("Alice:")  # no dangling colon
    assert "Alice: Knows: the password" in section


def test_character_state_multiline_knowledge_stays_indented(monkeypatch, tmp_path):
    from grimoire.store import (appearances, campaigns, characters, context,
                                playstate, scenes, worlds)
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    croot = campaigns.campaign_root(cid)
    ch = characters.create_character(croot, "Seraphine", "main", characters.blank_card("Seraphine"))[0]
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    playstate.write_state(croot, ch, playstate.compose_body("Hurt.", "line one\nline two", ""))
    section = {s["label"]: s["text"] for s in context.context_sections(cid, sid)}["Character state"]
    assert "  Knows: line one" in section
    assert "\n    line two" in section  # continuation re-indented, not flush-left


def test_plot_threads_section_injected(monkeypatch, tmp_path):
    from grimoire.store import campaigns, context, plot, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    sid = scenes.create_scene(cid, "Now")
    plot.set_movement(cid, "the-map", "The map", "advanced", "It is a forgery.", "s12")
    plot.set_movement(cid, "done", "Done", "closed", "resolved", "s5")
    section = {s["label"]: s["text"] for s in context.context_sections(cid, sid)}["Plot threads"]
    assert "The map (advanced): It is a forgery." in section
    assert "Done" not in section  # closed excluded


def test_plot_threads_absent_when_none(monkeypatch, tmp_path):
    from grimoire.store import campaigns, context, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    sid = scenes.create_scene(cid, "Now")
    assert "Plot threads" not in {s["label"] for s in context.context_sections(cid, sid)}


def test_plot_threads_tolerates_garbled(monkeypatch, tmp_path):
    from grimoire.store import campaigns, context, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    sid = scenes.create_scene(cid, "Now")
    (campaigns.campaign_root(cid) / "plot.json").write_text("{ not json", encoding="utf-8")
    context.context_sections(cid, sid)  # must not raise


def test_character_state_absent_when_none(monkeypatch, tmp_path):
    from grimoire.store import campaigns, context, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "Now")
    assert "Character state" not in {s["label"] for s in context.context_sections(cid, sid)}


def test_relationships_section_injected(monkeypatch, tmp_path):
    from grimoire.store import (appearances, campaigns, characters, context,
                                relationships, scenes, worlds)
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    a = characters.create_character(croot, "Ann", "main", characters.blank_card("Ann"))[0]
    b = characters.create_character(croot, "Bo", "main", characters.blank_card("Bo"))[0]
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", a, "main", "npc")
    appearances.appear(cid, sid, "characters", b, "main", "npc")
    relationships.set_feeling(cid, f"characters:{a}", f"characters:{b}", 4, 3, 1, "warm")
    system = {s["label"]: s["text"] for s in context.context_sections(cid, sid)}
    assert "Relationships" in system
    assert "Ann → Bo: trust 4, affection 3, tension 1 (warm)" in system["Relationships"]


def test_relationships_absent_when_none(monkeypatch, tmp_path):
    from grimoire.store import campaigns, context, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    sid = scenes.create_scene(cid, "Now")
    assert "Relationships" not in {s["label"] for s in context.context_sections(cid, sid)}


def test_history_projection_labels_and_merges(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    pid, pvid = pcs.create_pc(worlds.world_root(wid), "Elara Vane", [])
    ap.appear(cid, sid, "pcs", pid, pvid, "player")
    scenes.append_message(cid, sid, "user", "I enter.", speaker="Elara Vane")
    scenes.append_message(cid, sid, "assistant", '"You dare?"', speaker="Seraphine Vale")
    scenes.append_message(cid, sid, "assistant", "Thunder rolls.")
    hist = [m for m in context.build_messages(cid, sid) if m["role"] != "system"]
    assert hist == [
        {"role": "user", "content": "**Elara Vane:** I enter."},
        {"role": "assistant",
         "content": '**Seraphine Vale:** "You dare?"\n\n**Grimoire:** Thunder rolls.'},
    ]


def test_unstamped_user_lines_stay_bare(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "plain message")
    hist = [m for m in context.build_messages(cid, sid) if m["role"] != "system"]
    assert hist == [{"role": "user", "content": "plain message"}]


def test_response_format_section_lists_players(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    pid, pvid = pcs.create_pc(worlds.world_root(wid), "Elara Vane", [])
    ap.appear(cid, sid, "pcs", pid, pvid, "player")
    sections = {s["label"]: s["text"] for s in context.context_sections(cid, sid)}
    assert "Write your reply as a script" in sections["Response format"]
    assert "Elara Vane" in sections["Response format"]


def test_prose_style_resolves_scene_then_campaign_then_global(monkeypatch, tmp_path):
    from grimoire.store import config

    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "hello")

    # nothing set anywhere -> no prose-style block
    assert "Prose style" not in context.build_messages(cid, sid)[0]["content"]

    config.write_config(default_style_id="gothic-horror")
    text = context.build_messages(cid, sid)[0]["content"]
    assert "Prose style: Gothic Horror" in text

    campaigns.set_campaign_style(cid, "noir-detective")
    text = context.build_messages(cid, sid)[0]["content"]
    assert "Prose style: Noir Detective" in text
    assert "Gothic Horror" not in text

    scenes.set_style(cid, sid, "pulp-adventure")
    text = context.build_messages(cid, sid)[0]["content"]
    assert "Prose style: Pulp Adventure" in text
    assert "Noir Detective" not in text

    # a stale/unknown scene override falls back to the campaign default
    scenes.set_style(cid, sid, "does-not-exist")
    text = context.build_messages(cid, sid)[0]["content"]
    assert "Prose style: Noir Detective" in text


from grimoire.store import groupstate  # noqa: E402


def test_group_state_rides_group_activation(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "groups", "Salt Circle", "A quiet cabal.")  # keyless -> always-on
    groupstate.write_state(croot, "salt-circle", "## Goals\nFind the ledger.")
    scenes.append_message(cid, sid, "user", "hello")
    text = context.build_messages(cid, sid)[0]["content"]
    assert "A quiet cabal." in text
    assert "Find the ledger." in text
    assert "# Group state" in text


def test_keyed_group_state_absent_when_group_inactive(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "groups", "Salt Circle", "A quiet cabal.", keys="cabal")
    groupstate.write_state(croot, "salt-circle", "## Goals\nFind the ledger.")
    scenes.append_message(cid, sid, "user", "nothing relevant")
    text = context.build_messages(cid, sid)[0]["content"]
    assert "A quiet cabal." not in text
    assert "Find the ledger." not in text


def test_group_without_state_adds_no_section(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "groups", "Salt Circle", "A quiet cabal.")
    scenes.append_message(cid, sid, "user", "hello")
    text = context.build_messages(cid, sid)[0]["content"]
    assert "A quiet cabal." in text
    assert "# Group state" not in text


def test_group_state_in_context_sections(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "groups", "Salt Circle", "A quiet cabal.")
    groupstate.write_state(croot, "salt-circle", "## Goals\nFind the ledger.")
    scenes.append_message(cid, sid, "user", "hello")
    labels = [s["label"] for s in context.context_sections(cid, sid)]
    assert "Group state" in labels


# ---- Task 6: mechanics rules / sheets / response-format sections (#162) ----

import json  # noqa: E402

from grimoire.store import sheets as sheets_store  # noqa: E402


def _make_module(tmp_path, mid, sheet_types=None, checks=None, rules=None):
    """A minimal module pack under the campaign's user module library
    (mirrors test_modules_store.make_pack, kept local since test_context.py
    doesn't otherwise depend on that module)."""
    d = tmp_path / "modules" / mid
    (d / "rules").mkdir(parents=True)
    (d / "module.md").write_text("---\nname: Test Module\n---\n", encoding="utf-8")
    (d / "sheets.json").write_text(
        json.dumps({"groups": {}, "sheet_types": sheet_types or {}}), encoding="utf-8")
    if checks is not None:
        (d / "checks.json").write_text(json.dumps(checks), encoding="utf-8")
    for name, text in (rules or {}).items():
        (d / "rules" / f"{name}.md").write_text(text, encoding="utf-8")
    return mid


def test_mechanics_sections_for_module_bound_campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    mid = _make_module(
        tmp_path, "arcana",
        sheet_types={"adept": {"label": "Adept", "kind": "characters", "groups": [],
                               "fields": [{"key": "mana", "label": "Mana", "type": "resource", "max": 5},
                                          {"key": "focus", "label": "Focus", "type": "dots",
                                           "max": 5, "default": 0}]}},
        checks={"focus-check": {"label": "Focus Check", "requires": [], "roll": "1d20"}},
        rules={"core": "---\nalways: true\n---\nCore rule text.\n",
               "dragon-lore": "---\nkeys: dragon\n---\nDragon lore text.\n"})
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid, module=mid)
    sid = scenes.create_scene(cid, "S")
    croot = campaigns.campaign_root(cid)
    char = characters.create_character(croot, "Winifred", "default",
                                       characters.blank_card("Winifred"))[0]
    ap.appear(cid, sid, "characters", char, "default", "npc")
    sheets_store.write(cid, "characters", char, "adept",
                       {"mana": {"current": 3, "max": 5}, "focus": 2}, expected=None)
    scenes.append_message(cid, sid, "user", "We cast a spell.")

    sections = {s["label"]: s["text"] for s in context.context_sections(cid, sid)}
    assert {"Mechanics rules", "Mechanics sheets", "Mechanics response format"} <= sections.keys()

    rules_text = sections["Mechanics rules"]
    assert "Core rule text." in rules_text          # always -> always included
    assert "Dragon lore text." not in rules_text     # unmatched 'keys:' doc -> excluded

    sheet_text = sections["Mechanics sheets"]
    assert f"characters:{char}" in sheet_text        # kind:id ref
    assert "mana 3/5" in sheet_text                  # resource -> cur/max

    response_text = sections["Mechanics response format"]
    assert "focus-check" in response_text            # available check id listed


def test_mechanics_sections_absent_when_unbound(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)  # no module bound
    scenes.append_message(cid, sid, "user", "hello")
    labels = {s["label"] for s in context.context_sections(cid, sid)}
    assert not labels & {"Mechanics rules", "Mechanics sheets", "Mechanics response format"}


def test_mechanics_rules_keyword_cap(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    mid = _make_module(
        tmp_path, "keyed",
        rules={f"rule{i}": f"---\nkeys: kw{i}\n---\nBody {i}.\n" for i in range(1, 9)})
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid, module=mid)
    sid = scenes.create_scene(cid, "S")
    scenes.append_message(cid, sid, "user", "kw1 kw2 kw3 kw4 kw5 kw6 kw7 kw8")

    rules_text = next(s["text"] for s in context.context_sections(cid, sid)
                      if s["label"] == "Mechanics rules")
    present = [f"Body {i}." for i in range(1, 9) if f"Body {i}." in rules_text]
    assert len(present) == 6                        # 8 matches, capped at 6
    assert present == [f"Body {i}." for i in range(1, 7)]  # first six, in pack order


# ---- issue #137: macro expansion ({{roll:}}, {{random:}}, {{date}}/{{time}}/{{weekday}}) ----

def test_roll_macro_expands_to_a_number(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "You roll {{roll:1d20}} to hit.")
    text = context.build_messages(cid, sid)[-1]["content"]
    assert "{{roll" not in text
    n = int(text.removeprefix("You roll ").removesuffix(" to hit."))
    assert 1 <= n <= 20


def test_roll_macro_supports_dice_py_grammar(monkeypatch, tmp_path):
    # 2d6+3: dice.py's full notation (modifiers), not just bare NdM
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "Damage: {{roll:2d6+3}}")
    text = context.build_messages(cid, sid)[-1]["content"]
    n = int(text.removeprefix("Damage: "))
    assert 5 <= n <= 15


def test_random_macro_picks_one_option(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "The sky is {{random:red,blue,green}}.")
    text = context.build_messages(cid, sid)[-1]["content"]
    assert text in ("The sky is red.", "The sky is blue.", "The sky is green.")


def test_malformed_roll_macro_is_dropped_not_leaked(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "Bad: {{roll:not-dice}} end")
    text = context.build_messages(cid, sid)[-1]["content"]
    assert text == "Bad:  end"


def test_unknown_macro_is_dropped_not_leaked(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "{{lastMessage}} hi")
    text = context.build_messages(cid, sid)[-1]["content"]
    assert text == " hi"


def test_user_and_char_macros_still_stay_literal_when_unresolved(monkeypatch, tmp_path):
    # the cleanup pass must not regress the existing {{user}}/{{char}} contract
    # (test_character_cast_as_player_uses_persona_not_char): unresolved cast
    # macros stay literal, they are never silently dropped.
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "{{char}} is unknown here")
    text = context.build_messages(cid, sid)[-1]["content"]
    assert text == "{{char}} is unknown here"


def test_date_time_weekday_macros_expand_from_scene_datetime(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    sid = scenes.set_datetime(cid, sid, "2026-12-25T14:30")["id"]
    scenes.append_message(cid, sid, "user", "It is {{date}} ({{weekday}}) at {{time}}.")
    text = context.build_messages(cid, sid)[-1]["content"]
    assert text == "It is 25 December 2026 (Friday) at 14:30."


def test_date_time_weekday_macros_dropped_before_any_scene_time(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "Today is {{date}}, {{weekday}} at {{time}}.")
    text = context.build_messages(cid, sid)[-1]["content"]
    assert text == "Today is ,  at ."


def test_roll_macro_expands_in_opener_prompt(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    msgs = context.build_opener_messages(cid, sid, "Initiative: {{roll:1d20}}!")
    user_msg = next(m for m in msgs if m["role"] == "user")
    n = int(user_msg["content"].removeprefix("Initiative: ").removesuffix("!"))
    assert 1 <= n <= 20


def test_natural_prose_in_context_sections(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import config
    # A configured style makes the ordering assertion meaningful: the spec
    # requires Natural prose directly AFTER Prose style, mirroring system.j2.
    config.write_config(default_style_id="gothic-horror")
    secs = context.context_sections(cid, sid)
    labels = [s["label"] for s in secs]
    assert "Natural prose" in labels
    assert labels.index("Natural prose") == labels.index("Prose style") + 1
    text = next(s["text"] for s in secs if s["label"] == "Natural prose")
    assert text.startswith("# Natural prose")
    assert context.count_tokens(text) > 0  # it contributes to the token total


# ---- response budget (2026-07-26 response-presets design) ----

def _user_style(tmp_path, sid, name, body):
    d = tmp_path / "styles"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.md").write_text(f"---\nname: {name}\n---\n\n{body}", encoding="utf-8")


def test_budget_section_renders_with_resolved_numbers(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    text = context.build_messages(cid, sid)[0]["content"]
    assert "# Response budget" in text
    assert "550 words" in text                         # standard fallback
    assert "at most 5 blocks" in text


def test_budget_follows_the_scene_override(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.set_response(cid, sid, {"response_preset": "terse"})
    text = context.build_messages(cid, sid)[0]["content"]
    assert "150 words" in text
    assert "do not return to a character you have already written" in text


def test_repeats_allowed_wording(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.set_response(cid, sid, {"response_preset": "cinematic"})
    text = context.build_messages(cid, sid)[0]["content"]
    assert "No character takes more than 2 blocks." in text


def test_legacy_style_id_still_resolves_identically(monkeypatch, tmp_path):
    """Migration is a no-op: a store with only the legacy style_id keys must
    resolve the same style it does today, now through the new cascade."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    _user_style(tmp_path, "gothic-horror", "Gothic Horror", "Atmosphere first.")
    campaigns.set_campaign_style(cid, "gothic-horror")
    text = context.build_messages(cid, sid)[0]["content"]
    assert "Atmosphere first." in text


def test_stale_scene_style_falls_back_to_campaign(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    _user_style(tmp_path, "gothic-horror", "Gothic Horror", "Atmosphere first.")
    campaigns.set_campaign_style(cid, "gothic-horror")
    scenes.set_style(cid, sid, "deleted-style")
    text = context.build_messages(cid, sid)[0]["content"]
    assert "Atmosphere first." in text


def test_budget_section_appears_in_the_token_breakdown(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    labels = [s["label"] for s in context.context_sections(cid, sid)]
    assert "Response budget" in labels


def _bloat(cid, sid, turns, words):
    for _ in range(turns):
        scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "w " * words}])


def test_no_corrective_on_a_fresh_scene(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    messages = context.build_messages(cid, sid)
    assert not any("run long" in m["content"] for m in messages)


def test_no_corrective_while_replies_are_compliant(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.set_response(cid, sid, {"response_preset": "cinematic"})   # 900-word budget
    _bloat(cid, sid, turns=3, words=200)
    messages = context.build_messages(cid, sid)
    assert not any("run long" in m["content"] for m in messages)


def test_corrective_lands_in_the_last_message(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.set_response(cid, sid, {"response_preset": "terse"})       # 150-word budget
    _bloat(cid, sid, turns=3, words=600)
    messages = context.build_messages(cid, sid)
    last = messages[-1]
    assert last["role"] == "system"
    assert "run long" in last["content"]
    assert "Cut hard" in last["content"]
    assert "150 words total" in last["content"]


def test_trim_tier_wording(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.set_response(cid, sid, {"response_preset": "terse"})       # 150 -> trim band 188..262
    _bloat(cid, sid, turns=3, words=220)
    text = context.build_messages(cid, sid)[-1]["content"]
    assert "Trim toward the budget" in text
    assert "Cut hard" not in text


def test_structural_lines_appear_only_when_violated(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.set_response(cid, sid, {"response_preset": "terse"})       # speakers 2, repeats 1
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "Short."},
                                   {"speaker": "Mara", "content": "Again."}])
    text = context.build_messages(cid, sid)[-1]["content"]
    assert "give each character at most 1" in text
    assert "speaking characters" not in text     # the speaker cap was NOT broken
    assert "run long" not in text                # nor the word budget


def test_transition_between_replies_does_not_fire_a_false_block_violation(monkeypatch, tmp_path):
    """A budget-compliant reply followed by a scene transition must not
    measure as one over-cap turn."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.set_response(cid, sid, {"response_preset": "brisk"})       # blocks cap 4
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "locations", "Saltmarch Docks", "Wet rope.")
    entities.create_entity(croot, "locations", "The Long Stair", "Down.")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "One."},
                                   {"speaker": None, "content": "Two."},
                                   {"speaker": "Winifred", "content": "Three."},
                                   {"speaker": None, "content": "Four."}])
    scenes.set_location(cid, sid, "saltmarch-docks")
    scenes.set_location(cid, sid, "the-long-stair")
    scenes.append_reply(cid, sid, [{"speaker": "Mara", "content": "Five."}])
    messages = context.build_messages(cid, sid)
    assert not any("keep this one to at most 4" in m["content"] for m in messages)


def test_corrective_rides_alone_when_cards_have_no_post_history(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.set_response(cid, sid, {"response_preset": "terse"})
    _bloat(cid, sid, turns=3, words=600)
    messages = context.build_messages(cid, sid)
    assert messages[-1]["role"] == "system"
    assert "run long" in messages[-1]["content"]


def test_speaker_canonicalization_survives_a_cast_departure(monkeypatch, tmp_path):
    """A character who leaves still has blocks in the 3-turn window. Dropping
    their name from the canonicalization set would split 'Winifred' and
    'Winifred Vance' into two speakers on an ordinary departure — inventing a
    speakers violation and hiding the real blocks_per_speaker one."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    characters.create_character(croot, "Winifred Vance", "default",
                                characters.blank_card("Winifred Vance"))
    ap.appear(cid, sid, "characters", "winifred-vance", "default", "npc")
    scenes.set_response(cid, sid, {"response_preset": "terse"})   # speakers 2, blocks_per_speaker 1
    scenes.append_reply(cid, sid, [{"speaker": "Winifred", "content": "One."},
                                   {"speaker": "Winifred Vance", "content": "Two."}])
    ap.leave(cid, sid, "characters", "winifred-vance")
    text = context.build_messages(cid, sid)[-1]["content"]
    assert "give each character at most 1" in text   # one character, two blocks
    assert "speaking characters" not in text         # NOT two distinct speakers


def test_unrelated_world_character_does_not_poison_canonicalization(monkeypatch, tmp_path):
    """The roster must cover campaign history, not every character the campaign
    can SEE. An unrelated same-prefix world character would make 'Winifred'
    ambiguous and split one on-screen actor into two speakers."""
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    characters.create_character(croot, "Winifred Vance", "default",
                                characters.blank_card("Winifred Vance"))
    # never in this campaign's history — but visible in the world
    characters.create_character(worlds.world_root(wid), "Winifred Vale", "default",
                                characters.blank_card("Winifred Vale"))
    ap.appear(cid, sid, "characters", "winifred-vance", "default", "npc")
    scenes.set_response(cid, sid, {"response_preset": "terse"})   # blocks_per_speaker 1
    scenes.append_reply(cid, sid, [{"speaker": "Winifred", "content": "One."},
                                   {"speaker": "Winifred Vance", "content": "Two."}])
    text = context.build_messages(cid, sid)[-1]["content"]
    assert "give each character at most 1" in text   # still one character
    assert "speaking characters" not in text         # not two
