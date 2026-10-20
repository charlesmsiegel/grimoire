"""Prompt inspection distinguishes the next live target from a frozen attempted call."""

from __future__ import annotations

from fastapi import HTTPException

from ... import llm_sampling, store
from ...store.inference import resolve as inference
from .. import decision_capture
from ..common import _require_scene, model_window
from . import _targets
from ._router import router as router


@router.get("/campaigns/{cid}/scenes/{sid}/context")
def get_scene_context(cid: str, sid: str):
    """The context breakdown, as packed. `total_tokens` is what was actually
    sent, measured the way the packer measures it — a section the packer
    dropped still ships here, with its text and `dropped: true`, so the
    inspector can show what was cut without that cut counting toward the total
    it was cut to fit. See `context.context_breakdown` for why the total is not
    the sum of the rows.

    `token_count` says which tokenizer produced those numbers and whether it is
    this model's own (`tokens.counting`) -- for most backends it is not, and the
    inspector marks the counts as estimates.

    `model_window` is that model's window as the backend resolved it (01i),
    `{value, source}`, which the inspector draws the bar against."""
    _require_scene(cid, sid)
    resolved, model = _targets._chat_target(cid)
    # What the next turn would send, pictures included (#377): the Images row is
    # present exactly when they would reach this scene's routed connection. And
    # what the next ordinary turn's sampler preset sends on this connection,
    # and what its backend cannot take -- the inspector says so rather than the
    # facade dropping it in silence (sampler presets spec). A frozen snapshot
    # carries the same block for the attempt it captured.
    breakdown = store.context.context_breakdown(
        cid, sid, model=model, images=store.post_images.images_for(_targets._primary(resolved.chain)),
        resolved=resolved)
    return {"model": model, **breakdown,
            "model_window": model_window(_targets._primary(resolved.chain)),
            "token_count": store.tokens.counting(model, _targets._target_kind(resolved.chain),
                                                 breakdown.get("counted_with", "")),
            "sampling": llm_sampling.report(_targets._primary(resolved.chain))}


@router.get("/campaigns/{cid}/scenes/{sid}/prompts")
def get_scene_prompts(cid: str, sid: str):
    """This scene's frozen per-turn prompt snapshots, newest first (#157).

    Rows only — id, when, which kind of turn, the model, and the three totals.
    The section text lives in the individual entries, which are large enough
    that listing them all would defeat the point of a list."""
    _require_scene(cid, sid)
    return {"entries": store.prompt_log.list_entries(cid, sid)}


@router.get("/campaigns/{cid}/scenes/{sid}/prompts/{eid}")
def get_scene_prompt(cid: str, sid: str, eid: str):
    """One frozen breakdown, in the same shape `GET .../context` returns — so
    the inspector renders a past turn with the code it already has, pointed at
    stored text instead of a fresh composition.

    404 covers both "never existed" and "evicted by the retention window";
    nothing downstream can tell them apart and nothing needs to."""
    _require_scene(cid, sid)
    # Scoped to the scene, not merely nested under it in the URL: ids are
    # campaign-wide, so an unscoped read would serve one scene's prompt under
    # another's heading.
    entry = store.prompt_log.read_entry(cid, eid, scene=sid)
    if entry is None:
        raise HTTPException(status_code=404, detail="prompt snapshot not found")
    return entry


@router.get("/campaigns/{cid}/prompts")
def get_campaign_prompts(cid: str):
    """The campaign-level captures, newest first (roadmap 01b): decisions
    asked with no scene -- today the continuity reconcile sweep's, one entry
    per pass. Rows only, as the scene list's. Never a scene's entry: those are
    the scene routes', and these are not reachable through them (a path
    segment is never empty)."""
    _require_campaign(cid)
    return {"entries": store.prompt_log.list_entries(cid, store.prompt_log.NO_SCENE)}


@router.get("/campaigns/{cid}/prompts/{eid}")
def get_campaign_prompt(cid: str, eid: str):
    """One campaign-level capture, scoped to `NO_SCENE` so a scene's entry
    is never served here; 404 when it never existed or has aged out. There is
    no diff route at this level (roadmap 01b §3.6)."""
    _require_campaign(cid)
    entry = store.prompt_log.read_entry(cid, eid, scene=store.prompt_log.NO_SCENE)
    if entry is None:
        raise HTTPException(status_code=404, detail="prompt snapshot not found")
    return entry


def _require_campaign(cid: str) -> None:
    try:
        store.campaigns.read_campaign(cid)
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc


#: The `against` value that means "the composition as it stands now" rather than
#: a second frozen turn. Not a valid entry id -- those are all digits -- so it
#: cannot collide with one.
LIVE_SIDE = "live"


def _diff_side(entry: dict) -> dict:
    """One end of a comparison, as the panel labels it. The metadata only: the
    section rows are what the diff itself carries, and shipping them twice more
    would put the whole of both prompts in a response that exists to say what is
    different about them."""
    return {k: entry.get(k) for k in
            ("id", "task", "ts", "model", "total_tokens", "dropped_tokens", "budget_tokens",
             "token_count", "model_window")}


@router.get("/campaigns/{cid}/scenes/{sid}/prompts/{eid}/diff")
def get_scene_prompt_diff(cid: str, sid: str, eid: str, against: str = LIVE_SIDE):
    """What changed between two compositions of this scene's prompt (#130).

    `eid` is the BEFORE side and `against` the after -- either another entry id
    or `live`, the composition as it stands now, which is the comparison the
    feature is named for: a past turn against the preview. The direction is the
    caller's and is not reordered by timestamp, so a reader who picks an older
    turn to compare against gets the diff they asked for rather than a silently
    flipped one.

    Both entries are scoped to the scene for the same reason the detail route
    scopes one: ids are campaign-wide, and an unscoped `against` would diff this
    scene's prompt against another scene's while naming neither.

    404 covers a missing scene, a missing entry and an evicted one -- the
    retention window is why the third is routine rather than exceptional, and
    nothing downstream can tell it from the second. A decision entry
    (`decision_capture.is_decision`) against the live side is 409
    `not_comparable`: the live side is the chat composition.

    The live side is COMPOSED on each request, which is what makes it live and
    is also its one caveat, the same one `store.prompt_log` opens with:
    `context.macros.expand_macros` resolves `{{random:a,b}}` and `{{roll:1d20}}`
    at render time, so a section built out of those differs between two calls
    over an unchanged store. Against a preview, a handful of such lines can show
    as changed when nothing in the campaign moved. Two frozen entries do not
    have this: both were recorded, so the comparison is exact.
    """
    _require_scene(cid, sid)
    base = store.prompt_log.read_entry(cid, eid, scene=sid)
    if base is None:
        raise HTTPException(status_code=404, detail="prompt snapshot not found")
    if against == LIVE_SIDE and decision_capture.is_decision(base):
        # A decide prompt against the chat composition is noise, not a diff
        # (roadmap 01b §3.7); another frozen entry of the same task compares.
        raise HTTPException(status_code=409, detail={"kind": "not_comparable"})
    if against == LIVE_SIDE:
        # Composed here rather than read: `context_breakdown` runs the same
        # assemble/pack pass `GET .../context` does, so the side this diff calls
        # "live" is the one the Context panel is showing.
        # routing-ok: the live side of a prompt diff is a display; it must never refuse
        chat = inference.resolve("chat", cid)
        model = _targets._target_model(chat.chain)
        live = store.context.context_breakdown(cid, sid, model=model, resolved=chat,
                                               images=store.post_images.images_for(
                                                   _targets._primary(chat.chain)))
        head = {"id": LIVE_SIDE, "task": LIVE_SIDE, "ts": "", "model": model, **live,
                "model_window": model_window(_targets._primary(chat.chain)),
                "token_count": store.tokens.counting(model, _targets._target_kind(chat.chain),
                                                     live.get("counted_with", ""))}
    else:
        other = store.prompt_log.read_entry(cid, against, scene=sid)
        if other is None:
            raise HTTPException(status_code=404, detail="prompt snapshot not found")
        head = other
    return {"base": _diff_side(base), "head": _diff_side(head),
            **store.context.compare_breakdowns(base, head)}
