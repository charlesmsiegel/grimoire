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
provider default nobody chose to change.
"""

from __future__ import annotations

import math
from typing import NamedTuple


class Param(NamedTuple):
    """One sampler parameter as a preset stores it."""

    name: str
    label: str
    kind: str          # "float" | "int" | "stop"
    low: float
    high: float


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
_BY_NAME = {p.name: p for p in PARAMS}

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
WHY_INVALID = "the stored value is not valid"


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
    return {name: out[name] for name in NAMES if name in out}


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


def split(conn: dict) -> tuple[dict, list[dict]]:
    """What this connection will be sent from its attached preset, and what not.

    `applied` is keyed by WIRE name, ready to merge into a request body -- which
    for an extended endpoint means repetition penalty twice, once under each
    spelling (below). `dropped` is `[{param, reason}]` in `PARAMS` order.

    Never raises: this runs per attempt on the generation path, and a preset
    file edited by hand into nonsense costs that one parameter, reported as
    dropped, rather than the turn.
    """
    stored = _stored(conn)
    kind = conn.get("kind", "openrouter") if isinstance(conn, dict) else "openrouter"
    extended = conn.get("sampler_support") == "extended"
    listed = conn.get("model_params")
    # Strings only: a hand-edited or sync-mangled catalog with an object in the
    # list must cost that entry, not the turn (`set` of a dict raises).
    listed = {x for x in listed if isinstance(x, str)} if isinstance(listed, list) else None
    applied: dict = {}
    dropped: list[dict] = []
    for p in PARAMS:
        if p.name not in stored:
            continue
        try:
            value = _check(p, stored[p.name])
        except ValueError:
            dropped.append({"param": p.name, "reason": WHY_INVALID})
            continue
        why = _why_not(kind, extended, listed, p.name, value)
        if why:
            dropped.append({"param": p.name, "reason": why})
            continue
        applied[p.name] = value
        if kind == "openai_compatible" and p.name == "repetition_penalty":
            # llama.cpp and LM Studio read `repeat_penalty`; vLLM, TabbyAPI and
            # koboldcpp read `repetition_penalty`. Extended mode is for lenient
            # servers by definition (they ignore a field they do not know), so
            # sending both is what makes the one setting reach all five.
            applied["repeat_penalty"] = value
    return applied, dropped


def sent_names(conn: dict) -> list[str]:
    """The preset parameters this connection actually sends, canonical names."""
    applied, _ = split(conn)
    return [name for name in NAMES if name in applied]


def report(conn: dict | None) -> dict | None:
    """What a reader is shown about this connection's sampling, or None when
    nothing was resolved for it at all.

    `verified` is False only where a drop could be happening that this side
    cannot see: an OpenRouter connection with no cached catalog for its model,
    which sends everything and cannot say whether the model takes it.
    """
    if not isinstance(conn, dict) or not isinstance(conn.get("sampling"), dict):
        return None
    sampling = conn["sampling"]
    kind = conn.get("kind", "openrouter")
    applied, dropped = split(conn)
    verified = not (kind not in ("claude", "openai_compatible")
                    and not isinstance(conn.get("model_params"), list)
                    and bool(applied))
    return {"preset_id": sampling.get("preset_id", ""),
            "preset_name": sampling.get("preset_name", ""),
            "scope": sampling.get("scope", ""), "kind": kind,
            "applied": {name: applied[name] for name in NAMES if name in applied},
            "dropped": dropped, "verified": verified}


def table() -> list[dict]:
    """The parameter table, for a client that renders a form from it."""
    return [{"name": p.name, "label": p.label, "kind": p.kind,
             **({"min": p.low, "max": p.high} if p.kind != "stop"
                else {"max_entries": STOP_MAX, "max_chars": STOP_CHARS})}
            for p in PARAMS]
