"""The one capability resolver: what a (provider, model) can do, and who said so.

Every capability in `providers.CAPABILITIES` resolves to `yes`, `no` or
`unknown`, with the source of that answer (spec 6.2). Sources, highest
authority first -- the first one that says anything is the answer:

1. `adapter` -- the preset's `never`: the wire protocol cannot. A hard `no`
   that nothing below may claim past, however sure it is.
2. `test`, then `user` -- the model's facts (`facts.of`): a probe's verdict
   for the connection's current `rev`, then the user's `overrides`, then the
   user's own `vision` / `prefill` statements. The legacy connection `vision`
   field is NOT one of them: it is the post-image *setting*
   (`post_images.capability`), not a statement about the model.
3. `catalog` -- what the provider publishes for the model: `outputs`
   (`text` -> generate, `embeddings` -> embed; a stated list without one of
   them is a `no`), `vision`, `params` naming `structured_outputs` or
   `response_format` (their absence says nothing), and Anthropic's
   `features.structured_output`. A key the row does not state contributes
   nothing, so a row without `outputs` leaves the name rule room to apply.
4. `preset` -- the preset's `always` (`generate`, `stream` on the generative
   presets). `possible` never yields a `yes`: it only decides whether an
   unknown is worth offering as Unverified (`group_for`).
5. `name` -- a model id containing `embed` is an embedding model.
6. otherwise `unknown`.

`resolve_caps` is pure; `caps_for` reads the store for one connection and
never raises -- a source it cannot read contributes nothing.
"""

from __future__ import annotations

from typing import NamedTuple

from .. import llm_connections
from . import facts, providers

YES, NO, UNKNOWN = "yes", "no", "unknown"
VALUES: tuple[str, ...] = (YES, NO, UNKNOWN)
SOURCES: tuple[str, ...] = ("adapter", "test", "user", "catalog", "preset", "name", "unknown")

#: Every capability, in a fixed order (the same set as `providers.CAPABILITIES`).
NAMES: tuple[str, ...] = ("generate", "stream", "vision", "embed", "decide_native",
                          "structured_output", "prefill")


class Cap(NamedTuple):
    value: str
    source: str


_UNKNOWN = Cap(UNKNOWN, "unknown")
_STRUCTURED_PARAMS = frozenset({"structured_outputs", "response_format"})

#: What a role needs, and the capabilities that can answer it. `decide` is
#: either: structured generation can answer any decision (spec 7.4).
NEEDS: dict[str, tuple[str, ...]] = {
    "generate": ("generate",),
    "vision": ("vision",),
    "embed": ("embed",),
    "decide": ("decide_native", "generate"),
}

UNVERIFIED_REASON = "not known yet — a test call can check"

#: "<preset label> ..." when the preset rules the need out.
_ADAPTER_SAYS = {
    "generate": "serves no text generation",
    "vision": "reads no images",
    "embed": "serves no embeddings",
    "decide": "cannot decide",
}
_VERB = {"generate": "generate text", "vision": "read images",
         "embed": "produce embeddings", "decide_native": "decide natively"}
_NOUN = {"generate": "text generation", "vision": "image reading",
         "embed": "embeddings", "decide_native": "native decisions"}
_GERUND = {"generate": "generating text", "vision": "reading images",
           "embed": "producing embeddings", "decide_native": "deciding natively"}


def _yes_no(flag: bool) -> str:
    return YES if flag else NO


def _stated(model_facts: dict) -> dict[str, Cap]:
    """Step 2: a probe's verdict wins over the user's word for the same cap."""
    out: dict[str, Cap] = {}
    vision = model_facts.get("vision")
    if vision in ("on", "off"):
        out["vision"] = Cap(_yes_no(vision == "on"), "user")
    prefill = model_facts.get("prefill")
    if isinstance(prefill, bool):
        out["prefill"] = Cap(_yes_no(prefill), "user")
    overrides = model_facts.get("overrides")
    if isinstance(overrides, dict):
        for cap, value in overrides.items():
            if cap in NAMES and value in (YES, NO):
                out[cap] = Cap(value, "user")
    verified = model_facts.get("verified")
    if isinstance(verified, dict):
        for cap, result in verified.items():
            ok = result.get("ok") if isinstance(result, dict) else None
            if cap in NAMES and isinstance(ok, bool):
                out[cap] = Cap(_yes_no(ok), "test")
    return out


def _listed(row: dict | None) -> dict[str, Cap]:
    """Step 3: only what the catalog row states."""
    if not isinstance(row, dict):
        return {}
    out: dict[str, Cap] = {}
    outputs = row.get("outputs")
    if isinstance(outputs, list):
        out["generate"] = Cap(_yes_no("text" in outputs), "catalog")
        out["embed"] = Cap(_yes_no("embeddings" in outputs), "catalog")
    vision = row.get("vision")
    if isinstance(vision, bool):
        out["vision"] = Cap(_yes_no(vision), "catalog")
    params = row.get("params")
    if isinstance(params, list) and any(p in _STRUCTURED_PARAMS for p in params
                                        if isinstance(p, str)):
        out["structured_output"] = Cap(YES, "catalog")
    features = row.get("features")
    structured = features.get("structured_output") if isinstance(features, dict) else None
    if isinstance(structured, bool):
        out["structured_output"] = Cap(_yes_no(structured), "catalog")
    return out


def _named(model: str) -> dict[str, Cap]:
    """Step 5: the name rule."""
    if isinstance(model, str) and "embed" in model.lower():
        return {"embed": Cap(YES, "name"), "generate": Cap(NO, "name")}
    return {}


def resolve_caps(preset: providers.Preset, model: str, *, catalog_row: dict | None,
                 facts: dict) -> dict[str, Cap]:
    """Every capability of `model` behind `preset`, with its source. Pure."""
    stated = _stated(facts) if isinstance(facts, dict) else {}
    listed = _listed(catalog_row)
    named = _named(model)
    out: dict[str, Cap] = {}
    for cap in NAMES:
        if cap in preset.never:
            out[cap] = Cap(NO, "adapter")
        elif cap in stated:
            out[cap] = stated[cap]
        elif cap in listed:
            out[cap] = listed[cap]
        elif cap in preset.always:
            out[cap] = Cap(YES, "preset")
        else:
            out[cap] = named.get(cap, _UNKNOWN)
    return out


def _row(conn_id: str, model: str) -> dict | None:
    try:
        rows = llm_connections.cached_models(conn_id)["models"]
        return next((r for r in rows if isinstance(r, dict) and r.get("id") == model), None)
    except Exception:  # noqa: BLE001 - an unreadable catalog says nothing
        return None


def _facts(conn_id: str, model: str, rev: str) -> dict:
    try:
        return facts.of(conn_id, model, rev)
    except Exception:  # noqa: BLE001 - unreadable facts say nothing
        return {}


def caps_for(conn: dict, model: str | None = None) -> dict[str, Cap]:
    """`resolve_caps` for `conn`'s model (or `model`), reading its preset,
    cached catalog row and model facts. Never raises: a connection that is
    not one, or a source that cannot be read, contributes nothing."""
    if not isinstance(conn, dict):
        return dict.fromkeys(NAMES, _UNKNOWN)
    try:
        preset = providers.infer(conn)
    except Exception:  # noqa: BLE001 - a connection nothing can place says nothing
        return dict.fromkeys(NAMES, _UNKNOWN)
    if model is None:
        model = conn.get("model", "")
    model = model if isinstance(model, str) else ""
    conn_id = conn.get("id", "")
    conn_id = conn_id if isinstance(conn_id, str) else ""
    rev = conn.get("rev", "")
    rev = rev if isinstance(rev, str) else ""
    return resolve_caps(preset, model, catalog_row=_row(conn_id, model),
                        facts=_facts(conn_id, model, rev))


def _needed(need: str) -> tuple[str, ...]:
    try:
        return NEEDS[need]
    except KeyError:
        raise ValueError(f"unknown need: {need!r}") from None


def fits(caps: dict[str, Cap], need: str) -> str:
    """`yes` / `no` / `unknown`: whether `caps` answer `need` (a `NEEDS` key)."""
    values = [caps.get(c, _UNKNOWN).value for c in _needed(need)]
    if YES in values:
        return YES
    return NO if all(v == NO for v in values) else UNKNOWN


def _why_not(caps: dict[str, Cap], need: str, preset: providers.Preset) -> str:
    """What rules `need` out. The broadest culprit first: for `decide`, a model
    that does not generate, rather than the native endpoint most lack."""
    for cap in sorted(_needed(need), key=lambda c: c != "generate"):
        found = caps.get(cap, _UNKNOWN)
        if found.value != NO or found.source == "adapter":
            continue
        if found.source == "catalog":
            return f"the catalog says this model does not {_VERB[cap]}"
        if found.source == "test":
            return f"a test call found no {_NOUN[cap]}"
        if found.source == "user":
            return f"you marked this model as not {_GERUND[cap]}"
        if found.source == "name":
            return "its name marks it as an embedding model"
        return f"this model does not {_VERB[cap]}"
    return f"{preset.label} {_ADAPTER_SAYS[need]}"


def group_for(caps: dict[str, Cap], need: str,
              preset: providers.Preset) -> tuple[str, str]:
    """`(group, reason)` for a role picker (spec 6.3): `fits` (reason: the
    source that said yes), `unverified` (unknown, and the preset does not rule
    the need out) or `hidden` (reason: what rules it out)."""
    verdict = fits(caps, need)
    needed = _needed(need)
    if verdict == YES:
        source = next(caps[c].source for c in needed if caps.get(c, _UNKNOWN).value == YES)
        return "fits", source
    if verdict == UNKNOWN and not all(c in preset.never for c in needed):
        return "unverified", UNVERIFIED_REASON
    return "hidden", _why_not(caps, need, preset)
