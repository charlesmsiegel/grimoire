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
    # The purity that lets config.py import this module. If routing ever reaches
    # for the store, config -> routing -> ... -> config closes a cycle and the
    # import guard fails somewhere far away from here.
    import pathlib

    import grimoire.store.routing as mod
    src = pathlib.Path(mod.__file__ or "")
    assert src.name == "routing.py"
    text = src.read_text(encoding="utf-8")
    assert "import" in text
    for banned in ("from . import", "from .config", "from .llm_connections", "from .campaigns"):
        assert banned not in text, f"routing.py must stay a pure leaf; found {banned!r}"


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
    assert bundle["provenance"]["tagline"] == {"scope": "active"}


def test_the_campaign_bundle_omits_the_routes_a_campaign_cannot_override():
    bundle = routing.bundle(campaign_meta={}, cfg={}, exists=lambda cid: False,
                            scope="campaign")
    assert "tagline" not in bundle["routes"]
    assert "scenario" not in bundle["routes"]
    assert "scene" in bundle["routes"]


def test_the_global_bundle_carries_every_route():
    bundle = routing.bundle(campaign_meta={}, cfg={}, exists=lambda cid: False, scope="global")
    assert set(bundle["routes"]) == {r.key for r in routing.ROUTES}


@pytest.mark.parametrize("route", [r.key for r in routing.ROUTES])
def test_every_route_is_named_and_described_for_the_picker(route):
    got = routing.route_by_key(route)
    assert got.label and got.label[0].isupper()
    assert got.tasks
