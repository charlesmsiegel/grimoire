"""The per-task routing registry and its cascade (#142).

`store/routing.py` is a pure leaf: it is handed the two frontmatter dicts and a
predicate saying which connection ids exist, and answers which connection a task
should run on. Every test here is that function; nothing on disk is involved,
which is the point of keeping it pure (`config.py` imports it for the key list,
so it may not import back into the store).
"""

from __future__ import annotations

import pytest

from grimoire.store import routing


def _resolve(task, *, campaign=None, cfg=None, known=("openrouter", "cheap", "big")):
    return routing.resolve(task, campaign_meta=campaign or {}, cfg=cfg or {},
                           exists=lambda cid: cid in known)


# --- the registry itself ---

def test_every_route_key_has_a_config_key_and_they_agree():
    assert tuple(f"route_{r.key}" for r in routing.ROUTES) == routing.CONFIG_KEYS
    assert routing.config_key("scene") == "route_scene"


def test_no_task_is_claimed_by_two_routes():
    seen: dict[str, str] = {}
    for route in routing.ROUTES:
        for task in route.tasks:
            assert task not in seen, f"{task} claimed by {seen.get(task)} and {route.key}"
            seen[task] = route.key
    assert seen == routing.TASK_ROUTE


def test_the_six_routes_the_issue_named_are_spelled_as_the_issue_spelled_them():
    # #142 listed scene / opener / absorb / dossier / suggestions / tagline, and
    # named the scene turn's retries and director turns as part of ONE task.
    keys = {r.key for r in routing.ROUTES}
    assert {"scene", "opener", "absorb", "dossier", "suggestions", "tagline"} <= keys
    assert routing.TASK_ROUTE["retry"] == "scene"
    assert routing.TASK_ROUTE["director"] == "scene"
    assert routing.TASK_ROUTE["regenerate"] == "scene"


def test_a_route_whose_call_sites_have_no_campaign_takes_no_campaign_override():
    assert not routing.route_by_key("tagline").campaign_scoped
    assert not routing.route_by_key("scenario").campaign_scoped
    assert routing.route_by_key("scene").campaign_scoped
    # `route()` takes a TASK, and the two vocabularies overlap by coincidence
    # for a single-task route -- so say which one each caller means.
    assert routing.route("chat") is routing.route_by_key("scene")


# --- the cascade ---

def test_with_nothing_configured_a_task_resolves_to_the_active_connection():
    got = _resolve("chat")
    assert got["connection_id"] == ""
    assert got["scope"] == "active"
    assert got["route"] == "scene"


def test_a_global_route_key_wins_over_the_active_connection():
    got = _resolve("chat", cfg={"route_scene": "big"})
    assert got == {"route": "scene", "connection_id": "big", "scope": "global"}


def test_a_campaign_route_key_wins_over_the_global_one():
    got = _resolve("chat", campaign={"route_scene": "cheap"}, cfg={"route_scene": "big"})
    assert got == {"route": "scene", "connection_id": "cheap", "scope": "campaign"}


def test_every_task_of_a_route_follows_that_route():
    for task in ("chat", "retry", "regenerate", "director", "replay", "continuation"):
        assert _resolve(task, cfg={"route_scene": "big"})["connection_id"] == "big"


def test_one_route_does_not_answer_for_another():
    got = _resolve("absorb", cfg={"route_scene": "big"})
    assert got["connection_id"] == ""
    assert got["route"] == "absorb"


def test_an_empty_value_is_no_opinion_and_the_walk_continues():
    got = _resolve("chat", campaign={"route_scene": ""}, cfg={"route_scene": "big"})
    assert got == {"route": "scene", "connection_id": "big", "scope": "global"}


def test_whitespace_is_not_an_opinion_either():
    # A hand-edited campaign.md, or a field cleared to a space in a form.
    got = _resolve("chat", campaign={"route_scene": "   "}, cfg={"route_scene": "big"})
    assert got["connection_id"] == "big"


def test_an_id_naming_a_deleted_connection_is_no_opinion_not_a_failure():
    # `delete_connection` clears the config keys, but it cannot reach into every
    # campaign's frontmatter -- so a dangling campaign override degrades to the
    # next scope. Same rule as response_presets' missing-style check.
    got = _resolve("chat", campaign={"route_scene": "gone"}, cfg={"route_scene": "big"})
    assert got == {"route": "scene", "connection_id": "big", "scope": "global"}


def test_a_dangling_reference_at_every_scope_lands_on_the_active_connection():
    got = _resolve("chat", campaign={"route_scene": "gone"}, cfg={"route_scene": "also-gone"})
    assert got == {"route": "scene", "connection_id": "", "scope": "active"}


def test_a_campaign_override_is_ignored_for_a_route_that_has_none():
    # Nothing writes this through the API -- the PUT refuses it -- but a
    # hand-edited campaign.md must not route a world-scoped call either.
    got = _resolve("tagline", campaign={"route_tagline": "cheap"}, cfg={"route_tagline": "big"})
    assert got == {"route": "tagline", "connection_id": "big", "scope": "global"}


def test_an_unknown_task_resolves_to_the_active_connection_rather_than_raising():
    # The guard test is what keeps this from happening; a generation must not
    # 500 because someone added a call site and forgot the registry entry.
    got = _resolve("no-such-task")
    assert got == {"route": "", "connection_id": "", "scope": "active"}


def test_resolution_does_not_consult_the_store():
    """The purity that lets config.py import this module.

    If routing ever reaches for the store, `config -> routing -> config` closes
    a cycle and the import guard fails somewhere far away from here, with a
    message about a graph rather than about this rule.

    Read off the AST rather than by searching the text: `from . import config`
    and `import grimoire.store.config` are the same mistake spelled two ways,
    and a substring check catches whichever one the author of the check thought
    of.
    """
    import ast
    import pathlib as pl

    import grimoire.store.routing as mod

    src = pl.Path(mod.__file__ or "")
    assert src.name == "routing.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    reached = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # level>0 is a relative import, which inside `store/` can only be a
            # sibling of this module.
            if node.level:
                reached.append("." * node.level + (node.module or ""))
        elif isinstance(node, ast.Import):
            reached += [a.name for a in node.names if a.name.startswith("grimoire")]
    assert not reached, f"routing.py must stay a pure leaf; it imports {reached}"


# --- what the surfaces need ---

def test_the_bundle_reports_own_values_effective_values_and_provenance():
    bundle = routing.bundle(campaign_meta={"route_scene": "cheap"},
                            cfg={"route_absorb": "big"},
                            exists=lambda cid: cid in ("cheap", "big"),
                            scope="campaign")
    assert bundle["routes"]["scene"] == "cheap"      # what THIS scope says
    assert bundle["routes"]["absorb"] == ""          # the global key is not this scope's
    assert bundle["effective"]["scene"] == "cheap"
    assert bundle["effective"]["absorb"] == "big"
    assert bundle["provenance"]["scene"] == {"scope": "campaign"}
    assert bundle["provenance"]["absorb"] == {"scope": "global"}
    assert bundle["provenance"]["summary"] == {"scope": "active"}


def test_inherit_names_what_this_scope_would_get_by_saying_nothing():
    """`effective` includes this scope's OWN opinion, so it is the wrong label
    for an inherit option: a row already naming a connection would offer
    "inherit (that same one)" and then change the answer when you picked it."""
    bundle = routing.bundle(campaign_meta={"route_scene": "cheap"},
                            cfg={"route_scene": "big"},
                            exists=lambda cid: cid in ("cheap", "big"),
                            scope="campaign")
    assert bundle["effective"]["scene"] == "cheap"        # what runs today
    assert bundle["inherited"]["scene"] == "big"          # what inherit would get
    assert bundle["inherited_from"]["scene"] == {"scope": "global"}


def test_inherit_at_global_scope_falls_all_the_way_to_the_active_connection():
    bundle = routing.bundle(campaign_meta={}, cfg={"route_scene": "big"},
                            exists=lambda cid: cid == "big", scope="global")
    assert bundle["effective"]["scene"] == "big"
    assert bundle["inherited"]["scene"] == ""
    assert bundle["inherited_from"]["scene"] == {"scope": "active"}


def test_a_campaign_bundle_does_not_silence_the_global_scope_by_mistake():
    """Only the scope being edited is silenced. Silencing both would report the
    active connection as what inheriting gets you, which is a scope too far."""
    bundle = routing.bundle(campaign_meta={}, cfg={"route_absorb": "big"},
                            exists=lambda cid: cid == "big", scope="campaign")
    assert bundle["inherited"]["absorb"] == "big"


def test_the_campaign_bundle_omits_the_routes_a_campaign_cannot_override():
    bundle = routing.bundle(campaign_meta={}, cfg={}, exists=lambda cid: False,
                            scope="campaign")
    for key in ("routes", "effective", "provenance", "inherited", "inherited_from"):
        assert "tagline" not in bundle[key], key
        assert "scenario" not in bundle[key], key
        assert "scene" in bundle[key], key
    # All three maps describe the same routes, so a caller can iterate any one
    # of them and look the others up by key.
    assert set(bundle["routes"]) == set(bundle["effective"]) == set(bundle["provenance"])


def test_the_global_bundle_carries_every_route():
    bundle = routing.bundle(campaign_meta={}, cfg={}, exists=lambda cid: False, scope="global")
    assert set(bundle["routes"]) == {r.key for r in routing.ROUTES}


@pytest.mark.parametrize("route", [r.key for r in routing.ROUTES])
def test_every_route_is_named_and_described_for_the_picker(route):
    got = routing.route_by_key(route)
    assert got.label and got.label[0].isupper()
    assert got.tasks
