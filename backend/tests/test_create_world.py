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
            {"name": "The Counting House", "body": "Where the guild meets.",
             "keys": ["Counting House", "ledger room"], "fields": {"persistence": "0.5"}},
        ],
        "lore": [
            {"name": "The Salt Pact", "body": "An old agreement.", "keys": ["pact"]},
            {"name": "The Ledger", "body": "She owes the guild.",
             "owners": ["characters:Seraphine"], "secrecy": "secret"},
        ],
        "groups": [
            {"name": "Salt Circle", "body": "Reads the water for pay.",
             "keys": ["Salt Circle", "guild"],
             "fields": {"leader": "characters:Seraphine",
                        "headquarters": "locations:The Counting House"}},
        ],
        "items": [
            {"name": "Salt Knife", "body": "Brass, starred glass.",
             "owners": ["characters:Seraphine"],
             "fields": {"holder": "groups:Salt Circle"}},
        ],
        "greetings": [
            {"name": "Saltmarch Eve", "character": "Seraphine",
             "body": "{{char}} looks {{user}} over at the quay.",
             "location": "The Counting House", "leads_to": ["The Reckoning"]},
            {"name": "The Reckoning", "character": "Mara", "present": ["Mara", "Seraphine"],
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

    hall = entities.read_entity(root, "locations", "the-counting-house")
    assert hall["meta"]["keys"] == "Counting House, ledger room"
    assert hall["meta"]["persistence"] == "0.5"
    debt = entities.read_entity(root, "lore", "the-ledger")
    assert debt["meta"]["owners"] == "characters:seraphine"
    assert debt["meta"]["secrecy"] == "secret"
    guild = entities.read_entity(root, "groups", "salt-circle")
    assert guild["meta"]["leader"] == "characters:seraphine"
    assert guild["meta"]["headquarters"] == "locations:the-counting-house"
    lantern = entities.read_entity(root, "items", "salt-knife")
    assert lantern["meta"]["holder"] == "groups:salt-circle"

    g = greetings.read_greeting(root, "saltmarch-eve")
    assert g["meta"]["character"] == "seraphine"
    assert g["meta"]["location"] == "the-counting-house"
    # {{char}} is baked at write time by the store, {{user}} is left alone
    assert g["body"].strip() == "Seraphine looks {{user}} over at the quay."
    offer = greetings.read_greeting(root, "the-reckoning")
    assert offer["meta"]["present"] == ["mara", "seraphine"]
    assert offer["meta"]["requires_tags"] == ["outsider"]
    assert greetings.read_plotmap(root)["saltmarch-eve"]["leads_to"] == ["the-reckoning"]

    cal = calendars.read_calendar(root)
    assert cal["primary"]["provider"] == "gregorian"
    assert cal["confirmed"] is True


def test_apply_is_idempotent(home):
    first = _apply(_plan())
    root = worlds.world_root(first["world"])
    before = {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
    second = _apply(_plan(), world_id=first["world"])
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
            "lore": [{"name": "The Salt Pact", "body": "An older agreement."}]}
    result = _apply(plan, world_id=first["world"])
    assert _status(result, "lore") == {"The Salt Pact": "updated"}
    bell = entities.read_entity(root, "lore", "the-salt-pact")
    assert bell["body"].strip() == "An older agreement."
    assert bell["meta"]["keys"] == "pact"   # omitted from the plan, so untouched
    assert len(entities.entity_ids(root, "lore")) == 2


def test_new_record_named_like_an_existing_id_is_a_new_record(home):
    first = _apply(_plan())
    root = worlds.world_root(first["world"])
    # "the-salt-pact" is an id on disk, but nothing is NAMED that
    _apply({"lore": [{"name": "the-salt-pact", "body": "x"}]}, world_id=first["world"])
    assert len(entities.entity_ids(root, "lore")) == 3


def test_references_accept_ids_as_well_as_names(home):
    first = _apply(_plan())
    root = worlds.world_root(first["world"])
    _apply({"items": [{"name": "Moon Disc", "owners": ["characters:mara"]}]},
           world_id=first["world"])
    chart = entities.read_entity(root, "items", "moon-disc")
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
    ({"lore": [{"name": "X", "owners": ["groups:Salt Circle"]}]}, "must be <kind>:<name>"),
    ({"lore": [{"name": "X", "keys": 5}]}, "keys must be a list of names"),
    ({"lore": [{"name": "X", "keys": ["Salt, March"]}]}, "has a comma"),
    ({"lore": [{"name": "A\nB"}]}, "a name must be one line"),
    ({"greetings": [{"name": "A", "present": 5}]}, "present must be a list of names"),
    ({"pcs": [{"name": "Old Bram", "tags": 5}]}, "tags must be a list of names"),
    ({"items": [{"name": "X", "fields": {"holder": 5}}]}, "must be a reference"),
    ({"pcs": [{"name": "Old Bram", "summary": "one\nbirthdate: 1"}]}, "summary must be one line"),
    ({"locations": [{"name": "X", "fields": {"weather_zone": "a\nb"}}]},
     "weather_zone must be one line"),
    ({"lore": [{"name": "X", "keys": ["a\nb"]}]}, "must be one line"),
    ({"greetings": [{"name": "A", "phase": "a\nb"}]}, "phase must be one line"),
    ({"greetings": [{"name": "A", "character": 0}]}, "character must be a string"),
    ({"greetings": [{"name": "A", "location": {"x": 1}}]}, "location must be a string"),
    ({"world": "Salt\nmarch"}, "world: a name must be one line"),
    ({"lore": [{"name": "X", "keys": "pact\nsecrecy: secret"}]}, "must be one line"),
    ({"greetings": {}}, "greetings: must be a list"),
    ({"lore": ""}, "lore: must be a list"),
    ({"tags": {}}, "tags: must be a list"),
    ({"calendar": {"provider": "hebrew", "regoin": "IL"}}, "unknown keys ['regoin']"),
    ({"calendar": {"primary": {"provider": "gregorian"}, "secundary": None}},
     "unknown keys ['secundary']"),
    ({"module": 5}, "module: must be a module id"),
    ({"lore": [{"name": "X", "secrecy": "sercet"}]}, "secrecy must be one of"),
    ({"lore": [{"name": "X", "fields": {"climate": "x"}}]}, "lore has no fields ['climate']"),
    ({"locations": [{"name": "X", "fields": {"persistence": "2"}}]}, "invalid value"),
    ({"groups": [{"name": "X", "fields": {"leader": "locations:The Counting House"}}]},
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
    cid = campaigns.create_campaign("Silver Oath", wid)
    from grimoire.store import overlay
    overlay.create_character(cid, "Old Bram")
    problems = create_world.validate_plan(
        {"world": "Saltmarch", "characters": [{"name": "Old Bram"}]},
        worlds.world_root(wid), wid)
    assert any("already used" in p for p in problems), problems


def test_module_binds_on_a_fresh_world(home):
    wid = _apply(_plan(module="pool-basic"))["world"]
    assert create_world._world_module(wid) == "pool-basic"


def test_module_change_refused_once_campaigns_exist(home):
    wid = _apply(_plan())["world"]
    campaigns.create_campaign("Silver Oath", wid)
    with pytest.raises(create_world.PlanError) as exc:
        _apply(_plan(module="pool-basic"), world_id=wid)
    assert any("already has campaigns" in p for p in exc.value.problems)


def test_world_id_targets_an_existing_world(home):
    wid = worlds.create_world("Realm")
    result = _apply({"lore": [{"name": "The Salt Pact", "body": "x"}]}, world_id=wid)
    assert result["world"] == wid
    assert entities.entity_ids(worlds.world_root(wid), "lore") == ["the-salt-pact"]


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
    assert any("no onscreen greeting is startable without tags" in w for w in result["warnings"])


def test_check_reports_activation_choices(home):
    plan = _plan(locations=[{"name": "The Docks", "body": "A cove."}],
                 lore=[{"name": "The Pact", "body": "Everyone knows."}],
                 groups=[], items=[], greetings=[])
    wid = _apply(plan)["world"]
    warnings = "\n".join(create_world.check_world(wid)["warnings"])
    assert "locations/the-docks: no keys -- reaches the prompt only as the current setting" \
        in warnings
    assert "lore/the-pact: no keys and no owners -- always on" in warnings


def test_check_catches_hand_broken_references(home):
    wid = _apply(_plan())["world"]
    root = worlds.world_root(wid)
    entities.update_entity(root, "lore", "the-salt-pact", owners="characters:ghost")
    greetings.set_edges(root, "the-reckoning", leads_to=["saltmarch-eve"])
    errors = "\n".join(create_world.check_world(wid)["errors"])
    assert "lore/the-salt-pact: owner characters:ghost does not exist" in errors
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
    assert summary["plotmap"]["saltmarch-eve"]["leads_to"] == ["the-reckoning"]

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


def test_a_new_plan_never_merges_into_an_existing_world_by_name(home):
    wid = _apply(_plan())["world"]
    with pytest.raises(create_world.PlanError) as exc:
        _apply({"world": "Saltmarch", "lore": [{"name": "The Salt Pact", "body": "clobber"}]})
    assert any(f"--world-id {wid}" in p for p in exc.value.problems)
    body = entities.read_entity(worlds.world_root(wid), "lore", "the-salt-pact")["body"]
    assert body.strip() == "An old agreement."


def test_a_reference_that_is_one_name_and_another_id_is_refused(home):
    wid = _apply(_plan())["world"]
    root = worlds.world_root(wid)
    entities.update_entity(root, "locations", "the-counting-house", name="Great Hall")
    problems = create_world.validate_plan(
        {"locations": [{"name": "the-counting-house"}],
         "greetings": [{"name": "G", "location": "the-counting-house"}]}, root, wid)
    assert any("could mean more than one" in p for p in problems), problems


def test_a_cycle_closed_across_two_applies_is_refused(home):
    wid = _apply(_plan())["world"]
    root = worlds.world_root(wid)
    problems = create_world.validate_plan(
        {"greetings": [{"name": "The Reckoning", "leads_to": ["Saltmarch Eve"]}]}, root, wid)
    assert any("cycle" in p for p in problems), problems
    problems = create_world.validate_plan(
        {"greetings": [{"name": "The Reckoning", "leads_to": ["saltmarch-eve"]}]}, root, wid)
    assert any("cycle" in p for p in problems), problems


def test_reapply_keeps_a_greeting_version_chosen_in_the_app(home):
    wid = _apply(_plan())["world"]
    root = worlds.world_root(wid)
    card = characters.blank_card("Young Mara")
    vid = characters.create_version(root, "mara", "young", card)
    greetings.update_greeting(root, "the-reckoning", character="mara", version=vid)
    _apply(_plan(), world_id=wid)
    assert greetings.read_greeting(root, "the-reckoning")["meta"]["version"] == vid


def test_null_field_clears_rather_than_storing_none(home):
    wid = _apply(_plan())["world"]
    root = worlds.world_root(wid)
    _apply({"locations": [{"name": "The Counting House", "fields": {"persistence": None}}]},
           world_id=wid)
    assert "persistence" not in entities.read_entity(root, "locations", "the-counting-house")["meta"]


def test_duplicate_references_are_written_once(home):
    wid = _apply(_plan())["world"]
    root = worlds.world_root(wid)
    _apply({"greetings": [{"name": "Saltmarch Eve", "leads_to": ["The Reckoning", "the reckoning"]}]},
           world_id=wid)
    assert greetings.read_plotmap(root)["saltmarch-eve"]["leads_to"] == ["the-reckoning"]


def test_check_warns_about_an_owner_that_is_never_present(home):
    wid = _apply(_plan())["world"]
    root = worlds.world_root(wid)
    entities.update_entity(root, "lore", "the-salt-pact", owners="groups:salt-circle")
    warnings = "\n".join(create_world.check_world(wid)["warnings"])
    assert "owner groups:salt-circle can never be present" in warnings


def test_an_invalid_user_module_is_refused_before_any_write(home):
    from grimoire.store import modules
    mid = modules.create_module("Broken Pack")
    (home / "modules" / mid / "sheets.json").write_text("{not json", encoding="utf-8")
    assert modules.load_pack(mid)["errors"]
    problems = create_world.validate_plan(_plan(module=mid), None, None)
    assert any(p.startswith(f"module {mid}:") for p in problems), problems


def test_an_offscreen_opener_is_no_opening_for_a_pc(home):
    plan = _plan(greetings=[{"name": "Saltmarch Eve", "character": "Mara", "pcless": True}])
    wid = _apply(plan)["world"]
    result = create_world.check_world(wid)
    assert result["ok"], result["errors"]
    warnings = "\n".join(result["warnings"])
    assert "pcs/winifred: can start no onscreen greeting" in warnings
    assert "no onscreen greeting is startable: every opening" in warnings


@pytest.mark.parametrize("plan", [[], "Saltmarch", 5])
def test_a_plan_that_is_not_an_object_is_a_plan_error(home, tmp_path, capsys, plan):
    with pytest.raises(create_world.PlanError):
        _apply(plan)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    assert create_world.main(["apply", "--plan", str(path), "--dry-run"]) == 1
    assert "must be a JSON object" in capsys.readouterr().out


def test_an_empty_module_clears_the_binding(home):
    wid = _apply(_plan(module="pool-basic"))["world"]
    _apply({"module": ""}, world_id=wid)
    assert create_world._world_module(wid) == ""


def test_check_reports_a_malformed_plotmap_instead_of_raising(home):
    wid = _apply(_plan())["world"]
    root = worlds.world_root(wid)
    (root / "plotmap.json").write_text(json.dumps({"saltmarch-eve": []}), encoding="utf-8")
    result = create_world.check_world(wid)
    assert any("saltmarch-eve must map to" in e for e in result["errors"])
    assert create_world.validate_plan({"greetings": [{"name": "X"}]}, root, wid) == []


def test_check_flags_a_character_greeting_with_no_version(home):
    wid = _apply(_plan())["world"]
    root = worlds.world_root(wid)
    path = root / "greetings" / "saltmarch-eve.md"
    path.write_text(path.read_text(encoding="utf-8").replace("version: default", "version: ''"),
                    encoding="utf-8")
    assert greetings.read_greeting(root, "saltmarch-eve")["meta"]["version"] == ""
    errors = "\n".join(create_world.check_world(wid)["errors"])
    assert "greetings/saltmarch-eve: version (none) of seraphine does not exist" in errors
