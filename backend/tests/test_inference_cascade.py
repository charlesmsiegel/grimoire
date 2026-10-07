"""The selection, preset and fallback cascade (spec 5.1, 5.2, 5.5), on dicts."""

from __future__ import annotations

from grimoire.store import routing, sampler_presets
from grimoire.store.inference import cascade, keys
from grimoire.store.inference.cascade import Selection

CLEAR = sampler_presets.PRESET_CLEAR


def sel(provider: str, model: str = "m", preset: str = "") -> Selection:
    return Selection(provider, model, preset)


def slot(prefix_fn, provider: str, model: str = "m", preset: str = "") -> dict:
    """The three keys of a selection under `prefix_fn(part)`."""
    return {prefix_fn("provider"): provider, prefix_fn("model"): model,
            prefix_fn("preset"): preset}


def role(r: str, provider: str, model: str = "m", preset: str = "") -> dict:
    return slot(lambda p: keys.role_key(r, p), provider, model, preset)


def fb(r: str, provider: str, model: str = "m", preset: str = "") -> dict:
    return slot(lambda p: keys.fallback_key(r, p), provider, model, preset)


def pin(route: str, provider: str, model: str = "m", preset: str = "") -> dict:
    out = slot(lambda p: keys.pin_key(route, p), provider, model, preset)
    out[keys.use_key(route)] = keys.PIN
    return out


def use(route: str, value: str) -> dict:
    return {keys.use_key(route): value}


def exists(*ids: str):
    live = set(ids)
    return lambda pid: pid in live


ALL = exists("A", "B", "C", "D", "E", "F", "G")
SCENE = routing.route_by_key("scene")
SUMMARY = routing.route_by_key("summary")
TAGLINE = routing.route_by_key("tagline")
CONTINUITY = routing.route_by_key("continuity")


def go(route, campaign=None, glob=None, ex=ALL):
    return cascade.choose(route, campaign=campaign or {}, glob=glob or {}, exists=ex)


# ---- spec 5.1's four rows ----

def test_a_campaign_route_choice_wins():
    campaign = {**pin("summary", "A"), **role("fast", "B")}
    glob = {**pin("summary", "C"), **role("fast", "D")}
    got = go(SUMMARY, campaign, glob)
    assert got.selection == sel("A")
    assert (got.via, got.role, got.scope) == ("route", "", "campaign")
    # a campaign route naming a role resolves through campaign then global
    got = go(SUMMARY, {**use("summary", "primary")}, {**role("primary", "D")})
    assert got.selection == sel("D")
    assert (got.via, got.role, got.scope) == ("role", "primary", "global")
    got = go(SUMMARY, {**use("summary", "primary"), **role("primary", "B")},
             {**role("primary", "D")})
    assert (got.selection, got.scope) == (sel("B"), "campaign")


def test_a_campaign_role_beats_a_global_pin():
    got = go(SUMMARY, role("fast", "B"), {**pin("summary", "C"), **role("fast", "D")})
    assert got.selection == sel("B")
    assert (got.via, got.role, got.scope) == ("role", "fast", "campaign")


def test_a_global_pin_beats_the_global_role():
    got = go(SUMMARY, {}, {**pin("summary", "C"), **role("fast", "D")})
    assert got.selection == sel("C")
    assert (got.via, got.role, got.scope) == ("route", "", "global")


def test_the_global_role_answers_last():
    got = go(SUMMARY, {}, role("fast", "D"))
    assert got.selection == sel("D")
    assert (got.via, got.role, got.scope) == ("role", "fast", "global")


def test_the_campaign_role_considered_is_the_one_the_global_route_names():
    glob = {**use("summary", "primary"), **role("primary", "D"), **role("fast", "E")}
    got = go(SUMMARY, role("fast", "B"), glob)
    assert got.selection == sel("D")
    assert (got.via, got.role, got.scope) == ("role", "primary", "global")


def test_a_campaign_primary_reaches_a_fast_default_route():
    campaign = {**role("primary", "A"), **fb("primary", "B")}
    glob = {**role("primary", "D"), **fb("primary", "F")}
    got = go(SUMMARY, campaign, glob)
    assert got.selection == sel("A")
    assert (got.via, got.role, got.scope) == ("role", "primary", "campaign")
    assert got.fallback == sel("B")
    # spelling the same choice out changes nothing
    assert go(SUMMARY, {**campaign, **use("summary", "fast")}, glob) == got


def test_a_global_pin_beats_a_campaign_role_reached_only_by_inheritance():
    got = go(SUMMARY, role("primary", "A"), {**pin("summary", "C"), **role("primary", "D")})
    assert got.selection == sel("C")
    assert (got.via, got.scope) == ("route", "global")


def test_a_campaign_route_naming_an_empty_role_is_no_selection():
    glob = {**pin("summary", "C"), **fb("fast", "G")}
    got = go(SUMMARY, use("summary", "fast"), glob)
    assert got.selection is None
    assert (got.via, got.scope) == ("", "none")
    assert got.fallback == sel("G")


# ---- inheritance ----

def test_decision_inherits_fast_then_primary():
    r, supplier, scope = cascade.role_selection(
        "decision", campaign={}, glob=role("fast", "E"), exists=ALL)
    assert (r, supplier, scope) == (sel("E"), "fast", "global")
    r, supplier, scope = cascade.role_selection(
        "decision", campaign={}, glob=role("primary", "D"), exists=ALL)
    assert (r, supplier, scope) == (sel("D"), "primary", "global")
    # the global decision is reached before the campaign's fast is inherited
    r, supplier, scope = cascade.role_selection(
        "decision", campaign=role("fast", "B"), glob=role("decision", "D"), exists=ALL)
    assert (r, supplier, scope) == (sel("D"), "decision", "global")
    r, supplier, scope = cascade.role_selection(
        "decision", campaign=role("fast", "B"), glob=role("primary", "D"), exists=ALL)
    assert (r, supplier, scope) == (sel("B"), "fast", "campaign")


def test_embedding_does_not_inherit():
    glob = {**role("primary", "D"), **role("fast", "E")}
    assert cascade.role_selection("embedding", campaign={}, glob=glob, exists=ALL) \
        == (None, "", "none")
    assert cascade.role_selection(
        "embedding", campaign={}, glob=role("embedding", "F"), exists=ALL)[0] == sel("F")


# ---- walking past what is gone ----

def test_a_dangling_pin_is_walked_past():
    glob = {**pin("summary", "GONE"), **role("fast", "D")}
    got = go(SUMMARY, {}, glob)
    assert (got.selection, got.via) == (sel("D"), "role")
    # a dangling campaign pin falls through to the global answer
    got = go(SUMMARY, pin("summary", "GONE"), glob)
    assert got.selection == sel("D")
    # a dangling campaign role is no opinion either
    got = go(SUMMARY, role("fast", "GONE"), glob)
    assert got.selection == sel("D")
    # exists decides on the raw string: a padded id is not stripped into validity
    got = go(SUMMARY, {}, {**pin("summary", " C "), **role("fast", "D")})
    assert got.selection == sel("D")


def test_an_unknown_task_runs_on_primary():
    got = go(None, role("primary", "B"), {**role("primary", "D"), **role("fast", "E")})
    assert got.selection == sel("B")
    assert (got.via, got.role, got.scope) == ("role", "primary", "campaign")
    got = go(None, {}, {**role("primary", "D"), **role("fast", "E")})
    assert (got.selection, got.scope) == (sel("D"), "global")


def test_no_primary_anywhere_is_no_selection():
    got = go(SUMMARY, {}, {})
    assert got.selection is None
    assert (got.scope, got.fallback) == ("none", None)
    assert go(None).selection is None
    # a role route naming a role nobody set is also no selection
    assert go(SUMMARY, {}, use("summary", "primary")).selection is None


def test_a_global_only_route_ignores_the_campaign():
    campaign = {**pin("tagline", "A"), **role("fast", "B")}
    campaign.update(fb("fast", "B"))
    got = go(TAGLINE, campaign, {**role("primary", "D"), **role("fast", "E")})
    assert got.selection == sel("E")
    assert (got.scope, got.fallback) == ("global", None)
    got = go(TAGLINE, campaign, {**role("fast", "D"), **fb("fast", "G")})
    assert (got.selection, got.fallback) == (sel("D"), sel("G"))


# ---- fallback ----

def test_fallback_follows_the_role_the_route_uses():
    glob = {**role("primary", "D"), **role("fast", "E"),
            **fb("primary", "F"), **fb("fast", "G")}
    assert go(SUMMARY, {}, glob).fallback == sel("G")
    assert go(SCENE, {}, glob).fallback == sel("F")
    # the role named by use_<route> wins over the default role
    assert go(SUMMARY, {}, {**glob, **use("summary", "primary")}).fallback == sel("F")
    # a campaign fallback beats the global one
    assert go(SUMMARY, fb("fast", "B"), glob).fallback == sel("B")
    # an unset fast fallback inherits primary's
    assert go(SUMMARY, {}, {**role("fast", "E"), **fb("primary", "F")}).fallback == sel("F")
    # and decision's inherits fast's
    assert go(CONTINUITY, {}, {**role("decision", "E"), **fb("fast", "G")}).fallback \
        == sel("G")


def test_a_pin_uses_its_default_roles_fallback():
    glob = {**pin("summary", "C"), **fb("fast", "G"), **fb("primary", "F")}
    got = go(SUMMARY, {}, glob)
    assert got.selection == sel("C")
    assert got.fallback == sel("G")
    got = go(SUMMARY, pin("summary", "A"), glob)
    assert (got.selection, got.fallback) == (sel("A"), sel("G"))


def test_a_pin_keeps_its_fallback_when_no_role_is_set():
    got = go(SUMMARY, {}, {**pin("summary", "C"), **fb("primary", "F")})
    assert (got.selection, got.fallback) == (sel("C"), sel("F"))
    # and an unknown task falls back on Primary's
    assert go(None, {}, fb("primary", "F")).fallback == sel("F")


def test_a_dangling_fallback_is_none():
    got = go(SUMMARY, {}, {**role("fast", "D"), **fb("fast", "GONE")})
    assert got.selection == sel("D")
    assert got.fallback is None
    # the walk goes on past a dangling one to the next scope
    got = go(SUMMARY, fb("fast", "GONE"), {**role("fast", "D"), **fb("fast", "G")})
    assert got.fallback == sel("G")
    assert cascade.role_fallback("embedding", campaign={}, glob=fb("primary", "F"),
                                 exists=ALL) is None


# ---- presets ----

def preset_go(route, selection, campaign=None, glob=None, known=("p1", "p2", "p3")):
    return cascade.preset_for(route, selection, campaign=campaign or {},
                              glob=glob or {}, known=lambda p: p in known)


def test_route_preset_campaign_then_global_then_selection():
    own = sel("A", preset="p3")
    assert preset_go(SUMMARY, own, {"preset_summary": "p1"}, {"preset_summary": "p2"}) \
        == ("p1", "campaign")
    assert preset_go(SUMMARY, own, {}, {"preset_summary": "p2"}) == ("p2", "global")
    assert preset_go(SUMMARY, own) == ("p3", "connection")
    assert preset_go(SUMMARY, sel("A")) == ("", "none")
    assert preset_go(SUMMARY, None) == ("", "none")
    # a selection's own preset counts only while it is known
    assert preset_go(SUMMARY, sel("A", preset="gone")) == ("", "none")
    # no route: straight to the selection's own preset
    assert preset_go(None, own, {"preset_summary": "p1"}) == ("p3", "connection")


def test_preset_clear_stops_the_walk_with_its_scope():
    own = sel("A", preset="p3")
    assert preset_go(SUMMARY, own, {}, {"preset_summary": CLEAR}) == ("", "global")
    assert preset_go(SUMMARY, own, {"preset_summary": CLEAR}, {"preset_summary": "p2"}) \
        == ("", "campaign")


def test_an_unknown_preset_id_is_no_opinion():
    own = sel("A", preset="p3")
    assert preset_go(SUMMARY, own, {"preset_summary": "gone"}, {"preset_summary": "p2"}) \
        == ("p2", "global")
    assert preset_go(SUMMARY, own, {"preset_summary": "gone"}, {"preset_summary": "gone"}) \
        == ("p3", "connection")


def test_a_global_only_route_ignores_campaign_presets():
    own = sel("A", preset="p3")
    assert preset_go(TAGLINE, own, {"preset_tagline": "p1"}) == ("p3", "connection")
    assert preset_go(TAGLINE, own, {"preset_tagline": CLEAR}, {"preset_tagline": "p2"}) \
        == ("p2", "global")
