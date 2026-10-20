"""worlds: imports responsibilities."""

from __future__ import annotations

from fastapi import Depends, File, Form, Header, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from ... import store
from ...llm import LLMClient
from .. import runs
from ..common import (
    UsableInference,
    _dump,
    _world_root_or_404,
    draft_completion,
    get_llm,
    require_inference,
)
from ..models import LorebookCommit, ScenarioProposal, ScenarioUrlBody
from ._router import router as router


# ---- world lorebook import ----
@router.post("/worlds/{wid}/lorebook/parse")
async def post_lorebook_parse(wid: str, file: UploadFile = File(...), format: str = Form(...)):
    _world_root_or_404(wid)
    data = await file.read()
    try:
        return {"entries": store.lorebook.parse(data, format)}
    except (store.lorebook.LorebookError, store.cards.CardParseError) as exc:
        raise HTTPException(status_code=400, detail=f"could not parse: {exc}") from exc


@router.post("/worlds/{wid}/lorebook/import")
def post_lorebook_import(wid: str, body: LorebookCommit):
    root = _world_root_or_404(wid)
    try:
        created = store.lorebook.commit(root, [_dump(e) for e in body.entries])
    except store.lorebook.LorebookError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"created": created}


# ---- scenario-card import (#217) ----
# Three routes, one flow: get the card (from an upload or a URL), extract a
# proposal from it, and — after the user has edited that proposal — write it.
# Only the third writes anything, which is what makes the review gate real
# rather than a confirmation dialog over work already done.
async def _scenario_proposal(card: dict, client: LLMClient,
                             resolved: UsableInference, root) -> dict:
    """Extract a proposal from `card`, as a detached run's outcome.

    One bounded completion, exactly like the tagline and voice-anchor previews.
    A reply the extraction cannot use — prose, a refusal, a truncated object —
    is not an error: `parse_output` yields empty sections and the proposal falls
    back to what the card alone holds, which is its own world-info and its
    openers. A provider that *failed* is a different thing and surfaces as the
    status its failure maps to (#213), leaving the world untouched — reported
    on the run now rather than raised, so a client that was away while it
    failed still reads the reason.
    """
    # Metered like every other generation (#152): a world-level call, so the
    # row carries no campaign -- the same shape the tagline and voice-anchor
    # previews file.
    #
    # The world's roster is read in the SHAPE, after the call, so each proposed
    # row can say whether the import would REUSE a character of that name
    # rather than create one -- and so it describes the roster as it stands
    # when the proposal is built rather than as it stood minutes earlier, which
    # detachment now makes a real difference.
    return await draft_completion(
        client, resolved, store.scenario.build_prompt(card), "scenario",
        lambda text: store.scenario.proposal(
            card, store.scenario.parse_output(text),
            [c["name"] for c in store.characters.list_characters(root)]))


@router.post("/worlds/{wid}/scenario/parse", status_code=202)
async def post_scenario_parse(wid: str, request: Request,
                              file: UploadFile = File(...), format: str = Form(...),
                              client: LLMClient = Depends(get_llm),
                              x_grimoire_attempt: str | None = Header(default=None)):
    """Start a scenario proposal from an uploaded card. 202 and a run to poll.

    **Reserved before the upload is read**, which is the ordering these two
    routes need and the other ten do not: the attempt has to be discoverable
    for as long as the request could still be accepted, and reading a card off
    a phone is long enough for a connection to die inside it. Reserved after,
    a client whose POST vanished mid-upload asks `?attempt=` and is told,
    truthfully, that there is no such run -- and then the handler goes on to
    reserve one and spend a provider call, so the retry pays twice.

    A card that will not parse is still a 400 while the reader is holding the
    file picker: `runs.reservation` releases the run on that path, so the
    refusal reaches them as a refusal and the record does not stay `running`.
    """
    root = _world_root_or_404(wid)
    # Before the upload is read, and before the download in the sibling route:
    # "you have no model configured" is a setup mistake, and reporting it only
    # after the user has fixed a card (or waited on a slow host) tells them the
    # wrong thing first. Before the RESERVATION too, so a keyless install does
    # not leave a failed run behind for every press.
    resolved = require_inference("scenario")
    run, fresh = await _reserve_scenario(request, wid, x_grimoire_attempt)
    if not fresh:
        return {"run": runs.run_payload(run)}
    with runs.reservation(request.app, run):
        data = await file.read()
        try:
            card = store.cards.loads(data, format)
        except store.cards.CardParseError as exc:
            raise HTTPException(status_code=400, detail=f"could not parse card: {exc}") from exc
        return await run_in_threadpool(_detach_scenario, request, run, card,
                                       client, resolved, root)


@router.post("/worlds/{wid}/scenario/parse-url", status_code=202)
async def post_scenario_parse_url(wid: str, body: ScenarioUrlBody, request: Request,
                                  client: LLMClient = Depends(get_llm),
                                  x_grimoire_attempt: str | None = Header(default=None)):
    """The same, from a URL, and the sharper case for reserving first: the
    download is a whole HTTP fetch against somebody else's host, and it is the
    longest thing either of these routes does before any of the work is
    addressable.

    The download itself stays on the request rather than in the run: a URL that
    fetches nothing is a 404 the reader retypes, and hiding it behind a run
    would make "not a valid URL" arrive as a failed generation."""
    root = _world_root_or_404(wid)
    resolved = require_inference("scenario")
    run, fresh = await _reserve_scenario(request, wid, x_grimoire_attempt)
    if not fresh:
        return {"run": runs.run_payload(run)}
    with runs.reservation(request.app, run):
        try:
            # The download is blocking and this route is async, so it goes to the
            # threadpool rather than stalling the event loop for a slow host --
            # the same treatment `post_world_import` gives its unpacking.
            data, fmt, _url, _node = await run_in_threadpool(
                store.characters.download_card, body.url)
            card = store.cards.loads(data, fmt)
        except store.chub.ChubParseError as err:
            raise HTTPException(status_code=400, detail="not a valid URL") from err
        except store.chub.ChubFetchError as err:
            raise HTTPException(status_code=404,
                                detail="could not fetch a card from that URL") from err
        except store.cards.CardParseError as exc:
            raise HTTPException(status_code=400, detail=f"could not parse card: {exc}") from exc
        return await run_in_threadpool(_detach_scenario, request, run, card,
                                       client, resolved, root)


async def _reserve_scenario(request: Request, wid: str, attempt_id: str | None):
    """Reserve the run both parse routes publish before doing anything slow.

    **Through `run_in_threadpool`, and it has to be.** These two routes are
    `async def` -- one awaits an upload, the other a download -- so their
    bodies run ON the lifespan loop, and reserving a run builds its handshake
    events through the portal, which raises when called from the loop thread.
    Every other draft route is a plain `def` and is already in a worker; these
    two get there explicitly.
    """
    return await run_in_threadpool(
        runs.reserve_draft, request.app, runs.world_subject(wid), "scenario",
        attempt_id)


def _detach_scenario(request: Request, run, card: dict, client: LLMClient,
                     resolved: UsableInference, root) -> dict:
    """Hand the proposal to the runner. Both parse routes end here.

    One function because the two differ only in where the card came from, and
    that difference is settled by the time either gets here.

    In a worker for `_reserve_scenario`'s reason: `runner.start` submits
    through the same portal, which refuses to be called from the loop.
    """
    async def work():
        return await _scenario_proposal(card, client, resolved, root)

    runs.start_computing(request.app, run, work)
    return {"run": runs.run_payload(run)}


@router.post("/worlds/{wid}/scenario/import")
async def post_scenario_import(wid: str, body: ScenarioProposal):
    root = _world_root_or_404(wid)
    prop = _dump(body)
    # `art` is a request option, not part of the proposal — it rides on the same
    # body so the reviewer's "download the openers' images" checkbox needs no
    # second round trip, and is lifted back out here.
    prop.pop("art", None)
    # The scenario importer reuses an existing character with the same full
    # name, but a PC with that name is a different actor. Check the complete
    # proposal before `apply` writes the first character or greeting.
    existing = {row["name"].strip().casefold(): row["id"]
                for row in store.characters.list_characters(root)}
    try:
        for row in prop.get("characters", []):
            name = str(row.get("name") or "").strip()
            if name:
                known = existing.get(name.casefold())
                store.actor_names.require_unique(
                    name, scope="world", scope_id=wid,
                    actor_ref=f"characters:{known}" if known else None)
    except store.actor_names.ActorNameError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        # Localizing the openers' art downloads one image per reference, so the
        # whole write goes to the threadpool: without it a card with a dozen
        # illustrated openers holds the event loop for the length of a dozen
        # HTTP fetches.
        return await run_in_threadpool(store.scenario.apply, root, wid, prop, art=body.art)
    except store.lorebook.LorebookError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
