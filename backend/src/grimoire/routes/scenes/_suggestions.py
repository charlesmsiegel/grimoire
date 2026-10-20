"""Scene ideas are drafted against captured controls, then saved explicitly."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request

from ... import store
from ...llm import LLMClient
from .. import runs
from ..common import (
    _campaign_root_or_404,
    computes_only,
    draft_completion,
    get_llm,
    require_inference,
)
from ..models import SceneIdeaCreate, SceneIdeaStatus, SceneIntent, SceneSuggestionsRequest
from ._router import router as router


def _resolve_cast(cid: str, tokens: list[str], memo: dict[str, str] | None = None) -> list[dict]:
    """Cast tokens with the names to show for them.

    `memo` carries token -> name ACROSS calls, for the caller resolving a list
    of records rather than one: every miss is a markdown parse, and a campaign's
    ideas mostly cast the same handful of people over and over. Without it the
    scene ledger re-read the same character file once per row it appeared in --
    60 saved ideas casting three characters cost 180 file parses to render four
    cards. The suggestion and intent routes pass nothing and are unaffected:
    they resolve one record's cast, once.
    """
    out = []
    for tok in tokens:
        kind, _, aid = tok.partition(":")
        name = memo.get(tok) if memo is not None else None
        if name is None:
            try:
                if kind == "pcs":
                    name = store.pcs.read_pc(store.overlay.pc_root(cid, aid), aid)["meta"].get("name", aid)
                else:
                    name = store.characters.read_character(
                        store.overlay.char_root(cid, aid), aid)["meta"].get("name", aid)
            except (store.characters.CharacterNotFound, store.pcs.PCNotFound):
                name = aid
            if memo is not None:
                memo[tok] = name
        out.append({"kind": kind, "id": aid, "name": name})
    return out


@router.post("/campaigns/{cid}/scene-suggestions", status_code=202)
@computes_only
def post_scene_suggestions(cid: str, request: Request,
                           body: SceneSuggestionsRequest | None = None,
                           after: str | None = None, offscreen: bool = False,
                           direction: str = "", rank: bool = True,
                           client: LLMClient = Depends(get_llm),
                           x_grimoire_attempt: str | None = Header(default=None)):
    """Start a set of drafted scene ideas. 202 and a run to poll.

    The ranking half of the prompt is built HERE, while the request is still
    there: it is the expensive read, but it is a read, and a campaign that
    cannot be read has to be refused as a 404 rather than as a run state.

    The request is a JSON body (`SceneSuggestionsRequest`, capstone spec
    §16.3). A body, when present, wins wholly. With none, the query parameters
    build the request exactly as they always have -- they are deprecated, and
    kept for one release so a client that predates the body keeps working.

    Validation order (Decision 7), every refusal answered before a run is
    reserved, since one raised inside the work would surface as a failed run:
    404 for an unknown campaign; the connection (409 `missing_key` first, so a
    reader with no connection hears that before anything else); the snapshot;
    then `store.suggest.resolve_controls` against it -- every 400
    `bad_controls`, then one 409 `stale_drivers`; then the greeting
    candidates and the prompt. An unknown `time_mode` or relation is
    FastAPI's 422. The snapshot and the resolved controls are captured by the
    work, so the payload is parsed against exactly what the prompt showed.
    """
    req = body if body is not None else SceneSuggestionsRequest(
        after=after, offscreen=offscreen, direction=direction, rank=rank)
    try:
        store.campaigns.read_campaign(cid)
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    resolved = require_inference("suggestions", cid)
    snapshot = store.suggest.build_snapshot(cid, offscreen=req.offscreen)
    controls = _controls_or_refuse(cid, snapshot, req)
    # A refresh passes rank=false: re-ranking would reshuffle the greeting cards
    # under the user's cursor, and the ranking is the expensive half of the prompt.
    candidates = (store.suggest.greeting_candidates(cid, req.after, pcless=req.offscreen)
                  if req.rank else [])
    defaults = store.suggest.creation_defaults(cid)
    messages = store.suggest.build_prompt(snapshot, candidates, offscreen=req.offscreen,
                                          direction=req.direction, controls=controls,
                                          defaults=defaults)

    async def work():
        return await draft_completion(
            client, resolved, messages, "suggestions",
            lambda text: _suggestions_payload(cid, text, candidates, req.offscreen,
                                              snapshot, controls, defaults=defaults),
            cid=cid)

    return runs.run_draft(request.app, runs.campaign_subject(cid), "suggestions",
                          x_grimoire_attempt, work)


def _controls_or_refuse(cid: str, snapshot: dict,
                        req: SceneSuggestionsRequest) -> store.suggest.Controls:
    """The request's controls resolved against `snapshot`, or the refusal as
    the HTTP answer: a 400 `{kind, detail, reason}` or a 409 `{kind, detail,
    refs}` (spec §16.3)."""
    try:
        return store.suggest.resolve_controls(
            cid, snapshot, focus_refs=req.focus_refs, avoid_refs=req.avoid_refs,
            must_refs=req.must_refs, time_mode=req.time_mode,
            time_anchor_ref=req.time_anchor_ref,
            time_anchor_relation=req.time_anchor_relation)
    except store.suggest.ControlsError as e:
        raise HTTPException(e.status, detail={
            "kind": e.kind, "detail": e.detail,
            **({"reason": e.reason} if e.reason else {}),
            **({"refs": e.refs} if e.refs is not None else {})}) from e


def _suggestions_payload(cid: str, text: str, candidates: list[dict], offscreen: bool,
                         snapshot: dict, controls: store.suggest.Controls, *,
                         defaults: dict | None = None) -> dict:
    """The model's suggestions as the body this route has always returned,
    each row now carrying its validated provenance (spec §15.2): the claimed
    drivers, the time anchor, the date check and the constraint misses.

    Split from the route so the shape survives detachment unchanged: what a
    client receives is the same dict it received when this blocked, and only
    where it reads it from moved. Parsed against `snapshot` and `controls`,
    the route's own capture, so nothing here re-reads the drivers.
    """
    loc_names = {e["id"]: e.get("name", e["id"]) for e in store.overlay.list_entities(cid, "locations")}
    out = []
    for s in store.suggest.parse_output(text, cid, offscreen=offscreen, snapshot=snapshot,
                                        controls=controls):
        loc = {"id": s["location"], "name": loc_names.get(s["location"], s["location"])} if s["location"] else None
        out.append({**s, "cast": _resolve_cast(cid, s["cast"]), "location": loc})
    picks = (store.suggest.parse_greeting_picks(text, {c["id"] for c in candidates})
             if candidates else [])
    return {"suggestions": out, "greeting_picks": picks,
            **({"prompt_field_statuses": dict(defaults["statuses"])} if defaults is not None else {}),
            "next_date": store.suggest.parse_next_date(text, cid, snapshot=snapshot,
                                                       controls=controls)}


@router.post("/campaigns/{cid}/scene-intent", status_code=202)
@computes_only
def post_scene_intent(cid: str, body: SceneIntent, request: Request,
                      client: LLMClient = Depends(get_llm),
                      x_grimoire_attempt: str | None = Header(default=None)):
    """Metadata implied by the user's own scene-start description. Computes and
    returns; the confirm form is what decides whether any of it is written."""
    try:
        store.campaigns.read_campaign(cid)
    except store.campaigns.CampaignNotFound as exc:
        raise HTTPException(status_code=404, detail="campaign not found") from exc
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="empty scene description")
    resolved = require_inference("intent", cid)
    # The pilot of structured generation (01f-S4): the reply is asked for in
    # the schema the prompt carries, so a provider that enforces it cannot
    # invent a location id or a cast token. `parse_intent` still drops one.
    defaults = store.suggest.creation_defaults(cid)
    messages, schema = store.suggest.build_intent_request(cid, body.text,
                                                          offscreen=body.offscreen,
                                                          defaults=defaults)

    async def work():
        return await draft_completion(
            client, resolved, messages, "intent",
            lambda text: _intent_payload(cid, text, body.offscreen, defaults=defaults), cid=cid,
            schema=schema)

    return runs.run_draft(request.app, runs.campaign_subject(cid), "intent",
                          x_grimoire_attempt, work)


def _intent_payload(cid: str, text: str, offscreen: bool, *, defaults: dict | None = None) -> dict:
    """The parsed intent as the body this route has always returned -- see
    `_suggestions_payload` for why it is a function rather than inline."""
    got = store.suggest.parse_intent(text, cid, offscreen=offscreen)
    loc_names = {e["id"]: e.get("name", e["id"]) for e in store.overlay.list_entities(cid, "locations")}
    loc = ({"id": got["location"], "name": loc_names.get(got["location"], got["location"])}
           if got["location"] else None)
    return {"title": got["title"], "date": got["date"], "location": loc,
            "focus": got["focus"],
            **({"prompt_field_statuses": dict(defaults["statuses"])} if defaults is not None else {}),
            "cast": _resolve_cast(cid, got["cast"])}


# ---- the scene ledger (#88) ----
# Literal third segments, so they are registered before `entities`' generic
# `/campaigns/{cid}/{kind}` (see that module's docstring and
# tests/test_route_order.py). Named `scene-ideas` rather than `ledger`: that
# route is the continuity ledger's (`routes.campaigns.get_ledger`).
def _idea_card(cid: str, idea: dict, loc_names: dict[str, str],
               cast_names: dict[str, str]) -> dict:
    """A ledger row in the shape the picker already renders a suggestion in --
    cast resolved to names, location to an {id, name} or null -- plus the
    ledger's own fields. `validate_ideas` has already dropped every id the
    campaign no longer has, so nothing here has to guess at a dangling one.

    Both name maps are resolved once for the whole list and threaded through:
    locations as a dict the caller builds, actors as the memo `_resolve_cast`
    fills in as it goes."""
    loc = ({"id": idea["location"], "name": loc_names.get(idea["location"], idea["location"])}
           if idea["location"] else None)
    return {**idea, "cast": _resolve_cast(cid, idea["cast"], cast_names), "location": loc}


@router.get("/campaigns/{cid}/scene-ideas")
def get_scene_ideas(cid: str, greetings: bool = True):
    """The whole ledger: saved ideas (re-validated against the campaign as it
    stands now) followed by the greeting entries `playing` composes.

    Status and mode (`pcless`) filtering is deliberately absent -- the picker
    wants the active entries for its own mode, a management surface wants
    everything, and a campaign's ledger is small enough that one read serving
    both beats a query language neither needs.

    `greetings=false` is the exception, and it is about cost rather than taste.
    Composing the greeting half means `available_greetings`, which parses the
    frontmatter of every greeting in the campaign; the picker renders greetings
    from its own `/greetings/available` call (ranked, and chipped with
    `unlocked`) and drops every greeting row this route composes. Asking for
    the saved half alone is what keeps opening the chooser from paying for that
    sweep twice and using neither copy.

    Each half is read tolerantly, the failure policy `get_ledger` states for
    the continuity ledger: scene_ideas.json is hand-editable and read by a bare
    `json.loads`, so a garbled one must cost its own section rather than the
    whole view -- taking the greeting half, and the reader's ability to start a
    scene at all, down with it.
    """
    _campaign_root_or_404(cid)

    def _tolerant(read):
        try:
            return read()
        except Exception:  # noqa: BLE001 — a garbled file empties its half, not the view
            return []

    loc_names = {e["id"]: e.get("name", e["id"]) for e in store.overlay.list_entities(cid, "locations")}
    cast_names: dict[str, str] = {}
    saved = _tolerant(lambda: store.suggest.validate_ideas(cid, store.scene_ideas.records(cid)))
    # `loc_names` is the campaign's whole location list, already read a line
    # above to caption these very cards -- handing the ids over keeps
    # `greeting_ideas` from reading every location file a second time to answer
    # one request.
    composed = (_tolerant(lambda: store.playing.greeting_ideas(cid, known_locations=set(loc_names)))
                if greetings else [])
    return [_idea_card(cid, i, loc_names, cast_names) for i in saved + composed]


@router.post("/campaigns/{cid}/scene-ideas")
def post_scene_idea(cid: str, body: SceneIdeaCreate):
    """Save an idea. References are validated here as well as on every read, so
    a token this campaign never had cannot enter the file at all.

    Driver provenance is validated here too (`suggest.idea_provenance`, spec
    §17): existence rather than activity, so a card whose anchor fired while it
    was on screen keeps that anchor as the evidence its staleness is read from.
    `scene_ideas` only stores it -- it may not import continuity."""
    _campaign_root_or_404(cid)
    if not body.title.strip() and not body.premise.strip():
        raise HTTPException(status_code=400, detail="an idea needs a title or a premise")
    refs = store.suggest.valid_refs(cid, body.cast, body.location, body.date,
                                    offscreen=body.pcless)
    prov = store.suggest.idea_provenance(cid, body.drivers, body.time_anchor)
    return {"id": store.scene_ideas.add(cid, body.title, body.premise, refs["cast"],
                                        refs["location"], refs["date"], body.pcless,
                                        body.source, drivers=prov["drivers"],
                                        time_anchor=prov["time_anchor"], focus=body.focus)}


@router.put("/campaigns/{cid}/scene-ideas/{lid}")
def put_scene_idea(cid: str, lid: str, body: SceneIdeaStatus):
    """Dismiss, restore, or record that an idea became a scene.

    A greeting entry is not in scene_ideas.json -- its lifecycle is
    `played.json`'s -- so those ids delegate to `playing.mark_greeting` rather
    than being copied here: dismissed is "skipped", used is "completed" (the
    off-screen mark), active clears the mark. A greeting actually *played* in a
    scene refuses to move, which surfaces as the same 409 `POST
    /greetings/{gid}/mark` already returns.
    """
    _campaign_root_or_404(cid)
    if lid.startswith(store.scene_ideas.GREETING_PREFIX):
        gid = lid[len(store.scene_ideas.GREETING_PREFIX):]
        mark = {store.scene_ideas.ACTIVE: "none", store.scene_ideas.USED: "completed",
                store.scene_ideas.DISMISSED: "skipped"}[body.status]
        try:
            store.playing.mark_greeting(cid, gid, mark)
        except store.greetings.GreetingNotFound as err:
            raise HTTPException(status_code=404, detail="greeting not found") from err
        except store.playing.PlayError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}
    if not store.scene_ideas.set_status(cid, lid, body.status, body.scene):
        raise HTTPException(status_code=404, detail="idea not found")
    return {"ok": True}
