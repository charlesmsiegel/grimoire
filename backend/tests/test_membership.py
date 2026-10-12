"""A roster is derived from actor refs; raw edits preserve unrelated legacy tokens.

The cases deliberately distinguish membership from leadership and actor identity
from mere slug matching. They exercise the public helpers the later readers use.
"""

import hashlib
import json

import pytest
from pydantic import BaseModel

from grimoire import store
from grimoire.routes import common
from grimoire.store import entities, entity_schema, group_refs, membership


def _membership():
    module = getattr(store, "membership", None)
    assert module is not None, "the shared membership reader is missing"
    return module


def _group_refs():
    module = getattr(store, "group_refs", None)
    assert module is not None, "the shared group-ref leaf is missing"
    return module


@pytest.mark.parametrize("raw,want", [
    (None, ()), (False, ()), (42, ()), ([], ()), ({}, ()),
    ("Mara, Seraphine", ()),
    ("characters:mara, characters:mara, pcs:winifred", ("characters:mara", "pcs:winifred")),
    ("locations:saltmarch, groups:salt-circle, characters:../mara, pcs:winifred", ("pcs:winifred",)),
    ("characters:mara/other, characters:mara:other, characters:mara\nother", ()),
    ("characters:mara,\tpcs:winifred", ("characters:mara", "pcs:winifred")),
    ("characters:mara\u2028other", ()),
])
def test_parse_members_is_lenient(raw, want):
    assert _membership().parse_members({"members": raw}) == want


def test_parse_leader_keeps_the_first_valid_actor_ref():
    module = _membership()
    assert module.parse_leader({"leader": "Mara, pcs:winifred, characters:mara"}) == "pcs:winifred"
    assert module.parse_leader({"leader": ["characters:mara"]}) is None
    assert module.parse_leader({}) is None


@pytest.mark.parametrize("raw,actor,change,want", [
    ("Mara, characters:mara, pcs:winifred", "characters:mara", "leave", "Mara, pcs:winifred"),
    ("Mara, pcs:winifred", "characters:mara", "join", "Mara, pcs:winifred, characters:mara"),
    ("Mara, characters:mara, characters:mara", "characters:mara", "join", "Mara, characters:mara, characters:mara"),
    ("Mara, characters:mara, characters:mara", "characters:mara", "leave", "Mara"),
    (None, "pcs:winifred", "join", "pcs:winifred"),
    ("characters:mara", "characters:mara", "leave", ""),
])
def test_edited_line_preserves_unrelated_raw_tokens(raw, actor, change, want):
    assert _membership().edited_line(raw, actor, change) == want


def test_inherited_filter_keeps_owned_and_dangling_refs():
    module = _group_refs()
    meta = {"members": "characters:mara, pcs:winifred", "leader": "characters:mara"}
    off = {"characters/mara", "groups/salt-circle"}
    assert module.effective_refs(meta, inherited=True, detached=off) == (("pcs:winifred",), None)
    assert module.effective_refs(meta, inherited=False, detached=off) == (
        ("characters:mara", "pcs:winifred"), "characters:mara")
    assert module.group_slots(meta) == frozenset({
        ("members", "characters:mara"), ("members", "pcs:winifred"), ("leader", "characters:mara")})


def test_group_context_is_plain_pydantic():
    module = _group_refs()
    context = module.GroupMembershipContext(
        layer="world", members=("pcs:winifred",), leader=None,
        filtered_members=("characters:mara",), filtered_leader=None,
        unresolved_members=("characters:mara",), unresolved_leader=None)
    assert isinstance(context, BaseModel)
    assert common._dump(context) == {
        "layer": "world", "members": ("pcs:winifred",), "leader": None,
        "filtered_members": ("characters:mara",), "filtered_leader": None,
        "unresolved_members": ("characters:mara",), "unresolved_leader": None,
        "unresolved_leaders": ()}


def test_group_slots_include_dormant_valid_leader_tokens():
    assert _group_refs().group_slots({"leader": "characters:mara, pcs:winifred, Mara"}) == frozenset({
        ("leader", "characters:mara"), ("leader", "pcs:winifred")})


# S2 keeps the S1 parser contract above and adds a derived, serializable inverse.
def _rows():
    return [{"id": "salt-circle", "name": "Salt Circle", "group_type": "guild",
             "leader": "characters:seraphine",
             "members": "characters:mara, pcs:winifred, characters:seraphine",
             "headquarters": "locations:saltmarch"},
            {"id": "realm", "name": "Realm", "leader": "characters:mara",
             "members": "characters:seraphine"}]


def test_inverse_roles_order_and_dangling_refs():
    index = membership.index_from_rows(_rows())
    assert tuple(index.groups) == ("groups:realm", "groups:salt-circle")
    assert tuple(index.by_actor) == ("characters:mara", "characters:seraphine", "pcs:winifred")
    assert [(a.group, a.roles) for a in membership.groups_for(index, "characters:seraphine")] == [
        ("groups:realm", ("member",)), ("groups:salt-circle", ("leader", "member"))]
    assert membership.members_of(index, "groups:salt-circle") == (
        "characters:mara", "pcs:winifred", "characters:seraphine")
    assert membership.affiliated(index, "groups:salt-circle") == (
        "characters:seraphine", "characters:mara", "pcs:winifred")
    assert membership.groups_for(index, "characters:winifred") == ()
    assert membership.members_of(index, "groups:saltmarch") == ()
    assert membership.affiliated(index, "groups:saltmarch") == ()


def test_inverse_lenient_metadata():
    index = membership.index_from_rows([{
        "id": "salt-circle", "name": None, "secrecy": " SECRET ", "group_type": {},
        "members": "Mara, groups:realm, characters:, characters:mara, characters:mara, pcs:winifred",
        "leader": "Mara, locations:saltmarch, pcs:winifred, characters:mara",
        "headquarters": "characters:mara, locations:../saltmarch, locations:saltmarch, locations:realm"}])
    roster = index.groups["groups:salt-circle"]
    assert (roster.name, roster.secrecy, roster.group_type) == ("salt-circle", entities.SECRET, "")
    assert roster.members == ("characters:mara", "pcs:winifred")
    assert roster.leader == "pcs:winifred" and roster.headquarters == "locations:saltmarch"
    assert membership.affiliated(index, roster.ref) == ("pcs:winifred", "characters:mara")
    empty = membership.index_from_rows([{"id": "realm", "members": 7, "leader": None,
                                         "headquarters": "Mara", "secrecy": "unknown"}])
    assert empty.groups["groups:realm"].members == ()
    assert empty.groups["groups:realm"].leader is None
    assert empty.groups["groups:realm"].headquarters is None
    assert empty.groups["groups:realm"].secrecy == entities.PUBLIC


def test_co_affiliates_excludes_self_and_sorts_groups():
    index = membership.index_from_rows(_rows())
    assert membership.co_affiliates(index, "characters:mara") == {
        "characters:seraphine": ("groups:realm", "groups:salt-circle"),
        "pcs:winifred": ("groups:salt-circle",)}
    assert membership.co_affiliates(index, "characters:winifred") == {}
    leader_only = membership.index_from_rows([{"id": "realm", "leader": "characters:mara"}])
    assert membership.members_of(leader_only, "groups:realm") == ()
    assert membership.affiliated(leader_only, "groups:realm") == ("characters:mara",)
    assert membership.groups_for(leader_only, "characters:mara")[0].roles == ("leader",)


@pytest.mark.parametrize("field,value", [
    ("id", "realm"), ("name", "Mara"), ("secrecy", "secret"),
    ("group_type", "circle"), ("leader", "characters:mara"),
    ("members", "characters:mara"),
    ("members", "characters:seraphine, pcs:winifred, characters:mara"),
    ("headquarters", "locations:realm")])
def test_digest_covers_every_roster_field_and_member_order(field, value):
    row = _rows()[0]
    assert membership.index_from_rows([row]).digest != membership.index_from_rows([row | {field: value}]).digest


def test_digest_ignores_body_keys_tokens_and_state_metadata():
    rows = _rows()
    extra = [{**row, "body": "Mara keeps the roster.", "keys": "Saltmarch",
              "tokens": 77, "state": "Winifred waits.", "layer": "campaign"} for row in rows]
    assert membership.index_from_rows(rows).digest == membership.index_from_rows(reversed(extra)).digest
    assert membership.parse_members is group_refs.parse_members
    assert membership.parse_leader is group_refs.parse_leader
    assert membership.edited_line is group_refs.edited_line


def test_plain_pydantic_serializes_nested_inverse():
    index = membership.index_from_rows(_rows())
    assert isinstance(index, BaseModel)
    assert isinstance(index.groups["groups:realm"], BaseModel)
    assert isinstance(index.by_actor["characters:mara"][0], BaseModel)
    payload = json.loads(json.dumps(common._dump(index)))
    assert set(payload) == {"groups", "by_actor", "digest"}
    assert payload["groups"]["groups:realm"] == {
        "ref": "groups:realm", "name": "Realm", "secrecy": "public", "group_type": "",
        "leader": "characters:mara", "members": ["characters:seraphine"], "headquarters": None}
    assert payload["by_actor"]["characters:seraphine"][1] == {
        "group": "groups:salt-circle", "roles": ["leader", "member"]}
    canonical = [["groups:realm", "Realm", "public", "", "characters:mara", ["characters:seraphine"], None],
                 ["groups:salt-circle", "Salt Circle", "public", "guild", "characters:seraphine",
                  ["characters:mara", "pcs:winifred", "characters:seraphine"], "locations:saltmarch"]]
    assert index.digest == hashlib.sha256(json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


@pytest.mark.parametrize("secrecy,framed,expected", [
    ("public", False, True), ("public", True, True),
    ("secret", False, False), ("secret", True, True),
    ("gm-only", False, False), ("gm-only", True, False)])
def test_prompt_visible_never_exposes_gm_only(secrecy, framed, expected):
    roster = membership.index_from_rows([{"id": "realm", "secrecy": secrecy}]).groups["groups:realm"]
    assert membership.prompt_visible(roster, secret_framed=framed) is expected


def test_actor_kinds_are_the_members_field_kinds():
    """§4.3: the reader's actor kinds and the editor's `members` kinds are one set,
    so a ref the picker offers is never one the reader drops, and vice versa."""
    [members] = [f for f in entity_schema.FIELDS["groups"] if f["key"] == "members"]
    [leader] = [f for f in entity_schema.FIELDS["groups"] if f["key"] == "leader"]
    assert membership.ACTOR_KINDS == group_refs.ACTOR_KINDS
    assert tuple(members["kinds"]) == membership.ACTOR_KINDS
    assert tuple(leader["kinds"]) == membership.ACTOR_KINDS
