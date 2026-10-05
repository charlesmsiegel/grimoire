"""Regex rule files: one per level (global, world, campaign, connection), how
they read (never raising), validate against what they inherit, and stack."""

import json
import logging

import pytest

from grimoire.store import campaigns, llm_connections, worlds
from grimoire.store.regex import layers, rules


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    conn = llm_connections.create_connection("openrouter", "Seraphine", api_key="k")
    return wid, cid, conn


def _rule(name: str, pattern: str = "a", **extra) -> dict:
    return {"name": name, "pattern": pattern, "replacement": "b", **extra}


def _key(level: str, wid: str, cid: str, conn: str) -> str:
    return {"global": "", "world": wid, "campaign": cid, "connection": conn}[level]


def _names(entries: list[dict]) -> list[str]:
    return [e["rule"]["name"] for e in entries]


@pytest.mark.parametrize("level", layers.LEVELS)
def test_round_trip_each_level(home, level):
    wid, cid, conn = home
    key = _key(level, wid, cid, conn)
    assert layers.read_level(level, key) == {"rules": [], "off": []}
    written = layers.write_level(level, key, {"rules": [_rule("One")]})
    assert written["off"] == []
    assert written["rules"][0]["name"] == "One"
    assert written["rules"][0]["id"].startswith("r-")
    assert layers.read_level(level, key) == written
    assert layers.path(level, key).is_file()


def test_paths_land_where_the_spec_says(home):
    wid, cid, conn = home
    assert layers.path("global").name == "regex.json"
    assert layers.path("world", wid).parent.name == wid
    assert layers.path("campaign", cid).parent.name == cid
    assert layers.path("connection", conn) == llm_connections.regex_path(conn)
    assert layers.path("connection", conn).name == f"{conn}.regex.json"


def test_unparseable_reads_empty_and_logs_once(home, caplog):
    layers.path("global").write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="grimoire.store.regex.layers"):
        assert layers.read_level("global") == {"rules": [], "off": []}
        assert layers.read_level("global") == {"rules": [], "off": []}
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1


def test_missing_file_is_not_news(home, caplog):
    with caplog.at_level(logging.DEBUG, logger="grimoire.store.regex.layers"):
        assert layers.read_level("global") == {"rules": [], "off": []}
    assert not caplog.records


def test_one_bad_rule_dropped_rest_load(home, caplog):
    valid_a = rules.normalise(_rule("A"))
    valid_b = rules.normalise(_rule("B"))
    layers.path("global").write_text(
        json.dumps({"rules": [valid_a, {"pattern": 5}, valid_b]}), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="grimoire.store.regex.layers"):
        doc = layers.read_level("global")
    assert [r["name"] for r in doc["rules"]] == ["A", "B"]
    assert caplog.records, "a dropped rule is logged"


def test_duplicate_id_in_file_drops_second(home):
    one = rules.normalise(_rule("First"))
    two = {**rules.normalise(_rule("Second")), "id": one["id"]}
    layers.path("global").write_text(json.dumps({"rules": [one, two]}), encoding="utf-8")
    assert [r["name"] for r in layers.read_level("global")["rules"]] == ["First"]


def test_a_rewritten_file_is_reread(home):
    layers.write_level("global", "", {"rules": [_rule("Old")]})
    assert layers.read_level("global")["rules"][0]["name"] == "Old"
    layers.write_level("global", "", {"rules": [_rule("Newer")]})
    assert layers.read_level("global")["rules"][0]["name"] == "Newer"


def test_read_hands_back_a_copy(home):
    layers.write_level("global", "", {"rules": [_rule("Keep")]})
    layers.read_level("global")["rules"][0]["name"] = "Changed"
    assert layers.read_level("global")["rules"][0]["name"] == "Keep"


def test_unknown_world_or_campaign_reads_empty(home):
    assert layers.read_level("world", "no-such-world") == {"rules": [], "off": []}
    assert layers.read_level("world", "") == {"rules": [], "off": []}
    assert layers.read_level("campaign", "../x") == {"rules": [], "off": []}
    assert layers.read_level("connection", "../x") == {"rules": [], "off": []}


def _stack(home):
    wid, cid, conn = home
    layers.write_level("connection", conn, {"rules": [_rule("C")]})
    layers.write_level("global", "", {"rules": [_rule("G")]})
    layers.write_level("world", wid, {"rules": [_rule("W")]})
    layers.write_level("campaign", cid, {"rules": [_rule("K")]})
    return wid, cid, conn


def test_effective_order(home):
    _, cid, conn = _stack(home)
    got = layers.effective(cid=cid, connection=conn)
    assert _names(got) == ["C", "G", "W", "K"]
    assert [e["level"] for e in got] == ["connection", "global", "world", "campaign"]
    assert [e["source"] for e in got] == [conn, "", "", ""]
    assert not any(e["off"] for e in got)


def test_effective_without_campaign_or_connection(home):
    _stack(home)
    assert _names(layers.effective(cid=None)) == ["G"]


def test_campaign_off_switches_global_and_connection(home):
    wid, cid, conn = _stack(home)
    g = layers.read_level("global")["rules"][0]["id"]
    c = layers.read_level("connection", conn)["rules"][0]["id"]
    w = layers.read_level("world", wid)["rules"][0]["id"]
    k = layers.read_level("campaign", cid)["rules"][0]
    layers.write_level("campaign", cid, {"rules": [k], "off": [g, c, w]})
    got = layers.effective(cid=cid, connection=conn)
    assert {e["rule"]["name"]: e["off"] for e in got} == {
        "C": True, "G": True, "W": True, "K": False}


def test_world_off_inherited_by_campaign(home):
    wid, cid, conn = _stack(home)
    g = layers.read_level("global")["rules"][0]["id"]
    c = layers.read_level("connection", conn)["rules"][0]["id"]
    layers.write_level("world", wid, {"rules": layers.read_level("world", wid)["rules"],
                                      "off": [g, c]})
    got = layers.effective(cid=cid, connection=conn)
    assert {e["rule"]["name"]: e["off"] for e in got} == {
        "C": True, "G": True, "W": False, "K": False}


def test_inherited_lists_what_the_level_sits_on(home):
    wid, cid, conn = _stack(home)
    assert layers.inherited("global") == []
    assert layers.inherited("connection", conn) == []
    assert _names(layers.inherited("world", wid)) == ["C", "G"]
    assert _names(layers.inherited("campaign", cid)) == ["C", "G", "W"]


def test_inherited_off_comes_from_the_requesting_level(home):
    wid, cid, _ = _stack(home)
    g = layers.read_level("global")["rules"][0]["id"]
    layers.write_level("campaign", cid, {"rules": [], "off": [g]})
    got = {e["rule"]["name"]: e["off"] for e in layers.inherited("campaign", cid)}
    assert got == {"C": False, "G": True, "W": False}
    assert not any(e["off"] for e in layers.inherited("world", wid))


def test_world_of(home):
    wid, cid, _ = home
    assert layers.world_of(cid) == wid
    assert layers.world_of("no-such-campaign") == ""


def test_validate_rejects_off_on_global(home):
    with pytest.raises(rules.RuleError) as exc:
        layers.validate_doc({"rules": [], "off": ["r-1"]}, level="global",
                            inherited_ids={"r-1"})
    assert exc.value.field == "off"
    with pytest.raises(rules.RuleError):
        layers.validate_doc({"rules": [], "off": ["r-1"]}, level="connection",
                            inherited_ids={"r-1"})


def test_validate_rejects_unknown_off_id(home):
    with pytest.raises(rules.RuleError) as exc:
        layers.validate_doc({"rules": [], "off": ["r-nope"]}, level="world",
                            inherited_ids={"r-1"})
    assert exc.value.field == "off"


def test_validate_rejects_id_collision_with_inherited(home):
    with pytest.raises(rules.RuleError) as exc:
        layers.validate_doc({"rules": [_rule("A"), {**_rule("B"), "id": "r-1"}]},
                            level="campaign", inherited_ids={"r-1"})
    assert exc.value.index == 1
    assert exc.value.field == "id"


def test_validate_rejects_duplicate_ids_and_reports_the_bad_index(home):
    with pytest.raises(rules.RuleError) as exc:
        layers.validate_doc({"rules": [{**_rule("A"), "id": "r-x"}, {**_rule("B"), "id": "r-x"}]},
                            level="global", inherited_ids=set())
    assert exc.value.index == 1
    with pytest.raises(rules.RuleError) as exc:
        layers.validate_doc({"rules": [_rule("A"), {"pattern": 5}]},
                            level="global", inherited_ids=set())
    assert exc.value.index == 1 and exc.value.field == "pattern"


def test_validate_rejects_a_doc_that_is_not_an_object(home):
    with pytest.raises(rules.RuleError):
        layers.validate_doc([], level="global", inherited_ids=set())
    with pytest.raises(rules.RuleError):
        layers.validate_doc({"rules": {}}, level="global", inherited_ids=set())


def test_validate_dedupes_off_and_mints_ids(home):
    out = layers.validate_doc({"rules": [_rule("A")], "off": ["r-1", "r-1"]},
                              level="world", inherited_ids={"r-1"})
    assert out["off"] == ["r-1"]
    assert out["rules"][0]["id"].startswith("r-")


def test_write_campaign_validates_against_what_it_inherits(home):
    wid, cid, _ = _stack(home)
    w = layers.read_level("world", wid)["rules"][0]
    with pytest.raises(rules.RuleError):
        layers.write_campaign(cid, {"rules": [{**_rule("Clash"), "id": w["id"]}]})
    with pytest.raises(rules.RuleError):
        layers.write_level("campaign", cid, {"rules": [], "off": ["r-nope"]})
    # A refused write leaves the file as it was.
    assert _names(layers.effective(cid=cid)) == ["G", "W", "K"]


def test_write_world_validates_against_connections_and_global(home):
    wid, _, _ = _stack(home)
    g = layers.read_level("global")["rules"][0]
    with pytest.raises(rules.RuleError):
        layers.write_level("world", wid, {"rules": [{**_rule("Clash"), "id": g["id"]}]})


def test_deleted_connection_contributes_nothing(home):
    _, cid, conn = _stack(home)
    llm_connections.delete_connection(conn)
    assert _names(layers.effective(cid=cid, connection=conn)) == ["G", "W", "K"]
    assert _names(layers.inherited("campaign", cid)) == ["G", "W"]
    assert _names(layers.effective(cid=cid, connection="never-existed")) == ["G", "W", "K"]


def test_delete_connection_unlinks_rules(home):
    _, _, conn = home
    layers.write_level("connection", conn, {"rules": [_rule("C")]})
    p = llm_connections.regex_path(conn)
    assert p.is_file()
    llm_connections.delete_connection(conn)
    assert not p.exists()


def test_write_to_a_connection_that_is_not_there_is_refused(home):
    with pytest.raises(llm_connections.ConnectionNotFound):
        layers.write_level("connection", "never-existed", {"rules": []})


def test_unknown_level_is_a_programming_error(home):
    with pytest.raises(ValueError):
        layers.path("planet")
