"""The provider preset table: shipped facts, and inferring a preset from a connection."""

from __future__ import annotations

import pytest

from grimoire.store import llm_connections
from grimoire.store.inference import providers

CAPS = {"generate", "stream", "vision", "embed", "decide_native", "structured_output", "prefill"}
ALL_BUT_DECIDE = frozenset(CAPS - {"decide_native"})


def _oc(url: str) -> dict:
    return {"kind": "openai_compatible", "base_url": url}


@pytest.mark.parametrize(
    "conn, want",
    [
        ({"kind": "openrouter", "base_url": "https://openrouter.ai/api/v1"}, "openrouter"),
        ({"kind": "openrouter"}, "openrouter"),
        ({"kind": "claude"}, "claude"),
        ({"kind": "anthropic", "base_url": "https://api.anthropic.com"}, "anthropic"),
        (_oc("https://api.openai.com/v1"), "openai"),
        (_oc("https://api.openai.com/v1/"), "openai"),
        (_oc("https://api.z.ai/api/paas/v4"), "zai"),
        (_oc("https://api.z.ai/api/paas/v4/"), "zai"),
        (_oc("https://api.z.ai/api/coding/paas/v4"), "zai_coding"),
        (_oc("https://api.z.ai/api/coding/paas/v4/"), "zai_coding"),
        (_oc("http://localhost:11434/v1"), "ollama"),
        (_oc("http://localhost:11434/v1/"), "ollama"),
        (_oc("http://192.168.1.5:11434"), "ollama"),
        (_oc("http://localhost:1234/v1"), "lmstudio"),
        (_oc("http://127.0.0.1:1234/v1/"), "lmstudio"),
        (_oc("https://example.test/v1"), "custom"),
        (_oc("http://localhost:8080/v1"), "custom"),
        (_oc(""), "custom"),
        ({"kind": "openai_compatible"}, "custom"),
        (_oc("not a url"), "custom"),
        (_oc("http://localhost:notaport/v1"), "custom"),
    ],
)
def test_infer(conn, want):
    assert providers.infer(conn).id == want


def test_explicit_preset_wins_over_the_url():
    conn = {**_oc("https://example.test/v1"), "preset": "zai"}
    assert providers.infer(conn).id == "zai"
    conn = {**_oc("http://localhost:11434/v1"), "preset": "custom"}
    assert providers.infer(conn).id == "custom"


@pytest.mark.parametrize("bogus", ["nope", "", None, 3, ["zai"]])
def test_bogus_explicit_preset_falls_back_to_inference(bogus):
    conn = {**_oc("https://api.openai.com/v1"), "preset": bogus}
    assert providers.infer(conn).id == "openai"


@pytest.mark.parametrize(("conn", "want"), [
    ({"kind": "openrouter", "preset": "claude"}, "openrouter"),
    ({"kind": "claude", "preset": "openai"}, "claude"),
    ({"kind": "openrouter", "preset": "anthropic"}, "openrouter"),
    ({**_oc("https://api.openai.com/v1"), "preset": "openrouter"}, "openai"),
    # No kind reads as openrouter: its own preset is believed, another is not,
    # and the URL is no reason to leave the adapter it reads as.
    ({"preset": "openrouter"}, "openrouter"),
    ({"preset": "zai", "base_url": "https://api.openai.com/v1"}, "openrouter"),
])
def test_an_explicit_preset_on_another_adapter_is_ignored(conn, want):
    assert providers.infer(conn).id == want


def test_openai_compatible_presets_share_an_adapter():
    """Any of them may be chosen explicitly over what the URL suggests."""
    for preset in ("openai", "zai", "zai_coding", "ollama", "lmstudio", "custom"):
        conn = {**_oc("http://localhost:1234/v1"), "preset": preset}
        assert providers.infer(conn).id == preset


def test_billing_inferred_and_explicit():
    assert providers.billing({"kind": "claude"}) == "subscription"
    assert providers.billing(_oc("https://api.z.ai/api/coding/paas/v4")) == "subscription"
    assert providers.billing(_oc("https://api.z.ai/api/paas/v4")) == "metered"
    assert providers.billing({"kind": "openrouter"}) == "metered"
    # A custom endpoint's billing is the user's choice.
    assert providers.billing({**_oc("https://example.test/v1"), "billing": "subscription"}) == "subscription"
    assert providers.billing({**_oc("https://example.test/v1"), "billing": "bogus"}) == "metered"
    assert providers.billing(_oc("https://example.test/v1")) == "metered"


def test_labels_and_urls():
    p = providers.PRESETS
    assert {k: v.label for k, v in p.items()} == {
        "openrouter": "OpenRouter",
        "anthropic": "Anthropic API",
        "claude": "Claude subscription",
        "openai": "OpenAI",
        "zai": "z.ai",
        "zai_coding": "z.ai Coding Plan",
        "ollama": "Ollama",
        "lmstudio": "LM Studio",
        "custom": "Custom (OpenAI-compatible)",
    }
    assert p["openrouter"].base_url == "https://openrouter.ai/api/v1"
    assert p["anthropic"].base_url == "https://api.anthropic.com"
    assert p["claude"].base_url == ""
    assert p["openai"].base_url == "https://api.openai.com/v1"
    assert p["zai"].base_url == "https://api.z.ai/api/paas/v4"
    assert p["zai_coding"].base_url == "https://api.z.ai/api/coding/paas/v4"
    assert p["ollama"].base_url == "http://localhost:11434/v1"
    assert p["lmstudio"].base_url == "http://localhost:1234/v1"
    assert p["custom"].base_url == ""


def test_url_locked_billing_and_price_reporting():
    p = providers.PRESETS
    assert {k for k, v in p.items() if v.url_locked} == {
        "openrouter", "anthropic", "openai", "zai", "zai_coding"}
    assert {k for k, v in p.items() if v.billing == "subscription"} == {"claude", "zai_coding"}
    assert {k for k, v in p.items() if v.billing == "metered"} == set(p) - {"claude", "zai_coding"}
    assert {k for k, v in p.items() if v.reports_price} == {"openrouter", "claude"}


def _sets(preset):
    return preset.always, preset.possible, preset.never


def test_capability_sets_are_exact():
    fz = frozenset
    gs = fz({"generate", "stream"})
    p = providers.PRESETS
    assert _sets(p["openrouter"]) == (gs, fz({"vision", "embed", "decide_native", "structured_output", "prefill"}), fz())
    assert _sets(p["anthropic"]) == (gs, fz({"vision", "structured_output", "prefill"}), fz({"embed", "decide_native"}))
    assert _sets(p["claude"]) == (gs, fz(), fz({"vision", "embed", "decide_native", "structured_output", "prefill"}))
    assert _sets(p["openai"]) == (gs, fz({"vision", "embed", "decide_native", "structured_output", "prefill"}), fz())
    for k in ("zai", "zai_coding"):
        assert _sets(p[k]) == (gs, fz({"vision", "structured_output", "prefill"}), fz({"embed", "decide_native"}))
    for k in ("ollama", "lmstudio", "custom"):
        assert _sets(p[k]) == (fz(), ALL_BUT_DECIDE, fz({"decide_native"}))


def test_capability_sets_partition_the_vocabulary():
    for preset in providers.PRESETS.values():
        assert preset.always | preset.possible | preset.never == CAPS
        assert not preset.always & preset.possible
        assert not preset.always & preset.never
        assert not preset.possible & preset.never


def test_every_kind_is_a_known_adapter():
    for preset in providers.PRESETS.values():
        assert preset.kind in llm_connections.KINDS
    assert providers.PRESETS["anthropic"].kind == "anthropic"
    assert providers.PRESETS["claude"].kind == "claude"
    assert providers.PRESETS["openrouter"].kind == "openrouter"


def test_preset_ids_match_their_keys():
    assert all(k == v.id for k, v in providers.PRESETS.items())


def test_a_connection_with_no_kind_is_on_openrouter():
    """A missing `kind` reads as `openrouter` everywhere else (the facade's
    default), so the preset is OpenRouter's, not `custom`'s."""
    assert providers.infer({}).id == "openrouter"
    assert providers.infer({"model": "vendor/m"}).id == "openrouter"
    assert providers.infer({"kind": ""}).id == "openrouter"


@pytest.mark.parametrize("model, ruled_out", [
    ("claude-haiku-4-5-20251001", False),
    ("claude-sonnet-4-5-20250929", False),
    ("claude-3-7-sonnet-20250219", False),
    ("claude-opus-4-6", True),
    ("claude-opus-4-7", True),
    ("claude-opus-5", True),
    ("claude-mythos-preview", True),
    ("", True),
])
def test_anthropic_prefill_is_ruled_out_per_model(model, ruled_out):
    never = providers.never_for(providers.PRESETS["anthropic"], model)
    assert ("prefill" in never) is ruled_out
    assert {"embed", "decide_native"} <= never


def test_never_for_other_presets_is_the_presets_own():
    for key, preset in providers.PRESETS.items():
        if preset.kind != "anthropic":
            assert providers.never_for(preset, "claude-opus-4-7") == preset.never, key
