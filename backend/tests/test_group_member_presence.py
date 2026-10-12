"""Explicit member presence opens owner gates without activating group prose."""

import pytest

from grimoire.store import campaigns, entities, group_refs, membership, overlay, worlds
from grimoire.store.context import activation, world_state
from grimoire.store.context import actor as actor_scope


@pytest.mark.parametrize("present,want", [
    ({"characters:seraphine": {}, "characters:mara": {}}, {"type": "led_by", "via": "characters:seraphine"}),
    ({"characters:mara": {}}, {"type": "member_present", "via": "characters:mara"}),
    ({}, {"type": "headquarters", "via": "locations:saltmarch"}),
])
def test_group_presence_priority(present, want):
    entries = [{"kind": "groups", "id": "salt-circle", "refs": {
        "leader": ["characters:seraphine"], "members": ["characters:mara"], "headquarters": ["locations:saltmarch"]}}]
    assert activation.structural_presence(entries, present, "saltmarch")["groups:salt-circle"] == want


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    return cid, worlds.world_root(wid)


@pytest.mark.parametrize("members,leader,secrecy,detached,owned,want", [
    ("characters:mara", "", "public", False, False, True),
    ("groups:saltmarch", "", "public", False, False, False),
    ("", "pcs:winifred, characters:mara", "public", False, False, False),
    ("characters:mara", "", "gm-only", False, False, False),
    ("characters:mara", "", "public", True, False, False),
    ("characters:mara", "", "public", True, True, True),
])
def test_effective_stored_members_open_only_owner_gate(case, members, leader, secrecy, detached, owned, want):
    cid, root = case
    source = overlay.croot_of(cid) if owned else root
    entities.create_entity(source, "groups", "Salt Circle", "Seraphine keeps the roster.", keys="missing",
        secrecy=secrecy, fields={"members": members, "leader": leader})
    entities.create_entity(root, "lore", "Saltmarch", "Winifred keeps the ledger.", keys="ledger", owners="groups:salt-circle")
    if detached:
        overlay.add_detached(cid, "characters/mara")
    keyword, _, result, _ = world_state._world_info(cid, [], "ledger", scan_depth=1,
        present={"characters:mara": {"type": "cast"}, "groups:saltmarch": {"type": "cast"}})
    assert ("groups:salt-circle" in result.present) is want
    assert any(entry["id"] == "saltmarch" for entry in keyword) is want
    assert all(entry["kind"] != "groups" for entry in keyword)


def test_world_info_never_rereads_groups_after_snapshot(case, monkeypatch):
    cid, root = case
    entities.create_entity(root, "groups", "Salt Circle", fields={"members": "characters:mara"})
    real_list, real_read = overlay.list_entities, overlay.read_entity
    calls = []
    snapshot = membership.campaign_snapshot

    def listed(cid, kind, **kwargs):
        assert kind != "groups", "group listing would decouple visibility from membership"
        return real_list(cid, kind, **kwargs)

    def read(cid, kind, eid, **kwargs):
        assert kind != "groups", "group body would come from another version"
        return real_read(cid, kind, eid, **kwargs)

    def captured(cid):
        calls.append(1)
        return snapshot(cid)

    monkeypatch.setattr(overlay, "list_entities", listed)
    monkeypatch.setattr(overlay, "read_entity", read)
    monkeypatch.setattr(membership, "campaign_snapshot", captured)
    world_state._world_info(cid, [], "", scan_depth=1, present={"characters:mara": {"type": "cast"}})
    assert calls == [1]


# ---- §17.6, through the real assembly path ----------------------------------

def _keyword(result) -> dict[str, dict]:
    return {hit.ref: hit.reason for hit in result.keyword}


def _salt_circle(root, *, members="characters:mara", leader="characters:seraphine", secrecy=""):
    entities.create_entity(root, "groups", "Salt Circle", "Seraphine keeps the roster.", keys="missing",
        secrecy=secrecy, fields={"members": members, "leader": leader})


@pytest.mark.parametrize("present,want", [
    # A member alone: the reason names WHICH member, not just the rule.
    (("characters:mara",), {"type": "member_present", "via": "characters:mara"}),
    # A present leader wins over a present member (field order).
    (("characters:mara", "characters:seraphine"), {"type": "led_by", "via": "characters:seraphine"}),
    (("characters:seraphine",), {"type": "led_by", "via": "characters:seraphine"}),
    # No affiliate in the room: the key matching is not enough.
    (("characters:winifred",), None),
    ((), None),
])
def test_member_presence_reason_and_owner_gate_at_store_level(case, present, want):
    cid, root = case
    _salt_circle(root)
    entities.create_entity(root, "lore", "Saltmarch", "Winifred keeps the ledger.", keys="ledger",
                           owners="groups:salt-circle")
    keyword, _, result, _ = world_state._world_info(
        cid, [], "ledger", scan_depth=1, present={ref: {"type": "cast"} for ref in present})
    reasons = _keyword(result)
    if want is None:
        assert "groups:salt-circle" not in result.present
        assert "lore:saltmarch" not in reasons
        assert keyword == []
        return
    assert result.present["groups:salt-circle"] == want
    assert reasons["lore:saltmarch"]["owner"] == "groups:salt-circle"
    assert reasons["lore:saltmarch"]["owner_presence"] == want
    # The group's own entry did not activate: its key is not in the window.
    assert "groups:salt-circle" not in reasons


@pytest.mark.parametrize("present,cascades", [
    (("characters:mara",), True),
    (("characters:winifred",), False),
])
def test_an_item_the_group_holds_is_present_through_a_member(case, present, cascades):
    """§11.2's cascade: member -> group -> the item it holds -> that item's lore."""
    cid, root = case
    _salt_circle(root, leader="")
    entities.create_entity(root, "items", "Salt Ledger", "A brass-bound book.", keys="missing",
                           fields={"holder": "groups:salt-circle"})
    entities.create_entity(root, "lore", "Tidemark", "The ledger's last page is blank.", keys="ledger",
                           owners="items:salt-ledger")
    _, _, result, _ = world_state._world_info(
        cid, [], "ledger", scan_depth=1, present={ref: {"type": "cast"} for ref in present})
    reasons = _keyword(result)
    if not cascades:
        assert "items:salt-ledger" not in result.present
        assert "lore:tidemark" not in reasons
        return
    assert result.present["groups:salt-circle"] == {"type": "member_present", "via": "characters:mara"}
    assert result.present["items:salt-ledger"] == {"type": "held_by", "via": "groups:salt-circle"}
    assert reasons["lore:tidemark"]["owner"] == "items:salt-ledger"
    # Neither the item nor the group activated: presence only opened a gate.
    assert "items:salt-ledger" not in reasons and "groups:salt-circle" not in reasons


_ACTORS = ("characters:mara", "characters:seraphine", "characters:winifred")


def _knows_table(tmp_path, monkeypatch, *, recorded: bool) -> dict[tuple[str, str], bool]:
    """`actor.knows` over the entries `_world_info` builds, for a member (Mara), the
    leader (Seraphine) and someone in no group (Winifred), with or without the
    roster recorded at all."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / ("recorded" if recorded else "bare")))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    root = worlds.world_root(wid)
    _salt_circle(root, members="characters:mara" if recorded else "",
                 leader="characters:seraphine" if recorded else "")
    entities.create_entity(root, "items", "Salt Ledger", keys="missing", fields={"holder": "groups:salt-circle"})
    for owner in ("groups:salt-circle", "items:salt-ledger"):
        for secrecy in ("public", "secret", "gm-only"):
            entities.create_entity(root, "lore", f"{owner.partition(':')[2]} {secrecy}", keys="ledger",
                                   owners=owner, secrecy=secrecy)
    seen: list[list[dict]] = []
    real = activation.run

    def spy(*args, **kwargs):
        seen.append(kwargs["scene_entries"])
        return real(*args, **kwargs)

    monkeypatch.setattr(activation, "run", spy)
    _, _, result, _ = world_state._world_info(cid, [], "ledger", scan_depth=1,
                                              present={"characters:mara": {"type": "cast"}})
    monkeypatch.setattr(activation, "run", real)
    # The control: the roster really is what moves presence between the two stores.
    assert ("groups:salt-circle" in result.present) is recorded
    [entries] = seen
    lore = [e for e in entries if e["kind"] == "lore"]
    assert len(lore) == 6
    return {(e["id"], actor): actor_scope.knows(e, actor) for e in lore for actor in _ACTORS}


def test_knows_is_unchanged_by_membership(tmp_path, monkeypatch):
    """§11.2 / §17.6: presence moved, knowledge did not. A group (or an item it
    holds) is an object owner to `actor.knows` whoever is affiliated, so public
    lore it owns is everyone's, secret lore it owns is no actor's (the gap is
    11's, §12.4), and gm-only reaches nobody -- with or without a roster."""
    recorded = _knows_table(tmp_path, monkeypatch, recorded=True)
    bare = _knows_table(tmp_path, monkeypatch, recorded=False)
    assert recorded == bare
    for (eid, actor), known in recorded.items():
        assert known is eid.endswith("-public"), (eid, actor)


@pytest.mark.parametrize("actor_ref,want", [
    # The member's own call: her group's public lore, never its secret lore.
    ("characters:mara", {"lore:salt-circle-public"}),
    # Someone in no group still gets it: the room is the scene's, not the NPC's.
    ("characters:winifred", {"lore:salt-circle-public"}),
    # The narrator is not filtered by `knows`.
    (None, {"lore:salt-circle-public", "lore:salt-circle-secret"}),
])
def test_member_presence_grants_the_room_not_knowledge(case, actor_ref, want):
    cid, root = case
    _salt_circle(root, leader="")
    for secrecy in ("public", "secret", "gm-only"):
        entities.create_entity(root, "lore", f"salt-circle {secrecy}", keys="ledger",
                               owners="groups:salt-circle", secrecy=secrecy)
    _, _, result, _ = world_state._world_info(cid, [], "ledger", scan_depth=1, actor_ref=actor_ref,
                                              present={"characters:mara": {"type": "cast"}})
    assert result.present["groups:salt-circle"] == {"type": "member_present", "via": "characters:mara"}
    assert set(_keyword(result)) == want


@pytest.mark.parametrize("ledger", ["deleted.json", "detached.json"])
def test_a_campaign_with_no_groups_composes_past_a_damaged_identity_ledger(case, ledger):
    """Spec 07 section 11.1: a store with no members composes as before, so a
    campaign that sees no group at all is not refused over a ledger only group
    identity reads. One that does see a group still refuses."""
    cid, root = case
    entities.create_entity(root, "lore", "Saltmarch", "Winifred keeps the ledger.", keys="ledger")
    (overlay.croot_of(cid) / ledger).write_text("{", encoding="utf-8")
    keyword, _, _result, _ = world_state._world_info(cid, [], "ledger", scan_depth=1, present={})
    assert [entry["id"] for entry in keyword] == ["saltmarch"]
    entities.create_entity(root, "groups", "Salt Circle", fields={"members": "characters:mara"})
    with pytest.raises(group_refs.GroupIdentityConflict):
        world_state._world_info(cid, [], "ledger", scan_depth=1, present={})
