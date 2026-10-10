"""The one capability resolver: what a (provider, model) can do, and who said so.

Every capability in `providers.CAPABILITIES` resolves to `yes`, `no` or
`unknown`, with the source of that answer (spec 6.2). Sources, highest
authority first -- the first one that says anything is the answer:

1. `adapter` -- the preset's `never`: the wire protocol cannot (the Claude
   subscription's `tools`, until 01g-S8). A hard `no` that nothing below may
   claim past, however sure it is.
2. `test`, then `user` -- the model's facts (`facts.of`): a probe that
   PASSED for the connection's current `rev` (`yes`), then the user's
   `overrides`, then the user's own `prefill` statement and a facts
   `vision: on` (a facts `vision: off` is a post-image preference and says
   nothing about the model, spec 4.2), then a
   probe that FAILED (`unknown`, source `test`, carrying the provider's
   `error`). A failed test is never a `no` (spec 12: the row stays unverified
   with the error shown), so a test can never make the seam refuse; it
   outranks a catalog's `yes`, so that `yes` does not hide what the test
   found, but never the user's own word -- and never a catalog's `no`: an
   `unknown` does not override a lower source's known `no`
   (`_unknown_over_no`), so a failed test cannot lift a refusal or turn an
   Embedding role that is off back on. The legacy connection `vision` field
   is NOT one of these: it is the post-image *setting*
   (`post_images.capability`), not a statement about the model.
3. `catalog` -- what the provider publishes for the model: `outputs`
   (`text` -> generate, `embeddings` -> embed; a stated list without one of
   them is a `no`; `decisions` -> decide_native, whose absence says nothing),
   `vision`, `params` naming `structured_outputs` (its absence says
   nothing; `response_format` alone also covers JSON mode, so says
   nothing either) or `tools` (a stated list without it is a `no` on an
   OpenRouter connection only, `_params_caps`), and Anthropic's
   `features.structured_output`. A key the row does not state contributes
   nothing, so a row without `outputs` leaves the name rule room to apply.
4. `preset` -- the preset's `always` (`generate`, `stream` on the generative
   presets; `tools` on the Anthropic API's). `possible` never yields a `yes`: it only decides whether an
   unknown is worth offering as Unverified (`group_for`).
5. `name` -- a model id containing `embed` is an embedding model.
6. otherwise `unknown`.

`resolve_caps` is pure; `caps_for` reads the store for one connection and
never raises -- a source it cannot read contributes nothing.
"""

from __future__ import annotations

from typing import NamedTuple

from .. import image_drafts, llm_connections
from . import facts, providers

YES, NO, UNKNOWN = "yes", "no", "unknown"
VALUES: tuple[str, ...] = (YES, NO, UNKNOWN)
SOURCES: tuple[str, ...] = ("adapter", "test", "user", "catalog", "preset", "name", "unknown")

#: Every capability, in a fixed order (the same set as `providers.CAPABILITIES`).
NAMES: tuple[str, ...] = ("generate", "stream", "vision", "embed", "decide_native",
                          "structured_output", "prefill", "tools")


class Cap(NamedTuple):
    value: str
    source: str
    #: What a failed test call reported (source `test`, value `unknown`);
    #: empty for every other answer.
    error: str = ""


_UNKNOWN = Cap(UNKNOWN, "unknown")
#: The catalog parameters that say a model takes a JSON Schema. Not
#: `response_format` alone: that also covers JSON mode (`json_object`), which
#: answers with JSON but holds it to no schema (plan Minor 5).
_STRUCTURED_PARAMS = frozenset({"structured_outputs"})

#: What a role needs, and the capabilities that can answer it. `decide` is
#: either: structured generation can answer any decision (spec 7.4).
NEEDS: dict[str, tuple[str, ...]] = {
    "generate": ("generate",),
    "vision": ("vision",),
    "embed": ("embed",),
    "decide": ("decide_native", "generate"),
    # Not a role's need: a route's (`requires=("tools",)`, 01g), asked of a
    # pin picker the way the image route asks `vision`.
    "tools": ("tools",),
}

UNVERIFIED_REASON = "not known yet — a test call can check"
#: A row whose test call failed; the provider's error follows when it gave one.
FAILED_REASON = "a test call failed"

#: "<preset label> ..." when the preset rules the need out.
_ADAPTER_SAYS = {
    "generate": "serves no text generation",
    "vision": "reads no images",
    "embed": "serves no embeddings",
    "decide": "cannot decide",
    "tools": "calls no tools",
}
#: What a model lacking each capability cannot do: "... cannot <phrase>" and
#: "... does not <phrase>". The one table; the seam's `incapable` refusal
#: (`resolve.incapable_text`) and `group_for`'s reasons both read it.
CANNOT: dict[str, str] = {
    "generate": "generate text",
    "vision": "read images",
    "embed": "make embeddings",
    "structured_output": "return structured output",
    "prefill": "continue a prefilled reply",
    "decide_native": "make native decisions",
    "stream": "stream",
    "tools": "call tools",
}
_GERUND = {"generate": "generating text", "vision": "reading images",
           "embed": "producing embeddings", "decide_native": "deciding natively",
           "tools": "calling tools"}


def _yes_no(flag: bool) -> str:
    return YES if flag else NO


def _tested(verified: object) -> tuple[dict[str, Cap], dict[str, Cap]]:
    """`(passed, failed)`: the probes' verdicts. A pass is a `yes`; a failure
    is unverified, carrying the provider's error text (spec 12)."""
    passed: dict[str, Cap] = {}
    failed: dict[str, Cap] = {}
    if not isinstance(verified, dict):
        return passed, failed
    for cap, result in verified.items():
        ok = result.get("ok") if isinstance(result, dict) else None
        if cap not in NAMES or not isinstance(ok, bool):
            continue
        if ok:
            passed[cap] = Cap(YES, "test")
        else:
            error = result.get("error")
            failed[cap] = Cap(UNKNOWN, "test", error if isinstance(error, str) else "")
    return passed, failed


def _stated(model_facts: dict) -> dict[str, Cap]:
    """Step 2: a passed probe wins over the user's word for the same cap, and
    the user's word wins over a failed one."""
    passed, failed = _tested(model_facts.get("verified"))
    out: dict[str, Cap] = dict(failed)
    # Facts `vision` is the post-image preference (spec 4.2). "on" is the
    # user's word that the model reads images; "off" only stops post images
    # and says nothing about the model -- read as `no`, it would refuse image
    # descriptions that serve today. The user's `no` is `overrides.vision`.
    if model_facts.get("vision") == "on":
        out["vision"] = Cap(YES, "user")
    prefill = model_facts.get("prefill")
    if isinstance(prefill, bool):
        out["prefill"] = Cap(_yes_no(prefill), "user")
    overrides = model_facts.get("overrides")
    if isinstance(overrides, dict):
        for cap, value in overrides.items():
            if cap in NAMES and value in (YES, NO):
                out[cap] = Cap(value, "user")
    out.update(passed)
    return out


def _params_caps(params: set[str], kind: str) -> dict[str, Cap]:
    """What a stated parameter list (OpenRouter's `supported_parameters`,
    kept by `catalog.entry` as `params`) says.

    `structured_output` only ever `yes`: `response_format` alone also covers
    JSON mode, so its absence says nothing. `tools` is `yes` when the list
    names it from any provider, and `no` when it does not -- but only on an
    `openrouter` connection (01g, open question 5): OpenRouter states the
    parameters each model's endpoints take, per model, and has no second
    spelling for tools; any other server that happens to send such a list is
    believed when it says yes and never made a refusal of."""
    out: dict[str, Cap] = {}
    if params & _STRUCTURED_PARAMS:
        out["structured_output"] = Cap(YES, "catalog")
    if "tools" in params:
        out["tools"] = Cap(YES, "catalog")
    elif kind == "openrouter":
        out["tools"] = Cap(NO, "catalog")
    return out


def _listed(row: dict | None, kind: str = "") -> dict[str, Cap]:
    """Step 3: only what the catalog row states. `kind` is the adapter the
    row was listed by (`_params_caps`)."""
    if not isinstance(row, dict):
        return {}
    out: dict[str, Cap] = {}
    outputs = row.get("outputs")
    if isinstance(outputs, list):
        out["generate"] = Cap(_yes_no("text" in outputs), "catalog")
        out["embed"] = Cap(_yes_no("embeddings" in outputs), "catalog")
        # Only a yes: a list without "decisions" leaves the native endpoint
        # to the steps below (most text models have none, but a list of
        # outputs does not say so).
        if "decisions" in outputs:
            out["decide_native"] = Cap(YES, "catalog")
    vision = row.get("vision")
    if isinstance(vision, bool):
        out["vision"] = Cap(_yes_no(vision), "catalog")
    params = row.get("params")
    if isinstance(params, list):
        out.update(_params_caps({p for p in params if isinstance(p, str)}, kind))
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


def _unknown_over_no(higher: Cap, lower: Cap | None) -> bool:
    """Whether `higher` is an `unknown` standing over a `lower` known `no`
    -- which it never overrides. Only a failed test is a stated `unknown`,
    and only the catalog is a lower source that can know `no` (the preset
    knows only `yes`, the name rule only guesses), so this keeps a failed
    test from lifting a catalog's `no`: an unanswered question is not an
    answer, and lifting it would turn a refusal (or an Embedding role that
    is off) back on to send what could only fail."""
    return higher.value == UNKNOWN and lower is not None and lower.value == NO


def resolve_caps(preset: providers.Preset, model: str, *, catalog_row: dict | None,
                 facts: dict) -> dict[str, Cap]:
    """Every capability of `model` behind `preset`, with its source. Pure."""
    stated = _stated(facts) if isinstance(facts, dict) else {}
    listed = _listed(catalog_row, preset.kind)
    named = _named(model)
    never = providers.never_for(preset, model)
    out: dict[str, Cap] = {}
    for cap in NAMES:
        if cap in never:
            out[cap] = Cap(NO, "adapter")
        elif cap in stated and not _unknown_over_no(stated[cap], listed.get(cap)):
            out[cap] = stated[cap]
        elif cap in listed:
            out[cap] = listed[cap]
        elif cap in preset.always:
            out[cap] = Cap(YES, "preset")
        else:
            out[cap] = named.get(cap, _UNKNOWN)
    return out


def _facts(conn_id: str, model: str, rev: str) -> dict:
    try:
        return facts.of(conn_id, model, rev)
    except Exception:  # noqa: BLE001 - unreadable facts say nothing
        return {}


def caps_for(conn: dict | None, model: str | None = None) -> dict[str, Cap]:
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
    return resolve_caps(preset, model,
                        catalog_row=llm_connections.cached_row(conn_id, model),
                        facts=_facts(conn_id, model, rev))


def post_image_reach(kind: str, vision_fact: str, vision_cap: Cap) -> str:
    """"yes", "no" or "unknown": whether post images may be sent to a model
    (#377) -- `post_images.capability`'s rule, over values already in hand.

    A `kind` whose client cannot carry an image part
    (`image_drafts.SUPPORTED_KINDS`) is "no", whatever is said of it. Else the
    post-image preference `vision_fact` wins ("on" / "off": the connection's
    field at format 1, the model's facts at format 2), which is a setting
    rather than a statement about the model, so this resolver's `vision` never
    reads it. Else `vision_cap`, the model's `vision` capability, answers."""
    if kind not in image_drafts.SUPPORTED_KINDS:
        return NO
    if vision_fact == "on":
        return YES
    if vision_fact == "off":
        return NO
    return vision_cap.value


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
            return f"the catalog says this model does not {CANNOT[cap]}"
        if found.source == "user":
            return f"you marked this model as not {_GERUND[cap]}"
        if found.source == "name":
            return "its name marks it as an embedding model"
        return f"this model does not {CANNOT[cap]}"
    return f"{preset.label} {_ADAPTER_SAYS[need]}"


def group_for(caps: dict[str, Cap], need: str,
              preset: providers.Preset) -> tuple[str, str]:
    """`(group, reason)` for a role picker (spec 6.3): `fits` (reason: the
    source that said yes), `unverified` (unknown, and the preset does not rule
    the need out; reason: a failed test's error, or that nothing has said)
    or `hidden` (reason: what rules it out)."""
    verdict = fits(caps, need)
    needed = _needed(need)
    if verdict == YES:
        source = next(caps[c].source for c in needed if caps.get(c, _UNKNOWN).value == YES)
        return "fits", source
    if verdict == UNKNOWN and not all(c in preset.never for c in needed):
        return "unverified", _unverified_reason(caps, needed)
    return "hidden", _why_not(caps, need, preset)


def _unverified_reason(caps: dict[str, Cap], needed: tuple[str, ...]) -> str:
    """Why a row is only unverified: a failed test call, with what the
    provider said, when one is on record for a capability the need rests on;
    otherwise that nothing has said yet."""
    for cap in needed:
        found = caps.get(cap, _UNKNOWN)
        if found.value == UNKNOWN and found.source == "test":
            return f"{FAILED_REASON}: {found.error}" if found.error else FAILED_REASON
    return UNVERIFIED_REASON


# ---- the model list a role picker reads ----
def preset_body(preset: providers.Preset) -> dict:
    """A provider preset as the wire carries it: the namedtuple, with each
    capability set as a sorted list."""
    return {"id": preset.id, "label": preset.label, "kind": preset.kind,
            "base_url": preset.base_url, "url_locked": preset.url_locked,
            "billing": preset.billing, "reports_price": preset.reports_price,
            "always": sorted(preset.always), "possible": sorted(preset.possible),
            "never": sorted(preset.never)}


def cap_body(cap: Cap) -> dict:
    """One capability as the wire carries it: `{value, source}`, plus `error`
    when a failed test call left one."""
    body = {"value": cap.value, "source": cap.source}
    if cap.error:
        body["error"] = cap.error
    return body


def _catalog_rows(conn_id: str) -> list[dict]:
    try:
        rows = llm_connections.cached_models(conn_id)["models"]
    except Exception:  # noqa: BLE001 - an unreadable catalog lists nothing
        return []
    return sorted((r for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str)),
                  key=lambda r: r["id"])


def _facts_file(conn_id: str) -> dict:
    try:
        return facts.read(conn_id)
    except Exception:  # noqa: BLE001 - unreadable facts say nothing
        return {}


def grouped(conn: dict, need: str, model: str | None = None) -> dict:
    """`conn`'s cached catalog grouped for a role that needs `need` (spec 6.3):

        {"provider_preset": {...}, "need": str,
         "groups": {"fits": [row], "unverified": [row]},
         "hidden": [{"id", "reason"}], "reason": str | None}

    A row is the catalog entry (every row, embedding-only ones included: the
    Embedding picker lists those) plus `capabilities` (`{name: {value,
    source, error?}}`, `error` only after a failed test) and `reason` (what
    put it in its group: the source that said yes, or why it is only
    unverified -- a failed test's error among them). Each list is sorted by
    id, as the catalog is.

    When the preset rules the need out for EVERY model (z.ai for `embed`),
    both groups and `hidden` are empty and `reason` alone says why -- one
    sentence rather than the same one echoed per row. Otherwise `reason` is None.

    `model` narrows the answer to that one id, in whichever group it lands in;
    an id the catalog does not list is judged on what is known without a row
    (its name, the preset, the user's facts). Raises ValueError for an unknown
    `need`; reads only the store, never the network.
    """
    needed = _needed(need)
    preset = providers.infer(conn)
    out: dict = {"provider_preset": preset_body(preset), "need": need,
                 "groups": {"fits": [], "unverified": []}, "hidden": [], "reason": None}
    if all(c in preset.never for c in needed):
        out["reason"] = group_for(resolve_caps(preset, "", catalog_row=None, facts={}),
                                  need, preset)[1]
        return out
    conn_id = conn.get("id", "")
    conn_id = conn_id if isinstance(conn_id, str) else ""
    rev = conn.get("rev", "")
    rev = rev if isinstance(rev, str) else ""
    rows = _catalog_rows(conn_id)
    if model:
        rows = [next((r for r in rows if r["id"] == model), {"id": model})]
    stated = _facts_file(conn_id) if rows else {}
    for row in rows:
        known = _facts(conn_id, row["id"], rev) if row["id"] in stated else {}
        caps = resolve_caps(preset, row["id"], catalog_row=row, facts=known)
        group, reason = group_for(caps, need, preset)
        if group == "hidden":
            out["hidden"].append({"id": row["id"], "reason": reason})
            continue
        out["groups"][group].append({
            **row, "reason": reason,
            "capabilities": {n: cap_body(c) for n, c in caps.items()}})
    return out
