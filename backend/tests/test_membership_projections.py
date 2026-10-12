"""Scene affiliation is current metadata; model visibility is explicit."""

import copy

import pytest

from grimoire.store import membership


def _index():
    return membership.index_from_rows([
        {"id": "salt-circle", "name": "Salt Circle", "leader": "characters:mara",
         "members": "characters:seraphine, characters:mara"},
        {"id": "realm", "name": "Realm", "leader": "pcs:winifred"},
        {"id": "saltmarch", "name": "Saltmarch", "members": "characters:seraphine"},
    ])


def test_orders_intersection_and_independent_leader():
    result = membership.scene_groups(_index(), ["characters:mara", "characters/seraphine",
                                               "characters:seraphine", "pcs:winifred"])
    assert [(r.group, r.present, r.leader_present) for r in result] == [
        ("groups:salt-circle", ("characters:mara", "characters:seraphine"), True),
        ("groups:realm", ("pcs:winifred",), True),
        ("groups:saltmarch", ("characters:seraphine",), False),
    ]


def test_actor_normalization_ignores_nonactors_and_retains_dangling_refs():
    index = membership.index_from_rows([{"id": "realm", "members": "characters:mara"}])
    assert membership.scene_groups(index, ["characters/mara", "locations:mara", "mara", "../mara"])[0].present == ("characters:mara",)
    assert membership.scene_groups(index, ["characters:Mara"]) == ()


def test_metadata_uncapped_and_renderer_visibility_and_cap_explicit():
    index = membership.index_from_rows([
        {"id": f"realm-{n}", "name": f"Realm {n}", "members": "characters:mara",
         "secrecy": "public" if n < 8 else "secret" if n == 8 else "gm-only"}
        for n in range(10)
    ])
    cast = ["characters:mara"]
    all_rows = membership.scene_groups(index, cast)
    assert len(all_rows) == 10
    assert {r.secrecy for r in all_rows} == {"public", "secret", "gm-only"}
    assert len(membership.scene_groups(index, cast, visible=membership.prompt_visible)) == 8
    assert len(membership.scene_groups(index, cast, visible=lambda r: membership.prompt_visible(r, secret_framed=True))) == 9
    assert membership.scene_groups(index, cast, limit=membership.SCENE_GROUP_LIMIT) == all_rows[:8]


def test_candidates_come_from_inverse_without_library_iteration():
    index = _index()

    class CandidatesOnly(dict):
        def __iter__(self):
            raise AssertionError("whole-library scan")

        def values(self):
            raise AssertionError("whole-library scan")

        def items(self):
            raise AssertionError("whole-library scan")

    index.groups = CandidatesOnly(index.groups)
    assert [r.group for r in membership.scene_groups(index, ["pcs:winifred"])] == ["groups:realm"]


def test_present_intersection_never_traverses_offscene_roster_members():
    index = _index()

    class NoTraversal(tuple):
        __slots__ = ()

        def __iter__(self):
            raise AssertionError("off-scene roster traversal")

    roster = index.groups["groups:salt-circle"]
    roster.members = NoTraversal(roster.members)
    assert membership.scene_groups(index, ["characters:mara"])[0].present == ("characters:mara",)


@pytest.mark.parametrize("limit", [None, 0, -1])
def test_pure_projection_and_empty_or_nonpositive_limits(limit):
    index = _index()
    before = copy.deepcopy(index)
    assert membership.scene_groups(index, [], limit=limit) == ()
    rows = membership.scene_groups(index, ["characters:mara"], limit=limit)
    if limit is None:
        rows[0].name = "Realm"
        assert membership.scene_groups(index, ["characters:mara"])[0].name == "Salt Circle"
    else:
        assert rows == ()
    assert index == before
