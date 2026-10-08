"""The per-task routing registry (#142).

`store/routing.py` is a pure leaf: the routes, the tasks each claims and the
legacy keys they were stored under. Which connection a task runs on is the
inference resolver's answer (`test_inference_resolve.py`); the legacy cascade
that used to live here, and its tests, were retired in inference slice C.
Nothing on disk is involved, which is the point of keeping it pure
(`config.py` imports it for the key list, so it may not import back into the
store).
"""

from __future__ import annotations

import pytest

from grimoire.store import routing

# --- the registry itself ---

def test_every_route_key_has_a_config_key_and_they_agree():
    assert tuple(f"route_{r.key}" for r in routing.LEGACY_ROUTES) == routing.CONFIG_KEYS
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
    keys = {r.key for r in routing.LEGACY_ROUTES}
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


@pytest.mark.parametrize("route", [r.key for r in routing.LEGACY_ROUTES])
def test_every_route_is_named_and_described_for_the_picker(route):
    got = routing.route_by_key(route)
    assert got.label and got.label[0].isupper()
    assert got.tasks


def test_the_continuity_route_is_spelled_as_section_29_spells_it():
    # Capstone spec §29, verbatim: the only home of the continuity tasks, so a
    # third continuity call filed under another route fails here rather than
    # quietly missing from the Continuity checks picker.
    want = routing.Route(
        "continuity", "Continuity checks",
        "The duplicate check beside absorb and the reconciliation sweep after "
        "End Scene or a refresh.",
        ("continuity-identity", "continuity-reconcile"), True)
    got = routing.route_by_key("continuity")
    assert got[:5] == want[:5]
    assert (routing.TASK_ROUTE["continuity-identity"]
            == routing.TASK_ROUTE["continuity-reconcile"] == "continuity")
    others = [t for t in routing.TASK_ROUTE
              if t.startswith("continuity") and t not in want.tasks]
    assert others == []
    # §29: the scene-suggestion task keeps its name.
    assert routing.TASK_ROUTE["suggestions"] == "suggestions"


# --- the fifteen-route registry and the twelve-route legacy view ---

_ORIGINAL_TASKS = {
    "scene": ("chat", "retry", "regenerate", "extend", "director", "replay",
              "continuation", "response-selector"),
    "opener": ("opener",),
    "absorb": ("absorb", "audit"),
    "dossier": ("dossier",),
    "continuity": ("continuity-identity", "continuity-reconcile"),
    "summary": ("rolling-summary", "scene-break-title", "scene-break"),
    "tracker": ("tracker-update",),
    "suggestions": ("suggestions", "intent", "character-from-passage"),
    "voice": ("voice-anchor", "voice-drift"),
    "image": ("image-description",),
    "tagline": ("tagline",),
    "scenario": ("scenario",),
}


def test_three_routes_split_out_of_their_parents():
    assert routing.route("response-selector").key == "speaker"
    assert routing.route("scene-break").key == "scene_break"
    assert routing.route("voice-drift").key == "voice_drift"
    assert routing.route_by_key("speaker").legacy == "scene"
    assert routing.route_by_key("scene_break").legacy == "summary"
    assert routing.route_by_key("voice_drift").legacy == "voice"
    assert "response-selector" not in routing.route_by_key("scene").tasks
    assert routing.TASK_ROUTE["response-selector"] == "speaker"


def test_legacy_routes_keep_their_original_task_lists():
    assert {r.key: r.tasks for r in routing.LEGACY_ROUTES} == _ORIGINAL_TASKS
    assert [r.key for r in routing.LEGACY_ROUTES] == list(_ORIGINAL_TASKS)


def test_legacy_surfaces_still_see_twelve_routes():
    assert len(routing.LEGACY_ROUTES) == 12
    assert tuple(f"route_{r.key}" for r in routing.LEGACY_ROUTES) == routing.CONFIG_KEYS
    assert tuple(
        f"preset_{r.key}" for r in routing.LEGACY_ROUTES) == routing.PRESET_CONFIG_KEYS
    for new in ("speaker", "scene_break", "voice_drift"):
        assert f"route_{new}" not in routing.CONFIG_KEYS
        assert f"preset_{new}" not in routing.PRESET_CONFIG_KEYS
    assert routing.routes_for("global") == routing.LEGACY_ROUTES
    assert all(r.legacy == "" for r in routing.LEGACY_ROUTES)


def test_every_route_declares_operation_and_default_role():
    """A route flips to `decide` and the Decision role only in the task whose
    call sites decide (spec 14): scene-break, voice drift and the speaker pick
    (slice F), and both continuity checks (slice G)."""
    primary = {"scene", "opener", "suggestions", "voice"}
    decide = {"scene_break", "voice_drift", "speaker", "continuity"}
    for r in routing.ROUTES:
        assert r.operation in routing.OPERATIONS
        assert r.default_role in routing.DEFAULT_ROLES
        want = (("decide", "decision") if r.key in decide
                else ("generate", "primary") if r.key in primary
                else ("generate", "fast"))
        assert (r.operation, r.default_role) == want, r.key
    assert routing.route_by_key("image").requires == ("vision",)
    assert len(routing.ROUTES) == 15


def test_the_scene_break_title_is_drafted_on_the_summary_route():
    """The title a YES suggests is prose, not a decision: it rides the
    `summary` route (and so its model), never the Decision role."""
    summary = routing.route_by_key("summary")
    assert summary.tasks == ("rolling-summary", "scene-break-title")
    assert summary.label == "Summaries & scene titles"
    assert summary.hint == ("The live rolling summary, and the title a proposed "
                            "scene break suggests.")
    assert (summary.operation, summary.default_role) == ("generate", "fast")
    assert routing.TASK_ROUTE["scene-break-title"] == "summary"
