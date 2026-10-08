"""Effective controls (spec 8): one gateway function decides, per control, what
a connection is sent and why -- `llm_sampling.effective` -- and every older
helper (`split`, `sent_names`, `report`) is a view over it.

The existing kinds are held to a frozen copy of the pre-refactor split below:
for them, nothing a reader or a provider sees may move, except the one change
the spec asks for (`max_completion_tokens` at api.openai.com).
"""

from __future__ import annotations

import itertools

import pytest

from grimoire import llm_sampling as ls
from grimoire.llm import LLMClient
from grimoire.llm_errors import LLMError
from grimoire.store import llm_connections, sampler_presets
from grimoire.store.inference import capabilities, controls, providers
from tests.llm_fakes import RefusingProvider

ALL = {"temperature": 0.9, "top_p": 0.95, "top_k": 40, "min_p": 0.05,
       "repetition_penalty": 1.08, "frequency_penalty": 0.1, "presence_penalty": 0.2,
       "max_tokens": 800, "stop": ["\nYou:"]}

#: Claude 4.7+-style: adaptive thinking only, sampling parameters refused.
CURRENT = {"adaptive_thinking": True, "enabled_thinking": False,
           "effort": ["low", "medium", "high", "xhigh", "max"], "max_tokens": 64000}
#: An older Claude's row: budgeted thinking. Whether it takes sampling
#: parameters is its model id's question, not this row's.
OLDER = {"adaptive_thinking": False, "enabled_thinking": True, "max_tokens": 64000}


def _conn(kind, params=None, **fields):
    conn = {"kind": kind, "model": "m", **fields}
    if params is not None:
        conn["sampling"] = {"preset_id": "p", "preset_name": "P", "scope": "connection",
                            "params": params}
    return conn


#: Model ids on either side of the sampling line: Claude 4.7 and later refuse
#: sampling parameters, whatever thinking their catalog row lists.
CURRENT_ID = "claude-opus-4-7"
OLDER_ID = "claude-sonnet-4-5-20250929"


def _claude_api(params=None, features=None, model=CURRENT_ID, **fields):
    conn = _conn("anthropic", params, model=model, **fields)
    if features is not None:
        conn["model_features"] = features
    return conn


# ---- the frozen pre-refactor split, the reference for the existing kinds ----
def _today_why_not(kind, extended, listed, name, value):
    if kind == "claude":
        return ls.WHY_CLAUDE
    if kind == "openai_compatible":
        if extended:
            return ""
        if name not in ls.OPENAI_STANDARD:
            return ls.WHY_STANDARD
        if name == "stop" and isinstance(value, list) and len(value) > ls.OPENAI_STOP_MAX:
            return ls.WHY_STOP
        return ""
    return ls.WHY_CATALOG if listed is not None and name not in listed else ""


def _today_split(conn):
    sampling = conn.get("sampling") if isinstance(conn, dict) else None
    stored = sampling.get("params") if isinstance(sampling, dict) else None
    stored = stored if isinstance(stored, dict) else {}
    kind = conn.get("kind", "openrouter") if isinstance(conn, dict) else "openrouter"
    extended = conn.get("sampler_support") == "extended"
    listed = conn.get("model_params")
    listed = {x for x in listed if isinstance(x, str)} if isinstance(listed, list) else None
    applied, dropped = {}, []
    for p in ls.PARAMS:
        if p.name not in stored:
            continue
        try:
            value = ls._check(p, stored[p.name])
        except ValueError:
            dropped.append({"param": p.name, "reason": ls.WHY_INVALID})
            continue
        why = _today_why_not(kind, extended, listed, p.name, value)
        if why:
            dropped.append({"param": p.name, "reason": why})
            continue
        applied[p.name] = value
        if kind == "openai_compatible" and p.name == "repetition_penalty":
            applied["repeat_penalty"] = value
    return applied, dropped


def _today_report(conn):
    if not isinstance(conn, dict) or not isinstance(conn.get("sampling"), dict):
        return None
    sampling = conn["sampling"]
    kind = conn.get("kind", "openrouter")
    applied, dropped = _today_split(conn)
    verified = not (kind not in ("claude", "openai_compatible")
                    and not isinstance(conn.get("model_params"), list) and bool(applied))
    return {"preset_id": sampling.get("preset_id", ""),
            "preset_name": sampling.get("preset_name", ""),
            "scope": sampling.get("scope", ""), "kind": kind,
            "applied": {n: applied[n] for n in ls.NAMES if n in applied},
            "dropped": dropped, "verified": verified}


_PRESETS = [ALL, {}, {"temperature": 0.7, "min_p": 0.1}, {"stop": list("abcde")},
            {"temperature": "hot", "top_k": 40.0, "bogus": 1}, {"max_tokens": 0},
            {"repetition_penalty": 1.1}, {"top_k": 40, "max_tokens": 300.0}]
_CASES = [
    *({"kind": k} for k in ("openrouter", "claude", "openai_compatible")),
    {},                                                         # no kind: openrouter
    {"kind": "openrouter", "model_params": ["temperature", "top_p", "max_tokens", "stop"]},
    {"kind": "openrouter", "model_params": []},
    {"kind": "openrouter", "model_params": ["temperature", {"bad": 1}, 3]},
    {"kind": "openrouter", "model_params": "not a list"},
    {"kind": "openai_compatible", "sampler_support": "extended"},
    {"kind": "openai_compatible", "base_url": "http://localhost:1234/v1"},
    {"kind": "openai_compatible", "base_url": "https://api.z.ai/api/paas/v4",
     "model": "glm-5.3", "reasoning_effort": "high"},
    {"kind": "claude", "model_params": ["temperature"]},
]


@pytest.mark.parametrize("fields, params", list(itertools.product(_CASES, _PRESETS)))
def test_the_existing_kinds_split_exactly_as_before(fields, params):
    conn = {"model": "m", **fields, "sampling": {"preset_id": "p", "preset_name": "P",
                                                 "scope": "connection", "params": params}}
    assert ls.split(conn) == _today_split(conn)
    assert ls.report(conn) == _today_report(conn)
    applied, _ = _today_split(conn)
    assert ls.sent_names(conn) == [n for n in ls.NAMES if n in applied]


def test_no_preset_is_still_no_report_and_no_split():
    assert ls.report({"kind": "openrouter"}) is None
    assert ls.split({"kind": "openrouter"}) == ({}, [])
    assert ls.split(None) == ({}, [])  # type: ignore[arg-type]


# ---- the vocabulary ----
def test_the_table_still_has_exactly_the_nine_rows():
    assert [row["name"] for row in ls.table()] == list(ls.NAMES)
    assert len(ls.table()) == 9


@pytest.mark.parametrize("value", ls.REASONING)
def test_validate_takes_each_reasoning_effort(value):
    assert ls.validate({"reasoning_effort": value}) == {"reasoning_effort": value}


@pytest.mark.parametrize("value", ["max", "", "Low", True, 3, None, ["low"]])
def test_validate_refuses_any_other_reasoning_effort(value):
    with pytest.raises(ValueError, match="reasoning_effort"):
        ls.validate({"reasoning_effort": value})


def test_validate_lists_reasoning_after_the_nine():
    got = ls.validate({"reasoning_effort": "low", "temperature": 1})
    assert list(got) == ["temperature", "reasoning_effort"]


def test_effective_has_the_documented_shape():
    eff = ls.effective(_conn("openrouter", {"temperature": 0.5}))
    assert set(eff) == {"requested", "effective", "controls"}
    assert list(eff["controls"]) == list(ls.CONTROLS)
    for entry in eff["controls"].values():
        assert entry["state"] in ls.STATES
        assert set(entry) == {"state", "wire", "why", "source"}
    assert eff["requested"] == {"temperature": 0.5}


@pytest.mark.parametrize("conn", [None, "x", {}, {"kind": "anthropic"},
                                  {"kind": "anthropic", "model_features": "junk",
                                   "sampling": {"params": {"max_tokens": "lots"}}},
                                  {"kind": "openrouter", "sampling": "junk"},
                                  {"kind": "openai_compatible", "base_url": 7}])
def test_effective_never_raises(conn):
    eff = ls.effective(conn)  # type: ignore[arg-type]
    assert set(eff) == {"requested", "effective", "controls"}


def test_every_state_but_supported_says_why():
    for conn in (_conn("claude", ALL), _claude_api(ALL, CURRENT),
                 _conn("openai_compatible", ALL), _conn("openrouter", ALL),
                 _conn("openai_compatible", ALL, base_url="https://api.openai.com/v1")):
        for name, entry in ls.effective(conn)["controls"].items():
            if entry["state"] != "supported":
                assert entry["why"], (conn["kind"], name)


# ---- OpenAI: max_tokens is max_completion_tokens there ----
def test_the_openai_api_is_sent_max_completion_tokens():
    conn = _conn("openai_compatible", {"max_tokens": 800, "temperature": 0.5},
                 base_url="https://api.openai.com/v1")
    eff = ls.effective(conn)
    assert eff["controls"]["max_tokens"]["state"] == "translated"
    assert eff["controls"]["max_tokens"]["wire"] == "max_completion_tokens"
    assert eff["effective"] == {"temperature": 0.5, "max_completion_tokens": 800}
    assert ls.split(conn) == ({"temperature": 0.5, "max_completion_tokens": 800}, [])
    assert ls.sent_names(conn) == ["temperature", "max_tokens"]
    assert ls.report(conn)["applied"] == {"temperature": 0.5, "max_tokens": 800}


@pytest.mark.parametrize("url", ["https://api.openai.com/v1", "https://API.openai.com/v1/",
                                 "http://localhost:1234/v1", "https://api.z.ai/api/paas/v4",
                                 "https://api.openai.com.example.test/v1", "", "::bad::",
                                 "https://proxy.example.test/api.openai.com/v1"])
def test_the_openai_host_rule_agrees_with_the_provider_presets(url):
    conn = _conn("openai_compatible", {"max_tokens": 10}, base_url=url)
    translated = ls.effective(conn)["controls"]["max_tokens"]["wire"] == "max_completion_tokens"
    assert translated == (providers.infer(conn).id == "openai")


async def test_the_openai_endpoint_receives_max_completion_tokens():
    from tests.test_llm import FakeProvider
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("openai_compatible", {"max_tokens": 300}, base_url="https://api.openai.com/v1",
                 api_key="k")
    [c async for c in client.stream([], conn)]
    assert oc.calls[0][1]["sampling"] == {"max_completion_tokens": 300}


async def test_a_refusal_naming_the_translated_spelling_is_a_preset_refusal():
    provider = RefusingProvider(failing={"primary"}, status=400,
                                why="Unsupported value: 'max_completion_tokens' too large")
    client = LLMClient(openrouter=provider, claude=provider, openai_compatible=provider,
                       timeout=0, retries=0,
                       fallback=lambda: {"id": "b", "kind": "openrouter", "model": "backup",
                                         "api_key": "k"})
    conn = _conn("openai_compatible", {"max_tokens": 99999}, id="a", model="primary",
                 base_url="https://api.openai.com/v1")
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream([], conn)]
    assert "fallback connection was not tried" in exc.value.detail
    assert [m for m, _ in provider.calls] == ["primary"]


# ---- the Anthropic API ----
def test_review_focus_4_a_current_claude_model_is_sent_no_temperature():
    """Claude 4.7+ refuses sampling parameters: the preset's temperature is not
    sent, and the reader is told why."""
    conn = _claude_api({"temperature": 0.7}, CURRENT)
    eff = ls.effective(conn)
    assert "temperature" not in eff["effective"]
    assert eff["controls"]["temperature"]["state"] == "unsupported"
    assert eff["controls"]["temperature"]["wire"] is None
    assert "sampling" in eff["controls"]["temperature"]["why"]
    report = ls.report(conn)
    assert report["applied"] == {}
    assert report["dropped"] == [{"param": "temperature",
                                  "reason": eff["controls"]["temperature"]["why"]}]
    assert ls.sent_names(conn) == []


@pytest.mark.parametrize(("model", "version"), [
    ("claude-opus-4-6", (4, 6)), ("claude-sonnet-4-5-20250929", (4, 5)),
    ("claude-opus-4-20250514", (4, 0)), ("claude-3-7-sonnet-20250219", (3, 7)),
    ("claude-haiku-4-5", (4, 5)), ("claude-opus-4-7", (4, 7)), ("claude-opus-5", (5, 0)),
    ("claude-fable-5-1", (5, 1)), ("claude-opus-4-7[1m]", (4, 7)),
    ("claude-mythos-preview", None), ("claude-", None), ("gpt-4", None), ("", None),
    (None, None)])
def test_the_claude_version_is_read_from_the_model_id(model, version):
    assert ls._claude_version(model) == version


@pytest.mark.parametrize(("model", "source"), [
    ("claude-opus-4-7", "adapter"), ("claude-opus-5", "adapter"),
    ("claude-fable-5-1", "adapter"), ("claude-mythos-preview", "unknown"),
    ("anthropic-unnamed", "unknown")])
@pytest.mark.parametrize("features", [None, {}, OLDER, CURRENT])
def test_claude_4_7_and_later_are_sent_no_sampling_whatever_the_catalog_says(
        model, source, features):
    """The model id decides, not the thinking the catalog lists: a 4.7+ id is
    refused by the API (the adapter's knowledge), and an id with no version
    is nobody's answer."""
    eff = ls.effective(_claude_api({"temperature": 0.7, "top_p": 0.9, "top_k": 5},
                                   features, model=model))
    for name in ("temperature", "top_p", "top_k"):
        assert eff["controls"][name]["state"] == "unsupported"
        assert eff["controls"][name]["source"] == source
        assert eff["controls"][name]["why"] == ls.WHY_ANTHROPIC_SAMPLING
        assert name not in eff["effective"]


def test_an_opus_5_shaped_row_listing_budgeted_thinking_is_sent_no_temperature():
    """The Models API reports `enabled` thinking for Claude Opus 5, which still
    answers a non-default temperature with a 400: budgeted-thinking support is
    no proof a sampler is accepted."""
    features = {"adaptive_thinking": True, "enabled_thinking": True,
                "effort": ["low", "medium", "high"], "max_tokens": 128000}
    eff = ls.effective(_claude_api({"temperature": 0.7}, features, model="claude-opus-5"))
    assert "temperature" not in eff["effective"]
    assert eff["controls"]["temperature"]["state"] == "unsupported"
    assert ls.sent_names(_claude_api({"temperature": 0.7}, features,
                                     model="claude-opus-5")) == []


@pytest.mark.parametrize("features", [None, {}, OLDER, CURRENT])
def test_an_older_claude_model_takes_sampling_parameters(features):
    """Below 4.7 the id is the answer, whatever (or nothing) the row says --
    as long as no thinking is sent."""
    eff = ls.effective(_claude_api({"temperature": 0.7, "top_k": 5}, features, model=OLDER_ID))
    assert eff["effective"] == {"temperature": 0.7, "top_k": 5,
                                "max_tokens": 16000}
    assert {eff["controls"][n]["state"] for n in ("temperature", "top_k")} == {"supported"}
    assert {eff["controls"][n]["source"] for n in ("temperature", "top_k")} == {"name"}
    alone = ls.effective(_claude_api({"top_p": 0.9}, features, model=OLDER_ID))
    assert alone["effective"] == {"top_p": 0.9, "max_tokens": 16000}
    # The pair is refused by the models that take sampling at all: temperature wins.
    both = ls.effective(_claude_api({"temperature": 0.7, "top_p": 0.9}, features,
                                    model=OLDER_ID))
    assert both["effective"] == {"temperature": 0.7, "max_tokens": 16000}
    assert both["controls"]["top_p"]["state"] == "unsupported"


def test_sampling_is_unsupported_whenever_thinking_is_sent():
    eff = ls.effective(_claude_api({"temperature": 0.7, "reasoning_effort": "low"}, OLDER,
                                   model=OLDER_ID))
    assert "thinking" in eff["effective"]
    assert "temperature" not in eff["effective"]
    assert eff["controls"]["temperature"]["state"] == "unsupported"
    assert "thinking" in eff["controls"]["temperature"]["why"]


def test_the_anthropic_api_has_no_penalties_or_min_p():
    eff = ls.effective(_claude_api(ALL, OLDER))
    for name in ("min_p", "repetition_penalty", "frequency_penalty", "presence_penalty"):
        assert eff["controls"][name]["state"] == "unsupported"
        assert name not in eff["effective"]
    assert "repeat_penalty" not in eff["effective"]


@pytest.mark.parametrize("params, features, sent", [
    ({}, None, 16000),
    ({}, CURRENT, 16000),
    ({}, {"max_tokens": 8192}, 8192),
    ({"max_tokens": 4000}, CURRENT, 4000),
    ({"max_tokens": 100000}, CURRENT, 64000),
    ({"max_tokens": 0}, CURRENT, 16000),                 # invalid: the default stands
    ({}, {"max_tokens": True}, 16000),                   # a bool is not a limit
])
def test_anthropic_max_tokens_is_always_sent(params, features, sent):
    eff = ls.effective(_claude_api(params, features))
    assert eff["effective"]["max_tokens"] == sent


def test_a_capped_max_tokens_says_so_and_is_reported_as_sent():
    conn = _claude_api({"max_tokens": 100000}, CURRENT)
    assert "64000" in ls.effective(conn)["controls"]["max_tokens"]["why"]
    assert ls.report(conn)["applied"] == {"max_tokens": 64000}
    assert ls.split(conn) == ({"max_tokens": 64000}, [])


def test_an_invalid_anthropic_max_tokens_is_dropped_but_the_default_is_sent():
    conn = _claude_api({"max_tokens": 0}, CURRENT)
    assert ls.effective(conn)["controls"]["max_tokens"]["state"] == "unsupported"
    assert ls.effective(_claude_api({}, CURRENT))["controls"]["max_tokens"] == {
        "state": "supported", "wire": "max_tokens", "why": "", "source": "adapter"}
    assert ls.report(conn)["dropped"] == [{"param": "max_tokens", "reason": ls.WHY_INVALID}]
    assert ls.effective(conn)["effective"] == {"max_tokens": 16000}


def test_stop_is_sent_as_stop_sequences_and_reported_canonically():
    conn = _claude_api({"stop": ["\nYou:"]}, CURRENT)
    eff = ls.effective(conn)
    assert eff["controls"]["stop"]["state"] == "translated"
    assert eff["controls"]["stop"]["wire"] == "stop_sequences"
    assert eff["effective"]["stop_sequences"] == ["\nYou:"]
    assert "stop" not in eff["effective"]
    assert ls.report(conn)["applied"] == {"stop": ["\nYou:"]}
    assert ls.sent_names(conn) == ["stop"]


@pytest.mark.parametrize("level", ["low", "medium", "high"])
def test_adaptive_thinking_carries_the_effort(level):
    eff = ls.effective(_claude_api({"reasoning_effort": level}, CURRENT))
    assert eff["effective"]["thinking"] == {"type": "adaptive"}
    assert eff["effective"]["output_config"] == {"effort": level}
    assert "budget_tokens" not in str(eff["effective"])
    assert eff["controls"]["reasoning_effort"]["state"] == "translated"
    assert eff["controls"]["reasoning_effort"]["wire"] == "thinking"


def test_adaptive_is_preferred_where_both_are_listed():
    both = {**OLDER, "adaptive_thinking": True}
    eff = ls.effective(_claude_api({"reasoning_effort": "high"}, both))
    assert eff["effective"]["thinking"] == {"type": "adaptive"}


def test_an_effort_level_the_catalog_does_not_list_is_unsupported():
    features = {**CURRENT, "effort": ["high", "max"]}
    eff = ls.effective(_claude_api({"reasoning_effort": "low"}, features))
    assert eff["controls"]["reasoning_effort"]["state"] == "unsupported"
    assert "thinking" not in eff["effective"] and "output_config" not in eff["effective"]


@pytest.mark.parametrize("level, max_tokens, budget", [
    ("low", None, 1024), ("medium", None, 4096), ("high", None, 8000),
    ("high", 64000, 16000), ("high", 40000, 16000), ("medium", 6000, 3000),
    ("low", 1500, 1024)])
def test_budgeted_thinking_on_an_older_model(level, max_tokens, budget):
    params = {"reasoning_effort": level, **({"max_tokens": max_tokens} if max_tokens else {})}
    eff = ls.effective(_claude_api(params, OLDER))
    sent = eff["effective"]
    assert sent["thinking"] == {"type": "enabled", "budget_tokens": budget}
    assert ls.THINKING_MIN <= sent["thinking"]["budget_tokens"] < sent["max_tokens"]
    assert "output_config" not in sent


def test_no_room_to_think_is_unsupported():
    eff = ls.effective(_claude_api({"reasoning_effort": "low", "max_tokens": 1024}, OLDER))
    assert eff["controls"]["reasoning_effort"]["state"] == "unsupported"
    assert "thinking" not in eff["effective"]


@pytest.mark.parametrize("features", [None, {}, {"max_tokens": 8000}])
def test_thinking_the_catalog_does_not_describe_is_unknown_and_not_sent(features):
    eff = ls.effective(_claude_api({"reasoning_effort": "high"}, features))
    assert eff["controls"]["reasoning_effort"]["state"] == "unknown"
    assert eff["controls"]["reasoning_effort"]["why"]
    assert "thinking" not in eff["effective"]


def test_a_model_the_catalog_says_cannot_think_is_unsupported():
    features = {"adaptive_thinking": False, "enabled_thinking": False}
    eff = ls.effective(_claude_api({"reasoning_effort": "high"}, features))
    assert eff["controls"]["reasoning_effort"]["state"] == "unsupported"
    assert "thinking" not in eff["effective"]


def test_report_counts_a_sent_nothing_as_applied_only_when_it_is_supported():
    """An `off` honoured by sending nothing is applied; one whose effect is
    not known (the model's default may still reason) applied nothing anyone
    can vouch for, and is not listed."""
    older = _claude_api({"reasoning_effort": "off"}, OLDER)
    assert ls.report(older)["applied"] == {"reasoning_effort": "off"}
    for conn in (_claude_api({"reasoning_effort": "off"}, CURRENT),
                 _conn("openrouter", {"reasoning_effort": "off"},
                       model_params=["reasoning"]),
                 _conn("openai_compatible", {"reasoning_effort": "off"},
                       base_url="https://api.openai.com/v1")):
        assert ls.effective(conn)["controls"]["reasoning_effort"]["state"] == "unknown"
        assert "reasoning_effort" not in ls.report(conn)["applied"]


def test_off_omits_thinking():
    older = ls.effective(_claude_api({"reasoning_effort": "off"}, OLDER))
    assert "thinking" not in older["effective"]
    assert older["controls"]["reasoning_effort"]["state"] == "supported"
    current = ls.effective(_claude_api({"reasoning_effort": "off"}, CURRENT))
    assert "thinking" not in current["effective"]
    # Whether omitting it turns thinking off depends on the model, and the
    # catalog does not say: some current models cannot turn it off.
    assert current["controls"]["reasoning_effort"]["state"] == "unknown"
    assert "off" in current["controls"]["reasoning_effort"]["why"]


#: An Opus-5-shaped row: adaptive AND budgeted thinking, and thinking can be
#: turned off (`thinking.types.disabled.supported`).
OPUS_5 = {"adaptive_thinking": True, "enabled_thinking": True, "disabled_thinking": True,
          "effort": ["low", "medium", "high", "xhigh", "max"], "max_tokens": 128000}


def test_off_on_an_adaptive_model_that_can_turn_it_off_sends_disabled():
    """Omitting `thinking` on an adaptive model runs adaptive thinking -- even
    one that also lists budgeted thinking -- so off is sent as `disabled`."""
    conn = _claude_api({"reasoning_effort": "off"}, OPUS_5, model="claude-opus-5")
    eff = ls.effective(conn)
    assert eff["effective"]["thinking"] == {"type": "disabled"}
    assert "output_config" not in eff["effective"]
    entry = eff["controls"]["reasoning_effort"]
    assert (entry["state"], entry["wire"], entry["source"]) == ("translated", "thinking",
                                                                 "catalog")
    assert ls.sent_names(conn) == ["reasoning_effort"]
    assert ls.sent_fields(conn) == {"reasoning_effort": {"thinking": {"type": "disabled"}}}
    report = ls.report(conn)
    assert report["applied"] == {"reasoning_effort": "off"} and report["verified"]


@pytest.mark.parametrize("enabled", [True, False])
def test_off_on_an_adaptive_model_that_cannot_turn_it_off_is_unsupported(enabled):
    features = {**OPUS_5, "enabled_thinking": enabled, "disabled_thinking": False}
    conn = _claude_api({"reasoning_effort": "off"}, features, model="claude-opus-5")
    eff = ls.effective(conn)
    assert "thinking" not in eff["effective"]
    entry = eff["controls"]["reasoning_effort"]
    assert (entry["state"], entry["wire"], entry["source"]) == ("unsupported", None, "catalog")
    assert entry["why"]
    assert ls.sent_names(conn) == []
    report = ls.report(conn)
    assert "reasoning_effort" not in report["applied"]
    assert report["dropped"] == [{"param": "reasoning_effort", "reason": entry["why"]}]


@pytest.mark.parametrize("model", ["claude-sonnet-5-5", "claude-sonnet-5-5-20260928"])
def test_off_on_sonnet_5_5_is_sent_as_between_tools(model):
    """Sonnet 5.5 refuses `disabled`; `between_tools` is its off -- no thinking
    before the reply -- and the API takes it on that model alone."""
    features = {**OPUS_5, "disabled_thinking": False}
    conn = _claude_api({"reasoning_effort": "off"}, features, model=model)
    eff = ls.effective(conn)
    assert eff["effective"]["thinking"] == {"type": "between_tools"}
    assert "output_config" not in eff["effective"]
    entry = eff["controls"]["reasoning_effort"]
    assert (entry["state"], entry["wire"], entry["source"]) == ("translated", "thinking",
                                                                 "adapter")
    assert ls.report(conn)["applied"] == {"reasoning_effort": "off"}


def test_between_tools_is_never_sent_to_another_model():
    features = {**OPUS_5, "disabled_thinking": False}
    for model in ("claude-opus-5-5", "claude-sonnet-5", "claude-haiku-5-5"):
        eff = ls.effective(_claude_api({"reasoning_effort": "off"}, features, model=model))
        assert "thinking" not in eff["effective"], model
        assert eff["controls"]["reasoning_effort"]["state"] == "unsupported", model


@pytest.mark.parametrize("disabled", [True, False, None])
def test_off_on_a_budget_only_model_omits_thinking(disabled):
    features = {**OLDER, **({"disabled_thinking": disabled} if disabled is not None else {})}
    eff = ls.effective(_claude_api({"reasoning_effort": "off"}, features, model=OLDER_ID))
    assert "thinking" not in eff["effective"]
    entry = eff["controls"]["reasoning_effort"]
    assert (entry["state"], entry["wire"], entry["source"]) == ("supported", None, "catalog")


def test_off_on_a_model_that_does_not_think_sends_nothing():
    features = {"adaptive_thinking": False, "enabled_thinking": False, "disabled_thinking": True}
    eff = ls.effective(_claude_api({"reasoning_effort": "off"}, features))
    assert "thinking" not in eff["effective"]
    assert eff["controls"]["reasoning_effort"]["state"] == "supported"


@pytest.mark.parametrize("features", [
    {"adaptive_thinking": True, "enabled_thinking": True},     # disabled not stated
    {"adaptive_thinking": True},
    {}, None])
def test_off_where_the_catalog_does_not_say_is_unknown(features):
    eff = ls.effective(_claude_api({"reasoning_effort": "off"}, features))
    assert "thinking" not in eff["effective"]
    entry = eff["controls"]["reasoning_effort"]
    assert entry["state"] == "unknown" and entry["why"] == ls.WHY_THINKING_OFF


async def test_disabled_thinking_reaches_the_anthropic_request():
    from tests.test_llm import FakeProvider
    an = FakeProvider("an")
    client = LLMClient(openrouter=FakeProvider("or"), claude=FakeProvider("cl"),
                       openai_compatible=FakeProvider("oc"), anthropic=an)
    conn = _claude_api({"reasoning_effort": "off"}, OPUS_5, model="claude-opus-5",
                       api_key="k")
    [c async for c in client.stream([], conn)]
    assert an.calls[0][1]["effective"]["thinking"] == {"type": "disabled"}


def test_thinking_turned_off_leaves_sampling_to_a_model_that_takes_it():
    """`disabled` is not thinking: a pre-4.7 model sent it still takes its
    temperature, which the API refuses only while thinking is on."""
    features = {"adaptive_thinking": True, "enabled_thinking": True, "disabled_thinking": True}
    eff = ls.effective(_claude_api({"reasoning_effort": "off", "temperature": 0.7}, features,
                                   model="claude-opus-4-6"))
    assert eff["effective"]["thinking"] == {"type": "disabled"}
    assert eff["effective"]["temperature"] == 0.7
    assert eff["controls"]["temperature"]["state"] == "supported"


def test_report_names_the_effort_level_not_its_wire_translation():
    conn = _claude_api({"reasoning_effort": "high"}, CURRENT)
    assert ls.report(conn)["applied"] == {"reasoning_effort": "high"}


# ---- reasoning_effort on the other adapters ----
def test_openrouter_sends_reasoning_effort_where_the_catalog_lists_it():
    eff = ls.effective(_conn("openrouter", {"reasoning_effort": "high"},
                             model_params=["reasoning", "temperature"]))
    assert eff["effective"] == {"reasoning": {"effort": "high"}}
    assert eff["controls"]["reasoning_effort"]["state"] == "translated"


def test_openrouter_drops_it_where_the_catalog_does_not():
    conn = _conn("openrouter", {"reasoning_effort": "high"}, model_params=["temperature"])
    assert ls.effective(conn)["effective"] == {}
    assert ls.report(conn)["dropped"] == [{"param": "reasoning_effort",
                                          "reason": ls.WHY_CATALOG}]


def test_openrouter_without_a_catalog_sends_it_unverified():
    conn = _conn("openrouter", {"reasoning_effort": "low"})
    eff = ls.effective(conn)
    assert eff["controls"]["reasoning_effort"]["state"] == "unknown"
    assert eff["effective"] == {"reasoning": {"effort": "low"}}
    assert ls.report(conn)["verified"] is False


def test_the_openai_api_takes_reasoning_effort():
    conn = _conn("openai_compatible", {"reasoning_effort": "medium"},
                 base_url="https://api.openai.com/v1")
    eff = ls.effective(conn)
    assert eff["controls"]["reasoning_effort"]["state"] == "supported"
    assert eff["effective"] == {"reasoning_effort": "medium"}
    assert ls.sent_names(conn) == ["reasoning_effort"]


def test_a_strict_endpoint_is_not_sent_reasoning_effort():
    """Spec 8's strict-endpoint rule: `reasoning_effort` is not an OpenAI chat
    parameter, so a standard endpoint is not sent it -- the same gate as the
    three sampler extensions, and the same sentence."""
    conn = _conn("openai_compatible", {"reasoning_effort": "high"},
                 base_url="http://localhost:1234/v1")
    eff = ls.effective(conn)
    assert eff["controls"]["reasoning_effort"] == {
        "state": "unsupported", "wire": None, "why": ls.WHY_STANDARD, "source": "adapter"}
    assert eff["effective"] == {}
    assert ls.sent_names(conn) == []
    assert ls.report(conn)["dropped"] == [{"param": "reasoning_effort",
                                          "reason": ls.WHY_STANDARD}]


def test_an_extended_endpoint_is_sent_reasoning_effort_unverified():
    eff = ls.effective(_conn("openai_compatible", {"reasoning_effort": "high"},
                             base_url="http://localhost:1234/v1",
                             sampler_support="extended"))
    assert eff["controls"]["reasoning_effort"]["state"] == "unknown"
    assert eff["effective"] == {"reasoning_effort": "high"}


def test_the_agent_sdk_takes_no_reasoning_effort():
    eff = ls.effective(_conn("claude", {"reasoning_effort": "high"}))
    assert eff["controls"]["reasoning_effort"]["state"] == "unsupported"
    assert eff["effective"] == {}


GLM = {"base_url": "https://api.z.ai/api/paas/v4", "model": "glm-5.3"}


@pytest.mark.parametrize("legacy", ["", "low", "high", "max", "medium", "bogus"])
@pytest.mark.parametrize("model", ["glm-5.3", "vendor/GLM-5.3-flash", "another-model"])
def test_the_legacy_glm_setting_applies_exactly_as_before(legacy, model):
    from grimoire import llm_reasoning
    conn = _conn("openai_compatible", {}, base_url=GLM["base_url"], model=model,
                 reasoning_effort=legacy)
    expected = llm_reasoning.glm_effort(conn)
    got = ls.effective(conn)["effective"].get("reasoning_effort", "")
    assert got == expected
    # The legacy setting is the connection's, not the preset's: it is not a
    # sent preset parameter, so a refusal of it is not a preset refusal.
    assert ls.sent_names(conn) == []
    assert ls.report(conn)["applied"] == {}


def test_a_preset_effort_wins_over_the_legacy_glm_setting():
    conn = _conn("openai_compatible", {"reasoning_effort": "low"}, **GLM,
                 reasoning_effort="max")
    assert ls.effective(conn)["effective"] == {"reasoning_effort": "low"}


def test_glm_takes_only_the_levels_it_knows():
    eff = ls.effective(_conn("openai_compatible", {"reasoning_effort": "medium"}, **GLM))
    assert eff["controls"]["reasoning_effort"]["state"] == "unsupported"
    assert eff["effective"] == {}


def test_glm_off_is_reported_as_not_honoured():
    """GLM takes low, high or max; off sends nothing and must say so rather
    than vanish from both `applied` and `dropped`."""
    conn = _conn("openai_compatible", {"reasoning_effort": "off"}, **GLM)
    eff = ls.effective(conn)
    entry = eff["controls"]["reasoning_effort"]
    assert (entry["state"], entry["why"]) == ("unsupported", ls.WHY_GLM)
    assert eff["effective"] == {}
    report = ls.report(conn)
    assert report["dropped"] == [{"param": "reasoning_effort", "reason": ls.WHY_GLM}]


def test_the_legacy_setting_is_ignored_on_other_kinds():
    conn = _conn("openrouter", {}, model="glm-5.3", reasoning_effort="high")
    assert ls.effective(conn)["effective"] == {}


# ---- the facade sends what `effective` decided ----
async def test_an_openrouter_effort_reaches_the_request():
    from tests.test_llm import FakeProvider
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("openrouter", {"temperature": 0.5, "reasoning_effort": "high"},
                 api_key="k", model_params=["temperature", "reasoning"])
    [c async for c in client.stream([], conn)]
    assert op.calls[0][1]["sampling"] == {"temperature": 0.5, "reasoning": {"effort": "high"}}


async def test_an_openai_effort_reaches_the_request_as_a_keyword():
    from tests.test_llm import FakeProvider
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("openai_compatible", {"reasoning_effort": "low", "temperature": 1},
                 base_url="https://api.openai.com/v1", api_key="k")
    [c async for c in client.stream([], conn)]
    assert oc.calls[0][1]["reasoning_effort"] == "low"
    assert oc.calls[0][1]["sampling"] == {"temperature": 1}


async def test_a_refused_effort_is_a_preset_refusal():
    provider = RefusingProvider(failing={"primary"}, status=400,
                                why="Unrecognized request argument: reasoning")
    client = LLMClient(openrouter=provider, claude=provider, openai_compatible=provider,
                       timeout=0, retries=0,
                       fallback=lambda: {"id": "b", "kind": "openrouter", "model": "backup",
                                         "api_key": "k"})
    conn = _conn("openrouter", {"reasoning_effort": "high"}, id="a", model="primary",
                 api_key="k")
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream([], conn)]
    assert "fallback connection was not tried" in exc.value.detail


def _adaptive_refusal(why):
    provider = RefusingProvider(failing={"claude-opus-4-7"}, status=400, why=why)
    client = LLMClient(openrouter=provider, claude=provider, openai_compatible=provider,
                       anthropic=provider, timeout=0, retries=0,
                       fallback=lambda: {"id": "b", "kind": "openrouter", "model": "backup",
                                         "api_key": "k"})
    conn = _claude_api({"reasoning_effort": "high"}, CURRENT, id="a", api_key="k")
    assert ls.effective(conn)["effective"]["output_config"] == {"effort": "high"}
    return provider, client, conn


@pytest.mark.parametrize("why", [
    "output_config.effort 'high' is not supported by this model",
    "Unsupported field: output_config",
    "effort is not supported here"])
async def test_an_adaptive_effort_refused_by_its_effort_field_is_a_preset_refusal(why):
    """Adaptive effort is sent as `thinking` AND `output_config.effort`; a
    refusal naming only the second is still this preset's control refused."""
    provider, client, conn = _adaptive_refusal(why)
    with pytest.raises(LLMError) as exc:
        [c async for c in client.stream([], conn)]
    assert "fallback connection was not tried" in exc.value.detail
    assert [m for m, _ in provider.calls] == ["claude-opus-4-7"]


async def test_an_unrelated_400_beside_an_adaptive_effort_still_falls_back():
    provider, client, conn = _adaptive_refusal("prompt is too long: 300000 tokens")
    chunks = [c async for c in client.stream([], conn)]
    assert "from backup" in "".join(chunks)
    assert [m for m, _ in provider.calls] == ["claude-opus-4-7", "backup"]


@pytest.mark.parametrize("why", [
    ("You have reached your specified API usage limits. You will regain access on "
     "2026-11-01 at 00:00 UTC."),
    # A wording that happened to name a sent field is still the account refused.
    ("You have reached your specified workspace API usage limits for max_tokens and "
     "output_config.effort.")])
def test_a_spend_limit_400_is_never_a_preset_refusal(why):
    from grimoire.llm import _preset_refusal
    conn = _claude_api({"reasoning_effort": "high", "max_tokens": 900, "stop": ["x"]}, CURRENT)
    assert _preset_refusal(LLMError("bad_response", why, status=400), conn) is None


def test_a_thinking_type_is_not_a_spelling_of_its_own():
    """`thinking.type` is a discriminator, not a setting: a 400 about some
    other `type` (a content block's) must not read as the preset refused."""
    exc = LLMError("bad_response", "messages.0.content.0.type: Input should be 'text'",
                   status=400)
    from grimoire.llm import _preset_refusal
    assert _preset_refusal(exc, _claude_api({"reasoning_effort": "high"}, CURRENT)) is None


# ---- the preset store ----
@pytest.fixture()
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


def test_a_preset_keeps_its_reasoning_effort(home):
    pid = sampler_presets.create_preset("Mara's warm", {"temperature": 0.8,
                                                        "reasoning_effort": "high"})
    assert sampler_presets.read_preset(pid)["params"] == {"temperature": 0.8,
                                                          "reasoning_effort": "high"}
    with pytest.raises(ValueError, match="reasoning_effort"):
        sampler_presets.create_preset("Bad", {"reasoning_effort": "max"})


def test_a_sillytavern_reasoning_field_is_ignored():
    params, report = sampler_presets.from_sillytavern(
        {"temperature": 0.8, "reasoning_effort": "high", "openai_reasoning_effort": "low"})
    assert params == {"temperature": 0.8}
    assert "reasoning_effort" not in params
    assert not any(m["param"] == "reasoning_effort" for m in report["mapped"])


# ---- the store's preview, and each attempt's controls ----
def _openrouter(model="vendor/mara-7b", rows=None):
    cid = llm_connections.create_connection("openrouter", "Saltmarch", api_key="k",
                                            model=model)
    conn = llm_connections.read_connection_raw(cid)
    if rows is not None:
        llm_connections.set_cached_models(cid, rows, conn["rev"])
    return conn


def test_preview_is_effective_over_the_lowered_preset(home):
    conn = _openrouter(rows=[{"id": "vendor/winifred-2", "params": ["temperature"]}])
    pid = sampler_presets.create_preset("Warm", {"temperature": 0.8, "top_k": 30,
                                                 "reasoning_effort": "low"})
    got = controls.preview(pid, conn, "vendor/winifred-2")
    assert got["requested"] == {"temperature": 0.8, "top_k": 30, "reasoning_effort": "low"}
    assert got["effective"] == {"temperature": 0.8}
    assert got["controls"]["top_k"]["state"] == "unsupported"
    assert got["controls"]["temperature"]["source"] == "catalog"
    # The same decisions the gateway makes for the same lowered connection.
    eff = ls.effective({**conn, "model": "vendor/winifred-2",
                        "model_params": ["temperature"],
                        "sampling": {"params": {"temperature": 0.8, "top_k": 30,
                                                "reasoning_effort": "low"}}})
    assert {k: {**v, "source": None} for k, v in got["controls"].items()} == {
        k: {**v, "source": None} for k, v in eff["controls"].items()}


def test_preview_defaults_to_the_connections_own_model(home):
    conn = _openrouter(rows=[{"id": "vendor/mara-7b", "params": ["top_p"]}])
    pid = sampler_presets.create_preset("Cool", {"temperature": 0.2})
    got = controls.preview(pid, conn, "")
    assert got["controls"]["temperature"]["state"] == "unsupported"


def test_preview_of_no_preset_requests_nothing(home):
    conn = _openrouter()
    got = controls.preview("", conn, "vendor/mara-7b")
    assert got["requested"] == {} and got["effective"] == {}
    got = controls.preview("no-such-preset", conn, "vendor/mara-7b")
    assert got["requested"] == {}


def test_every_control_names_a_source(home):
    conn = _openrouter()
    got = controls.preview("", conn, "vendor/mara-7b")
    for entry in got["controls"].values():
        assert entry["source"] in capabilities.SOURCES


def test_a_control_mapped_to_a_capability_takes_its_source(home, monkeypatch):
    conn = _openrouter(rows=[{"id": "vendor/mara-7b", "params": ["structured_outputs"]}])
    monkeypatch.setitem(controls.CAPABILITY, "stop", "structured_output")
    got = controls.preview("", conn, "vendor/mara-7b")
    assert got["controls"]["stop"]["source"] == capabilities.caps_for(
        conn)["structured_output"].source == "catalog"


def test_preview_never_raises_on_an_unreadable_connection(home):
    got = controls.preview("", {"kind": "anthropic", "id": "../nope", "model": "claude-test-1"},
                           "claude-test-1")
    assert got["effective"] == {"max_tokens": 16000}
