"""The sampler parameter table and the per-backend split (sampler presets)."""

import dataclasses

import pytest

from grimoire import llm_sampling as ls
from grimoire import wire


def _target(fields: dict, params: dict | None = None) -> wire.Target:
    """A target stating `fields` -- named as a connection names them (`kind`,
    `model`, `base_url`, `sampler_support`, `model_params`, `model_features`)
    -- and nothing else, with a preset of `params` attached (None: none)."""
    model = fields.get("model", "m")
    listed = fields.get("model_params")
    return wire.Target(
        provider_id="", kind=fields.get("kind", "openrouter"), model=model,
        requested_model=model, base_url=fields.get("base_url", ""),
        sampler_support=fields.get("sampler_support", ""),
        model_params=None if listed is None else tuple(listed),
        model_features=fields.get("model_features"),
        sampling=(wire.Sampling("p", "P", "connection", dict(params)) if params is not None
                  else wire.Sampling()))


def _conn(kind, params, **fields):
    return _target({"kind": kind, **fields}, params)


ALL = {"temperature": 0.9, "top_p": 0.95, "top_k": 40, "min_p": 0.05,
       "repetition_penalty": 1.08, "frequency_penalty": 0.1, "presence_penalty": 0.2,
       "max_tokens": 800, "stop": ["\nYou:"]}


# ---- validate ----

def test_validate_accepts_every_param_in_bounds():
    assert ls.validate(ALL) == ALL


def test_validate_accepts_an_empty_preset():
    assert ls.validate({}) == {}


def test_validate_keeps_integral_floats_as_ints():
    assert ls.validate({"top_k": 40.0, "max_tokens": 300.0}) == {"top_k": 40, "max_tokens": 300}


@pytest.mark.parametrize("params, needle", [
    ({"temprature": 1}, "temprature"),
    ({"temperature": 70}, "temperature"),
    ({"temperature": "hot"}, "temperature"),
    ({"temperature": True}, "temperature"),
    ({"temperature": float("nan")}, "temperature"),
    ({"top_k": 40.7}, "top_k"),
    ({"max_tokens": 0}, "max_tokens"),
    ({"stop": "\n"}, "stop"),
    ({"stop": [""]}, "stop"),
    ({"stop": ["x" * 201]}, "stop"),
    ({"stop": ["a"] * 17}, "stop"),
    ({"stop": [3]}, "stop"),
])
def test_validate_names_the_offending_param(params, needle):
    with pytest.raises(ValueError, match=needle):
        ls.validate(params)


def test_validate_refuses_a_non_object():
    with pytest.raises(ValueError):
        ls.validate(["temperature"])  # type: ignore[arg-type]


# ---- split ----

def test_no_preset_sends_nothing():
    assert ls.split(_target({"kind": "openrouter"})) == ({}, [])


def test_openrouter_without_a_catalog_sends_everything():
    applied, dropped = ls.split(_conn("openrouter", ALL))
    assert applied == ALL and dropped == []


def test_openrouter_drops_what_the_models_catalog_does_not_list():
    conn = _conn("openrouter", ALL, model_params=["temperature", "top_p", "max_tokens", "stop"])
    applied, dropped = ls.split(conn)
    assert applied == {k: ALL[k] for k in ("temperature", "top_p", "max_tokens", "stop")}
    assert [d["param"] for d in dropped] == [
        "top_k", "min_p", "repetition_penalty", "frequency_penalty", "presence_penalty"]
    assert all("catalog" in d["reason"] for d in dropped)


def test_standard_openai_compatible_drops_the_three_extensions():
    applied, dropped = ls.split(_conn("openai_compatible", ALL))
    assert set(applied) == {"temperature", "top_p", "frequency_penalty", "presence_penalty",
                            "max_tokens", "stop"}
    assert [d["param"] for d in dropped] == ["top_k", "min_p", "repetition_penalty"]


def test_standard_openai_compatible_drops_a_stop_list_over_four():
    applied, dropped = ls.split(_conn("openai_compatible", {"stop": list("abcde")}))
    assert applied == {} and dropped[0]["param"] == "stop"
    assert "4" in dropped[0]["reason"]


def test_extended_sends_both_repetition_spellings():
    applied, dropped = ls.split(_conn("openai_compatible", ALL, sampler_support="extended"))
    assert dropped == []
    assert applied["repetition_penalty"] == applied["repeat_penalty"] == 1.08
    assert applied["top_k"] == 40 and applied["min_p"] == 0.05


def test_extended_keeps_a_long_stop_list():
    applied, _ = ls.split(_conn("openai_compatible", {"stop": list("abcde")},
                                sampler_support="extended"))
    assert applied == {"stop": list("abcde")}


def test_claude_sends_nothing_and_says_why():
    applied, dropped = ls.split(_conn("claude", {"temperature": 0.5, "max_tokens": 10}))
    assert applied == {}
    assert [d["param"] for d in dropped] == ["temperature", "max_tokens"]
    assert "Claude Agent SDK" in dropped[0]["reason"]


def test_a_malformed_stored_preset_never_raises():
    applied, dropped = ls.split(_conn("openrouter", {"temperature": "hot", "top_k": 40,
                                                     "bogus": 1}))
    assert applied == {"top_k": 40}
    assert [d["param"] for d in dropped] == ["temperature"]


# ---- report ----

def test_report_is_none_without_a_target():
    assert ls.report(None) is None


def test_report_describes_the_split():
    got = ls.report(_conn("openai_compatible", {"temperature": 0.7, "min_p": 0.1}))
    assert got == {"preset_id": "p", "preset_name": "P", "scope": "connection",
                   "kind": "openai_compatible", "applied": {"temperature": 0.7},
                   "dropped": [{"param": "min_p", "reason": got["dropped"][0]["reason"]}],
                   "verified": True}


def test_report_names_a_cleared_route_too():
    conn = dataclasses.replace(_target({"kind": "openrouter"}),
                               sampling=wire.Sampling("", "", "global", {}))
    assert ls.report(conn)["scope"] == "global"


def test_report_says_openrouter_is_unverified_without_a_catalog():
    assert ls.report(_conn("openrouter", {"temperature": 1}))["verified"] is False
    assert ls.report(_conn("openrouter", {"temperature": 1},
                           model_params=["temperature"]))["verified"] is True


def test_report_reports_canonical_names_not_wire_duplicates():
    got = ls.report(_conn("openai_compatible", {"repetition_penalty": 1.1},
                          sampler_support="extended"))
    assert got["applied"] == {"repetition_penalty": 1.1}


def test_sent_names_lists_what_went_on_the_wire():
    assert ls.sent_names(_conn("openai_compatible", {"temperature": 1, "min_p": 0.1})) == [
        "temperature"]


def test_a_huge_integer_is_a_validation_error_not_an_overflow():
    big = 10 ** 1000
    for name in ("top_k", "temperature"):
        with pytest.raises(ValueError, match=name):
            ls.validate({name: big})


def test_a_malformed_catalog_list_costs_its_entries_not_the_split():
    conn = _conn("openrouter", {"temperature": 0.5, "top_k": 40},
                 model_params=["temperature", {"bad": 1}, 3])
    applied, dropped = ls.split(conn)
    assert applied == {"temperature": 0.5} and dropped[0]["param"] == "top_k"


def test_anthropic_takes_temperature_or_top_p_not_both():
    """Models that take sampling at all (Claude before 4.7) refuse the pair
    with a 400; temperature wins, and top_p says why it was not sent."""
    conn = _target({"kind": "anthropic", "model": "claude-sonnet-4-5",
                    "model_features": {"enabled_thinking": True, "adaptive_thinking": False}},
                   {"temperature": 0.8, "top_p": 0.9, "top_k": 40})
    eff = ls.effective(conn)
    assert eff["effective"] == {"temperature": 0.8, "top_k": 40, "max_tokens": 16000}
    assert eff["controls"]["top_p"]["state"] == ls.UNSUPPORTED
    assert eff["controls"]["top_p"]["why"] == ls.WHY_ANTHROPIC_TOP_P
    applied, dropped = ls.split(conn)
    assert "top_p" not in applied
    assert {"param": "top_p", "reason": ls.WHY_ANTHROPIC_TOP_P} in dropped
    # top_p alone is still sent.
    alone = dataclasses.replace(conn, sampling=dataclasses.replace(conn.sampling,
                                                                  params={"top_p": 0.9}))
    assert ls.effective(alone)["effective"]["top_p"] == 0.9


# ---- `max`: GLM's own level (slice I, ratification item 1) ----
_GLM_URL = "https://api.z.ai/api/paas/v4"
_ADAPTIVE = {"adaptive_thinking": True, "enabled_thinking": False,
             "effort": ["low", "medium", "high", "xhigh", "max"], "max_tokens": 64000}

#: Every adapter row of spec 8 that is not GLM, as the connection `effective` reads.
_NOT_GLM = {
    "openrouter-catalog": {"kind": "openrouter", "model_params": ["reasoning"]},
    "openrouter-no-catalog": {"kind": "openrouter"},
    "openai-reasoning": {"kind": "openai_compatible", "base_url": "https://api.openai.com/v1",
                         "model": "o3"},
    "openai-not-reasoning": {"kind": "openai_compatible",
                             "base_url": "https://api.openai.com/v1", "model": "gpt-4o"},
    "openai-unknown-family": {"kind": "openai_compatible",
                              "base_url": "https://api.openai.com/v1", "model": "mystery-model"},
    "anthropic-adaptive": {"kind": "anthropic", "model": "claude-opus-4-7",
                           "model_features": _ADAPTIVE},
    "anthropic-budgeted": {"kind": "anthropic", "model": "claude-sonnet-4-5-20250929",
                           "model_features": {"enabled_thinking": True,
                                              "adaptive_thinking": False}},
    "claude": {"kind": "claude"},
    "openai-compatible-strict": {"kind": "openai_compatible",
                                 "base_url": "http://localhost:1234/v1", "model": "local-model"},
    "openai-compatible-extended": {"kind": "openai_compatible",
                                   "base_url": "http://localhost:1234/v1", "model": "local-model",
                                   "sampler_support": "extended"},
}


def test_max_is_a_preset_effort():
    assert "max" in ls.REASONING
    assert ls.validate({"reasoning_effort": "max"}) == {"reasoning_effort": "max"}


@pytest.mark.parametrize("row", ["glm", *_NOT_GLM])
def test_max_is_a_glm_only_effort(row):
    """GLM on `openai_compatible` is sent `max`; every other adapter answers
    it unsupported, by the adapter, and sends no reasoning field at all."""
    fields = ({"kind": "openai_compatible", "base_url": _GLM_URL, "model": "glm-5.3"}
              if row == "glm" else _NOT_GLM[row])
    conn = _target(fields, {"reasoning_effort": "max"})
    eff = ls.effective(conn)
    control = eff["controls"]["reasoning_effort"]
    if row == "glm":
        assert control["state"] == ls.SUPPORTED
        assert ls.reasoning_wire(eff) == {"reasoning_effort": "max"}
        assert ls.sent_names(conn) == ["reasoning_effort"]
        return
    assert (control["state"], control["why"], control["source"]) == (
        ls.UNSUPPORTED, ls.WHY_MAX, "adapter")
    assert ls.reasoning_wire(eff) == {}
    assert ls.report(conn)["dropped"] == [{"param": "reasoning_effort", "reason": ls.WHY_MAX}]


def test_the_glm_levels_are_named_without_the_connection():
    assert ls.WHY_GLM == "this GLM model takes low, high or max"
