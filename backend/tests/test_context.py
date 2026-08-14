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



from grimoire.store import appearances as ap  # noqa: E402
from grimoire.store import campaigns, characters, chronicle, entities, plot, pcs, scenes, worlds  # noqa: E402
from grimoire.store.context import cast as context_cast  # noqa: E402


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

    campaigns.set_campaign_response(cid, {"style_id": "noir-detective"})
    text = context.build_messages(cid, sid)[0]["content"]
    assert "Prose style: Noir Detective" in text
    assert "Gothic Horror" not in text

    scenes.set_response(cid, sid, {"style_id": "pulp-adventure"})
    text = context.build_messages(cid, sid)[0]["content"]
    assert "Prose style: Pulp Adventure" in text
    assert "Noir Detective" not in text

    # a stale/unknown scene override falls back to the campaign default
    scenes.set_response(cid, sid, {"style_id": "does-not-exist"})
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


def test_turn_override_beats_the_scene_setting(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.set_response(cid, sid, {"response_preset": "cinematic"})
    text = context.build_messages(cid, sid, turn={"response_preset": "terse"})[0]["content"]
    assert "150 words" in text


def test_turn_override_is_not_persisted(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.set_response(cid, sid, {"response_preset": "cinematic"})
    context.build_messages(cid, sid, turn={"response_preset": "terse"})
    assert scenes.read_scene(cid, sid)["meta"]["response_preset"] == "cinematic"
    assert "900 words" in context.build_messages(cid, sid)[0]["content"]


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
    campaigns.set_campaign_response(cid, {"style_id": "gothic-horror"})
    text = context.build_messages(cid, sid)[0]["content"]
    assert "Atmosphere first." in text


def test_stale_scene_style_falls_back_to_campaign(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    _user_style(tmp_path, "gothic-horror", "Gothic Horror", "Atmosphere first.")
    campaigns.set_campaign_response(cid, {"style_id": "gothic-horror"})
    scenes.set_response(cid, sid, {"style_id": "deleted-style"})
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


def test_drift_roster_is_not_built_for_a_scene_with_nothing_to_measure(monkeypatch, tmp_path):
    """Building the roster opens one card file per campaign actor. A scene with
    no recorded turns is not measured at all, so that work must not happen —
    it is pure I/O on the hot path of every single generation."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "hello")
    calls = []
    real = context_cast._drift_roster

    def counted(*args):
        calls.append(args)
        return real(*args)

    monkeypatch.setattr(context_cast, "_drift_roster", counted)
    context.build_messages(cid, sid)
    assert calls == []
    # once a generation has been recorded there IS something to measure
    scenes.append_reply(cid, sid, [{"speaker": None, "content": "A reply."}])
    context.build_messages(cid, sid)
    assert len(calls) == 1


# ---- POV filtering of NPC suspicions (#116) ---------------------------------
#
# `knows` is what a character holds as fact and the narration may lean on;
# `suspects` is a private, possibly-false belief. Handing every present NPC's
# suspicions about each other to the model on every turn is how a scene gets
# narration that quietly knows what nobody on stage has said.

def _two_npc_scene(monkeypatch, tmp_path, pcless=False):
    """Seraphine and Winifred both on stage, plus one absent character (Mara)
    for suspicions to be about."""
    from grimoire.store import appearances, campaigns, characters, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    ids = {}
    for name in ("Seraphine Vale", "Winifred", "Mara"):
        ids[name] = characters.create_character(croot, name, "main",
                                                characters.blank_card(name))[0]
    sid = scenes.create_scene(cid, "Now", pcless=pcless)
    appearances.appear(cid, sid, "characters", ids["Seraphine Vale"], "main", "npc")
    appearances.appear(cid, sid, "characters", ids["Winifred"], "main", "npc")
    return cid, sid, croot, ids


def _state_section(cid, sid):
    return {s["label"]: s["text"]
            for s in context.context_sections(cid, sid)}.get("Character state", "")


def test_suspicion_about_a_present_actor_is_withheld(monkeypatch, tmp_path):
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "The ledger is real.",
        "Winifred is lying about the crates.\n\nMara sold the manifest."))
    section = _state_section(cid, sid)
    assert "Mara sold the manifest." in section       # a separate paragraph: kept
    assert "Winifred is lying" not in section         # about someone in the room: withheld
    assert "Knows: The ledger is real." in section    # the fact tier is never filtered
    assert "Seraphine Vale: Wary." in section


def test_a_first_name_reference_counts_as_naming_a_present_actor(monkeypatch, tmp_path):
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Winifred"], playstate.compose_body(
        "Impatient.", "", "Seraphine is stalling."))   # card name is "Seraphine Vale"
    assert "Seraphine is stalling" not in _state_section(cid, sid)


def test_a_suspicion_about_oneself_is_kept(monkeypatch, tmp_path):
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Winifred"], playstate.compose_body(
        "", "", "Winifred thinks she is being followed."))
    # a character's own interiority is not a leak about somebody else
    assert "Winifred thinks she is being followed." in _state_section(cid, sid)


def test_a_suspicion_about_a_present_player_is_withheld(monkeypatch, tmp_path):
    from grimoire.store import appearances, campaigns, characters, pcs, playstate, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    ch = characters.create_character(croot, "Seraphine", "main",
                                     characters.blank_card("Seraphine"))[0]
    pid, _ = pcs.create_pc(croot, "Winifred", [], persona=pcs.blank_persona("Winifred"))
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    appearances.appear(cid, sid, "pcs", pid, "default", "player")
    playstate.write_state(croot, ch, playstate.compose_body(
        "Wary.", "", "Winifred is working for the Guild."))
    assert "working for the Guild" not in _state_section(cid, sid)


def test_a_suspicion_naming_the_player_through_the_macro_is_withheld(monkeypatch, tmp_path):
    """The one reference an alias sweep structurally cannot see. At storage time
    the entry holds `{{user}}`, which matches no name; `_system_text` then
    expands it to the present player's, and the private suspicion arrives at the
    model reading like any other. The filter runs first, so the filter has to
    know the macro."""
    from grimoire.store import (appearances, campaigns, characters, context, pcs,
                                playstate, scenes, worlds)
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    ch = characters.create_character(croot, "Seraphine", "main",
                                     characters.blank_card("Seraphine"))[0]
    pid, _ = pcs.create_pc(croot, "Winifred", [], persona=pcs.blank_persona("Winifred"))
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    appearances.appear(cid, sid, "pcs", pid, "default", "player")
    playstate.write_state(croot, ch, playstate.compose_body(
        "Wary.", "", "{{user}} is hiding the ledger.\n\nThe Guild sold the manifest."))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section
    assert "{{user}}" not in section                       # not merely unexpanded
    assert "The Guild sold the manifest." in section       # the absent world still shows
    # And nothing downstream re-introduces it once the macro resolves.
    assert "hiding the ledger" not in context.expand_macros(
        section, context.scene_substitutions(cid, sid), cid, sid)


def test_an_npc_whose_only_state_is_a_withheld_suspicion_drops_out(monkeypatch, tmp_path):
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "", "", "Winifred is lying."))
    # nothing left to say about her: no name with a dangling colon, no empty entry
    assert "Seraphine Vale" not in _state_section(cid, sid)


def test_a_pcless_scene_gets_full_disclosure(monkeypatch, tmp_path):
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path, pcless=True)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "", "Winifred is lying about the crates."))
    # a director turn has no player whose knowledge to respect, and moving NPCs
    # by what they privately believe is the whole point of one
    assert "Winifred is lying about the crates." in _state_section(cid, sid)


def test_the_absorb_snapshot_is_not_filtered(monkeypatch, tmp_path):
    """Absorb rewrites stored state from the snapshot. Filtering it there would
    make the model rewrite `suspects` from a version with lines missing and
    silently erase them on save."""
    from grimoire.store import absorb, playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "", "Winifred is lying about the crates."))
    snap = " ".join(absorb.state_snapshot(cid, sid).values())
    assert "Winifred is lying about the crates." in snap


def test_the_filter_uses_the_npc_s_own_name_not_the_card_s(monkeypatch, tmp_path):
    """The block is labelled from the character's meta name; the cast list the
    prompt's other sections use carries the locked CARD's name. A version rename
    can make the two disagree, and if the filter read the card's, the NPC's own
    interiority would be withheld as if it were about somebody else."""
    from grimoire.store import appearances, campaigns, characters, playstate, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    ch = characters.create_character(croot, "Seraphine", "main",
                                     characters.blank_card("Seraphine"))[0]
    card = characters.read_card(croot, ch, "main")
    card["data"]["name"] = "The Woman on the Pier"      # the card drifts from the meta name
    characters.update_version(croot, ch, "main", card)
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    playstate.write_state(croot, ch, playstate.compose_body(
        "", "", "Seraphine thinks she is being followed."))
    assert "Seraphine thinks she is being followed." in _state_section(cid, sid)


def test_a_multiline_suspicion_is_withheld_whole(monkeypatch, tmp_path):
    """The unit is the ENTRY, not the physical line. Dropping only the line that
    names the present actor publishes the half carrying the actual secret — the
    filter defeated by a line break."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        "Winifred is lying about the crates.\n"
        "She plans to steal them at midnight.\n"
        "\n"
        "Mara sold the manifest."))
    section = _state_section(cid, sid)
    assert "Winifred is lying" not in section
    assert "plans to steal them at midnight" not in section   # the continuation goes too
    assert "Mara sold the manifest." in section               # a new entry survives


def test_an_indented_or_bulleted_continuation_follows_its_entry(monkeypatch, tmp_path):
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        "- Winifred is lying about the crates.\n"
        "  The tally does not match the manifest.\n"
        "- Mara sold the manifest."))
    section = _state_section(cid, sid)
    assert "Winifred is lying" not in section
    assert "tally does not match" not in section   # indented continuation
    assert "Mara sold the manifest." in section    # the next bullet is its own entry


def test_a_new_entry_after_a_dropped_one_is_not_swept_up(monkeypatch, tmp_path):
    """`dropping` has to reset on the next entry, or one named suspicion silently
    becomes all-or-nothing for everything below it."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "", "", "Winifred is lying.\n\nThe Guild is watching the pier.\n\n"
                "A courier comes Thursday.")
    )
    section = _state_section(cid, sid)
    assert "Winifred is lying" not in section
    assert "The Guild is watching the pier." in section
    assert "A courier comes Thursday." in section


def test_an_entry_naming_a_present_actor_only_later_is_withheld_whole(monkeypatch, tmp_path):
    """A streaming filter can drop a line but cannot take back one it already
    kept, so an entry that names the actor in its SECOND line published its
    first. Entries are judged whole."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        "The theft was staged.\n"
        "She thinks Winifred planted the evidence.\n"
        "\n"
        "Mara sold the manifest."))
    section = _state_section(cid, sid)
    assert "The theft was staged." not in section     # the setup goes with the attribution
    assert "planted the evidence" not in section
    assert "Mara sold the manifest." in section       # an unrelated entry survives


def test_a_blank_line_does_not_merge_two_entries(monkeypatch, tmp_path):
    """Paragraph spacing separates suspicions; a blank line must not let a named
    entry swallow the paragraph after it."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "", "", "Winifred is lying.\n\nThe Guild is watching the pier."))
    section = _state_section(cid, sid)
    assert "Winifred is lying" not in section
    assert "The Guild is watching the pier." in section


def test_a_suspicion_naming_the_card_name_is_withheld(monkeypatch, tmp_path):
    """The block is labelled with the meta name, but the card's `data.name` is
    what the description section, the transcript and the cast UI show — so it is
    what another NPC's stored suspicion is likely to call them. Matching only
    the meta name left that hole open."""
    from grimoire.store import appearances, campaigns, characters, playstate, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    sera = characters.create_character(croot, "Seraphine", "main",
                                       characters.blank_card("Seraphine"))[0]
    card = characters.read_card(croot, sera, "main")
    card["data"]["name"] = "The Woman on the Pier"     # the card drifts from the meta name
    characters.update_version(croot, sera, "main", card)
    win = characters.create_character(croot, "Winifred", "main",
                                      characters.blank_card("Winifred"))[0]
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", sera, "main", "npc")
    appearances.appear(cid, sid, "characters", win, "main", "npc")
    playstate.write_state(croot, win, playstate.compose_body(
        "Impatient.", "", "The Woman on the Pier is hiding the ledger."))
    assert "hiding the ledger" not in _state_section(cid, sid)


def test_a_suspicion_naming_a_present_player_s_persona_is_withheld(monkeypatch, tmp_path):
    """Players reach the filter through their persona name; that alias has to be
    matched too."""
    from grimoire.store import appearances, campaigns, characters, pcs, playstate, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    ch = characters.create_character(croot, "Seraphine", "main",
                                     characters.blank_card("Seraphine"))[0]
    pid, _ = pcs.create_pc(croot, "Winifred", [], persona=pcs.blank_persona("Winifred"))
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    appearances.appear(cid, sid, "pcs", pid, "default", "player")
    playstate.write_state(croot, ch, playstate.compose_body(
        "Wary.", "", "Winifred is working for the Guild."))
    assert "working for the Guild" not in _state_section(cid, sid)


def test_a_capitalized_continuation_stays_with_its_entry(monkeypatch, tmp_path):
    """A paragraph is one entry. A whitelist of "words that continue a sentence"
    leaked three times; "At" is not a pronoun, so the continuation read as a
    fresh statement and survived while the line naming her was dropped."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "", "Winifred is lying.\nAt midnight, she plans to steal the crates."))
    section = _state_section(cid, sid)
    assert "Winifred is lying" not in section
    assert "steal the crates" not in section


def test_a_bullet_keeps_finer_granularity_than_the_paragraph(monkeypatch, tmp_path):
    """The cost of paragraph grouping is granularity; a bullet buys it back."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "", "- Winifred is lying.\n- Mara sold the manifest."))
    section = _state_section(cid, sid)
    assert "Winifred is lying" not in section
    assert "Mara sold the manifest." in section


def test_an_epithet_card_name_does_not_match_every_line(monkeypatch, tmp_path):
    """`The Woman on the Pier` once contributed the alias "The", so every
    suspicion containing the word "the" was read as naming her and withheld.
    Hiding almost all of an NPC's state is worse than the leak it was avoiding."""
    from grimoire.store import appearances, campaigns, characters, playstate, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    sera = characters.create_character(croot, "Seraphine", "main",
                                       characters.blank_card("Seraphine"))[0]
    card = characters.read_card(croot, sera, "main")
    card["data"]["name"] = "The Woman on the Pier"
    characters.update_version(croot, sera, "main", card)
    win = characters.create_character(croot, "Winifred", "main",
                                      characters.blank_card("Winifred"))[0]
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", sera, "main", "npc")
    appearances.appear(cid, sid, "characters", win, "main", "npc")
    playstate.write_state(croot, win, playstate.compose_body(
        "Impatient.", "", "Mara keeps the tally in the back room."))
    # unrelated, and contains "the" three times
    assert "Mara keeps the tally in the back room." in _state_section(cid, sid)


def test_a_one_word_name_that_is_an_ordinary_word_does_not_match_prose(monkeypatch, tmp_path):
    """`Will`, `May`, `Hope`, `Grace` — plenty of names are also ordinary words,
    and a case-insensitive whole-word match reads "Mara will steal the crates"
    as naming Will. With that actor on stage most of every other NPC's state
    disappears: the `The`-matches-everything bug again, reached without an
    epithet. Case is the signal already in the text, so no list of ambiguous
    names has to be right."""
    from grimoire.store import appearances, campaigns, characters, playstate, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    will = characters.create_character(croot, "Will", "main",
                                       characters.blank_card("Will"))[0]
    win = characters.create_character(croot, "Winifred", "main",
                                      characters.blank_card("Winifred"))[0]
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", will, "main", "npc")
    appearances.appear(cid, sid, "characters", win, "main", "npc")
    playstate.write_state(croot, win, playstate.compose_body(
        "Impatient.", "", "Mara will steal the crates.\n\nWill is lying about the tally."))
    section = _state_section(cid, sid)
    assert "Mara will steal the crates." in section     # the modal verb is not a name
    assert "Will is lying" not in section               # the capitalized one is


def test_a_name_in_a_script_without_word_separators_is_matched(monkeypatch, tmp_path):
    """`\\b` sits between a word character and a non-word one, and in a script
    written without spaces both neighbours are word characters — so the boundary
    never matched and the filter did nothing whatsoever for a campaign not
    written in a spaced script. Such a form is matched as a plain substring."""
    from grimoire.store import appearances, campaigns, characters, playstate, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    ming = characters.create_character(croot, "Ming", "main",
                                       characters.blank_card("Ming"))[0]
    card = characters.read_card(croot, ming, "main")
    card["data"]["name"] = "李明"
    characters.update_version(croot, ming, "main", card)
    win = characters.create_character(croot, "Winifred", "main",
                                      characters.blank_card("Winifred"))[0]
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", ming, "main", "npc")
    appearances.appear(cid, sid, "characters", win, "main", "npc")
    playstate.write_state(croot, win, playstate.compose_body(
        "Impatient.", "", "李明藏着账本。\n\nThe Guild sold the manifest."))
    section = _state_section(cid, sid)
    assert "李明藏着账本" not in section
    assert "The Guild sold the manifest." in section


def test_two_actors_sharing_a_given_name_keep_their_own_interiority(monkeypatch, tmp_path):
    """The self-suspicion exception is about the OWNER, and the collision is
    between the derived aliases: `Mara Chen` and `Mara Vance` both shorten to
    `Mara`, so subtracting stored names alone left it in `others` and the
    owner's own line was withheld as if it named the other actor."""
    from grimoire.store import appearances, campaigns, characters, playstate, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    chen = characters.create_character(croot, "Mara Chen", "main",
                                       characters.blank_card("Mara Chen"))[0]
    vance = characters.create_character(croot, "Mara Vance", "main",
                                        characters.blank_card("Mara Vance"))[0]
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", chen, "main", "npc")
    appearances.appear(cid, sid, "characters", vance, "main", "npc")
    playstate.write_state(croot, chen, playstate.compose_body(
        "Uneasy.", "",
        "Mara Chen fears she made a mistake.\n\nMara Vance is lying about the tally."))
    section = _state_section(cid, sid)
    assert "fears she made a mistake" in section     # her own interiority
    assert "Mara Vance is lying" not in section      # the other actor, in full


def test_a_shared_alias_collides_across_capitalization(monkeypatch, tmp_path):
    """The subtraction has to make the same comparison `_mentions` does. A
    one-word form matches any spelling that is not all lower case, so `MARA` and
    `Mara` are one form to the matcher — and subtracting exact strings left the
    other actor's `Mara` in `others`, withholding the owner's own line for
    exactly the pair the exception exists for."""
    from grimoire.store import appearances, campaigns, characters, playstate, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    chen = characters.create_character(croot, "MARA CHEN", "main",
                                       characters.blank_card("MARA CHEN"))[0]
    vance = characters.create_character(croot, "Mara Vance", "main",
                                        characters.blank_card("Mara Vance"))[0]
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", chen, "main", "npc")
    appearances.appear(cid, sid, "characters", vance, "main", "npc")
    playstate.write_state(croot, chen, playstate.compose_body(
        "Uneasy.", "",
        "Mara made a mistake.\n\nMara Vance is lying about the tally."))
    section = _state_section(cid, sid)
    assert "made a mistake" in section               # her own interiority
    assert "Mara Vance is lying" not in section      # the other actor, in full


def test_a_suspicion_naming_an_honorific_card_by_given_name_is_withheld(monkeypatch, tmp_path):
    """`Dr Mara Vance` used to yield no short alias at all, because the head was
    an honorific — so the card was matched only in full and prose naming her the
    way prose actually does reached the player-facing prompt."""
    from grimoire.store import appearances, campaigns, characters, playstate, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    mara = characters.create_character(croot, "Mara", "main",
                                       characters.blank_card("Mara"))[0]
    card = characters.read_card(croot, mara, "main")
    card["data"]["name"] = "Dr Mara Vance"
    characters.update_version(croot, mara, "main", card)
    win = characters.create_character(croot, "Winifred", "main",
                                      characters.blank_card("Winifred"))[0]
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", mara, "main", "npc")
    appearances.appear(cid, sid, "characters", win, "main", "npc")
    playstate.write_state(croot, win, playstate.compose_body(
        "Impatient.", "", "Mara is hiding the ledger.\n\nThe Guild sold the manifest."))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section
    assert "The Guild sold the manifest." in section   # the absent world still shows


def test_the_short_alias_is_only_derived_from_a_personal_name():
    from grimoire.store.context import world_state
    assert world_state._short_alias("Winifred Vance") == "Winifred"
    assert world_state._short_alias("Seraphine Vale") == "Seraphine"
    assert world_state._short_alias("Mara") == ""                    # nothing to shorten
    # An ARTICLE ends the search: what follows one is a common noun, and
    # deriving `Woman` here is the over-match that once emptied the block.
    assert world_state._short_alias("The Woman on the Pier") == ""
    assert world_state._short_alias("The Woman") == ""
    # An HONORIFIC is stepped over: what follows a title is a name. These three
    # used to return "" — the honorific was treated as a dead end, so a card
    # named `Dr Mara Vance` was matched only in full and "Mara is hiding the
    # ledger" reached the prompt.
    assert world_state._short_alias("Dr Mara Vance") == "Mara"
    assert world_state._short_alias("Lady Winifred") == "Winifred"
    assert world_state._short_alias("Dr Vance") == "Vance"
    # ...punctuated the conventional way too. Without stripping the period,
    # `Dr.` matched neither set and came back as the alias itself: it missed the
    # name it precedes and matched every line abbreviating a doctor.
    assert world_state._short_alias("Dr. Mara Vance") == "Mara"
    assert world_state._short_alias("St. Peter Vale") == "Peter"
    # Two characters is a name; one is an initial, and `J` would match every
    # capital J standing alone. Three used to be the floor as a proxy for "not a
    # short ordinary word" — a job `_mentions` now does with case.
    assert world_state._short_alias("Jo Li") == "Jo"
    assert world_state._short_alias("Dr. Li Chen") == "Li"
    assert world_state._short_alias("J Smith") == ""


def test_a_four_token_personal_name_still_yields_its_given_name():
    """The token cap was a proxy that failed on the thing it was meant to allow.
    A given name, a middle name and two surnames is an ordinary personal name,
    and capping at three meant the card matched only in full while prose said
    "Winifred is hiding the ledger". What rejects an epithet is the ARTICLE."""
    from grimoire.store.context import world_state
    assert world_state._short_alias("Winifred Mara Saltmarch Vale") == "Winifred"
    assert world_state._short_alias("Dr. Winifred Mara Saltmarch Vale") == "Winifred"
    # ...and an epithet is still rejected at any length. Lower-cased, by the
    # capitalization rule; title-cased, by the article wherever it sits.
    assert world_state._short_alias("The Woman on the Pier") == ""
    assert world_state._short_alias("Woman Of The Pier") == ""
    assert world_state._short_alias("Keeper Of The Flame") == ""
    assert world_state._short_alias("Dr.") == ""            # a bare honorific names nobody


def test_a_suspicion_naming_the_given_name_of_a_long_named_actor_is_withheld(
        monkeypatch, tmp_path):
    """End to end: the alias has to reach the filter, not merely exist."""
    from grimoire.store import appearances, campaigns, characters, playstate, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    long_name = "Winifred Mara Saltmarch Vale"
    watcher = characters.create_character(croot, "Seraphine", "main",
                                          characters.blank_card("Seraphine"))[0]
    named = characters.create_character(croot, long_name, "main",
                                        characters.blank_card(long_name))[0]
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", watcher, "main", "npc")
    appearances.appear(cid, sid, "characters", named, "main", "npc")
    playstate.write_state(croot, watcher, playstate.compose_body(
        "Wary.", "", "Winifred is hiding the ledger.\n\nThe Guild watches the pier."))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section
    assert "The Guild watches the pier." in section


def test_a_bullet_under_a_named_heading_goes_with_the_heading(monkeypatch, tmp_path):
    """The natural way to write these is a subject line and bullets under it.
    Each bullet is its own entry and names nobody, so on its own verdict it
    survived — publishing the private half of a suspicion whose subject was
    withheld one line above. Same leak `_entries` exists to close, arriving
    through the list marker instead of the line break."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        "Winifred:\n"
        "- is hiding the ledger\n"
        "- plans to sell it at the pier\n"
        "\n"
        "Mara sold the manifest."))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section
    assert "plans to sell it" not in section          # every bullet under the heading
    assert "Mara sold the manifest." in section       # the block after it is untouched


def test_a_heading_that_names_nobody_does_not_cost_its_other_bullets(monkeypatch, tmp_path):
    """Governed, not merged. Merging the block into one entry would bind an
    innocuous `Suspicions:` to every bullet under it, so one named suspicion
    would cost the whole list — and an unnamed heading is exactly where entry
    granularity is worth keeping."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        "Suspicions:\n"
        "- Winifred is lying about the crates\n"
        "- Mara sold the manifest"))
    section = _state_section(cid, sid)
    assert "Winifred is lying" not in section
    assert "Suspicions:" in section
    assert "Mara sold the manifest" in section


def test_a_nested_bullet_goes_with_the_bullet_that_heads_it(monkeypatch, tmp_path):
    """The chain is walked, not just one level: a sub-bullet under a named
    bullet is as private as the bullet above it."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        "Suspicions:\n"
        "- Winifred:\n"
        "  - keeps the ledger in the boathouse\n"
        "- Mara sold the manifest"))
    section = _state_section(cid, sid)
    assert "boathouse" not in section
    assert "Mara sold the manifest" in section


def test_a_lowercase_particle_does_not_disqualify_a_personal_name():
    """Nobiliary and patronymic particles are lower-case by convention INSIDE a
    name. Requiring every token to be capitalized rejected those names whole, so
    the card matched only in full while prose said the given name."""
    from grimoire.store.context import world_state
    assert world_state._short_alias("Winifred van Saltmarch") == "Winifred"
    assert world_state._short_alias("Mara de la Vance") == "Mara"
    assert world_state._short_alias("Dr. Seraphine von Realm") == "Seraphine"
    # The head is still required to be capitalized, so a particle can never
    # become the alias itself...
    assert world_state._short_alias("van Saltmarch") == ""
    # ...and the article rule is unchanged: `de la` passes, `the` does not.
    assert world_state._short_alias("Woman of the Pier") == ""
    assert world_state._short_alias("The Woman on the Pier") == ""


def test_a_suspicion_naming_the_given_name_of_a_particled_actor_is_withheld(
        monkeypatch, tmp_path):
    from grimoire.store import appearances, campaigns, characters, playstate, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    particled = "Winifred van Saltmarch"
    watcher = characters.create_character(croot, "Seraphine", "main",
                                          characters.blank_card("Seraphine"))[0]
    named = characters.create_character(croot, particled, "main",
                                        characters.blank_card(particled))[0]
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", watcher, "main", "npc")
    appearances.appear(cid, sid, "characters", named, "main", "npc")
    playstate.write_state(croot, watcher, playstate.compose_body(
        "Wary.", "", "Winifred is hiding the ledger.\n\nThe Guild watches the pier."))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section
    assert "The Guild watches the pier." in section


def test_an_emphasized_heading_still_governs_its_bullets(monkeypatch, tmp_path):
    """These blocks are authored prose, and `**Winifred:**` is how a subject line
    is conventionally written. A bare `endswith(":")` saw the closing `**`,
    called it an ordinary line, and left the bullets ungoverned — so the subject
    was withheld and the detail under it published."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        "**Winifred:**\n"
        "- is hiding the ledger\n"
        "\n"
        "_Mara:_\n"
        "- sold the manifest"))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section
    assert "sold the manifest" in section      # an absent character's block is untouched


def test_returning_to_the_outer_level_keeps_the_outer_heading(monkeypatch, tmp_path):
    """A list can descend into a sub-heading and come back out. Tracking one
    innermost heading, the way back out reset to "governed by nobody" instead of
    to the heading it never left, so the last bullet — a suspicion about a
    present actor, written without her name because the heading carried it —
    came back ungoverned and reached the prompt."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        "Winifred:\n"
        "- Plans:\n"
        "  - steal the ledger\n"
        "- knows the truth about the pier\n"
        "\n"
        "Mara sold the manifest."))
    section = _state_section(cid, sid)
    assert "steal the ledger" not in section          # the nested bullet
    assert "knows the truth" not in section           # and the one that came back out
    assert "Mara sold the manifest." in section       # the block after it is untouched


def test_a_sibling_sub_list_does_not_inherit_the_previous_sub_heading(monkeypatch, tmp_path):
    """The pop is by indentation, so a sub-heading governs its own children and
    stops there — an unnamed heading's bullets are not withheld because the
    sub-heading beside it named someone."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        "Suspicions:\n"
        "- Winifred:\n"
        "  - keeps the ledger in the boathouse\n"
        "- The Guild:\n"
        "  - is watching the pier"))
    section = _state_section(cid, sid)
    assert "boathouse" not in section
    assert "is watching the pier" in section


def test_the_surname_is_a_form_too():
    """Prose refers to a character by surname as readily as by given name, and a
    form set holding only the full name and the given name matched neither."""
    from grimoire.store.context import world_state
    assert world_state._surname_alias("Dr. Mara Vance") == "Vance"
    assert world_state._surname_alias("Winifred Mara Saltmarch Vale") == "Vale"
    assert world_state._surname_alias("Winifred van Saltmarch") == "Saltmarch"
    assert world_state._surname_alias("Mara") == ""              # nothing follows it
    assert world_state._surname_alias("Dr Vance") == ""          # already the given form
    assert world_state._surname_alias("The Woman on the Pier") == ""   # not a name at all
    assert world_state._forms({"Dr. Mara Vance"}) == {"Dr. Mara Vance", "Mara", "Vance"}


def test_a_suspicion_naming_only_the_surname_is_withheld(monkeypatch, tmp_path):
    from grimoire.store import appearances, campaigns, characters, playstate, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    full = "Dr. Mara Vance"
    watcher = characters.create_character(croot, "Seraphine", "main",
                                          characters.blank_card("Seraphine"))[0]
    named = characters.create_character(croot, full, "main", characters.blank_card(full))[0]
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", watcher, "main", "npc")
    appearances.appear(cid, sid, "characters", named, "main", "npc")
    playstate.write_state(croot, watcher, playstate.compose_body(
        "Wary.", "", "Vance is hiding the ledger.\n\nThe Guild watches the pier."))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section
    assert "The Guild watches the pier." in section


def test_a_punctuated_initial_is_still_not_an_alias():
    """`J.` is two characters and one letter. Measuring the RAW token let the
    conventional `J. Smith` through as the alias `J.` — the initial guard,
    defeated by the punctuation that marks it as an initial. Every capitalized
    `J.` in another NPC's suspicion then withheld the whole entry."""
    from grimoire.store.context import world_state
    assert world_state._short_alias("J. Smith") == ""
    assert world_state._short_alias("Dr. J. Smith") == ""
    assert world_state._short_alias("J Smith") == ""
    assert world_state._surname_alias("J. Smith") == "Smith"   # the handle it does have


def test_a_markdown_heading_governs_the_bullets_under_it(monkeypatch, tmp_path):
    """`## Winifred` carries no colon, and — unlike a colon heading — it is
    conventionally separated from its own list by a blank line, so it has to
    survive the blank that ends an ordinary paragraph."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        "## Winifred\n"
        "\n"
        "- is hiding the ledger\n"
        "\n"
        "## The Guild\n"
        "\n"
        "- is watching the pier"))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section
    assert "is watching the pier" in section     # the next section ends the previous one


def test_an_indented_plain_heading_is_popped_on_the_way_out(monkeypatch, tmp_path):
    """The mirror of the nesting leak, and it over-hides rather than leaks: an
    indented `Nested:` stayed open after the list returned to the outer level,
    so a bullet that belongs to the outer heading was withheld because the
    nested one named someone."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        "Notes:\n"
        "- the tide turns at dusk\n"
        "  Winifred:\n"
        "  - keeps the ledger in the boathouse\n"
        "- the Guild meets on Thursdays"))
    section = _state_section(cid, sid)
    assert "boathouse" not in section                    # still withheld
    assert "the Guild meets on Thursdays" in section     # but the way back out is not


def test_a_setext_heading_governs_the_bullets_under_it(monkeypatch, tmp_path):
    """The other standard markdown heading: the title is an ordinary line and the
    line BELOW it makes it a heading, so it is the one form not recognizable from
    its own line. The title was withheld and the bullets under it were not."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        "Winifred\n"
        "--------\n"
        "- is hiding the ledger\n"
        "\n"
        "The Guild\n"
        "=========\n"
        "- watches the pier"))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section
    assert "watches the pier" in section          # the next heading ends the first


def test_a_horizontal_rule_is_not_read_as_a_heading(monkeypatch, tmp_path):
    """`---` after a blank line underlines nothing. Treating the blank as a
    heading would govern the whole list below it by an entry that names nobody —
    harmless here, but it would make the rule look like it did something."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        "Winifred is lying.\n"
        "\n"
        "---\n"
        "- Mara sold the manifest"))
    section = _state_section(cid, sid)
    assert "Winifred is lying" not in section
    assert "Mara sold the manifest" in section


def test_a_nested_bullet_goes_with_its_parent_without_a_colon(monkeypatch, tmp_path):
    """A nested list is written `- Winifred` / `  - is hiding the ledger` as
    readily as with a colon. Requiring one left the subjectless child ungoverned
    — a bullet governs what is indented under it because that is what indenting
    under it means."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        "- Winifred\n"
        "  - is hiding the ledger\n"
        "  - plans to sell it\n"
        "- Mara\n"
        "  - sold the manifest"))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section
    assert "plans to sell it" not in section
    assert "sold the manifest" in section       # a sibling bullet governs nothing


def test_a_malformed_card_name_costs_only_its_own_actor(monkeypatch, tmp_path):
    """A card is hand-editable and importable, so `data.name` can arrive as a
    list — and adding that to a set raises TypeError, which escaped into
    `_character_states`' outer catch and emptied the state block for EVERY actor
    in the scene. The failure policy here is per actor."""
    from grimoire.store import characters, playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    card = characters.read_card(croot, ids["Winifred"], "main")
    card["data"]["name"] = ["Winifred", "The Harbourmaster's Daughter"]   # not a string
    characters.update_version(croot, ids["Winifred"], "main", card)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary and precise.", "The ledger is real.", "Mara sold the manifest."))

    section = _state_section(cid, sid)
    assert "Wary and precise." in section          # the other actor survives intact
    assert "The ledger is real." in section
    assert "Mara sold the manifest." in section


def test_an_uncased_script_still_yields_its_name_parts():
    """`isupper()` is False for every token of an Arabic or Hebrew name, so
    requiring upper case rejected the whole name and derived no alias at all —
    the `\\b` failure again: not a missed edge, an entire class of campaign for
    which the filter did nothing. What marks an epithet is an explicitly LOWER
    case token, and an uncased one is neither."""
    from grimoire.store.context import world_state
    assert world_state._short_alias("ليلى حسن") == "ليلى"
    assert world_state._surname_alias("ליאורה כהן") == "כהן"
    # A single CJK character stays out, and does not need to be in: the
    # one-character floor is the initial guard, and an unspaced `李明` is one
    # token that `_mentions` already matches in full.
    assert world_state._short_alias("李 明") == ""
    # The cased rules are unchanged, in both directions.
    assert world_state._short_alias("Winifred Vance") == "Winifred"
    assert world_state._short_alias("The Woman on the Pier") == ""
    assert world_state._short_alias("Woman of the Pier") == ""


def test_an_indented_continuation_stays_under_its_bullet(monkeypatch, tmp_path):
    """A list item continues across a blank line when the paragraph below is
    indented inside it — ordinary markdown. Popping the governor at the blank
    made that paragraph independent: the named bullet withheld, and the
    continuation, which has no subject of its own, published."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        "- Winifred is lying.\n"
        "\n"
        "  She hid the ledger at the pier.\n"
        "\n"
        "Mara sold the manifest."))
    section = _state_section(cid, sid)
    assert "Winifred is lying" not in section
    assert "hid the ledger" not in section          # the continuation goes with it
    assert "Mara sold the manifest." in section     # an UNindented paragraph does not


def test_a_name_suffix_is_stepped_over_when_choosing_the_surname():
    """Taking the last token blindly made `Jr` the alias and left `Vance` — the
    name prose actually uses — matching nothing. The family name is the last
    token that IS one."""
    from grimoire.store.context import world_state
    assert world_state._surname_alias("Mara Vance Jr.") == "Vance"
    assert world_state._surname_alias("Dr. Mara Vance III") == "Vance"
    assert world_state._forms({"Mara Vance Jr."}) == {"Mara Vance Jr.", "Mara", "Vance"}
    # A name that is nothing but suffixes after its head yields none, the same
    # as one that ends in a particle.
    assert world_state._surname_alias("Mara Jr.") == ""
    assert world_state._surname_alias("Mara de") == ""


def test_an_all_upper_case_mention_is_recognized(monkeypatch, tmp_path):
    """Pinning a one-word form to the stored spelling missed `WINIFRED is hiding
    the ledger` — a shape headings and imported prose produce, and one where the
    writer is plainly naming her. The guard the ordinary-word rule needs is
    `will` from `Will`; upper case is on the far side of it."""
    from grimoire.store import playstate
    from grimoire.store.context import world_state
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        "WINIFRED is hiding the ledger.\n\nMara sold the manifest."))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section
    assert "Mara sold the manifest." in section   # an absent character is untouched
    # The lower-case guard is intact in both directions, and an uncased form —
    # which `islower()` also answers False for — is unaffected.
    assert world_state._mentions("Mara will steal the crates.", "Will") is False
    assert world_state._mentions("WILL HAS THE LEDGER", "Will") is True
    assert world_state._mentions("李明藏着账本", "李明") is True


def test_a_given_name_behind_an_unlisted_title_is_a_form(monkeypatch, tmp_path):
    """`_HONORIFIC` cannot enumerate every title a person carries, and reading
    the head as the given name made `Professor` the alias of `Professor Mara
    Vance` while `Mara` — the name prose uses — matched nothing. The token after
    the head is a name either way, so it is taken without deciding which."""
    from grimoire.store import appearances, campaigns, characters, playstate, scenes, worlds
    from grimoire.store.context import world_state
    assert world_state._forms({"Professor Mara Vance"}) == {
        "Professor Mara Vance", "Professor", "Mara", "Vance"}
    # A two-token name has nothing between its ends, and a name whose second
    # token is only a particle yields no form of its own.
    assert world_state._interior_aliases("Mara Vance") == set()
    assert world_state._interior_aliases("Mara de Vance") == set()
    assert world_state._interior_aliases("The Woman on the Pier") == set()

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    titled = "Professor Mara Vance"
    watcher = characters.create_character(croot, "Seraphine", "main",
                                          characters.blank_card("Seraphine"))[0]
    named = characters.create_character(croot, titled, "main",
                                        characters.blank_card(titled))[0]
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", watcher, "main", "npc")
    appearances.appear(cid, sid, "characters", named, "main", "npc")
    playstate.write_state(croot, watcher, playstate.compose_body(
        "Wary.", "", "Mara is hiding the ledger.\n\nThe Guild watches the pier."))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section
    assert "The Guild watches the pier." in section


def test_an_elided_particle_still_yields_both_ends_of_the_name(monkeypatch, tmp_path):
    """`_PARTICLE` is matched whole, so `d'Ormesson` -- one token, lower-cased
    head, in no set -- read as an epithet and rejected the WHOLE name: neither
    end was derived and the suspicion went to the prompt. The apostrophe is the
    signal, and the name behind it is a form in its own right because prose
    drops the particle as readily as it keeps it."""
    from grimoire.store import appearances, campaigns, characters, playstate, scenes, worlds
    from grimoire.store.context import world_state
    assert world_state._short_alias("Jean d'Ormesson") == "Jean"
    assert world_state._surname_alias("Jean d'Ormesson") == "d'Ormesson"
    assert world_state._forms({"Jean d'Ormesson"}) == {
        "Jean d'Ormesson", "Jean", "d'Ormesson", "Ormesson"}
    assert world_state._forms({"Mara dell'Acqua"}) == {
        "Mara dell'Acqua", "Mara", "dell'Acqua", "Acqua"}
    # An epithet is still an epithet: the article rejects it however it is
    # punctuated, and a bare lower-case token still has no apostrophe to save it.
    assert world_state._name_tokens("The Woman o' the Pier") == []
    assert world_state._name_tokens("Mara vance") == []

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    elided = "Jean d'Ormesson"
    watcher = characters.create_character(croot, "Seraphine", "main",
                                          characters.blank_card("Seraphine"))[0]
    named = characters.create_character(croot, elided, "main",
                                        characters.blank_card(elided))[0]
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", watcher, "main", "npc")
    appearances.appear(cid, sid, "characters", named, "main", "npc")
    playstate.write_state(croot, watcher, playstate.compose_body(
        "Wary.", "",
        "Jean is hiding the ledger.\n\nOrmesson sold the manifest.\n\n"
        "The Guild watches the pier."))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section
    assert "sold the manifest" not in section        # the particle-less form too
    assert "The Guild watches the pier." in section


def test_a_colon_heading_survives_the_blank_before_its_list(monkeypatch, tmp_path):
    """`Winifred:` / blank / `- is hiding the ledger` is as ordinary as writing
    it tight. Popping the governor at the blank withheld the heading that names
    her and published the subjectless bullet under it — the governor leak again,
    reached through the blank line instead of the syntax."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        "Winifred:\n"
        "\n"
        "- is hiding the ledger\n"
        "\n"
        "Mara sold the manifest."))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section
    assert "Mara sold the manifest." in section     # an absent character is untouched


def test_a_blockquoted_list_is_still_a_list(monkeypatch, tmp_path):
    """A blockquote is a wrapper, not a syntax of its own. Classifying the raw
    line saw `>` where the bullet is, called it an ordinary paragraph, popped the
    heading that named her, and published the subjectless detail underneath."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        "Winifred:\n"
        "\n"
        "> - is hiding the ledger\n"
        ">   - and moved it at midnight\n"
        "\n"
        "Mara sold the manifest."))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section
    assert "moved it at midnight" not in section   # the quoted list still nests
    assert "Mara sold the manifest." in section    # an absent character is untouched


def test_a_quote_nested_in_a_list_stays_under_its_parent(monkeypatch, tmp_path):
    """The whitespace BEFORE the marker is what places a quote under its parent
    bullet. Measuring the indent only inside the quote put the child at column
    zero, where the same-indent pop took the parent out — the named bullet
    withheld and the subjectless one under it published."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        "- Winifred:\n"
        "  > - is hiding the ledger\n"
        "\n"
        "Mara sold the manifest."))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section
    assert "Mara sold the manifest." in section


def test_leaving_a_blockquote_ends_what_was_inside_it(monkeypatch, tmp_path):
    """A change of quote nesting is a container boundary, and the one a blank
    line does not mark. Without it the outer text read as a continuation of the
    quoted paragraph, or stayed governed by a heading opened inside the quote —
    either way an unrelated statement was withheld with the entry naming her."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    for body in (
        "> Winifred is lying.\n"
        "Mara watches the pier.",

        "> Winifred:\n"
        "> - hid the ledger\n"
        "Mara watches the pier.",
    ):
        playstate.write_state(croot, ids["Seraphine Vale"],
                              playstate.compose_body("Wary.", "", body))
        section = _state_section(cid, sid)
        assert "Winifred is lying" not in section
        assert "hid the ledger" not in section
        assert "Mara watches the pier." in section, body


def test_entering_a_deeper_quote_does_not_break_the_paragraph(monkeypatch, tmp_path):
    """The other half of the asymmetry. Leaving a quote ends a container, so it
    ends the entry; ENTERING one does not, and treating a deeper line as a fresh
    entry gave the subjectless continuation its own verdict — it names nobody, so
    it survived while the line naming her was withheld. The multiline leak again,
    reached through the quote marker instead of the line break."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    for body in (
        "> Winifred is lying.\n"
        ">> At midnight, she steals the ledger.",

        "Winifred is lying.\n"
        "> At midnight, she steals the ledger.",
    ):
        playstate.write_state(croot, ids["Seraphine Vale"],
                              playstate.compose_body("Wary.", "", body))
        section = _state_section(cid, sid)
        assert "Winifred is lying" not in section
        assert "steals the ledger" not in section, body


def test_an_indented_heading_stays_under_its_list_item(monkeypatch, tmp_path):
    """A heading indented inside a list item is part of that item. Popping every
    non-ATX governor regardless of indent severed the named parent, so the
    bullets under `Plans` were governed only by a heading that names nobody."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        "- Winifred\n"
        "  ### Plans\n"
        "  - is hiding the ledger\n"
        "\n"
        "Mara sold the manifest."))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section
    assert "Mara sold the manifest." in section
    # An UNindented heading still ends the list it follows — that is what the
    # pop was written for, and this fix must not cost it.
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        "- Winifred\n"
        "## Elsewhere\n"
        "- The Guild watches the pier."))
    section = _state_section(cid, sid)
    assert "The Guild watches the pier." in section


def test_stacked_unrecognized_titles_still_yield_the_given_name(monkeypatch, tmp_path):
    """Taking ONE interior token and stepping over only the tokens `_usable`
    rejects meant a second title the honorific set does not list stopped the
    walk: `Professor Reverend Mara Vance` yielded `Reverend` and lost `Mara`.
    Titles stack, and how many is no more knowable than which."""
    from grimoire.store import appearances, campaigns, characters, playstate, scenes, worlds
    from grimoire.store.context import world_state
    assert world_state._forms({"Professor Reverend Mara Vance"}) == {
        "Professor Reverend Mara Vance", "Professor", "Reverend", "Mara", "Vance"}

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    stacked = "Professor Reverend Mara Vance"
    watcher = characters.create_character(croot, "Seraphine", "main",
                                          characters.blank_card("Seraphine"))[0]
    named = characters.create_character(croot, stacked, "main",
                                        characters.blank_card(stacked))[0]
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", watcher, "main", "npc")
    appearances.appear(cid, sid, "characters", named, "main", "npc")
    playstate.write_state(croot, watcher, playstate.compose_body(
        "Wary.", "", "Mara is hiding the ledger.\n\nThe Guild watches the pier."))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section
    assert "The Guild watches the pier." in section


def test_a_top_level_heading_with_leading_spaces_is_not_nested(monkeypatch, tmp_path):
    """Markdown allows a top-level heading up to three leading spaces. Closing
    one because a later column-zero bullet is "less indented" withheld the line
    that names her and published the subjectless bullet under it — what makes a
    heading nested is the open LIST it sits inside, not its column."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        # NOT the first line of the block: `playstate` strips that one's indent,
        # so the reviewer's own example cannot reach `_entries` with its spaces
        # intact. A heading anywhere else keeps them, which is the same defect
        # by a shape the store can actually hold.
        "Notes:\n"
        "\n"
        "  ### Winifred\n"
        "- is hiding the ledger\n"
        "\n"
        "## Elsewhere\n"
        "- Mara sold the manifest."))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section
    assert "Mara sold the manifest." in section     # the next section is its own


def test_a_colon_heading_with_leading_spaces_governs_a_top_level_list(monkeypatch, tmp_path):
    """The same cosmetic-indent trap as the ATX fix, one governor kind over.
    Ranking `  Winifred:` by its raw column made a following column-zero bullet
    look like a return to an outer level, popping the heading that names her and
    leaving the subjectless bullet ungoverned."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        # Not the block's first line — `playstate` strips that one's indent.
        "Notes:\n"
        "\n"
        "  Winifred:\n"
        "- is hiding the ledger\n"
        "\n"
        "Mara sold the manifest."))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section
    assert "Mara sold the manifest." in section
    # Real nesting still ranks by its indent: an indented `Plans:` inside a list
    # must not govern the outer bullet that follows it (round twenty).
    from grimoire.store.context import world_state
    assert world_state._visible_suspects(
        "Winifred:\n- Plans:\n  - steal it\n- knows the truth", {"Winifred"}) == ""


def test_a_paragraph_after_the_blank_is_not_governed(monkeypatch, tmp_path):
    """Only a LIST keeps a colon heading open across the blank. An ordinary
    paragraph below one is a new statement, which is the case the pop was
    written for — without this the fix above becomes the all-or-nothing
    behaviour `_entries` exists to avoid."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "",
        "Winifred:\n"
        "\n"
        "The crates moved again last night."))
    section = _state_section(cid, sid)
    assert "Winifred:" not in section                     # the heading names her
    assert "The crates moved again last night." in section


def test_a_quoted_nickname_is_matched_without_its_quotes(monkeypatch, tmp_path):
    """A nickname is written set off by quotes or brackets, and `_interior_aliases`
    lands on exactly that token — so keeping the marks made the form `"Red"` and
    `_mentions` then required the suspicion to quote her too. The name is what
    is inside the marks."""
    from grimoire.store import appearances, campaigns, characters, playstate, scenes, worlds
    from grimoire.store.context import world_state
    assert world_state._forms({'Mara "Red" Vance'}) == {
        'Mara "Red" Vance', "Mara", "Red", "Vance"}
    assert world_state._forms({"Mara (Red) Vance"}) == {
        "Mara (Red) Vance", "Mara", "Red", "Vance"}
    # Interior punctuation is untouched, which is what keeps the elided particle
    # whole while the quotes around a nickname come off.
    assert world_state._surname_alias("Jean d'Ormesson") == "d'Ormesson"

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    nicknamed = 'Mara "Red" Vance'
    watcher = characters.create_character(croot, "Seraphine", "main",
                                          characters.blank_card("Seraphine"))[0]
    named = characters.create_character(croot, nicknamed, "main",
                                        characters.blank_card(nicknamed))[0]
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", watcher, "main", "npc")
    appearances.appear(cid, sid, "characters", named, "main", "npc")
    playstate.write_state(croot, watcher, playstate.compose_body(
        "Wary.", "", "Red is hiding the ledger.\n\nThe Guild watches the pier."))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section
    assert "The Guild watches the pier." in section


def test_a_stacked_title_does_not_consume_the_given_name_slot(monkeypatch, tmp_path):
    """`_name_tokens` strips only a leading token it RECOGNIZES as a title, so
    with titles stacked the interior one is still a title — and stopping on it
    yielded no given-name form at all, which is the same leak the second-token
    rule was written to close, one token further in."""
    from grimoire.store import appearances, campaigns, characters, playstate, scenes, worlds
    from grimoire.store.context import world_state
    assert world_state._interior_aliases("Professor Dr. Mara Vance") == {"Mara"}
    assert world_state._interior_aliases("Professor J. Mara Vance") == {"Mara"}  # nor an initial
    assert world_state._interior_aliases("Professor Dr. Vance") == set()   # nothing between
    # Titles STACK, and how many is not knowable either: every interior token
    # that is name-shaped is a form, so a second unrecognized one cannot stop
    # the walk short of the given name.
    assert world_state._interior_aliases("Professor Reverend Mara Vance") == {
        "Reverend", "Mara"}

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    stacked = "Professor Dr. Mara Vance"
    watcher = characters.create_character(croot, "Seraphine", "main",
                                          characters.blank_card("Seraphine"))[0]
    named = characters.create_character(croot, stacked, "main",
                                        characters.blank_card(stacked))[0]
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", watcher, "main", "npc")
    appearances.appear(cid, sid, "characters", named, "main", "npc")
    playstate.write_state(croot, watcher, playstate.compose_body(
        "Wary.", "", "Mara is hiding the ledger.\n\nThe Guild watches the pier."))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section
    assert "The Guild watches the pier." in section


def test_a_suspicion_naming_a_player_s_container_name_is_withheld(monkeypatch, tmp_path):
    """A PC's container name and its locked persona name can differ. Taking only
    the persona left a suspicion written against the canonical PC name unmatched
    — the asymmetry with the character branch was an oversight."""
    from grimoire.store import appearances, campaigns, characters, pcs, playstate, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = campaigns.create_campaign("Run", worlds.create_world("W"))
    croot = campaigns.campaign_root(cid)
    ch = characters.create_character(croot, "Seraphine", "main",
                                     characters.blank_card("Seraphine"))[0]
    persona = pcs.blank_persona("Winifred")
    persona["name"] = "The Harbourmaster's Daughter"     # persona drifts from the PC name
    pid, vid = pcs.create_pc(croot, "Winifred", [], persona=persona)
    sid = scenes.create_scene(cid, "Now")
    appearances.appear(cid, sid, "characters", ch, "main", "npc")
    appearances.appear(cid, sid, "pcs", pid, vid, "player")
    playstate.write_state(croot, ch, playstate.compose_body(
        "Wary.", "", "Winifred is working for the Guild."))
    assert "working for the Guild" not in _state_section(cid, sid)

# ---- archive retrieval (#127) ----
#
# Record ids sort against the SCENE id (`before`), and real scene ids carry an
# ordinal prefix -- so an "older" scene has to be `000--…`, below the fixture
# scene's `001--…`. A bare date id like `2026-06-20` sorts ABOVE it and reads as
# a future scene, which silently empties the archive and makes every
# absence-assertion below prove nothing. Hence the prefix, and hence the
# positive control in each negative test.

def _absorbed(cid, sid_, keywords, summary="", one_line="", date=""):
    chronicle.absorb(cid, {"id": sid_, "one_line": one_line or "A thing happened.",
                           "summary": summary or "A longer account of the thing.",
                           "keywords": list(keywords), "cast": [], "location": "", "date": date})


def _archive(cid, sid):
    return next((s["text"] for s in context.context_sections(cid, sid)
                 if s["label"] == "Earlier scenes"), None)


def test_archive_recalls_an_old_scene_by_keyword(monkeypatch, tmp_path):
    """The scene has fallen out of the recap window, so nothing else in the
    prompt can reach it -- a keyword in the scan window is the only way back."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import config
    config.write_config(recap_depth="1")
    _absorbed(cid, "000--b--newer", ["clinic"], summary="They met at the clinic.")
    _absorbed(cid, "000--a--older", ["saltmarch"],
              summary="The Saltmarch crossing went badly.", date="2026-06-20")
    scenes.append_message(cid, sid, "user", "What happened at Saltmarch?")
    text = _archive(cid, sid)
    assert text is not None
    assert "The Saltmarch crossing went badly." in text
    assert "2026-06-20" in text                       # the date rides along
    assert "already happened" in text                 # labelled as past, not current
    assert "They met at the clinic." not in text      # no keyword hit


def test_archive_stays_silent_without_a_keyword_hit(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import config
    config.write_config(recap_depth="0")  # nothing in the recap window at all
    _absorbed(cid, "000--a--older", ["saltmarch"], summary="The crossing went badly.")
    scenes.append_message(cid, sid, "user", "Nothing relevant here.")
    assert _archive(cid, sid) is None
    # control: the same fixture DOES retrieve once the word is said, so the
    # silence above was the missing keyword and not a dead fixture
    scenes.append_message(cid, sid, "assistant", "But then: Saltmarch.")
    assert _archive(cid, sid) is not None


def test_archive_never_duplicates_the_recap_window(monkeypatch, tmp_path):
    """A scene the recap already shows must not come back a second time under
    a heading that calls it a recalled memory."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import config
    config.write_config(recap_depth="5")
    _absorbed(cid, "000--a--older", ["saltmarch"], one_line="Saltmarch went badly.",
              summary="The Saltmarch crossing went badly.")
    scenes.append_message(cid, sid, "user", "What happened at Saltmarch?")
    sections = {s["label"]: s["text"] for s in context.context_sections(cid, sid)}
    assert "Saltmarch went badly." in sections["Story so far"]   # the recap has it
    assert "Earlier scenes" not in sections                      # so the archive does not


def test_archive_excludes_the_scene_being_played(monkeypatch, tmp_path):
    """An absorbed scene that is then continued still has a chronicle record;
    recalling it while its own transcript is in the history would narrate the
    present as a past event."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import config
    config.write_config(recap_depth="0")
    _absorbed(cid, sid, ["saltmarch"], summary="This very scene, as history.")
    _absorbed(cid, "000--a--older", ["saltmarch"], summary="A genuinely earlier scene.")
    scenes.append_message(cid, sid, "user", "What happened at Saltmarch?")
    text = _archive(cid, sid)
    assert "A genuinely earlier scene." in text        # control: retrieval is live
    assert "This very scene, as history." not in text


def test_archive_never_recalls_a_scene_later_than_the_one_being_played(monkeypatch, tmp_path):
    """Continuing an older scene leaves later absorbed scenes outside the recap
    window; recalling one under a heading that swears it already happened would
    narrate the future as history."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)   # sid sorts first (ordinal prefix)
    later = scenes.create_scene(cid, "Later")
    latest = scenes.create_scene(cid, "Latest")
    from grimoire.store import config
    config.write_config(recap_depth="1")                # only `latest` is in the recap
    _absorbed(cid, later, ["saltmarch"], summary="The Saltmarch crossing is still ahead.")
    _absorbed(cid, latest, ["saltmarch"], summary="Long after the crossing.")
    scenes.append_message(cid, sid, "user", "What of Saltmarch?")

    assert sid < later < latest                          # the ordering the fix relies on
    assert _archive(cid, sid) is None                    # neither future scene leaks

    # ...and the bound is directional: from the latest scene, the earlier one
    # IS legitimately archive material
    scenes.append_message(cid, latest, "user", "What of Saltmarch?")
    assert "The Saltmarch crossing is still ahead." in _archive(cid, latest)


def test_archive_keyless_record_is_never_always_on(monkeypatch, tmp_path):
    """Unlike a keyless lore entry: every absorbed scene would qualify and the
    section would grow without bound as the campaign runs."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import config
    config.write_config(recap_depth="0")
    _absorbed(cid, "000--a--keyless", [], summary="Something happened once.")
    _absorbed(cid, "000--b--keyed", ["saltmarch"], summary="The crossing went badly.")
    scenes.append_message(cid, sid, "user", "What happened at Saltmarch?")
    text = _archive(cid, sid)
    assert "The crossing went badly." in text          # control: retrieval is live
    assert "Something happened once." not in text


def test_archive_matches_whole_words_only(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import config
    config.write_config(recap_depth="0")
    _absorbed(cid, "000--a--pac", ["pac"], summary="The Pac accord.")
    _absorbed(cid, "000--b--pact", ["pact"], summary="The Pact itself.")
    scenes.append_message(cid, sid, "user", "They spoke of the pact.")
    text = _archive(cid, sid)
    assert "The Pact itself." in text                  # control: retrieval is live
    assert "The Pac accord." not in text               # 'pact' must not trigger 'pac'


def test_archive_caps_at_archive_depth_newest_first(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import config
    config.write_config(recap_depth="0", archive_depth="2")
    for n in (1, 2, 3, 4):
        _absorbed(cid, f"000--0{n}--scene", ["saltmarch"], summary=f"Crossing number {n}.")
    scenes.append_message(cid, sid, "user", "Tell me about Saltmarch.")
    text = _archive(cid, sid)
    assert "Crossing number 4." in text and "Crossing number 3." in text
    assert "Crossing number 2." not in text and "Crossing number 1." not in text
    assert text.index("Crossing number 4.") < text.index("Crossing number 3.")  # newest first


def test_archive_depth_zero_disables_retrieval(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import config
    config.write_config(recap_depth="0", archive_depth="0")
    _absorbed(cid, "000--a--older", ["saltmarch"], summary="The crossing went badly.")
    scenes.append_message(cid, sid, "user", "What happened at Saltmarch?")
    assert _archive(cid, sid) is None
    # control: the same fixture retrieves with a depth, so 0 was the reason
    config.write_config(archive_depth="3")
    assert _archive(cid, sid) is not None


def test_archive_tolerates_a_garbled_chronicle(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "hello")
    (campaigns.campaign_root(cid) / "chronicle.json").write_text("{ not valid json", encoding="utf-8")
    assert _archive(cid, sid) is None   # must not raise
    context.build_messages(cid, sid)    # nor may the real consumer


def test_archive_falls_back_to_one_line_without_a_summary(monkeypatch, tmp_path):
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import config
    config.write_config(recap_depth="0")
    chronicle.absorb(cid, {"id": "000--a--older", "one_line": "The crossing went badly.",
                           "summary": "", "keywords": ["saltmarch"]})
    scenes.append_message(cid, sid, "user", "What happened at Saltmarch?")
    assert "The crossing went badly." in _archive(cid, sid)


# ---- tiered budget packing (#126) ----

from grimoire.store.context import pack as context_pack  # noqa: E402


def _sec(label, tier, text):
    return {"label": label, "text": text, "tier": tier}


def _packed_labels(result):
    return [s["label"] for s in result["sections"] if not s["dropped"]]


def test_pack_without_a_budget_drops_nothing(monkeypatch, tmp_path):
    """0 is the default and every pre-existing install: the packed prompt must
    be the unpacked one, not merely a close approximation of it."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    sections = [_sec("A", context_pack.ARCHIVE, "x " * 500), _sec("B", context_pack.LOCK_IN, "y")]
    out = context_pack.pack(sections, [{"role": "user", "content": "z " * 500}], budget=0)
    assert _packed_labels(out) == ["A", "B"]
    assert out["history_trimmed"] == 0


def test_pack_drops_archive_before_background_before_spotlight(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    sections = [_sec("keep", context_pack.LOCK_IN, "lock "),
                _sec("spot", context_pack.SPOTLIGHT, "spot " * 20),
                _sec("back", context_pack.BACKGROUND, "back " * 20),
                _sec("arch", context_pack.ARCHIVE, "arch " * 20)]
    total = sum(context.count_tokens(s["text"]) for s in sections)
    one = context.count_tokens("arch " * 20)

    # room for everything but one section -> the archive is the one that goes
    out = context_pack.pack(sections, [], budget=total - 1)
    assert _packed_labels(out) == ["keep", "spot", "back"]
    # room for two fewer -> background follows it
    out = context_pack.pack(sections, [], budget=total - one - 1)
    assert _packed_labels(out) == ["keep", "spot"]
    # room for one section only -> spotlight goes too
    out = context_pack.pack(sections, [], budget=context.count_tokens("lock "))
    assert _packed_labels(out) == ["keep"]


def test_pack_never_drops_lock_in_even_when_it_cannot_fit(monkeypatch, tmp_path):
    """The floor of the whole design: a full answer in the wrong shape is worse
    than a smaller one, so an impossible budget ships over-budget rather than
    dropping the brief."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    sections = [_sec("brief", context_pack.LOCK_IN, "lock " * 50),
                _sec("arch", context_pack.ARCHIVE, "arch " * 50)]
    out = context_pack.pack(sections, [], budget=1)
    assert _packed_labels(out) == ["brief"]


def test_pack_trims_history_after_the_archive_and_before_the_frame(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    sections = [_sec("back", context_pack.BACKGROUND, "back " * 20),
                _sec("arch", context_pack.ARCHIVE, "arch " * 20)]
    history = [{"role": "user", "content": f"line {n} " * 10} for n in range(6)]
    hist_total = sum(context.count_tokens(m["content"]) for m in history)
    back = context.count_tokens("back " * 20)
    # a budget that fits the background section plus roughly half the history
    out = context_pack.pack(sections, history, budget=back + hist_total // 2)
    assert _packed_labels(out) == ["back"]        # archive went first
    assert out["history_trimmed"] > 0             # then history gave way
    assert len(out["history"]) == len(history) - out["history_trimmed"]
    assert out["history"][-1] == history[-1]      # trimmed from the front


def test_pack_keeps_a_history_floor(monkeypatch, tmp_path):
    """Below the floor the model is answering a turn it cannot see."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    history = [{"role": "user", "content": f"line {n} " * 20} for n in range(8)]
    out = context_pack.pack([], history, budget=1)
    assert len(out["history"]) == context_pack.HISTORY_FLOOR
    assert out["history"] == history[-context_pack.HISTORY_FLOOR:]


def test_pack_drops_the_largest_section_in_a_tier_first(monkeypatch, tmp_path):
    """Fewest drops that reach the ceiling."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    sections = [_sec("small", context_pack.BACKGROUND, "s " * 5),
                _sec("large", context_pack.BACKGROUND, "l " * 60)]
    total = sum(context.count_tokens(s["text"]) for s in sections)
    out = context_pack.pack(sections, [], budget=total - 1)
    assert _packed_labels(out) == ["small"]


def test_pack_charges_the_reserved_post_history_without_dropping_it(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    sections = [_sec("arch", context_pack.ARCHIVE, "arch " * 20)]
    fits = context.count_tokens("arch " * 20)
    assert _packed_labels(context_pack.pack(sections, [], reserved=0, budget=fits)) == ["arch"]
    # the same budget, now partly spent on the post-history block
    assert _packed_labels(context_pack.pack(sections, [], reserved=fits, budget=fits)) == []


def test_pack_reports_dropped_sections_rather_than_deleting_them(monkeypatch, tmp_path):
    """A drop the user cannot see is the silent truncation this replaced."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    sections = [_sec("arch", context_pack.ARCHIVE, "arch " * 20)]
    out = context_pack.pack(sections, [], budget=1)
    assert [s["label"] for s in out["sections"]] == ["arch"]
    assert out["sections"][0]["dropped"] is True
    assert out["sections"][0]["text"] == "arch " * 20     # text survives, for the inspector


def test_budget_tokens_reads_config_and_tolerates_nonsense(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import config
    assert context.budget_tokens() == 0                  # default: unbounded
    config.write_config(context_budget="4000")
    assert context.budget_tokens() == 4000
    config.write_config(context_budget="-5")
    assert context.budget_tokens() == 0
    config.write_config(context_budget="not a number")   # hand-edited config.md
    assert context.budget_tokens() == 0


def test_a_budget_actually_shrinks_the_prompt(monkeypatch, tmp_path):
    """End to end through build_messages: the same scene, with and without a
    budget, and the budgeted prompt is the smaller one."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import config
    config.write_config(recap_depth="0", system_prompt="Never speak for the PC.")
    characters.create_character(campaigns.campaign_root(cid), "Seraphine", "default",
                                _npc_card("Seraphine", description="A keeper of the Saltmarch road."))
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    _absorbed(cid, "000--a--older", ["saltmarch"], summary="The Saltmarch crossing went badly.")
    for n in range(8):
        scenes.append_message(cid, sid, "user", f"Turn {n} on the Saltmarch road. " * 10)

    unbounded = context.build_messages(cid, sid)
    size = sum(context.count_tokens(m["content"]) for m in unbounded)
    assert "Saltmarch crossing went badly" in unbounded[0]["content"]  # archive was recalled

    config.write_config(context_budget=str(size // 2))
    bounded = context.build_messages(cid, sid)
    assert sum(context.count_tokens(m["content"]) for m in bounded) < size
    assert "Saltmarch crossing went badly" not in bounded[0]["content"]  # archive dropped first
    assert "Never speak for the PC." in bounded[0]["content"]            # lock-in survived


def test_context_sections_reports_tiers_and_drops(monkeypatch, tmp_path):
    """The inspector's view and the prompt come off one render + one pack, so a
    section shown as kept is a section that was sent."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import config
    config.write_config(recap_depth="0")
    _absorbed(cid, "000--a--older", ["saltmarch"], summary="The Saltmarch crossing went badly.")
    scenes.append_message(cid, sid, "user", "The Saltmarch road again. " * 40)

    secs = {s["label"]: s for s in context.context_sections(cid, sid)}
    assert secs["Earlier scenes"]["tier"] == context_pack.ARCHIVE
    assert secs["Earlier scenes"]["dropped"] is False
    assert secs["Response format"]["tier"] == context_pack.LOCK_IN
    assert secs["Conversation history"]["tier"] == context_pack.HISTORY

    sent = context.build_messages(cid, sid)[0]["content"]
    config.write_config(context_budget=str(context.count_tokens(sent) // 2))
    secs = {s["label"]: s for s in context.context_sections(cid, sid)}
    assert secs["Earlier scenes"]["dropped"] is True
    assert secs["Earlier scenes"]["text"]                       # still inspectable
    assert secs["Response format"]["dropped"] is False
    # and what it reports as kept is exactly what build_messages sent
    kept = context.build_messages(cid, sid)[0]["content"]
    assert secs["Earlier scenes"]["text"] not in kept
    assert secs["Response format"]["text"] in kept


def test_a_budget_trims_the_history_but_keeps_the_latest_turns(monkeypatch, tmp_path):
    """History is the pressure valve after the archive. Roles alternate because
    _project_history merges consecutive same-role turns, and it is the merged
    list build_messages sends."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import config
    config.write_config(recap_depth="0")
    for n in range(8):
        scenes.append_message(cid, sid, "user" if n % 2 == 0 else "assistant",
                              f"Turn {n} on the Saltmarch road. " * 20)

    unbounded = context.build_messages(cid, sid)
    size = sum(context.count_tokens(m["content"]) for m in unbounded)
    config.write_config(context_budget=str(size // 3))
    bounded = context.build_messages(cid, sid)

    assert len(bounded) < len(unbounded)                 # turns were trimmed
    assert bounded[-1] == unbounded[-1]                  # the newest end is intact
    assert "Turn 7" in "".join(m["content"] for m in bounded)
    assert "Turn 0" not in "".join(m["content"] for m in bounded)
    # never below the floor, whatever the budget
    config.write_config(context_budget="1")
    starved = context.build_messages(cid, sid)
    assert len([m for m in starved if m["role"] != "system"]) >= context_pack.HISTORY_FLOOR


def _fits(messages, budget):
    return sum(context.count_tokens(m["content"]) for m in messages) <= budget


def test_every_appended_message_is_charged_to_the_budget(monkeypatch, tmp_path):
    """A message the packer cannot drop must be counted before it packs.
    Reserving only post_history let a large director note / opener prompt /
    guidance block leave droppable sections in place and then push the request
    back over the ceiling -- reintroducing the provider-side truncation the
    packer exists to prevent."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import config
    config.write_config(recap_depth="0")
    _absorbed(cid, "000--a--older", ["saltmarch"], summary="The Saltmarch crossing went badly. " * 20)
    for n in range(6):
        scenes.append_message(cid, sid, "user" if n % 2 == 0 else "assistant",
                              f"Turn {n} on the Saltmarch road. " * 20)

    budget = context.count_tokens(
        "".join(m["content"] for m in context.build_messages(cid, sid)))
    config.write_config(context_budget=str(budget))
    assert _fits(context.build_messages(cid, sid), budget)     # baseline: it fits

    # a note big enough that ignoring it would overrun -- but not so big the
    # packer cannot absorb it, which would overrun for the legitimate reason
    # (lock-in plus the history floor is the floor)
    note = "Consider the Saltmarch road and everything on it. " * 60
    assert context.count_tokens(note) > budget // 4
    assert _fits(context.build_director_messages(cid, sid, note), budget)

    # the same for an appended guidance block on a plain turn -- named in
    # `appended`, which reserves it AND appends it, so the two cannot disagree
    assert _fits(context.build_messages(
        cid, sid, appended=(("Regenerate guidance", "system", note),)), budget)

    # and for the opener, whose prompt and shape rules both ride after packing
    assert _fits(context.build_opener_messages(cid, sid, note), budget)


def test_an_unreserved_append_is_what_overruns(monkeypatch, tmp_path):
    """The negative half of the test above: appending a block the packer was
    never told about overruns, which is what makes `appended` load-bearing
    rather than tidy."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import config
    config.write_config(recap_depth="0")
    _absorbed(cid, "000--a--older", ["saltmarch"], summary="The Saltmarch crossing went badly. " * 20)
    for n in range(6):
        scenes.append_message(cid, sid, "user" if n % 2 == 0 else "assistant",
                              f"Turn {n} on the Saltmarch road. " * 20)
    budget = context.count_tokens(
        "".join(m["content"] for m in context.build_messages(cid, sid)))
    config.write_config(context_budget=str(budget))
    note = "Consider the Saltmarch road and everything on it. " * 60
    unreserved = context.build_messages(cid, sid) + [{"role": "system", "content": note}]
    assert not _fits(unreserved, budget)
    assert _fits(context.build_messages(
        cid, sid, appended=(("Regenerate guidance", "system", note),)), budget)


def test_pack_measures_the_composed_message_not_the_sum(monkeypatch, tmp_path):
    """Token counts are not additive: a sum leaves the separators between
    sections uncharged, and on the tiktoken-less Android path each section's
    `len // 4` discards its own remainder too. A sum can therefore clear a
    ceiling the composed message misses -- the one error this must not make."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    sections = [_sec(f"s{n}", context_pack.BACKGROUND, "word " * 5) for n in range(6)]
    summed = sum(context.count_tokens(s["text"]) for s in sections)

    def costly_join(texts):  # an exaggerated separator, to make the gap visible
        return ("\n\n" + "PAD " * 20).join(texts)

    assert context.count_tokens(costly_join([s["text"] for s in sections])) > summed
    out = context_pack.pack(sections, [], budget=summed, compose=costly_join)
    kept = [s["text"] for s in out["sections"] if not s["dropped"]]
    assert len(kept) < len(sections)                              # the join forced a drop
    assert context.count_tokens(costly_join(kept)) <= summed      # and it is genuinely under


def test_the_real_prompt_fits_the_budget_it_was_packed_to(monkeypatch, tmp_path):
    """The invariant the packer exists for, measured on the messages that ship
    rather than on the packer's own arithmetic."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import config
    config.write_config(recap_depth="0")
    _absorbed(cid, "000--older", ["saltmarch"], summary="The Saltmarch crossing went badly. " * 20)
    for n in range(8):
        scenes.append_message(cid, sid, "user" if n % 2 == 0 else "assistant",
                              f"Turn {n} on the Saltmarch road. " * 20)
    full = sum(context.count_tokens(m["content"]) for m in context.build_messages(cid, sid))
    # The irreducible floor: lock-in plus the history floor, which the packer
    # will not go under. Below it an overrun is the documented, deliberate
    # outcome (see test_pack_never_drops_lock_in_even_when_it_cannot_fit), so
    # the invariant is only meaningful at or above it.
    config.write_config(context_budget="1")
    floor = sum(context.count_tokens(m["content"]) for m in context.build_messages(cid, sid))
    assert floor < full, "the fixture has nothing droppable, so this proves nothing"
    for budget in (full - 1, (full + floor) // 2, floor):
        config.write_config(context_budget=str(budget))
        assert _fits(context.build_messages(cid, sid), budget), f"overran a budget of {budget}"


# ---- the tiktoken-less path (Android) ----

def _heuristic(monkeypatch):
    """Force the length-heuristic path: tiktoken is a desktop-only wheel, so
    Android counts every string as `ceil(len / 4)` and that is where rounding
    decides whether short turns cost anything at all."""
    monkeypatch.setattr(context.tokens, "_encoder", lambda: None)


def test_a_nonempty_string_never_costs_nothing_on_the_heuristic(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    _heuristic(monkeypatch)
    assert context.count_tokens("") == 0        # empty really is free
    assert context.count_tokens("a") >= 1
    assert context.count_tokens("yes") >= 1     # floor division made this 0


def test_short_history_turns_are_still_trimmed_on_the_heuristic(monkeypatch, tmp_path):
    """The failure this guards: the packer counts each history message on its
    own, so with floor division a scene of short alternating turns summed to
    zero and no budget, however small, could make it trim anything."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    _heuristic(monkeypatch)
    history = [{"role": "user" if n % 2 == 0 else "assistant", "content": "yes"}
               for n in range(200)]
    out = context_pack.pack([], history, budget=1)
    assert len(out["history"]) == context_pack.HISTORY_FLOOR
    assert out["history_trimmed"] == 200 - context_pack.HISTORY_FLOOR


def test_the_breakdown_counts_history_as_the_messages_it_is_sent_as(monkeypatch, tmp_path):
    """The history row is DISPLAYED joined but must be ACCOUNTED per message —
    that is how the packer charges it and how it goes on the wire. Recounting
    the joined display string would let the inspector's total disagree with the
    request it describes."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    _heuristic(monkeypatch)                     # where the two representations differ
    for n in range(10):
        scenes.append_message(cid, sid, "user" if n % 2 == 0 else "assistant", "no")
    body = context.context_breakdown(cid, sid)
    row = next(r for r in body["sections"] if r["label"] == "Conversation history")
    turns = [m for m in context.build_messages(cid, sid) if m["role"] != "system"]
    assert row["tokens"] == sum(context_pack.message_cost(m["content"]) for m in turns)
    # Discriminating against both wrong answers: the joined display string, and
    # the bare content sum with no per-message framing. The first two really do
    # disagree on this fixture -- if a change ever makes them coincide here,
    # this fails and the fixture wants rechoosing rather than passing blind.
    assert context.count_tokens(row["text"]) != sum(context.count_tokens(m["content"])
                                                    for m in turns)
    assert row["tokens"] != context.count_tokens(row["text"])


def test_the_breakdown_total_is_the_cost_of_the_real_request(monkeypatch, tmp_path):
    """Not the sum of the rows: the blank lines joining the sections are real
    tokens, and per-string counts do not add across a join."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    _heuristic(monkeypatch)
    from grimoire.store import config
    config.write_config(system_prompt="Never speak for the PC.")
    for n in range(6):
        scenes.append_message(cid, sid, "user" if n % 2 == 0 else "assistant",
                              f"Turn {n} on the Saltmarch road.")
    body = context.context_breakdown(cid, sid)
    messages = context.build_messages(cid, sid)
    wire = sum(context.count_tokens(m["content"]) for m in messages)
    turns = [m for m in messages if m["role"] != "system"]
    # The content that ships is the floor; the difference is exactly the
    # per-message framing allowance, charged once per history message.
    assert body["total_tokens"] == wire + context_pack.MESSAGE_OVERHEAD * len(turns)
    assert body["total_tokens"] > wire
    # ...and it is NOT the sum of the rows, which is what it would be if the
    # total were re-derived from the breakdown. Same vacuity guard as above.
    assert body["total_tokens"] != sum(r["tokens"] for r in body["sections"] if not r["dropped"])


def test_archive_ignores_a_string_keywords_field(monkeypatch, tmp_path):
    """A model that answers the absorb prompt with a bare string instead of an
    array gets persisted as one entry per CHARACTER, and a key of "a" whole-word
    matches ordinary prose — recalling unrelated scenes at random."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import config
    config.write_config(recap_depth="0")
    chronicle.absorb(cid, {"id": "000--a--letters", "one_line": "Nonsense.",
                           "summary": "Should never be recalled.",
                           "keywords": "saltmarch"})          # a string, not a list
    chronicle.absorb(cid, {"id": "000--b--proper", "one_line": "Fine.",
                           "summary": "The crossing went badly.",
                           "keywords": ["saltmarch"]})        # a real list
    scenes.append_message(cid, sid, "user", "A message with a and s and m in it.")
    text = _archive(cid, sid)
    assert text is None or "Should never be recalled." not in text
    # control: the well-formed record still retrieves on a real keyword
    scenes.append_message(cid, sid, "assistant", "They spoke of Saltmarch.")
    text = _archive(cid, sid)
    assert "The crossing went badly." in text
    assert "Should never be recalled." not in text


def test_history_is_charged_for_the_framing_a_provider_adds(monkeypatch, tmp_path):
    """No provider sends bare content — and `claude_agent._flatten` serialises
    the whole conversation into one string with `[role]` prefixes. Charging
    content alone leaves that uncounted."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assert context_pack.message_cost("hello") == context.count_tokens("hello") + \
        context_pack.MESSAGE_OVERHEAD
    # and the packer uses it: a history of empty-ish turns still costs something
    history = [{"role": "user", "content": "hi"} for _ in range(50)]
    out = context_pack.pack([], history, budget=10)
    assert len(out["history"]) == context_pack.HISTORY_FLOOR


def test_trimmed_history_counts_toward_the_dropped_total(monkeypatch, tmp_path):
    """A pack that fits by trimming history alone drops no SECTION, so a
    dropped-token total taken from the section rows would be zero and the
    inspector would say nothing about the cut it just made."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import config
    config.write_config(recap_depth="0")
    for n in range(10):
        scenes.append_message(cid, sid, "user" if n % 2 == 0 else "assistant",
                              f"Turn {n} on the Saltmarch road. " * 20)
    full = context.context_breakdown(cid, sid)["total_tokens"]
    config.write_config(context_budget=str(full * 3 // 4))

    body = context.context_breakdown(cid, sid)
    row = next(r for r in body["sections"] if r["label"] == "Conversation history")
    assert row["trimmed"] > 0
    assert not any(r["dropped"] for r in body["sections"]), "this fixture must trim, not drop"
    assert body["dropped_tokens"] > 0            # the trim is reported, not silent


def test_a_director_note_seeds_archive_retrieval(monkeypatch, tmp_path):
    """The note is this turn's input and is never persisted, so if it does not
    seed the scan window, naming an old scene in a director note cannot recall
    it — the opener already seeds retrieval with its ephemeral prompt."""
    _wid, cid, sid = _campaign(monkeypatch, tmp_path)
    from grimoire.store import config
    config.write_config(recap_depth="0")
    _absorbed(cid, "000--a--older", ["saltmarch"], summary="The Saltmarch crossing went badly.")
    scenes.append_message(cid, sid, "user", "Nothing relevant here.")

    plain = context.build_messages(cid, sid)[0]["content"]
    assert "Saltmarch crossing went badly" not in plain     # nothing said the word yet

    directed = context.build_director_messages(cid, sid, "Have them recall Saltmarch.")
    assert "Saltmarch crossing went badly" in directed[0]["content"]

# ------------------------------------------------------- voice drift (#59)

from grimoire.store import voice_anchors, voice_drift  # noqa: E402

def _voice_scene(monkeypatch, tmp_path):
    """A scene with one present, anchored NPC (Winifred), ready to have a drift
    flag raised on them."""
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Winifred", "default", _npc_card("Winifred", description="d"))
    voice_anchors.write(wroot, "winifred", "Clipped. Never uses contractions.")
    ap.appear(cid, sid, "characters", "winifred", "default", "npc")
    scenes.append_message(cid, sid, "user", "hi")
    return wid, cid, sid


def test_no_flag_renders_no_voice_corrective(monkeypatch, tmp_path):
    """The cost of the feature on a campaign that is in voice must be nothing —
    including no stray post-history system message."""
    _wid, cid, sid = _voice_scene(monkeypatch, tmp_path)
    sys_msgs = [m for m in context.build_messages(cid, sid) if m["role"] == "system"]
    assert len(sys_msgs) == 1                      # the system prompt only
    assert "drifted out of voice" not in sys_msgs[0]["content"]


def test_an_unresolved_flag_rides_the_post_history_message(monkeypatch, tmp_path):
    """The corrective goes in the LAST message before generation, not the system
    prompt — the same slot, and the same reasoning, as the length corrective."""
    _wid, cid, sid = _voice_scene(monkeypatch, tmp_path)
    voice_drift.write(campaigns.campaign_root(cid), "winifred", "She hedged; Winifred never hedges.")
    msgs = context.build_messages(cid, sid)
    assert msgs[-1]["role"] == "system"
    last = msgs[-1]["content"]
    assert "Winifred has drifted out of voice" in last and "never hedges" in last
    assert "drifted out of voice" not in msgs[0]["content"]


def test_the_corrective_names_the_character_not_the_id(monkeypatch, tmp_path):
    _wid, cid, sid = _voice_scene(monkeypatch, tmp_path)
    voice_drift.write(campaigns.campaign_root(cid), "winifred", "note")
    last = context.build_messages(cid, sid)[-1]["content"]
    assert "Winifred has drifted" in last and "winifred has drifted" not in last


def test_clearing_the_flag_removes_the_corrective(monkeypatch, tmp_path):
    _wid, cid, sid = _voice_scene(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    voice_drift.write(croot, "winifred", "She hedged.")
    assert "drifted out of voice" in context.build_messages(cid, sid)[-1]["content"]
    voice_drift.write(croot, "winifred", "")
    assert "drifted out of voice" not in context.build_messages(cid, sid)[-1]["content"]


def test_a_flag_on_an_absent_character_is_not_rendered(monkeypatch, tmp_path):
    """A corrective is an instruction about the voice the model is about to
    write, so a flag on someone who is not in this scene has nothing to
    correct."""
    wid, cid, sid = _voice_scene(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Mara", "default", _npc_card("Mara"))
    voice_anchors.write(worlds.world_root(wid), "mara", "Dry.")
    voice_drift.write(campaigns.campaign_root(cid), "mara", "Mara went flat.")
    assert "Mara" not in context.build_messages(cid, sid)[-1]["content"]


def test_a_flag_on_a_player_character_is_not_rendered(monkeypatch, tmp_path):
    """A player character's voice is the user's to drift."""
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Mara", "default", _npc_card("Mara"))
    voice_anchors.write(worlds.world_root(wid), "mara", "Dry.")
    ap.appear(cid, sid, "characters", "mara", "default", "player")
    scenes.append_message(cid, sid, "user", "hi")
    voice_drift.write(campaigns.campaign_root(cid), "mara", "Mara went flat.")
    assert "drifted out of voice" not in context.build_messages(cid, sid)[-1]["content"]


def test_two_flagged_npcs_render_one_listed_corrective(monkeypatch, tmp_path):
    wid, cid, sid = _voice_scene(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Mara", "default", _npc_card("Mara"))
    voice_anchors.write(worlds.world_root(wid), "mara", "Dry.")
    ap.appear(cid, sid, "characters", "mara", "default", "npc")
    croot = campaigns.campaign_root(cid)
    voice_drift.write(croot, "winifred", "She hedged.")
    voice_drift.write(croot, "mara", "She went flat.")
    last = context.build_messages(cid, sid)[-1]["content"]
    assert "Some of the cast have drifted out of voice" in last
    assert "- Winifred: She hedged." in last and "- Mara: She went flat." in last


def test_voice_corrective_precedes_the_length_corrective(monkeypatch, tmp_path):
    """Length is about trimming what was written; voice is about who is writing
    it. The identity correction has to land before the instruction to be brief."""
    from grimoire.store import lengths, response_presets  # noqa: F401
    _wid, cid, sid = _voice_scene(monkeypatch, tmp_path)
    voice_drift.write(campaigns.campaign_root(cid), "winifred", "She hedged.")
    # Three turns far over any budget -> the length corrective renders too.
    for _ in range(3):
        scenes.append_reply(cid, sid, [{"speaker": None, "content": "word " * 4000}])
    last = context.build_messages(cid, sid)[-1]["content"]
    assert "have run long" in last, "expected the length corrective to render too"
    assert last.index("drifted out of voice") < last.index("have run long")


def test_post_history_instructions_still_come_first(monkeypatch, tmp_path):
    """The card's own post-history instructions keep their slot; the corrective
    is appended after them, blank-line separated."""
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Winifred", "default",
                                _npc_card("Winifred", description="d",
                                          post_history_instructions="STAY IN CHARACTER"))
    voice_anchors.write(worlds.world_root(wid), "winifred", "Clipped.")
    ap.appear(cid, sid, "characters", "winifred", "default", "npc")
    scenes.append_message(cid, sid, "user", "hi")
    voice_drift.write(campaigns.campaign_root(cid), "winifred", "She hedged.")
    last = context.build_messages(cid, sid)[-1]["content"]
    assert last.startswith("STAY IN CHARACTER\n\n")
    assert "Winifred has drifted out of voice" in last


def test_removing_the_anchor_silences_a_standing_flag(monkeypatch, tmp_path):
    """Removing the anchor is the documented opt-out, and absorb stops judging
    an anchorless character the moment it goes — so a flag raised before the
    removal has no path back to cleared. Honouring it forever would correct
    every remaining turn of the campaign."""
    wid, cid, sid = _voice_scene(monkeypatch, tmp_path)
    voice_drift.write(campaigns.campaign_root(cid), "winifred", "She hedged.")
    assert "drifted out of voice" in context.build_messages(cid, sid)[-1]["content"]
    voice_anchors.write(worlds.world_root(wid), "winifred", "")
    assert "drifted out of voice" not in context.build_messages(cid, sid)[-1]["content"]


def test_a_campaign_side_anchor_keeps_the_flag_live(monkeypatch, tmp_path):
    """The gate reads through the overlay, so a campaign that has diverged is
    judged against — and corrected by — its own anchor."""
    wid, cid, sid = _voice_scene(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    voice_drift.write(croot, "winifred", "She hedged.")
    voice_anchors.write(worlds.world_root(wid), "winifred", "")   # world anchor gone
    voice_anchors.write(croot, "winifred", "Clipped, in this campaign.")
    assert "drifted out of voice" in context.build_messages(cid, sid)[-1]["content"]


def test_the_corrective_uses_the_locked_card_name(monkeypatch, tmp_path):
    """The same model holds the NPC cards and the transcript, and both identify
    the character by the LOCKED VERSION's card name. A corrective addressed to
    the container's name is one the model can ignore — or, in a multi-NPC scene,
    apply to the wrong character."""
    wid, cid, sid = _voice_scene(monkeypatch, tmp_path)
    aroot = ap.locked_actor_root(cid)
    card = characters.read_card(aroot, "winifred", "default")
    card["data"]["name"] = "Winifred Vance"          # diverges from the container's "Winifred"
    characters.update_version(aroot, "winifred", "default", card)
    voice_drift.write(campaigns.campaign_root(cid), "winifred", "She hedged.")
    last = context.build_messages(cid, sid)[-1]["content"]
    assert "Winifred Vance has drifted out of voice" in last


def test_a_committed_flag_goes_quiet_when_its_anchor_is_replaced(monkeypatch, tmp_path):
    """absorb's apply-time fingerprint only guards the pending-review window. A
    saved flag outlives it, so without this check the note would keep citing a
    standard the user has since replaced."""
    wid, cid, sid = _voice_scene(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    rec = voice_anchors.read_record(worlds.world_root(wid), "winifred")
    voice_drift.write(croot, "winifred", "She hedged.",
                      voice_drift.anchor_fingerprint(rec["text"], rec["id"]))
    assert "drifted out of voice" in context.build_messages(cid, sid)[-1]["content"]

    voice_anchors.write(worlds.world_root(wid), "winifred", "Warm and rambling now.")
    assert "drifted out of voice" not in context.build_messages(cid, sid)[-1]["content"]


def test_removing_and_restoring_an_anchor_does_not_resurrect_a_flag(monkeypatch, tmp_path):
    """The restored anchor is a new standard; the old note was never judged
    against it."""
    wid, cid, sid = _voice_scene(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    croot = campaigns.campaign_root(cid)
    _r = voice_anchors.read_record(wroot, "winifred")
    voice_drift.write(croot, "winifred", "She hedged.",
                      voice_drift.anchor_fingerprint(_r["text"], _r["id"]))
    voice_anchors.write(wroot, "winifred", "")                 # opt out
    voice_anchors.write(wroot, "winifred", "A different standard.")   # opt back in
    assert "drifted out of voice" not in context.build_messages(cid, sid)[-1]["content"]


def test_reformatting_the_anchor_keeps_a_committed_flag_live(monkeypatch, tmp_path):
    wid, cid, sid = _voice_scene(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    _r = voice_anchors.read_record(wroot, "winifred")
    voice_drift.write(campaigns.campaign_root(cid), "winifred", "She hedged.",
                      voice_drift.anchor_fingerprint(_r["text"], _r["id"]))
    voice_anchors.write(wroot, "winifred", "  Clipped. Never uses contractions.\n\n")
    assert "drifted out of voice" in context.build_messages(cid, sid)[-1]["content"]


def test_a_flag_with_no_recorded_provenance_still_renders(monkeypatch, tmp_path):
    """Flags written before the field existed must keep working -- treating an
    unrecorded provenance as stale would retire real user data on upgrade."""
    _wid, cid, sid = _voice_scene(monkeypatch, tmp_path)
    voice_drift.write(campaigns.campaign_root(cid), "winifred", "She hedged.")   # no fingerprint
    assert "drifted out of voice" in context.build_messages(cid, sid)[-1]["content"]


def test_restoring_an_identical_anchor_does_not_resurrect_a_flag(monkeypatch, tmp_path):
    """Clearing an anchor is the opt-out, so a flag it silenced must stay
    silenced — even if the user later types the exact same sentence again. A
    content-only digest cannot express that; the anchor's nonce can."""
    wid, cid, sid = _voice_scene(monkeypatch, tmp_path)
    wroot, croot = worlds.world_root(wid), campaigns.campaign_root(cid)
    rec = voice_anchors.read_record(wroot, "winifred")
    voice_drift.write(croot, "winifred", "She hedged.",
                      voice_drift.anchor_fingerprint(rec["text"], rec["id"]))
    assert "drifted out of voice" in context.build_messages(cid, sid)[-1]["content"]

    voice_anchors.write(wroot, "winifred", "")                  # opt out
    voice_anchors.write(wroot, "winifred", rec["text"])          # restore, character for character
    assert "drifted out of voice" not in context.build_messages(cid, sid)[-1]["content"]


def test_the_note_and_its_provenance_come_from_one_read(monkeypatch, tmp_path):
    """The flag is replaced atomically, so reading the note and its fingerprint
    separately can straddle a chronicle save and validate a stale note against
    the fresh provenance — injecting a retired correction into the next
    generation. Pin the single snapshot by counting the reads."""
    wid, cid, sid = _voice_scene(monkeypatch, tmp_path)
    rec = voice_anchors.read_record(worlds.world_root(wid), "winifred")
    voice_drift.write(campaigns.campaign_root(cid), "winifred", "She hedged.",
                      voice_drift.anchor_fingerprint(rec["text"], rec["id"]))
    reads = []
    real = voice_drift._read_file
    monkeypatch.setattr(voice_drift, "_read_file",
                        lambda croot, char_id: (reads.append(char_id), real(croot, char_id))[1])
    assert "drifted out of voice" in context.build_messages(cid, sid)[-1]["content"]
    assert reads == ["winifred"]


def test_read_record_agrees_with_the_single_field_readers():
    """`read`/`judged_anchor` stay as the one-field convenience calls, so the
    snapshot must not drift away from what they return."""
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        croot = Path(d)
        voice_drift.write(croot, "winifred", "She hedged.", "abc123")
        assert voice_drift.read_record(croot, "winifred") == {
            "note": voice_drift.read(croot, "winifred"),
            "anchor": voice_drift.judged_anchor(croot, "winifred")}
        assert voice_drift.read_record(croot, "mara") == {"note": "", "anchor": ""}


def test_a_corrective_is_suppressed_when_the_name_is_not_unique(monkeypatch, tmp_path):
    """A corrective addresses the model BY NAME, so an ambiguous name makes it an
    instruction the model can apply to the wrong character. absorb's clash guard
    cannot cover this: a flag committed while the name was unique is consumed by
    every later generation, and a same-named actor can join with no absorb in
    between to re-examine it."""
    wid, cid, sid = _voice_scene(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    voice_drift.write(croot, "winifred", "She hedged.")
    assert "drifted out of voice" in context.build_messages(cid, sid)[-1]["content"]

    # a second NPC joins, wearing the same locked card name
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Mara", "default", characters.blank_card("Mara"))
    card = characters.read_card(wroot, "mara", "default")
    card["data"]["name"] = "Winifred"
    characters.update_version(wroot, "mara", "default", card)
    ap.appear(cid, sid, "characters", "mara", "default", "npc")

    assert "drifted out of voice" not in context.build_messages(cid, sid)[-1]["content"]


def test_a_corrective_for_a_reserved_label_is_suppressed(monkeypatch, tmp_path):
    """"You" and "Grimoire" are what the transcript calls the user's lines and
    unstamped narration. A rename AFTER the flag was committed never passes
    absorb's guard again, so the same reserved labels are seeded here."""
    _wid, cid, sid = _voice_scene(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    voice_drift.write(croot, "winifred", "She hedged.")
    assert "drifted out of voice" in context.build_messages(cid, sid)[-1]["content"]

    aroot = ap.locked_actor_root(cid)
    card = characters.read_card(aroot, "winifred", "default")
    card["data"]["name"] = "You"
    characters.update_version(aroot, "winifred", "default", card)
    assert "drifted out of voice" not in context.build_messages(cid, sid)[-1]["content"]


def test_a_corrective_for_a_prefix_ambiguous_name_is_suppressed(monkeypatch, tmp_path):
    """Whole-name comparison is not the rule. "Winifred Vance" and "Winifred
    Vale" are distinct strings, but neither owns the label "Winifred" — and
    `scenes.match_name` already treats that prefix as ambiguous."""
    wid, cid, sid = _voice_scene(monkeypatch, tmp_path)
    croot, aroot = campaigns.campaign_root(cid), ap.locked_actor_root(cid)
    card = characters.read_card(aroot, "winifred", "default")
    card["data"]["name"] = "Winifred Vance"
    characters.update_version(aroot, "winifred", "default", card)
    voice_drift.write(croot, "winifred", "She hedged.")
    assert "Winifred Vance has drifted" in context.build_messages(cid, sid)[-1]["content"]

    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Mara", "default", characters.blank_card("Mara"))
    mcard = characters.read_card(wroot, "mara", "default")
    mcard["data"]["name"] = "Winifred Vale"        # shares the "Winifred" label
    characters.update_version(wroot, "mara", "default", mcard)
    ap.appear(cid, sid, "characters", "mara", "default", "npc")

    assert "drifted out of voice" not in context.build_messages(cid, sid)[-1]["content"]


def test_reformatting_the_anchor_body_keeps_a_committed_flag_live(monkeypatch, tmp_path):
    """End to end for the fingerprint's whitespace rule: rewrapping the anchor
    must not silently retire the corrective judged against it."""
    wid, cid, sid = _voice_scene(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    rec = voice_anchors.read_record(wroot, "winifred")
    voice_drift.write(campaigns.campaign_root(cid), "winifred", "She hedged.",
                      voice_drift.anchor_fingerprint(rec["text"], rec["id"]))
    assert "drifted out of voice" in context.build_messages(cid, sid)[-1]["content"]

    voice_anchors.write(wroot, "winifred", rec["text"].replace(". ", ".\n\n"))
    assert "drifted out of voice" in context.build_messages(cid, sid)[-1]["content"]


def test_a_corrective_is_suppressed_when_the_card_has_no_name(monkeypatch, tmp_path):
    """`scene_cast` substitutes the actor id for a card carrying no usable name,
    and a slug is not something the model can match: the NPC card in front of it
    has no `name` at all, so a corrective addressed to the slug is an
    instruction about nobody."""
    _wid, cid, sid = _voice_scene(monkeypatch, tmp_path)
    voice_drift.write(campaigns.campaign_root(cid), "winifred", "She hedged.")
    assert "drifted out of voice" in context.build_messages(cid, sid)[-1]["content"]

    aroot = ap.locked_actor_root(cid)
    card = characters.read_card(aroot, "winifred", "default")
    card["data"].pop("name", None)
    characters.update_version(aroot, "winifred", "default", card)
    assert [a["name"] for a in ap.scene_cast(cid, sid) if a["id"] == "winifred"] == ["winifred"]

    assert "drifted out of voice" not in context.build_messages(cid, sid)[-1]["content"]


def test_indentation_is_measured_in_columns_not_characters(monkeypatch, tmp_path):
    """Markdown nests by column, and a tab is one character but four columns. A
    child bullet written with a tab counted as shallower than its space-indented
    parent, so the parent naming her was popped and the subjectless child was
    left ungoverned."""
    from grimoire.store import playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    playstate.write_state(croot, ids["Seraphine Vale"], playstate.compose_body(
        "Wary.", "", "Notes:\n  - Winifred\n\t- is hiding the ledger"))
    section = _state_section(cid, sid)
    assert "hiding the ledger" not in section


def test_a_name_soft_wrapped_across_a_line_break_is_still_matched(monkeypatch, tmp_path):
    """An epithet-shaped name yields no short alias, so the full form is the
    only thing that can match her — and a multi-word form is only ever found
    within one line. Wrapped across the break it appeared in neither, and the
    whole private suspicion survived into the prompt."""
    from grimoire.store import appearances, characters, playstate
    cid, sid, croot, ids = _two_npc_scene(monkeypatch, tmp_path)
    # An epithet-shaped name: `_forms` derives no short alias from it, so the
    # whole string is the only form she has.
    epithet = "The Woman on the Pier"
    wid = characters.create_character(croot, epithet, "main",
                                      characters.blank_card(epithet))[0]
    appearances.appear(cid, sid, "characters", wid, "main", "npc")
    for body in (
        "The Woman on the\nPier is hiding the ledger.",
        # Under markdown the continuation carries its own indent, and joining
        # the raw lines put three spaces where the form has one.
        "- The Woman on the\n  Pier is hiding the ledger.",
        # A quoted continuation puts a marker in the middle of the name.
        "> The Woman on the\n> Pier is hiding the ledger.",
    ):
        playstate.write_state(croot, ids["Seraphine Vale"],
                              playstate.compose_body("Wary.", "", body))
        assert "hiding the ledger" not in _state_section(cid, sid), body


# ---------------------------------------------------------- section identity

def test_section_ids_are_unique(monkeypatch, tmp_path):
    """The layout keys off identity, and a label cannot supply one: labels are
    user-editable from #29 onward, so two rows may legitimately share a string."""
    from grimoire.store.context import assemble
    ids = [s.id for s in assemble.SECTIONS]
    assert len(ids) == len(set(ids))
    assert all(i and i.replace("_", "").isalnum() for i in ids)


def test_every_breakdown_row_carries_an_id(monkeypatch, tmp_path):
    """Every row, not just the section ones: `ContextBreakdown` keys on `id`, so
    a row without one collides with the next row that also lacks it."""
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "Seraphine", "default",
                                _npc_card("Seraphine", description="keeper"))
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    scenes.append_message(cid, sid, "user", "hello")
    rows = context.context_sections(cid, sid)
    ids = [r["id"] for r in rows]
    assert all(ids) and len(ids) == len(set(ids))
    assert "character_descriptions" in ids and "history" in ids
