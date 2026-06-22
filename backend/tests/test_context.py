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
