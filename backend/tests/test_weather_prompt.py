from grimoire import prompts
from grimoire.store import campaigns, context, entities, scenes, worlds


def scene_at(monkeypatch, tmp_path, location=True, when="2026-06-14T09:00"):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Chronicle", "realm")
    sid = scenes.create_scene(cid, "Arrival")
    if location:
        lid = entities.create_entity(campaigns.campaign_root(cid), "locations", "Saltmarch Docks")
        scenes.set_location(cid, sid, lid)
    if when:
        # set_datetime renames the scene file on first set, so re-read the id.
        sid = scenes.set_datetime(cid, sid, when).get("id", sid)
    return cid, sid


def test_section_renders_a_sentence():
    out = prompts.render("scene/sections/weather.j2",
                         weather={"condition": "overcast", "temperature": "cold", "wind": "breeze"})
    assert "overcast" in out and "cold" in out and "breeze" in out


def test_section_is_empty_without_weather():
    assert prompts.render("scene/sections/weather.j2", weather=None).strip() == ""


def test_assemble_carries_weather(monkeypatch, tmp_path):
    cid, sid = scene_at(monkeypatch, tmp_path)
    data = context._assemble(cid, sid)["data"]
    assert set(data["weather"]) == {"condition", "temperature", "wind", "notes"}


def test_assemble_omits_weather_without_a_location(monkeypatch, tmp_path):
    cid, sid = scene_at(monkeypatch, tmp_path, location=False)
    assert context._assemble(cid, sid)["data"]["weather"] is None


def test_assemble_omits_weather_without_a_moment(monkeypatch, tmp_path):
    cid, sid = scene_at(monkeypatch, tmp_path, when=None)
    assert context._assemble(cid, sid)["data"]["weather"] is None


def test_weather_reaches_the_system_prompt(monkeypatch, tmp_path):
    # _SECTIONS is only the token-breakdown view; the prompt itself comes from
    # system.j2's hard-coded include chain. Registering in one and not the
    # other yields a weather line in the breakdown and in no actual prompt.
    cid, sid = scene_at(monkeypatch, tmp_path)
    data = context._assemble(cid, sid)["data"]
    rendered = prompts.render("scene/system.j2", **data)
    assert data["weather"]["condition"] in rendered


def test_weather_is_in_the_token_breakdown(monkeypatch, tmp_path):
    cid, sid = scene_at(monkeypatch, tmp_path)
    assert any(s["label"] == "Weather" for s in context.context_sections(cid, sid))


def test_no_weather_line_appears_when_there_is_none(monkeypatch, tmp_path):
    cid, sid = scene_at(monkeypatch, tmp_path, location=False)
    data = context._assemble(cid, sid)["data"]
    assert "Weather:" not in prompts.render("scene/system.j2", **data)
