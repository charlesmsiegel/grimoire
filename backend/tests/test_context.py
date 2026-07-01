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
from grimoire.store import campaigns, characters, entities, pcs, scenes, worlds  # noqa: E402


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


def test_multi_npc_char_token_joined(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Seraphine", "default", _npc_card("Seraphine", description="A"))
    characters.create_character(wroot, "Drowned King", "default", _npc_card("Drowned King", description="B"))
    ap.appear(cid, sid, "characters", "seraphine", "default", "npc")
    ap.appear(cid, sid, "characters", "drowned-king", "default", "npc")
    scenes.append_message(cid, sid, "user", "{{char}} arrives")
    msgs = context.build_messages(cid, sid)
    assert "A" in msgs[0]["content"] and "B" in msgs[0]["content"]
    # scene_cast sorts by (kind, id): 'drowned-king' precedes 'seraphine'
    assert msgs[-1]["content"] == "Drowned King, Seraphine arrives"


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
    # no cast, no always-on entry, key outside depth -> empty context -> no system message.
    sys_msgs = [m for m in context.build_messages(cid, sid) if m["role"] == "system"]
    assert sys_msgs == []


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
    assert context.build_messages(cid, sid) == [{"role": "user", "content": "plain message"}]


def test_character_cast_as_player_uses_persona_not_char(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    characters.create_character(worlds.world_root(wid), "desmond", "default",
                                _npc_card("desmond", description="a tall man", personality="gruff"))
    ap.appear(cid, sid, "characters", "desmond", "default", "player")
    scenes.append_message(cid, sid, "user", "I am {{user}}, not {{char}}")
    msgs = context.build_messages(cid, sid)
    assert "a tall man" in msgs[0]["content"]          # injected as persona, not an NPC block
    # {{user}} resolves to the player; {{char}} stays literal (no NPCs in scene)
    assert msgs[-1]["content"] == "I am desmond, not {{char}}"


def test_substitution_in_card_and_worldinfo(monkeypatch, tmp_path):
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
    assert "Seraphine greets Elara" in sys   # substitution inside card text
    assert "Seraphine knows Elara" in sys     # substitution inside world-info


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
    entities.create_entity(croot, "lore", "Always", "ambient lore", keys="")
    entities.create_entity(croot, "lore", "Salt", "salt lore", keys="salt")
    msgs = context.build_opener_messages(cid, sid, "A storm over the salt marshes for {{user}}.")
    assert msgs[0]["role"] == "system" and msgs[-1]["role"] == "user"
    sys = msgs[0]["content"]
    assert "a scholar" in sys          # player persona present
    assert "ambient lore" in sys       # always-on lore present
    assert "salt lore" in sys          # 'salt' activated by the prompt text
    assert "{{user}}" not in sys       # substituted
    assert msgs[-1]["content"] == "A storm over the salt marshes for Elara."


def test_depth_zero_and_unparseable_fallback(monkeypatch, tmp_path):
    from grimoire.store import config
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "lore", "Salt", "pact lore", keys="pact")
    scenes.append_message(cid, sid, "user", "the pact matters")
    config.write_config(context_scan_depth="0")  # no scan window -> keyword entry not activated
    assert [m for m in context.build_messages(cid, sid) if m["role"] == "system"] == []
    config.write_config(context_scan_depth="abc")  # unparseable -> fallback 8 -> 'pact' activates
    assert "pact lore" in context.build_messages(cid, sid)[0]["content"]


def test_cast_directory_tiers(monkeypatch, tmp_path):
    from grimoire.store import briefs
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)

    # present in this scene (full card)
    characters.create_character(wroot, "Aese", "main", _npc_card("Aese", description="present-desc"))
    # appeared elsewhere in the campaign (paragraph) — needs a brief to be snapshotted
    characters.create_character(wroot, "Myval", "main", _npc_card("Myval", description="m"))
    briefs.write_brief(wroot, "myval", "A raccoongirl rogue.", "Myval prowls the dusk road.",
                       briefs.default_card_hash(wroot, "myval"))
    # world-only with a brief and two versions (sentence + version list)
    characters.create_character(wroot, "Akane", "main", _npc_card("Akane", description="a"))
    characters.create_version(wroot, "akane", "futa", _npc_card("Akane", description="a"))
    briefs.write_brief(wroot, "akane", "An eager doggirl.", "Akane wants to please.",
                       briefs.default_card_hash(wroot, "akane"))
    # world-only WITHOUT a brief (must be skipped)
    characters.create_character(wroot, "Ghost", "main", _npc_card("Ghost", description="g"))

    # Myval appears in a different scene -> roster, not in this scene's cast
    other = scenes.create_scene(cid, "Other")
    ap.appear(cid, other, "characters", "myval", "main", "npc")
    # Aese appears in our scene
    ap.appear(cid, sid, "characters", "aese", "main", "npc")
    scenes.append_message(cid, sid, "user", "hi")

    sys = context.build_messages(cid, sid)[0]["content"]
    assert "present-desc" in sys                                  # tier 1 full card
    assert "Myval: Myval prowls the dusk road." in sys           # tier 2 paragraph
    assert "Akane: An eager doggirl. (available as: futa, main)" in sys  # tier 3 sentence + versions
    assert "Ghost" not in sys                                     # un-briefed world char skipped
    assert "Myval" not in sys.split("## Known to exist")[1]       # roster char not in tier 3


def test_cast_directory_absent_when_no_briefs(monkeypatch, tmp_path):
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
    # keyless location would otherwise be always-on in world-info too; exclude prevents a double-inject
    assert sys.count("A drowned basilica of black salt.") == 1


def test_no_setting_block_when_unset(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "hi")
    msgs = context.build_messages(cid, sid)
    sys = msgs[0]["content"] if msgs and msgs[0]["role"] == "system" else ""
    assert "# Current setting" not in sys


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
    scenes.set_datetime(cid, sid, "2026-12-25")
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
    scenes.set_datetime(cid, sid, "2026-12-25")
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
    system = context._assemble(cid, sid)["system"]
    assert "Story so far" in [label for label, _ in system]
    text = dict(system)["Story so far"]
    assert "They first met." in text and text.startswith("# Story so far")


def test_story_so_far_absent_when_empty(monkeypatch, tmp_path):
    from grimoire.store import campaigns, context, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "Now")
    assert "Story so far" not in [label for label, _ in context._assemble(cid, sid)["system"]]


def test_story_so_far_tolerates_garbled_chronicle(monkeypatch, tmp_path):
    from grimoire.store import campaigns, context, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "Now")
    (campaigns.campaign_root(cid) / "chronicle.json").write_text("{ not valid json", encoding="utf-8")
    labels = [label for label, _ in context._assemble(cid, sid)["system"]]  # must not raise
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
    system = dict(context._assemble(cid, sid)["system"])
    assert "Character state" in system
    assert "Seraphine: Wounded; travels with the party." in system["Character state"]


def test_character_state_absent_when_none(monkeypatch, tmp_path):
    from grimoire.store import campaigns, context, scenes, worlds
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "Now")
    assert "Character state" not in [l for l, _ in context._assemble(cid, sid)["system"]]
