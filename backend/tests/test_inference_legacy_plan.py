"""The one planner for the legacy settings layout (slice I, Task 4).

`store.inference.legacy_plan` holds the legacy-to-format-2 mapping (moved,
not copied, from `translate` and `migrate`), the model-facts overlay, the
derived reasoning presets and the notes for what cannot be carried over. It
changes nothing at runtime in this task: `translate` and `migrate` delegate
to it, so the mapping it plans is the one the migration has always persisted
(N2), and the derivation is planned but persisted by nothing yet.

`MAPPED` pins the move. It was recorded from `migrate.global_fields` and
`migrate.campaign_fields` on the tree BEFORE this task changed them, one
literal per state of both frozen baselines, and is never regenerated: a
change that makes `test_the_global_mapping_is_translates` fail has changed
what the migration writes.

Invented names (Realm, Saltmarch, Mara) and fake keys only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import grimoire.store as store
from grimoire import llm_reasoning, llm_sampling
from grimoire.store import (
    config,
    inference_keys,
    llm_connections,
    routing,
    sampler_presets,
)
from grimoire.store.frontmatter import parse_frontmatter
from grimoire.store.inference import facts, legacy_plan, migrate, retired
from tests import inference_baseline as base
from tests import inference_baseline_c as base_c
from tests.inference_fixtures import legacy_store

keys = inference_keys
GLM_URL = base_c.GLM_URL
CLEAR = sampler_presets.PRESET_CLEAR

#: What the migration persisted for each frozen state before this task: the
#: non-empty global fields (every other `OWNED_GLOBAL_KEYS` member is ""),
#: the campaign's fields, and whether the first read raised on an unreadable
#: connection (`inference_baseline.UNREADABLE`, recorded after removing it).
MAPPED: dict[str, dict] = {
    "claude_active": {
        "raises": False, "campaign": {},
        "global": {"role_embedding_model": "embed-small", "role_embedding_provider": "local",
                   "role_primary_model": "opus", "role_primary_provider": "claude"}},
    "embed_openrouter_legacy": {
        "raises": False, "campaign": {},
        "global": {"role_primary_model": "vendor/active",
                   "role_primary_provider": "openrouter"}},
    "embed_with_dangling": {
        "raises": True, "campaign": {},
        "global": {"role_embedding_model": "embed-small", "role_embedding_provider": "local",
                   "role_primary_model": "opus", "role_primary_provider": "claude",
                   "use_dossier": "model", "use_dossier_provider": "spare",
                   "use_voice": "model", "use_voice_drift": "model",
                   "use_voice_drift_provider": "gone", "use_voice_provider": "gone"}},
    "fresh": {
        "raises": False, "campaign": {},
        "global": {"role_primary_model": "vendor/active",
                   "role_primary_provider": "openrouter"}},
    "glm_max_under_route_preset": {
        "raises": False, "campaign": {},
        "global": {"preset_scene_break": "cold", "role_primary_model": "glm-5.3",
                   "role_primary_preset": "warm", "role_primary_provider": "glm"}},
    "glm_reasoning": {
        "raises": False, "campaign": {},
        "global": {"role_primary_model": "glm-5.3", "role_primary_preset": "warm",
                   "role_primary_provider": "glm"}},
    "keyless": {
        "raises": False, "campaign": {},
        "global": {"role_decision_fallback_provider": "nokey",
                   "role_fast_fallback_provider": "nokey",
                   "role_primary_fallback_provider": "nokey",
                   "role_primary_model": "vendor/active",
                   "role_primary_provider": "openrouter",
                   "use_absorb": "model", "use_absorb_provider": "nokey"}},
    "no_active": {
        "raises": False,
        "campaign": {"preset_speaker": "warm", "use_absorb": "model",
                     "use_absorb_provider": "deleted", "use_scene": "model",
                     "use_scene_model": "local-model", "use_scene_preset": "warm",
                     "use_scene_provider": "local", "use_speaker": "model",
                     "use_speaker_model": "local-model", "use_speaker_preset": "warm",
                     "use_speaker_provider": "local", "use_tracker": "model",
                     "use_tracker_model": "vendor/spare", "use_tracker_provider": "spare"},
        "global": {"preset_scene_break": "⁣none", "preset_speaker": "cold",
                   "role_decision_fallback_model": "vendor/spare",
                   "role_decision_fallback_provider": "spare",
                   "role_fast_fallback_model": "vendor/spare",
                   "role_fast_fallback_provider": "spare",
                   "role_primary_fallback_model": "vendor/spare",
                   "role_primary_fallback_provider": "spare",
                   "use_dossier": "model", "use_dossier_model": "local-model",
                   "use_dossier_preset": "warm", "use_dossier_provider": "local",
                   "use_scene_break": "model", "use_scene_break_model": "opus",
                   "use_scene_break_provider": "claude",
                   "use_summary": "model", "use_summary_model": "opus",
                   "use_summary_provider": "claude",
                   "use_tracker": "model", "use_tracker_model": "local-model",
                   "use_tracker_preset": "warm", "use_tracker_provider": "local",
                   "use_voice": "model", "use_voice_drift": "model",
                   "use_voice_drift_provider": "gone", "use_voice_provider": "gone"}},
    "openrouter_glm_with_effort": {
        "raises": False, "campaign": {},
        "global": {"role_primary_model": "z-ai/glm-5.3",
                   "role_primary_provider": "router-glm"}},
    "pre_connections": {"raises": False, "campaign": {}, "global": {}},
    "prefill_strict": {
        "raises": False, "campaign": {},
        "global": {"role_primary_model": "vendor/active",
                   "role_primary_provider": "openrouter"}},
    "routed": {
        "raises": False,
        "campaign": {"preset_speaker": "warm", "use_absorb": "model",
                     "use_absorb_provider": "deleted", "use_scene": "model",
                     "use_scene_model": "local-model", "use_scene_preset": "warm",
                     "use_scene_provider": "local", "use_speaker": "model",
                     "use_speaker_model": "local-model", "use_speaker_preset": "warm",
                     "use_speaker_provider": "local", "use_tracker": "model",
                     "use_tracker_model": "vendor/spare", "use_tracker_provider": "spare"},
        "global": {"preset_scene_break": "⁣none", "preset_speaker": "cold",
                   "role_decision_fallback_model": "vendor/spare",
                   "role_decision_fallback_provider": "spare",
                   "role_fast_fallback_model": "vendor/spare",
                   "role_fast_fallback_provider": "spare",
                   "role_primary_fallback_model": "vendor/spare",
                   "role_primary_fallback_provider": "spare",
                   "role_primary_model": "vendor/active", "role_primary_preset": "warm",
                   "role_primary_provider": "openrouter",
                   "use_dossier": "model", "use_dossier_model": "local-model",
                   "use_dossier_preset": "warm", "use_dossier_provider": "local",
                   "use_scene_break": "model", "use_scene_break_model": "opus",
                   "use_scene_break_provider": "claude",
                   "use_summary": "model", "use_summary_model": "opus",
                   "use_summary_provider": "claude",
                   "use_tracker": "model", "use_tracker_model": "local-model",
                   "use_tracker_preset": "warm", "use_tracker_provider": "local",
                   "use_voice": "model", "use_voice_drift": "model",
                   "use_voice_drift_provider": "gone", "use_voice_provider": "gone"}},
    "vision_off": {
        "raises": False, "campaign": {},
        "global": {"role_primary_model": "vendor/active",
                   "role_primary_provider": "openrouter"}},
    "vision_on_catalog_no": {
        "raises": False, "campaign": {},
        "global": {"role_primary_model": "vendor/active",
                   "role_primary_provider": "openrouter"}},
    "whitespace": {
        "raises": False, "campaign": {},
        "global": {"role_decision_fallback_provider": "  spare  ",
                   "role_embedding_model": "embed-small", "role_embedding_provider": "local",
                   "role_fast_fallback_provider": "  spare  ",
                   "role_primary_fallback_provider": "  spare  ",
                   "role_primary_model": "vendor/active",
                   "role_primary_provider": "openrouter",
                   "use_dossier": "model", "use_dossier_model": "local-model",
                   "use_dossier_provider": "local"}},
}

#: Every frozen state, from both baselines: name -> its builder.
ALL_STATES = {**base.STATES, **base_c.STATES}


@pytest.fixture()
def home(monkeypatch, tmp_path):
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("GRIMOIRE_HOME", str(root))
    legacy_store(root)
    return root


def _plan(cfg: dict | None = None, mode: str = "migrate") -> legacy_plan.Plan:
    cfg = config.read_config() if cfg is None else cfg
    return legacy_plan.global_plan(cfg, legacy_plan.lookup(mode=mode),
                                   sampler_presets.read_preset)


def _campaign(meta: dict, *, glob: dict | None = None, global_current: bool = True,
              cid: str = "saltmarch", lookup=None, presets=None) -> legacy_plan.Plan:
    """`campaign_plan` for `meta` under the global view `glob` (none set)."""
    return legacy_plan.campaign_plan(
        meta, glob={} if glob is None else glob, global_current=global_current,
        lookup=legacy_plan.lookup(mode="migrate") if lookup is None else lookup,
        presets=sampler_presets.read_preset if presets is None else presets, cid=cid)


def _glm(name: str, effort: str, preset: str = "", model: str = "glm-5.3") -> str:
    return llm_connections.create_connection(
        "openai_compatible", name, base_url=GLM_URL, api_key="sk-test-glm", model=model,
        reasoning_effort=effort, sampler_preset=preset)


def _derived(plan: legacy_plan.Plan) -> dict[str, legacy_plan.Derived]:
    return {d.id: d for d in plan.presets}


def _wire(conn: dict, params: dict) -> dict:
    """What `conn` sends with a preset of `params` attached."""
    return llm_sampling.effective({**conn, "sampling": {"params": params}})["effective"]


def _forbid(_conn_id: str):
    raise AssertionError("a retired scope reads nothing")


# ---- the derivation ----
def test_derived_name_and_id():
    assert legacy_plan.derived_name("Warm", "high") == "Warm · reasoning high"
    assert legacy_plan.derived_name("", "low") == "Reasoning low"
    d = legacy_plan.derive({"name": "Warm", "params": {"temperature": 0.9}}, "high",
                           lambda _: None)
    assert d == legacy_plan.Derived("warm-reasoning-high", "Warm · reasoning high",
                                    {"temperature": 0.9, "reasoning_effort": "high"})


def test_a_taken_id_gets_a_deterministic_suffix():
    taken = {"name": "Warm · reasoning high", "params": {"temperature": 0.1}}
    d1 = legacy_plan.derive({"name": "Warm", "params": {"temperature": 0.9}}, "high",
                            lambda _: taken)
    d2 = legacy_plan.derive({"name": "Warm", "params": {"temperature": 0.9}}, "high",
                            lambda _: taken)
    assert d1 == d2 and d1.id.startswith("warm-reasoning-high-")
    assert len(d1.id) == len("warm-reasoning-high-") + 8


def test_an_id_holding_the_same_preset_is_reused():
    """A derived preset already written (a resumed retirement, another
    device) is the same derivation: its id is kept, not suffixed."""
    same = {"name": "Warm · reasoning high",
            "params": {"temperature": 0.9, "reasoning_effort": "high"}}
    d = legacy_plan.derive({"name": "Warm", "params": {"temperature": 0.9}}, "high",
                           lambda _: same)
    assert d.id == "warm-reasoning-high"


def test_bases_whose_names_slug_alike_get_distinct_stable_ids():
    """M-4: three bases with equal params whose derived names slugify to one
    slug ("Warm", "Warm!", "Warm?"). The first takes the slug; the others are
    suffixed by a digest that includes their own name, so neither takes the
    other's id, and every id is the same on a second plan."""
    bases = [{"id": pid, "name": name, "params": {"temperature": 0.9}}
             for pid, name in (("warm", "Warm"), ("warm-2", "Warm!"), ("warm-3", "Warm?"))]

    def run() -> list[legacy_plan.Derived]:
        made: dict[str, legacy_plan.Derived] = {}

        def existing(pid: str) -> dict | None:
            got = made.get(pid)
            return {"name": got.name, "params": got.params} if got else None

        out = []
        for each in bases:
            d = legacy_plan.derive(each, "high", existing)
            made.setdefault(d.id, d)
            out.append(d)
        return out

    first, second = run(), run()
    assert first == second
    assert len({d.id for d in first}) == 3
    assert first[0].id == "warm-reasoning-high"
    assert all(d.id.startswith("warm-reasoning-high-") for d in first[1:])


def test_identical_derivations_from_two_bases_share_one_id():
    """The suffix is keyed on what the preset IS (its name and params), so
    two bases that derive the same preset collapse even when the slug is held
    by another preset."""
    foreign = {"warm-reasoning-high": {"name": "Someone else's", "params": {}}}
    one = legacy_plan.derive({"id": "warm", "name": "Warm", "params": {"temperature": 0.9}},
                             "high", foreign.get)
    two = legacy_plan.derive({"id": "warm-2", "name": "Warm",
                              "params": {"temperature": 0.9}}, "high", foreign.get)
    assert one == two and one.id.startswith("warm-reasoning-high-")


def test_representable_is_computed_from_the_two_vocabularies():
    computed = tuple(e for e in llm_reasoning.GLM_EFFORTS if e in llm_sampling.REASONING)
    assert computed == legacy_plan.REPRESENTABLE
    assert computed == ("low", "high", "max")           # ratification item 1


def test_derived_presets_keep_the_wire_in_memory(home):
    """Primary on `glm` (effort high, its own preset "warm"), the role
    fallback on `glm2` (effort low, no preset), and Saltmarch pinning `scene`
    to glm at a preset id that names nothing: each slot is repointed at a
    derived preset that carries the effort, and each sends exactly what the
    legacy connection sent with its own preset."""
    sampler_presets.create_preset("Warm", {"temperature": 0.9})
    _glm("glm", "high", preset="warm")
    _glm("glm2", "low")
    config.write_config(active_connection_id="glm", fallback_connection_id="glm2")

    cfg = config.read_config()
    plan = _plan(cfg)

    assert plan.mapped[keys.role_key("primary", "preset")] == "warm"     # N2: the base kept
    assert plan.repoint[keys.role_key("primary", "preset")] == "warm-reasoning-high"
    for role in keys.GENERATIVE_ROLES:
        assert plan.repoint[keys.fallback_key(role, "preset")] == "reasoning-low"
    derived = _derived(plan)
    assert derived["warm-reasoning-high"] == legacy_plan.Derived(
        "warm-reasoning-high", "Warm · reasoning high",
        {"temperature": 0.9, "reasoning_effort": "high"})
    assert derived["reasoning-low"] == legacy_plan.Derived(
        "reasoning-low", "Reasoning low", {"reasoning_effort": "low"})
    assert plan.notes == ()

    saltmarch = {keys.FORMAT_KEY: keys.CURRENT_FORMAT, keys.use_key("scene"): keys.PIN,
                 keys.pin_key("scene", "provider"): "glm",
                 keys.pin_key("scene", "model"): "glm-5.3",
                 keys.pin_key("scene", "preset"): "gone"}
    camp = _campaign(saltmarch, glob=legacy_plan.planned(cfg, plan))
    assert camp.mapped == {}
    assert camp.repoint == {keys.pin_key("scene", "preset"): "reasoning-high"}
    assert _derived(camp)["reasoning-high"].params == {"reasoning_effort": "high"}

    # The wire: the legacy connection with its own preset, against the same
    # connection with its effort gone and the derived preset attached.
    for conn_id, base_params, derived_id in (("glm", {"temperature": 0.9}, "warm-reasoning-high"),
                                             ("glm2", {}, "reasoning-low")):
        raw = llm_connections.read_connection_raw(conn_id)
        legacy = _wire(raw, base_params)
        assert legacy["reasoning_effort"] == raw["reasoning_effort"]
        assert _wire({**raw, "reasoning_effort": ""}, derived[derived_id].params) == legacy
    raw = llm_connections.read_connection_raw("glm")
    assert (_wire({**raw, "reasoning_effort": ""}, _derived(camp)["reasoning-high"].params)
            == _wire(raw, {}))


def test_identical_derivations_collapse(home):
    sampler_presets.create_preset("Warm", {"temperature": 0.9})
    _glm("glm", "high", preset="warm")
    config.write_config(active_connection_id="glm", route_dossier="glm")

    plan = _plan()

    assert plan.repoint[keys.role_key("primary", "preset")] == "warm-reasoning-high"
    assert plan.repoint[keys.pin_key("dossier", "preset")] == "warm-reasoning-high"
    assert [d.id for d in plan.presets] == ["warm-reasoning-high"]


def test_two_bases_with_one_name_do_not_share_an_id(home):
    """Two different presets that derive the same name in one plan: the
    second takes the suffixed id, deterministically, rather than one id naming
    two sets of params."""
    sampler_presets.create_preset("Warm", {"temperature": 0.9})
    sampler_presets.create_preset("Warm", {"temperature": 0.7})        # id warm-2
    _glm("glm", "high", preset="warm")
    _glm("glm-b", "high", preset="warm-2")
    config.write_config(active_connection_id="glm", route_dossier="glm-b")

    first, second = _plan(), _plan()

    ids = [d.id for d in first.presets]
    assert len(ids) == 2 and len(set(ids)) == 2
    assert first == second


def test_a_preset_that_already_sets_reasoning_is_left_alone(home):
    sampler_presets.create_preset("Hot", {"temperature": 1.2, "reasoning_effort": "low"})
    _glm("glm", "high", preset="hot")
    config.write_config(active_connection_id="glm")

    plan = _plan()

    assert plan.repoint == {} and plan.presets == () and plan.notes == ()


def test_openrouter_and_non_glm_slots_derive_nothing(home):
    """The `openrouter_glm_with_effort` shape (a GLM id on OpenRouter, which
    never sent the field), and an `openai_compatible` model that is not GLM."""
    llm_connections.create_connection("openrouter", "router glm", api_key="sk-test",
                                      model="z-ai/glm-5.3", reasoning_effort="high")
    _glm("local", "high", model="local-model")
    config.write_config(active_connection_id="router-glm", route_dossier="local")

    plan = _plan()

    assert plan.repoint == {} and plan.presets == () and plan.notes == ()


def test_an_effort_glm_never_took_derives_nothing(home):
    _glm("glm", "medium")
    config.write_config(active_connection_id="glm")
    assert _plan().repoint == {}


def test_max_is_derived(home):
    """Ratification item 1: `max` is a preset effort, sent to GLM only, so a
    GLM connection at `max` is repointed like any other level."""
    _glm("glm", "max")
    config.write_config(active_connection_id="glm")

    plan = _plan()

    assert plan.repoint[keys.role_key("primary", "preset")] == "reasoning-max"
    assert _derived(plan)["reasoning-max"] == legacy_plan.Derived(
        "reasoning-max", "Reasoning max", {"reasoning_effort": "max"})
    assert plan.notes == ()


# ---- the notes ----
def _format2(**fields) -> dict:
    return {keys.FORMAT_KEY: keys.CURRENT_FORMAT, **fields}


def test_a_route_preset_over_a_glm_effort_is_noted(home):
    """A route-level preset that sets no reasoning effort is shared by the
    route's primary and fallback, so it is not derived (ratification item 3):
    the effort it hid is noted instead, once per (scope, route, provider)."""
    sampler_presets.create_preset("Warm", {"temperature": 0.9})
    sampler_presets.create_preset("Cold", {"temperature": 0.2})
    _glm("glm", "high", preset="warm")
    cfg = _format2(role_primary_provider="glm", role_primary_model="glm-5.3",
                   role_primary_preset="warm", preset_summary="cold")

    plan = _plan(cfg)

    [note] = plan.notes
    assert (note.kind, note.scope, note.subject, note.provider_id, note.effort) == (
        "route_preset", "global", "summary", "glm", "high")
    assert note.id == retired.note_id("global", "summary", "glm", "high", "route_preset")
    assert "was not carried over" in note.text
    # The route preset itself is never repointed; the role's own slot is.
    assert set(plan.repoint) == {keys.role_key("primary", "preset")}


def test_a_cleared_route_preset_is_noted_and_a_dangling_one_is_not(home):
    """`PRESET_CLEAR` stops the walk at no preset, so the effort it hid is
    lost too. A route preset that names nothing is no opinion: the
    selection's own (derived) preset applies, and nothing is lost."""
    _glm("glm", "low")
    cfg = _format2(role_primary_provider="glm", role_primary_model="glm-5.3",
                   preset_summary=CLEAR, preset_tracker="nosuch")

    plan = _plan(cfg)

    assert [(n.subject, n.effort) for n in plan.notes] == [("summary", "low")]


def test_a_route_preset_over_a_glm_fallback_is_noted(home):
    """The route preset follows the route onto its fallback, so a GLM
    fallback under it loses its effort as well: noted under that provider."""
    sampler_presets.create_preset("Cold", {"temperature": 0.2})
    _glm("glm2", "low")
    cfg = _format2(role_primary_provider="openrouter", role_primary_model="vendor/active",
                   role_fast_fallback_provider="glm2", role_fast_fallback_model="glm-5.3",
                   preset_summary="cold")

    plan = _plan(cfg)

    assert [(n.subject, n.provider_id, n.effort) for n in plan.notes] == [
        ("summary", "glm2", "low")]


def test_a_campaign_route_preset_is_noted_in_the_campaigns_scope(home):
    sampler_presets.create_preset("Cold", {"temperature": 0.2})
    _glm("glm", "high")
    meta = _format2(use_tracker=keys.PIN, use_tracker_provider="glm",
                    use_tracker_model="glm-5.3", preset_tracker="cold")

    plan = _campaign(meta)

    [note] = plan.notes
    assert (note.scope, note.subject, note.provider_id) == (
        "campaign:saltmarch", "tracker", "glm")


def test_a_campaign_route_preset_over_an_inherited_selection_is_noted(home):
    """I-1 (a): a legacy campaign that sets only `preset_scene: cold`, under a
    global Primary on GLM at `high`. The campaign's scene route (and
    `speaker`, split from it) runs on the inherited Primary under the
    campaign's preset, which sets no effort -- a loss noted in the campaign's
    scope, which the global plan cannot see."""
    sampler_presets.create_preset("Warm", {"temperature": 0.9})
    sampler_presets.create_preset("Cold", {"temperature": 0.2})
    _glm("glm", "high", preset="warm")
    config.write_config(active_connection_id="glm")
    cfg = config.read_config()
    glob_plan = _plan(cfg)
    assert glob_plan.notes == ()

    camp = _campaign({"preset_scene": "cold"}, glob=legacy_plan.planned(cfg, glob_plan),
                     global_current=False)

    assert [(n.scope, n.subject, n.provider_id, n.effort, n.kind) for n in camp.notes] == [
        ("campaign:saltmarch", "scene", "glm", "high", "route_preset"),
        ("campaign:saltmarch", "speaker", "glm", "high", "route_preset")]


def test_a_campaign_pin_under_a_global_route_preset_is_noted(home):
    """I-1 (b): a campaign pins `tracker` to a GLM provider, and only
    `config.md` sets a no-effort `preset_tracker`, which the pin runs under.
    The global scope's own tracker selection is not GLM, so only the campaign
    loses anything, and the note is the campaign's."""
    sampler_presets.create_preset("Cold", {"temperature": 0.2})
    _glm("glm", "low")
    glob = _format2(role_primary_provider="openrouter", role_primary_model="vendor/active",
                    preset_tracker="cold")
    meta = _format2(use_tracker=keys.PIN, use_tracker_provider="glm",
                    use_tracker_model="glm-5.3")

    assert _plan(glob).notes == ()
    camp = _campaign(meta, glob=glob)

    assert [(n.scope, n.subject, n.provider_id, n.effort) for n in camp.notes] == [
        ("campaign:saltmarch", "tracker", "glm", "low")]


def test_a_global_note_is_not_repeated_per_campaign(home):
    """A global route preset over the global scope's own GLM selection is the
    global plan's note; a campaign that runs that same selection under it
    adds none of its own."""
    sampler_presets.create_preset("Cold", {"temperature": 0.2})
    _glm("glm", "high")
    glob = _format2(role_primary_provider="glm", role_primary_model="glm-5.3",
                    preset_tracker="cold")
    meta = _format2(use_tracker=keys.PIN, use_tracker_provider="glm",
                    use_tracker_model="glm-5.3")

    assert [(n.scope, n.subject) for n in _plan(glob).notes] == [("global", "tracker")]
    assert _campaign(meta, glob=glob).notes == ()


#: I-A: one `openai_compatible` provider, `glm` (legacy effort `low`), run on a
#: model that is not GLM at the global scope and on GLM at the campaign, under
#: a global route preset that sets no effort. The global plan notes nothing
#: (its model never took the effort), so the campaign's loss is the
#: campaign's to note -- in each of the three shapes a campaign reaches it by.
_SHARED_PROVIDER = {
    "campaign pin": (
        {"role_primary_provider": "glm", "role_primary_model": "qwen3", "preset_tracker": "cold"},
        {"use_tracker": keys.PIN, "use_tracker_provider": "glm", "use_tracker_model": "glm-5.3"},
        "tracker"),
    "campaign role override": (
        {"role_primary_provider": "glm", "role_primary_model": "qwen3", "preset_scene": "cold"},
        {"role_primary_provider": "glm", "role_primary_model": "glm-5.3"},
        "scene"),
    "inherited global fallback": (
        {"role_primary_provider": "openrouter", "role_primary_model": "vendor/active",
         "role_fast_fallback_provider": "glm", "role_fast_fallback_model": "qwen3",
         "preset_tracker": "cold"},
        {"use_tracker": keys.PIN, "use_tracker_provider": "glm", "use_tracker_model": "glm-5.3"},
        "tracker"),
}


@pytest.mark.parametrize("shape", sorted(_SHARED_PROVIDER))
def test_a_provider_the_global_scope_runs_off_glm_is_noted_for_the_campaign(home, shape):
    sampler_presets.create_preset("Cold", {"temperature": 0.2})
    _glm("glm", "low")
    glob_fields, meta_fields, route = _SHARED_PROVIDER[shape]
    glob = _format2(**glob_fields)

    assert _plan(glob).notes == ()
    camp = _campaign(_format2(**meta_fields), glob=glob)

    assert [(n.scope, n.subject, n.provider_id, n.effort) for n in camp.notes] == [
        ("campaign:saltmarch", route, "glm", "low")]


@pytest.mark.parametrize("shape", sorted(_SHARED_PROVIDER))
def test_a_provider_the_global_scope_runs_on_glm_is_noted_once(home, shape):
    """The same shapes with the global scope on GLM as well: the global plan
    notes the provider on that route, and the campaign adds nothing."""
    sampler_presets.create_preset("Cold", {"temperature": 0.2})
    _glm("glm", "low")
    glob_fields, meta_fields, route = _SHARED_PROVIDER[shape]
    glob = _format2(**{k: ("glm-5.3" if v == "qwen3" else v) for k, v in glob_fields.items()})

    assert [(n.scope, n.subject, n.provider_id) for n in _plan(glob).notes
            if n.subject == route] == [("global", route, "glm")]
    assert _campaign(_format2(**meta_fields), glob=glob).notes == ()


def test_a_campaign_plan_names_its_campaign():
    """M-1: `cid` is required -- a defaulted scope would give every
    campaign's notes one shared id."""
    with pytest.raises(TypeError):
        legacy_plan.campaign_plan({}, glob={}, global_current=True,  # type: ignore[call-arg]
                                  lookup=_forbid, presets=_forbid)


def test_notes_have_stable_ids(home):
    sampler_presets.create_preset("Cold", {"temperature": 0.2})
    _glm("glm", "high")
    config.write_config(active_connection_id="glm", preset_summary="cold")

    first, second = _plan(), _plan()

    # The legacy `preset_summary` reaches both routes split from `summary`.
    assert [n.subject for n in first.notes] == ["summary", "scene_break"]
    assert first.notes == second.notes
    assert len({n.id for n in first.notes}) == len(first.notes)
    for n in first.notes:
        # The id is about what the note says was lost, never its wording (N14).
        assert n.id == retired.note_id(n.scope, n.subject, n.provider_id, n.effort, n.kind)


# ---- by state ----
def test_a_retired_scope_plans_nothing(home):
    cfg = _format2(**{legacy_plan.RETIRED_KEY: "1"}, role_primary_provider="glm",
                   role_primary_model="glm-5.3", preset_summary="cold")
    assert legacy_plan.global_plan(cfg, _forbid, _forbid) == legacy_plan.empty()
    meta = _format2(**{legacy_plan.RETIRED_KEY: "1"}, use_scene=keys.PIN,
                    use_scene_provider="glm", use_scene_model="glm-5.3")
    assert _campaign(meta, glob=cfg, lookup=_forbid, presets=_forbid) == legacy_plan.empty()
    assert legacy_plan.Plan({}, {}, {}, (), ()) == legacy_plan.empty()


def test_the_empty_plan_is_fresh_every_time():
    """M-2: no caller can mutate the empty plan another holds."""
    first = legacy_plan.empty()
    first.mapped["role_primary_provider"] = "glm"
    assert legacy_plan.empty() == legacy_plan.Plan({}, {}, {}, (), ())


def test_a_marked_unretired_scope_plans_the_derivation_only(home):
    _glm("glm", "high")
    cfg = _format2(role_primary_provider="glm", role_primary_model="glm-5.3")
    plan = _plan(cfg)
    assert plan.mapped == {} and plan.facts == {}
    assert plan.repoint == {keys.role_key("primary", "preset"): "reasoning-high"}

    meta = _format2(use_scene=keys.PIN, use_scene_provider="glm", use_scene_model="glm-5.3")
    camp = _campaign(meta, glob=cfg)
    assert camp.mapped == {}
    assert camp.repoint == {keys.pin_key("scene", "preset"): "reasoning-high"}


def test_an_unmarked_campaign_under_a_current_store_is_mapped_and_derived(home):
    sampler_presets.create_preset("Warm", {"temperature": 0.9})
    _glm("glm", "high", preset="warm")
    meta = {"route_scene": "glm"}

    plan = _campaign(meta)

    assert plan.mapped[keys.pin_key("scene", "preset")] == "warm"
    assert plan.repoint[keys.pin_key("scene", "preset")] == "warm-reasoning-high"
    assert plan.repoint[keys.pin_key("speaker", "preset")] == "warm-reasoning-high"


def test_the_migration_never_persists_a_repoint(home):
    """N2: a legacy GLM store migrated without retirement keeps the slot's
    base preset, and every preset key it persisted names a preset file that
    exists -- no derived id is written before its preset is."""
    sampler_presets.create_preset("Warm", {"temperature": 0.9})
    _glm("glm", "high", preset="warm")
    _glm("glm2", "low")
    config.write_config(active_connection_id="glm", fallback_connection_id="glm2",
                        route_dossier="glm")

    assert migrate.ensure().state == "done"

    raw, _ = parse_frontmatter((home / "config.md").read_text(encoding="utf-8"))
    assert raw[keys.role_key("primary", "preset")] == "warm"
    assert raw[keys.fallback_key("primary", "preset")] == ""
    preset_keys = [k for k in raw if k.endswith("_preset") or k.startswith("preset_")]
    for k in preset_keys:
        assert raw[k] in ("", CLEAR) or sampler_presets.read_preset(raw[k]) is not None, k
    assert [p["id"] for p in sampler_presets.list_presets()] == ["warm"]


@pytest.mark.parametrize("state", sorted(ALL_STATES))
def test_the_global_mapping_is_translates(state, tmp_path):
    """`Plan.mapped` is what the migration persisted before this task, for
    every state of both frozen baselines (`MAPPED`, never regenerated)."""
    want = MAPPED[state]
    with base.client_at(tmp_path) as _client:
        ctx = ALL_STATES[state](_client)
        if want["raises"]:
            with pytest.raises(llm_connections.ConnectionUnreadableError):
                _plan()
            (store.home() / "llm_connections" / f"{base.UNREADABLE[state]}.md").unlink()
        # One read, as recorded: `pre_connections` seeds its connections (and
        # writes `active_connection_id`) on the first lookup, after it.
        cfg = config.read_config()
        plan = _plan(cfg)
        assert set(plan.mapped) == set(migrate.OWNED_GLOBAL_KEYS)
        assert {k: v for k, v in plan.mapped.items() if v != ""} == want["global"]
        assert migrate.global_fields(cfg, migrate._lookup()) == plan.mapped
        meta = store.campaigns.read_campaign(ctx["cid"])["meta"]
        camp = _campaign(meta, glob=legacy_plan.planned(cfg, plan), global_current=False,
                         cid=ctx["cid"])
        assert camp.mapped == want["campaign"]
        assert migrate.campaign_fields(meta, migrate._lookup()) == camp.mapped


def test_the_facts_overlay_is_step_3s(home):
    """`Plan.facts` holds, as values, what the migration's step 3 writes into
    each model's facts: the legacy `vision`, `prefill` and `post_process`
    that differ from the defaults, keyed by the model they run (`opus` for
    an unset Claude model)."""
    llm_connections.update_connection("openrouter", api_key="sk-test", model="vendor/active",
                                      vision="off", prefill=True, post_process="strict")
    llm_connections.update_connection("claude", model="", vision="on")
    _glm("glm", "high")                                       # states nothing

    plan = _plan()

    assert plan.facts == {
        "openrouter": {"vendor/active": {"vision": "off", "prefill": True,
                                         "post_process": "strict"}},
        "claude": {"opus": {"vision": "on"}},
    }
    assert migrate.ensure().state == "done"
    for conn_id, by_model in plan.facts.items():
        rev = llm_connections.read_connection_raw(conn_id)["rev"]
        for model, stated in by_model.items():
            written = facts.of(conn_id, model, rev)
            assert {f: written[f] for f in stated} == stated


@pytest.mark.parametrize("state", sorted(ALL_STATES))
def test_legacy_embeds_agrees_with_embed_space_resolve(state, tmp_path):
    with base.client_at(tmp_path) as client:
        ALL_STATES[state](client)
        for cfg in (config.read_config(), *base.EMBEDDING_CFGS.values()):
            assert legacy_plan.legacy_embeds(cfg, legacy_plan.lookup(mode="soft")) == (
                store.embed_space.resolve(cfg) is not None), cfg


def test_legacy_embeds_honours_what_is_known_of_the_model(home):
    """A catalog `no` for `embed` turns the legacy choice off; the user's
    `embed: yes` over it turns it back on -- as `embed_space.resolve` says."""
    llm_connections.create_connection("openai_compatible", "vectors",
                                      base_url="http://localhost:1234/v1")
    cfg = {"embeddings_connection_id": "vectors", "embeddings_model": "embed-small"}
    soft = legacy_plan.lookup(mode="soft")
    assert legacy_plan.legacy_embeds(cfg, soft) is True
    rev = llm_connections.read_connection_raw("vectors")["rev"]
    llm_connections.set_cached_models("vectors", [{"id": "embed-small", "outputs": ["text"]}],
                                      rev)
    assert legacy_plan.legacy_embeds(cfg, legacy_plan.lookup(mode="soft")) is False
    assert store.embed_space.resolve(cfg) is None
    facts.set_overrides("vectors", "embed-small", {"embed": "yes"})
    assert legacy_plan.legacy_embeds(cfg, legacy_plan.lookup(mode="soft")) is True
    assert store.embed_space.resolve(cfg) is not None


# ---- the lookups ----
def _connection_file(home: Path, conn_id: str) -> Path:
    path = home / "llm_connections" / f"{conn_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_the_migrate_lookup_is_cs(home):
    """A12: C's strict, memoised read. A zero-byte file is unreadable, not
    absent, and raises; an absent one answers None."""
    llm_connections.ensure_migrated()
    _connection_file(home, "held").write_bytes(b"")
    lookup = legacy_plan.lookup(mode="migrate")
    with pytest.raises(llm_connections.ConnectionUnreadableError):
        lookup("held")
    assert lookup("nobody") is None
    assert lookup("openrouter")["id"] == "openrouter"


@pytest.mark.parametrize("damage", [b"", b"   \n", b"kind: openai_compatible\nname: glm\n",
                                    b"---\nname: glm\n---\n"],
                         ids=["zero-bytes", "whitespace", "unfenced", "no-kind"])
def test_the_retire_lookup_refuses_what_it_cannot_read(home, damage):
    """N1: a file that is there but holds no record raises; absent is None."""
    llm_connections.ensure_migrated()
    _connection_file(home, "glm").write_bytes(damage)
    lookup = legacy_plan.lookup(mode="retire")
    with pytest.raises(llm_connections.ConnectionUnreadableError):
        lookup("glm")
    assert lookup("nobody") is None


def test_the_soft_lookup_reads_an_unreadable_file_as_absent(home):
    llm_connections.ensure_migrated()
    _connection_file(home, "glm").write_bytes(b"\xff\xfe not text")
    assert legacy_plan.lookup(mode="soft")("glm") is None


def test_the_migrations_readers_are_the_planners(home):
    """`migrate._lookup` and `connection_reader` (the lookup a fork's
    translation reads with, ruling 15) are the planner's migrate mode."""
    llm_connections.ensure_migrated()
    _connection_file(home, "held").write_bytes(b"")
    for reader in (migrate._lookup(), migrate.connection_reader()):
        with pytest.raises(llm_connections.ConnectionUnreadableError):
            reader("held")
        assert reader("nobody") is None


# ---- the marker key ----
def test_the_retired_marker_is_a_config_key(home):
    assert inference_keys.RETIRED_KEY == "inference_retired"
    assert legacy_plan.RETIRED_KEY == inference_keys.RETIRED_KEY
    assert config.read_config()[inference_keys.RETIRED_KEY] == ""
    assert inference_keys.RETIRED_KEY not in routing.CONFIG_KEYS
