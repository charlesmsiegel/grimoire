"""Sampler parameters: what a preset may set, and what each backend will take.

A leaf on the gateway side, like `llm_errors` and `catalog`: the facade needs
`split` per attempt and must not import the store (#239), and the store needs
`validate` and the table to check what it writes -- so the one copy of both
lives here, where either side can reach it without a cycle.

The governing rule is the request's: **a parameter the serving backend cannot
take is dropped, and dropping it is said out loud.** `split` therefore never
answers with one list. It answers what is sent and what is not, with a reason
per dropped parameter, and the reason is written for the reader of the scene
inspector, the routing picker and the connection editor -- the three surfaces
that show it (see the sampler-presets spec).

A preset supplies exactly the parameters it sets. An absent parameter is the
backend's own default, never a zero: defaulting it here would override a
provider default nobody chose to change. (The one exception is the Anthropic
API's `max_tokens`, which that API requires on every request.)

`effective` is the one function that decides, per control, what a connection is
sent and why (spec 8); `split`, `sent_names` and `report` are views over it.
"""

from __future__ import annotations

import math
import re
from typing import NamedTuple
from urllib.parse import urlsplit

from . import llm_reasoning


class Param(NamedTuple):
    """One sampler parameter as a preset stores it."""

    name: str
    label: str
    kind: str          # "float" | "int" | "stop" | "choice"
    low: float
    high: float
    choices: tuple[str, ...] = ()


#: Every parameter a preset may set, in the order every surface lists them.
#: The bounds are sanity bounds -- a typed temperature of 70 is refused -- not
#: a claim about any one provider's accepted range; a provider that refuses a
#: value inside them says so with its own 400 (see `llm._resilient`).
PARAMS: tuple[Param, ...] = (
    Param("temperature", "Temperature", "float", 0.0, 5.0),
    Param("top_p", "Top-p", "float", 0.0, 1.0),
    Param("top_k", "Top-k", "int", 0, 1000),
    Param("min_p", "Min-p", "float", 0.0, 1.0),
    Param("repetition_penalty", "Repetition penalty", "float", 0.0, 3.0),
    Param("frequency_penalty", "Frequency penalty", "float", -2.0, 2.0),
    Param("presence_penalty", "Presence penalty", "float", -2.0, 2.0),
    Param("max_tokens", "Max tokens", "int", 1, 200000),
    Param("stop", "Stop strings", "stop", 0, 0),
)
NAMES: tuple[str, ...] = tuple(p.name for p in PARAMS)

#: A preset's provider-neutral reasoning effort (spec 4.3). Not in `PARAMS`, so
#: `split` stays about samplers; the editor's `table` lists it after them.
#: `max` is GLM's own level (slice I, ratification item 1): sent to a GLM model
#: on `openai_compatible` only, and unsupported on every other adapter
#: (`WHY_MAX`), so a legacy GLM connection at `max` keeps its wire as a preset.
REASONING: tuple[str, ...] = ("off", "low", "medium", "high", "max")
REASONING_PARAM = Param("reasoning_effort", "Reasoning effort", "choice", 0, 0, REASONING)

#: Every control a preset may set, in the order every answer lists them.
CONTROLS: tuple[str, ...] = (*NAMES, REASONING_PARAM.name)
_BY_NAME = {p.name: p for p in (*PARAMS, REASONING_PARAM)}

#: What `effective` says of each control (spec 8). `n/a` is for an operation
#: that takes no sampling at all (an embedding, a native decision); nothing that
#: generates text is ever `n/a`.
SUPPORTED, TRANSLATED, UNSUPPORTED, UNKNOWN, NOT_APPLICABLE = (
    "supported", "translated", "unsupported", "unknown", "n/a")
STATES: tuple[str, ...] = (SUPPORTED, TRANSLATED, UNSUPPORTED, UNKNOWN, NOT_APPLICABLE)
#: The states whose control goes on the wire when it has a wire name.
_SENT = frozenset({SUPPORTED, TRANSLATED, UNKNOWN})

#: The Anthropic API requires `max_tokens`; this is what it is sent when the
#: preset sets none (spec 8), before the model's own limit caps it.
ANTHROPIC_MAX_TOKENS = 16000
#: Budgeted thinking (`thinking: {type: enabled}`), for the models that take
#: it: the spec's fixed budgets, each then held to at most half of the
#: effective `max_tokens` -- the budget counts against `max_tokens`, so a budget
#: just under it would leave the reply nothing -- and never under the API's
#: minimum. A request with no room for the minimum sends no thinking at all.
THINKING_BUDGET: dict[str, int] = {"low": 1024, "medium": 4096, "high": 16000}
THINKING_MIN = 1024

#: Wire fields only the reasoning control writes. `split` is about samplers,
#: so these never appear in it; the facade hands them over beside it.
REASONING_WIRE: tuple[str, ...] = ("reasoning", "reasoning_effort", "thinking",
                                   "output_config")

#: Stop strings a preset may hold, and how long each may be.
STOP_MAX = 16
STOP_CHARS = 200
#: The OpenAI API's own limit on `stop`. A standard OpenAI-compatible endpoint
#: is held to the API it claims to speak, so a longer list is dropped whole --
#: sending the first four would be a different preset than the one chosen.
OPENAI_STOP_MAX = 4

#: The parameters the OpenAI chat-completions API defines. The other three are
#: extensions that local servers (llama.cpp, vLLM, LM Studio, koboldcpp,
#: TabbyAPI) take and a strict endpoint answers with a 400 -- the same reason
#: `openai_compatible` declines to send `stream_options`.
OPENAI_STANDARD = frozenset({"temperature", "top_p", "frequency_penalty", "presence_penalty",
                             "max_tokens", "stop"})

#: Why each kind of drop happened, in the reader's words.
WHY_CATALOG = "this model does not take it (OpenRouter's model catalog)"
WHY_STANDARD = ("not part of the OpenAI API — turn on extended samplers if this "
                "endpoint takes it")
WHY_STOP = f"the OpenAI API takes at most {OPENAI_STOP_MAX} stop strings"
WHY_CLAUDE = "the Claude Agent SDK takes no sampling options"
WHY_NATIVE = "a native decision takes no sampling"
WHY_INVALID = "the stored value is not valid"
WHY_UNVERIFIED = ("OpenRouter's catalog for this model is not cached, so whether it "
                  "takes this is not known")
WHY_COMPLETION_TOKENS = "the OpenAI API spells it max_completion_tokens"
WHY_STOP_SEQUENCES = "the Anthropic API spells it stop_sequences"
WHY_ANTHROPIC_NONE = "the Anthropic API has no such parameter"
WHY_ANTHROPIC_SAMPLING = "Claude 4.7 and later refuse sampling parameters"
WHY_ANTHROPIC_THINKING = "the Anthropic API refuses sampling parameters while thinking is on"
WHY_ANTHROPIC_TOP_P = "Claude takes temperature or top_p, not both; temperature was sent"
WHY_REASONING_OFF = ("off sends no reasoning setting, so the model's own default "
                     "applies, and it may still reason")
WHY_THINKING_OFF = ("off sends no thinking setting; some current Claude models think "
                    "anyway and cannot turn it off")
WHY_THINKING_DISABLED = "sent as thinking disabled: left unset, this model would think"
WHY_THINKING_ALWAYS = "the catalog says this model's thinking cannot be turned off"
WHY_THINKING_BETWEEN = ("sent as thinking between_tools: this model refuses disabled, and "
                        "between_tools is its off -- no thinking before the reply")
#: The model ids that take `thinking: {"type": "between_tools"}` -- Claude
#: Sonnet 5.5, which refuses `disabled` and offers this as its off. The API
#: answers it with a 400 on every other model, and no catalog field names it.
_BETWEEN_TOOLS = re.compile(r"claude-sonnet-5-5(?:$|[^0-9])")
WHY_THINKING_UNKNOWN = ("the catalog does not say which thinking this model takes, so "
                        "none is sent")
WHY_THINKING_NONE = "the catalog says this model takes no thinking"
WHY_REASONING_ENDPOINT = "whether this endpoint takes reasoning_effort is not known"
WHY_OPENAI_NOT_REASONING = "this OpenAI model is not a reasoning model"
WHY_OPENAI_REASONING_UNVERIFIED = ("whether this model takes reasoning_effort is unverified: "
                                   "its id is not an OpenAI model family known here")
#: OpenAI model families by id (lowercased, after any `vendor/` prefix). The
#: reasoning families take `reasoning_effort`; the others answer it with a 400,
#: which `llm._preset_refusal` would turn into a refusal that skips the
#: fallback. An id in neither is sent it unverified.
_OPENAI_REASONING = re.compile(r"o\d|gpt-5|gpt-oss")
#: `gpt-5-chat-latest` and its kin are the non-reasoning chat snapshots of a
#: reasoning family: checked before `_OPENAI_REASONING` would claim them.
_OPENAI_NOT_REASONING = re.compile(r"gpt-4|gpt-3|chatgpt-|gpt-5[^/]*-chat")
WHY_GLM = "this GLM model takes low, high or max"
#: Why `max` is sent nowhere but GLM: no other adapter is documented here as
#: taking it, so nothing else claims it.
WHY_MAX = "max is a GLM level"


def _check_stop(p: Param, value: object) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{p.name} must be a list of strings")
    if len(value) > STOP_MAX:
        raise ValueError(f"{p.name} holds at most {STOP_MAX} strings")
    for item in value:
        if not isinstance(item, str) or not item or len(item) > STOP_CHARS:
            raise ValueError(f"each {p.name} entry must be 1 to {STOP_CHARS} characters")
    return list(value)


def _check(p: Param, value: object) -> object:
    """`value` as `p` stores it, or ValueError naming `p`."""
    if p.kind == "stop":
        return _check_stop(p, value)
    if p.kind == "choice":
        # Exactly one of the listed strings: a level is not case-folded or
        # guessed, any more than a temperature is clamped.
        if not isinstance(value, str) or value not in p.choices:
            raise ValueError(f"{p.name} must be one of {', '.join(p.choices)}")
        return value
    # bool is an int to Python and a mistake to a reader: `true` is not a
    # temperature.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{p.name} must be a number")
    # An int is always finite, and asking `math.isfinite` about a thousand-digit
    # one converts it to a float first and raises OverflowError -- which no
    # caller catches, so a typed `top_k` of 1e1000-as-digits was a 500.
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{p.name} must be a finite number")
    if p.kind == "int":
        if value != int(value):
            raise ValueError(f"{p.name} must be a whole number")
        value = int(value)
    else:
        try:
            value = float(value)
        except OverflowError:
            raise ValueError(f"{p.name} must be between {p.low:g} and {p.high:g}") from None
    if not p.low <= value <= p.high:
        raise ValueError(f"{p.name} must be between {p.low:g} and {p.high:g}")
    return value


def validate(params: object) -> dict:
    """A preset's parameters, checked, or ValueError naming the first bad one.

    Hand-written over a bare dict rather than a typed pydantic model: pydantic
    1.10 (the Android dependency set) coerces 40.7 into an `int` field where v2
    refuses it, and the two builds must agree about what a preset is.
    """
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    out: dict = {}
    for key, value in params.items():
        p = _BY_NAME.get(key)
        if p is None:
            raise ValueError(f"unknown sampler parameter: {key!r}")
        out[key] = _check(p, value)
    return {name: out[name] for name in CONTROLS if name in out}


def _stored(conn: dict) -> dict:
    sampling = conn.get("sampling") if isinstance(conn, dict) else None
    params = sampling.get("params") if isinstance(sampling, dict) else None
    return params if isinstance(params, dict) else {}


def _why_not(kind: str, extended: bool, listed: set | None, name: str, value: object) -> str:
    """Why this backend will not be sent `name`, or "" when it will."""
    if kind == "claude":
        return WHY_CLAUDE
    if kind == "openai_compatible":
        if extended:
            return ""
        if name not in OPENAI_STANDARD:
            return WHY_STANDARD
        if name == "stop" and isinstance(value, list) and len(value) > OPENAI_STOP_MAX:
            return WHY_STOP
        return ""
    return WHY_CATALOG if listed is not None and name not in listed else ""


def _openai_api(base_url: object) -> bool:
    """Whether `base_url` is the OpenAI API itself: the host rule of
    `store.inference.providers`' `openai` preset, restated because this module
    never imports the store (#239). A test holds the two equal."""
    if not isinstance(base_url, str) or not base_url.strip():
        return False
    try:
        parts = urlsplit(base_url.strip())
        parts.port  # noqa: B018 - a malformed port makes the URL unparsable, as there
    except ValueError:
        return False
    return (parts.hostname or "").lower() == "api.openai.com"


class _Control(NamedTuple):
    """One control's decision. `fields` is what it adds to the wire body --
    filled only by the decisions whose wire body is not just `{wire: value}`."""

    state: str
    wire: str | None
    why: str
    #: What decided it, in `store.inference.capabilities.SOURCES`' words:
    #: `adapter` (the wire protocol), `catalog` (the model's catalog row),
    #: `preset` (the provider preset: the OpenAI API), `name` (the model id),
    #: `user` (the user's own setting or stored value), `unknown`.
    source: str
    fields: dict | None = None


class _Conn(NamedTuple):
    """What `effective` reads of a connection, read defensively once."""

    kind: object
    #: The connection's model id, or "" when it names none.
    model: str
    extended: bool
    #: The catalog's supported parameters (OpenRouter), or None: not known.
    listed: set | None
    #: The catalog's `features` (Anthropic), or {}.
    features: dict
    #: An `openai_compatible` connection at the OpenAI API itself.
    openai: bool


def _context(conn: dict) -> _Conn:
    listed = conn.get("model_params")
    # Strings only: a hand-edited or sync-mangled catalog with an object in the
    # list must cost that entry, not the turn (`set` of a dict raises).
    listed = {x for x in listed if isinstance(x, str)} if isinstance(listed, list) else None
    features = conn.get("model_features")
    kind = conn.get("kind", "openrouter")
    model = conn.get("model")
    return _Conn(kind, model if isinstance(model, str) else "",
                 conn.get("sampler_support") == "extended", listed,
                 features if isinstance(features, dict) else {},
                 kind == "openai_compatible" and _openai_api(conn.get("base_url")))


def _sampler(c: _Conn, name: str, value: object) -> _Control:
    """A sampler parameter on `openrouter`, `openai_compatible` or `claude`:
    today's `_why_not`, with the OpenAI API's one spelling of its own."""
    why = _why_not(c.kind, c.extended, c.listed, name, value)  # type: ignore[arg-type]
    if c.kind == "claude":
        return _Control(UNSUPPORTED, None, why, "adapter")
    if c.kind == "openai_compatible":
        source = "user" if c.extended else "adapter"
        if why:
            return _Control(UNSUPPORTED, None, why, source)
        if name == "max_tokens" and c.openai:
            return _Control(TRANSLATED, "max_completion_tokens", WHY_COMPLETION_TOKENS, "preset")
        return _Control(SUPPORTED, name, "", source)
    if c.listed is None:
        return _Control(UNKNOWN, name, WHY_UNVERIFIED, "unknown")
    if why:
        return _Control(UNSUPPORTED, None, why, "catalog")
    return _Control(SUPPORTED, name, "", "catalog")


#: The first Claude version that refuses sampling parameters: the API answers
#: a non-default temperature, top_p or top_k with a 400 from Claude 4.7 on
#: (and on Claude Mythos Preview). Its catalog row is no help -- Claude Opus 5
#: lists budgeted thinking and still refuses them -- so the model id decides.
ANTHROPIC_SAMPLING_UNTIL = (4, 7)
#: An 8-digit snapshot date ends the version (`claude-opus-4-20250514` is 4.0).
_DATE = re.compile(r"\d{8}")
_SMALL = re.compile(r"\d{1,2}")


def claude_version(model: object) -> tuple[int, int] | None:
    """The Claude version a model id names, as `(major, minor)`, or None when
    it names none: the first run of small numbers after `claude-`, read in
    order and stopped by a snapshot date (`claude-3-7-sonnet-20250219` is 3.7,
    `claude-opus-4-6` is 4.6, `claude-opus-5` is 5.0). `claude-mythos-preview`
    names no version."""
    if not isinstance(model, str):
        return None
    lowered = model.lower()
    at = lowered.find("claude-")
    if at < 0:
        return None
    run: list[int] = []
    for token in re.split(r"[^a-z0-9]+", lowered[at + len("claude-"):]):
        if _DATE.fullmatch(token):
            break
        if _SMALL.fullmatch(token):
            run.append(int(token))
            if len(run) == 2:
                break
        elif run:
            break
    if not run:
        return None
    return run[0], run[1] if len(run) > 1 else 0


def _anthropic_sampler(c: _Conn, name: str, thinking: bool, temperature: bool) -> _Control:
    """A sampler parameter on the Anthropic Messages API (spec 8). `temperature`:
    whether a temperature is being sent -- `CONTROLS` decides it before `top_p`.
    Whether the model takes sampling at all is its id's version, never the
    thinking its catalog row lists (`ANTHROPIC_SAMPLING_UNTIL`)."""
    if name in ("temperature", "top_p", "top_k"):
        version = claude_version(c.model)
        if version is None or version >= ANTHROPIC_SAMPLING_UNTIL:
            # A version the API refuses is the adapter's knowledge; an id that
            # names none is nobody's answer, and nothing is sent on a guess.
            return _Control(UNSUPPORTED, None, WHY_ANTHROPIC_SAMPLING,
                            "unknown" if version is None else "adapter")
        if thinking:
            return _Control(UNSUPPORTED, None, WHY_ANTHROPIC_THINKING, "adapter")
        if name == "top_p" and temperature:
            # The models that take sampling at all refuse the pair with a 400.
            return _Control(UNSUPPORTED, None, WHY_ANTHROPIC_TOP_P, "adapter")
        return _Control(SUPPORTED, name, "", "name")
    if name == "stop":
        return _Control(TRANSLATED, "stop_sequences", WHY_STOP_SEQUENCES, "adapter")
    if name == "max_tokens":
        return _Control(SUPPORTED, "max_tokens", "", "adapter")
    return _Control(UNSUPPORTED, None, WHY_ANTHROPIC_NONE, "adapter")


def _anthropic_max_tokens(c: _Conn, value: object) -> tuple[int, str]:
    """`(max_tokens, why)`: the preset's value or the default, capped at the
    model's own limit when its catalog row names one (`why` says so)."""
    want = value if isinstance(value, int) and not isinstance(value, bool) else ANTHROPIC_MAX_TOKENS
    limit = c.features.get("max_tokens")
    if isinstance(limit, int) and not isinstance(limit, bool) and 0 < limit < want:
        return limit, f"capped at this model's limit of {limit} tokens"
    return want, ""


def _openrouter_reasoning(c: _Conn, value: str | None) -> _Control:
    if c.listed is not None and "reasoning" not in c.listed:
        return _Control(UNSUPPORTED, None, WHY_CATALOG, "catalog")
    if value == "off":
        return _Control(UNKNOWN, None, WHY_REASONING_OFF,
                        "unknown" if c.listed is None else "catalog")
    fields = {"reasoning": {"effort": value}} if value else {}
    if c.listed is None:
        return _Control(UNKNOWN, "reasoning", WHY_UNVERIFIED, "unknown", fields)
    return _Control(TRANSLATED, "reasoning", "OpenRouter takes it as reasoning.effort",
                    "catalog", fields)


def _openai_reasoning(c: _Conn, conn: dict, value: str | None) -> _Control:
    """`openai_compatible`: GLM by `llm_reasoning`'s rule (the preset's value
    only: with none, nothing is sent -- a legacy connection's effort rides on a
    derived reasoning preset, slice I), the OpenAI API by the model's family
    (`_openai_api_reasoning`), and any other
    endpoint by the strict-endpoint rule the samplers follow (spec 8): it is not
    an OpenAI chat parameter, so it is held back unless extended samplers are
    on, and then sent unverified."""
    if llm_reasoning.is_glm(conn):
        if value is None:
            return _Control(SUPPORTED, "reasoning_effort", "", "name", {})
        if value == "off":
            # GLM takes low, high or max; off is a level it has not got.
            return _Control(UNSUPPORTED, None, WHY_GLM, "name")
        effort = llm_reasoning.glm_effort(str(conn.get("model", "") or ""), value)
        if not effort:
            return _Control(UNSUPPORTED, None, WHY_GLM, "name")
        return _Control(SUPPORTED, "reasoning_effort", "", "name", {"reasoning_effort": effort})
    if c.openai:
        return _openai_api_reasoning(c, value)
    if value == "off":
        return _Control(UNKNOWN, None, WHY_REASONING_OFF, "unknown")
    fields = {"reasoning_effort": value} if value else {}
    if not c.extended:
        return _Control(UNSUPPORTED, None, WHY_STANDARD, "adapter")
    return _Control(UNKNOWN, "reasoning_effort", WHY_REASONING_ENDPOINT, "unknown", fields)


def _openai_api_reasoning(c: _Conn, value: str | None) -> _Control:
    """`reasoning_effort` at the OpenAI API itself, decided by the model's family
    (spec 8): a reasoning model is sent it, a non-reasoning one is not (it
    would answer a 400; and since it does not reason, `off` is honoured by
    sending nothing), and an id in neither family is sent it unverified."""
    family = c.model.lower().rsplit("/", 1)[-1]
    fields = {"reasoning_effort": value} if value and value != "off" else {}
    if _OPENAI_NOT_REASONING.match(family):
        if value == "off":
            return _Control(SUPPORTED, None, "", "name")
        return _Control(UNSUPPORTED, None, WHY_OPENAI_NOT_REASONING, "name")
    if _OPENAI_REASONING.match(family):
        if value == "off":
            return _Control(UNKNOWN, None, WHY_REASONING_OFF, "preset")
        return _Control(SUPPORTED, "reasoning_effort", "", "name", fields)
    if value == "off":
        return _Control(UNKNOWN, None, WHY_REASONING_OFF, "preset")
    return _Control(UNKNOWN, "reasoning_effort", WHY_OPENAI_REASONING_UNVERIFIED, "unknown",
                    fields)


def _thinking_off(c: _Conn, adaptive: bool, omission_is_off: bool) -> _Control:
    """`off` on the Anthropic API. Left unset, an adaptive model runs adaptive
    thinking -- even one that also lists budgeted thinking, as Claude Opus 5
    does -- so off has to be SENT there, and only to a model whose catalog says
    it takes `disabled` (one that cannot turn thinking off answers it with a
    400) -- except Claude Sonnet 5.5, which refuses `disabled` and takes
    `between_tools` as its off (`_BETWEEN_TOOLS`). That exception is decided by
    the id alone, whatever the row's `disabled_thinking` says or omits: it is
    the model's documented behaviour, and a row cached before that field was
    read would otherwise leave the model thinking. On a budget-only model, or
    one that does not think at all (`omission_is_off`), sending nothing is off."""
    if adaptive:
        if _BETWEEN_TOOLS.search(c.model.lower()):
            return _Control(TRANSLATED, "thinking", WHY_THINKING_BETWEEN, "adapter",
                            {"thinking": {"type": "between_tools"}})
        disabled = c.features.get("disabled_thinking")
        if disabled is True:
            return _Control(TRANSLATED, "thinking", WHY_THINKING_DISABLED, "catalog",
                            {"thinking": {"type": "disabled"}})
        if disabled is False:
            return _Control(UNSUPPORTED, None, WHY_THINKING_ALWAYS, "catalog")
        return _Control(UNKNOWN, None, WHY_THINKING_OFF, "catalog")
    if omission_is_off:
        return _Control(SUPPORTED, None, "", "catalog")
    return _Control(UNKNOWN, None, WHY_THINKING_OFF, "unknown")


def _thinking(c: _Conn, value: str | None, max_tokens: int) -> _Control:
    """The Anthropic API's thinking for a preset effort (spec 8, as amended):
    adaptive thinking at that effort where the catalog lists it (current models
    refuse a budget), else a budget where it lists `enabled`, else nothing."""
    adaptive_flag = c.features.get("adaptive_thinking")
    enabled_flag = c.features.get("enabled_thinking")
    adaptive, enabled = adaptive_flag is True, enabled_flag is True
    thinks_not = adaptive_flag is False and enabled_flag is False
    if value == "off":
        return _thinking_off(c, adaptive, enabled or thinks_not)
    if thinks_not:
        return _Control(UNSUPPORTED, None, WHY_THINKING_NONE, "catalog")
    if not (adaptive or enabled):
        return _Control(UNKNOWN, None, WHY_THINKING_UNKNOWN, "unknown")
    if adaptive:
        levels = c.features.get("effort")
        if value is not None and isinstance(levels, list) and value not in levels:
            named = ", ".join(x for x in levels if isinstance(x, str)) or "none"
            return _Control(UNSUPPORTED, None, f"this model takes effort {named}", "catalog")
        return _Control(TRANSLATED, "thinking", "sent as adaptive thinking at this effort",
                        "catalog", ({"thinking": {"type": "adaptive"},
                                     "output_config": {"effort": value}} if value else {}))
    if value is None:
        return _Control(TRANSLATED, "thinking", "sent as a thinking budget", "catalog")
    budget = max(THINKING_MIN, min(THINKING_BUDGET[value], max_tokens // 2))
    if budget >= max_tokens:
        return _Control(UNSUPPORTED, None,
                        f"max_tokens of {max_tokens} leaves no room for the "
                        f"{THINKING_MIN}-token minimum thinking budget", "adapter")
    return _Control(TRANSLATED, "thinking", f"sent as a thinking budget of {budget} tokens",
                    "catalog", {"thinking": {"type": "enabled", "budget_tokens": budget}})


def _reasoning(c: _Conn, conn: dict, value: str | None, max_tokens: int) -> _Control:
    """`reasoning_effort` per adapter (spec 8). `value` None: the preset sets
    none, and the answer describes what setting one would do. `max` is GLM's
    level alone: every other adapter answers it unsupported, sending nothing."""
    if value == "max" and not (c.kind == "openai_compatible" and llm_reasoning.is_glm(conn)):
        return _Control(UNSUPPORTED, None, WHY_MAX, "adapter")
    if c.kind == "claude":
        return _Control(UNSUPPORTED, None, WHY_CLAUDE, "adapter")
    if c.kind == "anthropic":
        return _thinking(c, value, max_tokens)
    if c.kind == "openai_compatible":
        return _openai_reasoning(c, conn, value)
    return _openrouter_reasoning(c, value)


def effective(conn: dict) -> dict:
    """What `conn` is sent from its attached preset, control by control.

    `{"requested": {name: stored value}, "effective": {wire name: value},
    "controls": {name: {"state", "wire", "why", "source"}}}`, every control in
    `CONTROLS` order whether the preset sets it or not -- an unset control says
    what setting it would do. `effective` is the wire body's share, ready to
    merge: the sampler parameters, `repeat_penalty` beside `repetition_penalty`
    on an `openai_compatible` endpoint, the reasoning control's fields, and the
    Anthropic API's `max_tokens`, which it is always sent. `why` is a sentence
    for every state but `supported` (and on a `supported` value it changed).

    Reads only the connection dict: `kind`, `model`, `base_url`,
    `sampling.params`, `sampler_support`, `model_params`, `model_features` and
    the legacy `reasoning_effort`. Never raises: this runs per attempt on the
    generation path, and a preset file edited by hand into nonsense costs that
    one control, reported as unsupported (`WHY_INVALID`), rather than the turn.
    """
    conn = conn if isinstance(conn, dict) else {}
    stored = _stored(conn)
    c = _context(conn)
    requested = {name: stored[name] for name in CONTROLS if name in stored}
    values: dict = {}
    invalid: set[str] = set()
    for name, value in requested.items():
        try:
            values[name] = _check(_BY_NAME[name], value)
        except ValueError:
            invalid.add(name)
    max_tokens, capped = (_anthropic_max_tokens(c, values.get("max_tokens"))
                          if c.kind == "anthropic" else (0, ""))
    reasoning = (_Control(UNSUPPORTED, None, WHY_INVALID, "user")
                 if "reasoning_effort" in invalid
                 else _reasoning(c, conn, values.get("reasoning_effort"), max_tokens))
    # Whether thinking is ON: `disabled` is the reasoning control's field too,
    # and a model that takes sampling takes it with thinking turned off.
    wired = (reasoning.fields or {}).get("thinking")
    thinking = isinstance(wired, dict) and wired.get("type") not in ("disabled",
                                                                      "between_tools")
    sent: dict = {}
    controls: dict[str, dict] = {}
    for name in CONTROLS:
        if name == REASONING_PARAM.name:
            decided = reasoning
            sent.update(decided.fields or {})
        elif name in invalid:
            decided = _Control(UNSUPPORTED, None, WHY_INVALID, "user")
        else:
            decided = (_anthropic_sampler(c, name, thinking, "temperature" in sent)
                       if c.kind == "anthropic" else _sampler(c, name, values.get(name)))
            if name in values and decided.state in _SENT and decided.wire:
                sent[decided.wire] = values[name]
                if c.kind == "openai_compatible" and name == "repetition_penalty":
                    # llama.cpp and LM Studio read `repeat_penalty`; vLLM,
                    # TabbyAPI and koboldcpp read `repetition_penalty`. Extended
                    # mode is for lenient servers by definition (they ignore a
                    # field they do not know), so sending both is what makes
                    # the one setting reach all five.
                    sent["repeat_penalty"] = values[name]
            if name == "max_tokens" and capped:
                decided = decided._replace(why=capped, source="catalog")
        if name == "max_tokens" and c.kind == "anthropic":
            # Required by that API: sent even when the preset's own is invalid.
            sent["max_tokens"] = max_tokens
        controls[name] = {"state": decided.state, "wire": decided.wire, "why": decided.why,
                          "source": decided.source}
    return {"requested": requested, "effective": sent, "controls": controls}


def not_applicable(conn: dict, why: str) -> dict:
    """`effective`'s answer for an operation that takes no sampling at all (a
    native decision): what `conn`'s preset stores is still `requested`, as
    `effective` reports it, nothing is sent, and every control in `CONTROLS`
    is `n/a` for `why`."""
    conn = conn if isinstance(conn, dict) else {}
    stored = _stored(conn)
    return {"requested": {name: stored[name] for name in CONTROLS if name in stored},
            "effective": {},
            "controls": {name: {"state": NOT_APPLICABLE, "wire": "", "why": why,
                                "source": "adapter"} for name in CONTROLS}}


def reasoning_wire(eff: dict) -> dict:
    """The reasoning control's share of `effective(...)["effective"]` -- what
    the facade hands an adapter beside the sampler parameters `split` sends."""
    return {k: eff["effective"][k] for k in REASONING_WIRE if k in eff["effective"]}


def _sent(eff: dict, name: str) -> bool:
    """Whether the preset's `name` went on the wire."""
    entry = eff["controls"][name]
    return (name in eff["requested"] and entry["state"] in _SENT
            and entry["wire"] is not None and entry["wire"] in eff["effective"])


def _dropped(eff: dict, names: tuple[str, ...]) -> list[dict]:
    return [{"param": name, "reason": eff["controls"][name]["why"]} for name in names
            if name in eff["requested"] and eff["controls"][name]["state"] == UNSUPPORTED]


def split(conn: dict) -> tuple[dict, list[dict]]:
    """What this connection will be sent from its attached preset's SAMPLER
    parameters, and what not -- a view over `effective`.

    `applied` is keyed by WIRE name, ready to merge into a request body -- which
    for an extended endpoint means repetition penalty twice, once under each
    spelling, and at the OpenAI API `max_completion_tokens`. `dropped` is
    `[{param, reason}]` in `PARAMS` order. The reasoning control is not here:
    it is `reasoning_wire`'s. Never raises (see `effective`).
    """
    eff = effective(conn)
    applied: dict = {}
    for name in NAMES:
        if _sent(eff, name):
            wire = eff["controls"][name]["wire"]
            applied[wire] = eff["effective"][wire]
            if wire == "repetition_penalty" and "repeat_penalty" in eff["effective"]:
                applied["repeat_penalty"] = eff["effective"]["repeat_penalty"]
    return applied, _dropped(eff, NAMES)


def sent_names(conn: dict) -> list[str]:
    """The preset controls this connection actually sends, canonical names."""
    eff = effective(conn)
    return [name for name in CONTROLS if _sent(eff, name)]


def sent_fields(conn: dict) -> dict[str, dict]:
    """`{canonical name: that control's share of the wire body}` for every
    control `sent_names` lists, in the same order.

    A sampler's share is its one field (and `repeat_penalty` beside
    `repetition_penalty` where both are sent); the reasoning control's is every
    field it wrote -- adaptive thinking is `thinking` AND `output_config`, and a
    provider refusing it may name either. `llm._preset_refusal` reads the
    spellings a refusal could echo from here."""
    eff = effective(conn)
    shares: dict[str, dict] = {}
    for name in CONTROLS:
        if not _sent(eff, name):
            continue
        if name == REASONING_PARAM.name:
            shares[name] = reasoning_wire(eff)
            continue
        wire = eff["controls"][name]["wire"]
        share = {wire: eff["effective"][wire]}
        if wire == "repetition_penalty" and "repeat_penalty" in eff["effective"]:
            share["repeat_penalty"] = eff["effective"]["repeat_penalty"]
        shares[name] = share
    return shares


def report(conn: dict | None) -> dict | None:
    """What a reader is shown about this connection's sampling, or None when
    nothing was resolved for it at all.

    `applied` is every control the preset set that is honoured, under its
    canonical name and with the value sent (a capped `max_tokens` capped; an
    `off` that is honoured by sending nothing, as `off`). `verified` is False
    only where a drop could be happening that this side cannot see: an applied
    control whose state is `unknown` -- an OpenRouter connection with no cached
    catalog for its model, which sends everything and cannot say whether the
    model takes it.
    """
    if not isinstance(conn, dict) or not isinstance(conn.get("sampling"), dict):
        return None
    sampling = conn["sampling"]
    eff = effective(conn)
    applied: dict = {}
    for name in CONTROLS:
        entry = eff["controls"][name]
        if name not in eff["requested"] or entry["state"] == UNSUPPORTED:
            continue
        if _sent(eff, name) and name == REASONING_PARAM.name:
            # Its wire value is a translation (`thinking: {...}`, `reasoning:
            # {effort}`); the reader asked for a level, and that is what was
            # honoured.
            applied[name] = eff["requested"][name]
        elif _sent(eff, name):
            applied[name] = eff["effective"][entry["wire"]]
        elif entry["wire"] is None and entry["state"] == SUPPORTED:
            # Honoured by sending nothing (an `off` the model takes as off).
            # An `unknown` that sends nothing has applied nothing anyone knows.
            applied[name] = eff["requested"][name]
    verified = not any(eff["controls"][name]["state"] == UNKNOWN for name in applied)
    return {"preset_id": sampling.get("preset_id", ""),
            "preset_name": sampling.get("preset_name", ""),
            "scope": sampling.get("scope", ""), "kind": conn.get("kind", "openrouter"),
            "applied": applied, "dropped": _dropped(eff, CONTROLS), "verified": verified}


def table() -> list[dict]:
    """The parameter table, for a client that renders a form from it: the
    samplers, then the reasoning effort as a choice of `REASONING`."""
    rows = [{"name": p.name, "label": p.label, "kind": p.kind,
             **({"min": p.low, "max": p.high} if p.kind != "stop"
                else {"max_entries": STOP_MAX, "max_chars": STOP_CHARS})}
            for p in PARAMS]
    rows.append({"name": REASONING_PARAM.name, "label": REASONING_PARAM.label,
                 "kind": REASONING_PARAM.kind, "choices": list(REASONING_PARAM.choices)})
    return rows
