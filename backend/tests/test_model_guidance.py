"""Model profiles belong to the measured, frozen scene context."""

import importlib
import shutil

import pytest

from grimoire import prompts
from grimoire.store import campaigns, chronicle, config, context, scenes, worlds
from grimoire.store.context import assemble, layout, macros, pack


@pytest.fixture
def scene(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    sid = scenes.create_scene(cid, "Scene")
    return cid, sid


@pytest.fixture
def template_root(monkeypatch, tmp_path):
    root = tmp_path / "templates"
    shutil.copytree(prompts.templates_dir(), root)
    monkeypatch.setenv("GRIMOIRE_TEMPLATES", str(root))
    prompts._env.cache_clear()
    yield root
    prompts._env.cache_clear()


def test_profile_is_measured_before_packing(scene):
    messages, breakdown = context.compose_turn(*scene, model="glm-5.3")
    row = next(r for r in breakdown["sections"] if r["id"] == "model_guidance")
    assert row["tier"] == pack.LOCK_IN
    assert row["text"] in messages[0]["content"]
    assert row["tokens"] == context.count_tokens(row["text"])
    assert breakdown["total_tokens"] == sum(context.count_tokens(m["content"]) for m in messages)


@pytest.mark.parametrize("model", ["", "unknown", "other/glm-5.3", "../glm-5.3", "GLM-5.3"])
def test_unknown_model_is_exact_noop(scene, model):
    expected = context.compose_turn(*scene)
    assert context.compose_turn(*scene, model=model) == expected


def test_registry_exact_alias_editable_and_safe(template_root):
    guidance = importlib.import_module("grimoire.model_guidance")
    directory = template_root / "scene" / "model_guidance"
    directory.mkdir(exist_ok=True)
    (directory / "custom-model.j2").write_text("Custom guidance", encoding="utf-8")
    (directory / "_helper.j2").write_text("Helper", encoding="utf-8")
    (directory / "nested").mkdir()
    (directory / "nested" / "hidden.j2").write_text("Hidden", encoding="utf-8")
    profiles = guidance.freeze_profiles()
    assert guidance.guidance_for("custom-model", profiles) == "Custom guidance"
    assert guidance.guidance_for("z-ai/glm-5.3", profiles) == profiles["glm-5.3"]
    for model in ("../custom-model", "nested/hidden", "_helper", "vendor/custom-model", ""):
        assert guidance.guidance_for(model, profiles) == ""
    (directory / "custom-model.j2").write_text("Edited guidance", encoding="utf-8")
    assert guidance.guidance_for("custom-model", guidance.freeze_profiles()) == "Edited guidance"
    assert guidance.guidance_for("custom-model", profiles) == "Custom guidance"


def test_layout_disable_removes_all_profiles(scene):
    config.write_config(prompt_layout_enabled="on")
    layout.write_layout([{"id": "model_guidance", "enabled": False}])
    messages, breakdown = context.compose_turn(*scene, model="glm-5.3")
    assert "model_guidance" not in {r["id"] for r in breakdown["sections"]}
    assert messages.for_model("unknown") == messages


def test_variants_repack_frozen_content_and_preserve_extras(scene, template_root, monkeypatch):
    directory = template_root / "scene" / "model_guidance"
    directory.mkdir(exist_ok=True)
    (directory / "long-model.j2").write_text("Long model guidance. " * 300, encoding="utf-8")
    config.write_config(system_prompt="Stable {{random:one,two,three}}", context_budget="0")
    _, base_breakdown = context.compose_turn(*scene)
    config.write_config(context_budget=str(base_breakdown["total_tokens"] + 50))
    appended = (("Result", "system", "A resolved result"),)
    messages, _ = context.compose_turn(*scene, appended=appended)
    captured = []
    messages.on_variant = lambda model, detail: captured.append((model, detail))
    def forbidden(*args, **kwargs):
        raise AssertionError("Fallback reread or rerendered generation inputs")
    monkeypatch.setattr(assemble, "_assemble", forbidden)
    monkeypatch.setattr(config, "read_config", forbidden)
    monkeypatch.setattr(macros, "expand_macros", forbidden)
    monkeypatch.setattr(prompts, "render", forbidden)
    fallback = messages.for_model("long-model")
    assert "Long model guidance." in fallback[0]["content"]
    assert fallback[-1] == {"role": "system", "content": "A resolved result"}
    assert captured[0][1]["total_tokens"] > base_breakdown["total_tokens"]
    assert messages.for_model("unknown") == messages
    fallback[0]["content"] = "mutated"
    assert messages.for_model("long-model")[0]["content"] != "mutated"
    assert [model for model, _ in captured] == ["long-model", "unknown"]


@pytest.mark.parametrize("kind", ["turn", "director", "opener"])
def test_known_to_unknown_and_composer_wrappers(scene, kind):
    if kind == "turn":
        compose = lambda model: context.compose_turn(*scene, model=model)
        build = lambda: context.build_messages(*scene, model="glm-5.3")
    elif kind == "director":
        compose = lambda model: context.compose_director_turn(*scene, "Wait", model=model)
        build = lambda: context.build_director_messages(*scene, "Wait", model="glm-5.3")
    else:
        compose = lambda model: context.compose_opener(*scene, "Wait", model=model)
        build = lambda: context.build_opener_messages(*scene, "Wait", model="glm-5.3")
    messages, _ = compose("glm-5.3")
    assert build() == messages
    assert messages.for_model("unknown") == compose("unknown")[0]


def test_prepared_messages_callbacks_are_once_and_failsoft():
    guidance = importlib.import_module("grimoire.model_guidance")
    calls = []
    def factory(model):
        calls.append(model)
        return [{"role": "system", "content": model}], {"model": model}
    messages = guidance.PreparedMessages("primary", factory)
    observed = []
    def callback(model, breakdown):
        observed.append(model)
        breakdown["model"] = "changed"
        raise RuntimeError("Cannot record prompt")
    messages.on_variant = callback
    messages[0]["content"] = "mutated"
    assert messages.for_model("primary")[0]["content"] == "primary"
    assert messages.for_model("fallback")[0]["content"] == "fallback"
    messages.for_model("fallback")
    assert calls == ["primary", "fallback"]
    assert observed == ["fallback"]


def test_describe_false_unbounded_never_counts(scene, monkeypatch):
    config.write_config(context_budget="0")
    def forbidden(*args):
        raise AssertionError("Unbounded describe=False counted tokens")
    monkeypatch.setattr(context.tokens, "count_tokens", forbidden)
    messages, breakdown = context.compose_turn(*scene, model="glm-5.3", describe=False)
    assert breakdown is None
    assert messages.for_model("unknown")


def test_long_profile_evicts_recap_and_inspector_prices_exact_variant(scene, template_root):
    cid, _ = scene
    chronicle.absorb(cid, {"id": "2026-01-01-past", "summary": "A previous scene. " * 200,
                           "keywords": [], "one_line": "A previous scene. " * 200})
    directory = template_root / "scene" / "model_guidance"
    (directory / "long-model.j2").write_text("Guidance for concrete replies. " * 60, encoding="utf-8")
    config.write_config(context_budget="0")
    _, baseline = context.compose_turn(*scene)
    config.write_config(context_budget=str(baseline["total_tokens"] + 20))
    messages, primary = context.compose_turn(*scene)
    observed = []
    messages.on_variant = lambda model, detail: observed.append(detail)
    outgoing = messages.for_model("long-model")
    rows = {r["id"]: r for r in observed[0]["sections"]}
    assert not next(r for r in primary["sections"] if r["id"] == "story_so_far")["dropped"]
    assert rows["story_so_far"]["dropped"]
    assert not rows["model_guidance"]["dropped"]
    assert observed[0]["total_tokens"] <= observed[0]["budget_tokens"]
    assert observed[0]["total_tokens"] == sum(context.count_tokens(m["content"]) for m in outgoing)


def test_shared_heading_split_and_absent_guidance(scene, monkeypatch):
    a = assemble._assemble(*scene)
    a["data"]["offscene_active"] = [{"name": "Mara", "dossier": "Elsewhere"}]
    a["data"]["offscene_known"] = [{"id": "winifred", "name": "Winifred", "tagline": "Known", "versions": []}]
    wanted = ("off_scene_cast_active", "model_guidance", "off_scene_cast_known")
    selected = {s.id: s for s in assemble.SECTIONS}
    monkeypatch.setattr(assemble, "SECTIONS", [selected[sid] for sid in wanted])
    monkeypatch.setattr(assemble, "_assemble", lambda *args, **kwargs: a)
    messages, detail = context.compose_turn(*scene, model="glm-5.3")
    heading = prompts.render("scene/off_scene_cast_heading.j2").strip()
    assert messages[0]["content"].count(heading) == 2
    without = messages.for_model("unknown")
    assert without[0]["content"].count(heading) == 1
    assert detail["total_tokens"] == context.count_tokens(messages[0]["content"])


def test_profile_and_base_macros_are_frozen_for_generation(scene, template_root, monkeypatch):
    directory = template_root / "scene" / "model_guidance"
    (directory / "macro-model.j2").write_text('{% raw %}{{random:Mara,Winifred}}{% endraw %}', encoding="utf-8")
    config.write_config(system_prompt="The clue: {{random:Mara,Winifred}}")
    messages, _ = context.compose_turn(*scene)
    expected_base = next(r["text"] for r in messages.breakdown["sections"] if r["id"] == "global_system_prompt")
    (directory / "macro-model.j2").write_text("Changed after composition", encoding="utf-8")
    def forbidden(*args, **kwargs):
        raise AssertionError("Macros must not reroll on fallback")
    monkeypatch.setattr(macros.random, "choice", forbidden)
    fallback = messages.for_model("macro-model")
    assert expected_base in fallback[0]["content"]
    assert "Changed after composition" not in fallback[0]["content"]
    assert "{{random" not in fallback[0]["content"]
    assert messages.for_model("macro-model") == fallback


def test_live_inspector_selects_model(scene):
    _, detail = context.compose_turn(*scene, model="z-ai/glm-5.3")
    assert context.context_breakdown(*scene, model="glm-5.3") == detail
    assert context.context_sections(*scene, model="glm-5.3") == detail["sections"]


def test_blank_shared_heading_keeps_bodies_for_known_and_unknown_models(scene, template_root, monkeypatch):
    (template_root / "scene" / "off_scene_cast_heading.j2").write_text("", encoding="utf-8")
    a = assemble._assemble(*scene)
    a["data"]["offscene_active"] = [{"name": "Mara", "dossier": "Elsewhere"}]
    a["data"]["offscene_known"] = [{"id": "winifred", "name": "Winifred", "tagline": "Known", "versions": []}]
    selected = {s.id: s for s in assemble.SECTIONS}
    monkeypatch.setattr(assemble, "SECTIONS", [selected[sid] for sid in
                        ("off_scene_cast_active", "model_guidance", "off_scene_cast_known")])
    monkeypatch.setattr(assemble, "_assemble", lambda *args, **kwargs: a)
    messages, _ = context.compose_turn(*scene, model="glm-5.3")
    for outgoing in (messages, messages.for_model("unknown")):
        assert "Mara" in outgoing[0]["content"]
        assert "Winifred" in outgoing[0]["content"]


@pytest.mark.parametrize("include", [
    '{% include "scene/header.j2" %}',
    '{% set selected = "scene/header.j2" %}{% include selected %}',
])
def test_system_template_includes_are_frozen_for_fallback(scene, template_root, include):
    system = template_root / "scene" / "system.j2"
    system.write_text(include + '\n{{ sections | join("\\n\\n") }}', encoding="utf-8")
    header = template_root / "scene" / "header.j2"
    header.write_text("Original scene header", encoding="utf-8")
    messages, _ = context.compose_turn(*scene)
    header.write_text("Edited scene header", encoding="utf-8")
    assert messages.for_model("")[0]["content"].startswith("Original scene header")
    assert messages.for_model("glm-5.3")[0]["content"].startswith("Original scene header")
