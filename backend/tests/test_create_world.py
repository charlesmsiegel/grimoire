import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import create_world

from grimoire.store import (
    calendars,
    campaigns,
    characters,
    entities,
    greetings,
    pcs,
    tags,
    voice_anchors,
    worlds,
)


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


def _plan(**over) -> dict:
    plan = {
        "world": "Saltmarch",
        "calendar": {"provider": "gregorian", "region": "US"},
        "tags": ["Outsider", "Guild Member"],
        "characters": [
            {"name": "Seraphine", "description": "The harbourmaster.",
             "personality": "dry, exacting", "voice_anchor": "Short sentences."},
            {"name": "Mara", "description": "A tide-reader."},
        ],
        "pcs": [{"name": "Winifred", "tags": ["Outsider"], "pronouns": "she/her",
                 "summary": "A stranger off the packet boat."}],
        "locations": [
            {"name": "The Tide Hall", "body": "Where the guild meets.",
             "keys": ["Tide Hall", "hall"], "fields": {"persistence": "0.5"}},
        ],
        "lore": [
            {"name": "The Drowned Bell", "body": "Rings before a storm.", "keys": ["bell"]},
            {"name": "Seraphine's Debt", "body": "She owes the guild.",
             "owners": ["characters:Seraphine"], "secrecy": "secret"},
        ],
        "groups": [
            {"name": "The Tide Guild", "body": "Reads the water for pay.",
             "keys": ["Tide Guild", "guild"],
             "fields": {"leader": "characters:Seraphine",
                        "headquarters": "locations:The Tide Hall"}},
        ],
        "items": [
            {"name": "Seraphine's Lantern", "body": "Brass, starred glass.",
             "owners": ["characters:Seraphine"],
             "fields": {"holder": "groups:The Tide Guild"}},
        ],
        "greetings": [
            {"name": "Off the Packet Boat", "character": "Seraphine",
             "body": "{{char}} looks {{user}} over at the quay.",
             "location": "The Tide Hall", "leads_to": ["The Guild's Offer"]},
            {"name": "The Guild's Offer", "character": "Mara", "present": ["Mara", "Seraphine"],
             "body": "{{char}} slides a contract across the table.",
             "requires_tags": ["Outsider"]},
        ],
    }
    plan.update(over)
    return plan


def _apply(plan, **kw) -> dict:
    return create_world.apply_plan(plan, **kw)


def _status(result, kind) -> dict:
    return {r["name"]: r["status"] for r in result["records"] if r["kind"] == kind}


def test_apply_builds_every_kind_through_the_store(home):
    result = _apply(_plan())
    wid = result["world"]
    root = worlds.world_root(wid)
    assert worlds.world_name(wid) == "Saltmarch"

    assert tags.read_tags(root) == {"outsider": "Outsider", "guild-member": "Guild Member"}
    card = characters.read_card(root, "seraphine", characters.default_version(root, "seraphine"))
    assert card["data"]["description"] == "The harbourmaster."
    assert card["data"]["personality"] == "dry, exacting"
    assert voice_anchors.read(root, "seraphine") == "Short sentences."

    pc = pcs.read_pc(root, "winifred")
    assert pc["meta"]["tags"] == ["outsider"]
    assert pcs.read_persona(root, "winifred", pc["meta"]["default_version"])["pronouns"] == "she/her"

    hall = entities.read_entity(root, "locations", "the-tide-hall")
    assert hall["meta"]["keys"] == "Tide Hall, hall"
    assert hall["meta"]["persistence"] == "0.5"
    debt = entities.read_entity(root, "lore", "seraphine-s-debt")
    assert debt["meta"]["owners"] == "characters:seraphine"
    assert debt["meta"]["secrecy"] == "secret"
    guild = entities.read_entity(root, "groups", "the-tide-guild")
    assert guild["meta"]["leader"] == "characters:seraphine"
    assert guild["meta"]["headquarters"] == "locations:the-tide-hall"
    lantern = entities.read_entity(root, "items", "seraphine-s-lantern")
    assert lantern["meta"]["holder"] == "groups:the-tide-guild"

    g = greetings.read_greeting(root, "off-the-packet-boat")
    assert g["meta"]["character"] == "seraphine"
    assert g["meta"]["location"] == "the-tide-hall"
    # {{char}} is baked at write time by the store, {{user}} is left alone
    assert g["body"].strip() == "Seraphine looks {{user}} over at the quay."
    offer = greetings.read_greeting(root, "the-guild-s-offer")
    assert offer["meta"]["present"] == ["mara", "seraphine"]
    assert offer["meta"]["requires_tags"] == ["outsider"]
    assert greetings.read_plotmap(root)["off-the-packet-boat"]["leads_to"] == ["the-guild-s-offer"]

    cal = calendars.read_calendar(root)
    assert cal["primary"]["provider"] == "gregorian"
    assert cal["confirmed"] is True


def test_apply_is_idempotent(home):
    first = _apply(_plan())
    root = worlds.world_root(first["world"])
    before = {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
    second = _apply(_plan())
    assert second["world"] == first["world"]
    after = {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
    assert after == before
    statuses = {r["status"] for r in second["records"] if r["kind"] != "world"}
    assert statuses == {"unchanged"}
    assert len(worlds.list_worlds()) == 1


def test_reapply_updates_by_name_and_leaves_omitted_keys(home):
    first = _apply(_plan())
    root = worlds.world_root(first["world"])
    plan = {"world": "Saltmarch",
            "lore": [{"name": "The Drowned Bell", "body": "Rings twice before a storm."}]}
    result = _apply(plan)
    assert _status(result, "lore") == {"The Drowned Bell": "updated"}
    bell = entities.read_entity(root, "lore", "the-drowned-bell")
    assert bell["body"].strip() == "Rings twice before a storm."
    assert bell["meta"]["keys"] == "bell"   # omitted from the plan, so untouched
    assert len(entities.entity_ids(root, "lore")) == 2


def test_new_record_named_like_an_existing_id_is_a_new_record(home):
    first = _apply(_plan())
    root = worlds.world_root(first["world"])
    # "the-drowned-bell" is an id on disk, but nothing is NAMED that
    _apply({"world": "Saltmarch", "lore": [{"name": "the-drowned-bell", "body": "x"}]})
    assert len(entities.entity_ids(root, "lore")) == 3


def test_references_accept_ids_as_well_as_names(home):
    first = _apply(_plan())
    root = worlds.world_root(first["world"])
    _apply({"world": "Saltmarch",
            "items": [{"name": "Mara's Chart", "owners": ["characters:mara"]}]})
    chart = entities.read_entity(root, "items", "mara-s-chart")
    assert chart["meta"]["owners"] == "characters:mara"


def test_invalid_plan_writes_nothing(home):
    plan = _plan(greetings=[{"name": "Lost", "character": "Nobody", "requires_tags": ["Ghost"]}])
    with pytest.raises(create_world.PlanError) as exc:
        _apply(plan)
    text = "\n".join(exc.value.problems)
    assert "no characters record named 'Nobody'" in text
    assert "no tags record named 'Ghost'" in text
    assert worlds.list_worlds() == []


@pytest.mark.parametrize("over, needle", [
    ({"lore": [{"name": "X", "owners": ["people:Seraphine"]}]}, "must be <kind>:<name>"),
    ({"lore": [{"name": "X", "secrecy": "sercet"}]}, "secrecy must be one of"),
    ({"lore": [{"name": "X", "fields": {"climate": "x"}}]}, "lore has no fields ['climate']"),
    ({"locations": [{"name": "X", "fields": {"persistence": "2"}}]}, "invalid value"),
    ({"groups": [{"name": "X", "fields": {"leader": "locations:The Tide Hall"}}]},
     "must be <kind>:<name>"),
    ({"lore": [{"name": "X"}, {"name": "x"}]}, "listed twice"),
    ({"lore": [{"name": "X", "colour": "red"}]}, "unknown keys ['colour']"),
    ({"pcs": [{"name": "Seraphine"}]}, "a pcs entry has the same name"),
    ({"greetings": [{"name": "A", "leads_to": ["B"]}, {"name": "B", "leads_to": ["A"]}]},
     "cycle"),
    ({"greetings": [{"name": "A", "predecessor_join": "some"}]}, "predecessor_join"),
    ({"greetings": [{"name": "A", "pcless": "yes"}]}, "pcless must be true or false"),
    ({"calendar": {"provider": "nonesuch"}}, "unknown calendar provider"),
    ({"module": "nonesuch"}, "no module 'nonesuch'"),
    ({"planets": []}, "unknown top-level keys"),
])
def test_validation_refuses(home, over, needle):
    problems = create_world.validate_plan(_plan(**over), None, None)
    assert any(needle in p for p in problems), problems


def test_actor_name_taken_in_a_campaign_is_refused(home):
    wid = _apply(_plan())["world"]
    cid = campaigns.create_campaign("Night Tide", wid)
    from grimoire.store import overlay
    overlay.create_character(cid, "Corvin")
    problems = create_world.validate_plan(
        {"world": "Saltmarch", "characters": [{"name": "Corvin"}]},
        worlds.world_root(wid), wid)
    assert any("already used" in p for p in problems), problems


def test_module_binds_on_a_fresh_world(home):
    wid = _apply(_plan(module="pool-basic"))["world"]
    assert create_world._world_module(wid) == "pool-basic"


def test_module_change_refused_once_campaigns_exist(home):
    wid = _apply(_plan())["world"]
    campaigns.create_campaign("Night Tide", wid)
    with pytest.raises(create_world.PlanError) as exc:
        _apply(_plan(module="pool-basic"))
    assert any("already has campaigns" in p for p in exc.value.problems)


def test_world_id_targets_an_existing_world(home):
    wid = worlds.create_world("Realm")
    result = _apply({"lore": [{"name": "The Drowned Bell", "body": "x"}]}, world_id=wid)
    assert result["world"] == wid
    assert entities.entity_ids(worlds.world_root(wid), "lore") == ["the-drowned-bell"]


def test_check_passes_a_well_formed_world(home):
    wid = _apply(_plan())["world"]
    result = create_world.check_world(wid)
    assert result["ok"], result["errors"]
    warnings = "\n".join(result["warnings"])
    assert "characters/mara: no voice anchor" in warnings
    assert "seraphine" not in [w.split(":")[0] for w in result["warnings"]
                               if "voice anchor" in w]


def test_check_flags_a_world_nobody_can_start(home):
    plan = _plan(pcs=[], greetings=[
        {"name": "Gated", "character": "Mara", "requires_tags": ["Guild Member"]}])
    wid = _apply(plan)["world"]
    result = create_world.check_world(wid)
    assert not result["ok"]
    assert any("no greeting can open a scene" in e for e in result["errors"])


def test_check_warns_when_only_tagged_pcs_can_start(home):
    plan = _plan(greetings=[
        {"name": "Gated", "character": "Mara", "requires_tags": ["Outsider"]}])
    wid = _apply(plan)["world"]
    result = create_world.check_world(wid)
    assert result["ok"], result["errors"]
    assert any("no greeting is startable without tags" in w for w in result["warnings"])


def test_check_reports_activation_choices(home):
    plan = _plan(locations=[{"name": "Quiet Cove", "body": "A cove."}],
                 lore=[{"name": "Common Knowledge", "body": "Everyone knows."}],
                 groups=[], items=[], greetings=[])
    wid = _apply(plan)["world"]
    warnings = "\n".join(create_world.check_world(wid)["warnings"])
    assert "locations/quiet-cove: no keys -- reaches the prompt only as the current setting" \
        in warnings
    assert "lore/common-knowledge: no keys and no owners -- always on" in warnings


def test_check_catches_hand_broken_references(home):
    wid = _apply(_plan())["world"]
    root = worlds.world_root(wid)
    entities.update_entity(root, "lore", "the-drowned-bell", owners="characters:ghost")
    greetings.set_edges(root, "the-guild-s-offer", leads_to=["off-the-packet-boat"])
    errors = "\n".join(create_world.check_world(wid)["errors"])
    assert "lore/the-drowned-bell: owner characters:ghost does not exist" in errors
    assert "leads_to cycle" in errors


def test_cli_round_trip(home, tmp_path, capsys):
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan()), encoding="utf-8")
    assert create_world.main(["apply", "--plan", str(plan_path), "--dry-run"]) == 0
    assert worlds.list_worlds() == []
    capsys.readouterr()
    assert create_world.main(["apply", "--plan", str(plan_path)]) == 0
    wid = json.loads(capsys.readouterr().out)["world"]
    assert create_world.main(["check", "--world", wid]) == 0
    capsys.readouterr()
    assert create_world.main(["summary", "--world", wid]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert {c["id"] for c in summary["characters"]} == {"seraphine", "mara"}
    assert summary["plotmap"]["off-the-packet-boat"]["leads_to"] == ["the-guild-s-offer"]

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"world": "Saltmarch", "lore": [{"name": "X", "owners": "nope"}]}),
                   encoding="utf-8")
    assert create_world.main(["apply", "--plan", str(bad)]) == 1


def test_the_skill_example_plan_applies_cleanly(home):
    """The SKILL.md example is the format's documentation, so it is held to
    the CLI: the first ```json block there must apply and pass `check`."""
    skill = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "create-world" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    block = text.split("```json\n", 1)[1].split("```", 1)[0]
    plan = json.loads(block)
    assert create_world.validate_plan(plan, None, None) == []
    wid = _apply(plan)["world"]
    assert create_world.check_world(wid)["ok"]
