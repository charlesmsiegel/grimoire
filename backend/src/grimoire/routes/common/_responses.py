"""common: responses responsibilities."""

from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel

from ... import store


def _dump(model: BaseModel) -> dict:
    """model_dump() on pydantic v2, dict() on v1. The Android build may pin the
    pure-python pydantic 1.x wheel (docs/android-architecture.md §7); this is
    the only v2-specific dump the codebase uses. Its one sibling is
    `model_schema.structured`, which asks `model_json_schema` or v1's `schema`
    for a model's JSON Schema (spec 07 section 9.1.1) -- never a dump."""
    dump = getattr(model, "model_dump", None)
    return dump() if dump is not None else model.dict()


def _turn_override(body) -> dict | None:
    """A request body's one-shot per-turn response override as a plain dict.

    The wire type is `ResponseSettings`, like every other response write path,
    so a malformed payload is rejected at the boundary instead of reaching
    response_presets.resolve mid-generation. Unset fields are dropped: a scope
    dict means "these fields have an opinion", and a None would read as one.
    """
    if body is None or getattr(body, "response", None) is None:
        return None
    fields = {k: v for k, v in _dump(body.response).items() if v is not None}
    for key in store.response_targets.FIELDS:
        if key in fields and (not isinstance(fields[key], str)
                              or not fields[key].strip().isascii()
                              or not fields[key].strip().isdigit()
                              or store.response_targets.coerce(fields[key]) is None):
            raise HTTPException(400, detail=f"{key} must be a positive whole number")
    return fields


# ---- response bundle (scope endpoints) ----
def _response_body(scene_meta: dict, campaign_meta: dict, cfg: dict, own: dict) -> dict:
    """The shape every scope returns. `own` is that scope's raw frontmatter."""
    resolved = store.response_presets.resolve(
        scene_meta=scene_meta, campaign_meta=campaign_meta, config=cfg)
    targets = store.response_targets.resolve(
        scene_meta=scene_meta, campaign_meta=campaign_meta, config=cfg)
    fields = {k: own.get(k, "") for k in store.scenes.RESPONSE_FIELDS}
    # Global stores the style as default_style_id; normalize so the picker sees
    # one spelling at every scope. The on-disk key is deliberately unchanged.
    if not fields["style_id"]:
        fields["style_id"] = own.get("default_style_id", "")
    return {**fields,
            "effective": {**{k: resolved[k] for k in ("style_id", *store.lengths.KNOBS)},
                          "opening": targets["opening"],
                          "continuation": targets["continuation"]},
            "provenance": {**resolved["provenance"], **targets["provenance"]}}


def _write_response(setter, fields: dict, style_key: str = "style_id") -> None:
    """Map the picker's style_id back onto the scope's own spelling."""
    for key in store.response_targets.FIELDS:
        if key in fields and fields[key] != "" and store.response_targets.coerce(fields[key]) is None:
            raise HTTPException(status_code=400, detail=f"{key} must be a positive integer")
    out = dict(fields)
    if style_key != "style_id" and "style_id" in out:
        out[style_key] = out.pop("style_id")
    setter(out)


def computes_only(fn):
    """Mark a campaign-scoped POST that persists nothing.

    POST is not a synonym for write. These routes compute and return -- a
    generated voice anchor for the user to accept or discard, scene
    suggestions, a roll replayed against its stored inputs -- so treating them
    as campaign activity moves a campaign up the recents rail for merely being
    *looked* at.

    Declared at the route rather than as a path list in the middleware,
    deliberately: a path list sits far from the thing it describes and goes
    stale silently, which is how the activity sweep leaked for six rounds. The
    next preview route's author sees this on its neighbours.
    """
    fn.grimoire_computes_only = True
    return fn


def stamps_campaign_write(fn):
    """A route whose writer owns activity and revision stamping.

    Campaign settings (#73) may accept a save that changes nothing. The store
    stamps after its durable change under the campaign lock; middleware must
    stamp neither a successful no-op nor an exception before publication.
    """
    fn.grimoire_stamps_campaign_write = True
    return fn


def leaves_campaign_unchanged(fn):
    """Mark a campaign-scoped write that does not write to the campaign it names.

    There is exactly one, and its whole design rests on it:
    `POST /campaigns/{cid}/fork` copies `cid` into a *second* campaign and
    touches the source not at all (`store/fork.py`). It is still activity in the
    source -- a fork is something that happened there, and the recents rail
    should say so -- but it must not move the source's revision token (#409),
    which answers "has this campaign changed since I priced against it?". A fork
    that answered yes would break the one composition the token exists for:
    checkpoint this campaign, then advance it against the state the checkpoint
    was taken from.

    A sibling of `computes_only` rather than a second meaning bolted onto it,
    because the two say different things -- "nothing was written" and "something
    was written, elsewhere" -- and the middleware acts on them differently.
    Declared at the route for the same reason: a path list in the middleware
    sits far from what it describes and goes stale in silence.
    """
    fn.grimoire_leaves_campaign = True
    return fn
